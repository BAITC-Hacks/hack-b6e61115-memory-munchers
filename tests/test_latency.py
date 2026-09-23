"""Hard response deadlines and single-pass planning, using only offline fakes."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.agent import Assistant
from app.config import Settings
from app.store import Store


class LocalCatalog:
    def __init__(self):
        self.items = {
            index: {"id": index, "article": f"88000000{index}_", "name": f"Тестовый автомат {index}",
                    "price": 100 * index, "quantity": 20, "stock_fresh": True,
                    "stock_source": "offline_test", "properties": {"NOMINALNYY_TOK": "16"},
                    "certificates": [], "stores": [], "url": f"https://ekt.kz/catalog/test-{index}/"}
            for index in range(1, 4)
        }

    def get(self, key):
        if isinstance(key, str) and not key.isdigit():
            return next((deepcopy(p) for p in self.items.values() if p["article"] == key), None)
        return deepcopy(self.items.get(int(key))) if str(key).isdigit() else None

    def search(self, query, limit=5):
        product = self.get(query)
        return ([product] if product else [deepcopy(self.items[1])])[:limit]

    def alternatives(self, product_id, limit=3):
        return [deepcopy(p) for pid, p in self.items.items() if pid != product_id][:limit]

    async def refresh(self, product_id):
        return self.get(product_id)


def tool_response(name, arguments):
    call = SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments),
                           call_id="offline-call-1", id="offline-item-1")
    return SimpleNamespace(output=[call], output_text="", usage=None)


class LatencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name)
        self.config = Settings(api_key="", telegram_token="", bot_username="",
                               data_dir=path, db_path=path / "latency.sqlite",
                               public_base_url="http://localhost:8000",
                               response_timeout=0.15, model_timeout=0.08)
        self.catalog = LocalCatalog()
        self.store = Store(self.config.db_path)
        self.assistant = Assistant(self.catalog, self.store, self.config)
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.close()
        self.temp.cleanup()

    def install_model(self, *, side_effect=None, response=None):
        create = AsyncMock(side_effect=side_effect, return_value=response)
        self.assistant.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        return create

    def propose(self, items=(1,), quantity=2):
        return self.store.propose("client", [self.catalog.get(pid) | {"quantity": quantity} for pid in items])

    def assert_cart_untouched(self):
        self.assertEqual(self.store.get_cart("client")["items"], [])

    async def bounded(self, coroutine, maximum=0.45):
        started = time.perf_counter()
        # An outer watchdog makes regressions fail quickly instead of sleeping 60s.
        result = await asyncio.wait_for(coroutine, timeout=maximum)
        self.assertLess(time.perf_counter() - started, maximum)
        return result

    async def test_model_hanging_for_sixty_seconds_returns_bounded_fallback(self):
        cancelled = asyncio.Event()

        async def hang(**_kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        create = self.install_model(side_effect=hang)
        result = await self.bounded(self.assistant.reply("client", "Помоги выбрать защиту для оборудования"))
        self.assertTrue(result["text"])
        self.assertNotEqual(result.get("status"), "confirmed")
        self.assertIsNone(result.get("proposal"))
        self.assertEqual(create.await_count, 1)
        self.assertTrue(cancelled.is_set(), "The timed-out model coroutine was left running")
        self.assertIsNone(self.store.pending("client"))
        self.assert_cart_untouched()

    async def test_timeout_after_draft_creation_leaves_no_dangling_proposal(self):
        self.install_model(response=tool_response("propose_cart", {
            "items": [{"product_id": 1, "quantity": 2}]}))
        original_tool = self.assistant.run_tool
        created = asyncio.Event()

        async def slow_proposal(user_id, name, args, manager=False):
            value = await original_tool(user_id, name, args, manager)
            if name == "propose_cart":
                self.assertEqual(value["status"], "pending")
                created.set()
                await asyncio.sleep(60)
            return value

        self.assistant.run_tool = slow_proposal
        result = await self.bounded(self.assistant.reply("client", "Подготовь два автомата в корзину"))
        self.assertTrue(created.is_set())
        self.assertIsNone(result.get("proposal"))
        self.assertIsNone(self.store.pending("client"))
        self.assert_cart_untouched()

    async def test_busy_user_gets_immediate_reply_without_second_model_call(self):
        entered = asyncio.Event()

        async def hang(**_kwargs):
            entered.set()
            await asyncio.sleep(60)

        create = self.install_model(side_effect=hang)
        first = asyncio.create_task(self.assistant.reply("client", "Помоги выбрать оборудование"))
        self.tasks.append(first)
        await asyncio.wait_for(entered.wait(), 0.15)
        second = await self.bounded(self.assistant.reply("client", "А какой вариант лучше?"), maximum=0.08)
        self.assertEqual(second.get("status"), "busy")
        self.assertFalse(first.done(), "The queued message waited for the first request")
        await self.bounded(first)
        self.assertEqual(create.await_count, 1)
        self.assert_cart_untouched()

    async def test_confirmation_of_hanging_stock_is_bounded_and_cancels_refresh(self):
        proposal = self.propose()
        cancelled = asyncio.Event()

        async def hang(_product_id):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        self.catalog.refresh = hang
        result = await self.bounded(self.assistant.confirm("client", proposal["id"], explicit=True))
        self.assertTrue(result["text"])
        self.assertNotEqual(result.get("status"), "confirmed")
        self.assertTrue(cancelled.is_set())
        self.assert_cart_untouched()

    async def test_multiple_stock_checks_run_in_parallel_before_atomic_confirmation(self):
        proposal = self.propose(items=(1, 2, 3), quantity=2)
        entered = set()
        all_started = asyncio.Event()

        async def concurrent_stock(product_id):
            entered.add(product_id)
            if len(entered) == 3:
                all_started.set()
            # A sequential implementation cannot pass this barrier before timeout.
            await all_started.wait()
            await asyncio.sleep(0.01)
            return self.catalog.get(product_id)

        self.catalog.refresh = concurrent_stock
        result = await self.bounded(self.assistant.confirm("client", proposal["id"], explicit=True))
        self.assertEqual(entered, {1, 2, 3})
        self.assertEqual(result["status"], "confirmed")
        cart = self.store.get_cart("client")
        self.assertEqual(len(cart["items"]), 3)
        self.assertEqual(cart["total"], 1200)
        self.assertTrue(all(item["quantity"] == 2 for item in cart["items"]))

    async def test_busy_confirmation_cannot_queue_a_second_mutation(self):
        proposal = self.propose()
        entered = asyncio.Event()

        async def hang(_product_id):
            entered.set()
            await asyncio.sleep(60)

        self.catalog.refresh = hang
        first = asyncio.create_task(self.assistant.confirm("client", proposal["id"], explicit=True))
        self.tasks.append(first)
        await asyncio.wait_for(entered.wait(), 0.15)
        second = await self.bounded(self.assistant.confirm("client", proposal["id"], explicit=True), maximum=0.08)
        self.assertEqual(second.get("status"), "busy")
        self.assertFalse(first.done())
        await self.bounded(first)
        self.assert_cart_untouched()

    async def test_questions_about_payment_and_delivery_preserve_pending_composition(self):
        original = self.propose()
        create = self.install_model(side_effect=AssertionError("Published terms must not call OpenAI"))
        for question in ("Как оплатить?", "А сколько стоит доставка?", "Какая минимальная партия?"):
            with self.subTest(question=question):
                result = await self.bounded(self.assistant.reply("client", question))
                self.assertTrue(result["text"])
                pending = self.store.pending("client")
                self.assertIsNotNone(pending)
                self.assertEqual(pending["id"], original["id"])
                self.assertEqual(pending["items"], original["items"])
                self.assert_cart_untouched()
        self.assertEqual(create.await_count, 0)

    async def test_changed_quantity_replaces_old_pending_composition(self):
        original = self.propose(quantity=2)
        result = await self.bounded(self.assistant.reply("client", "Подготовь 880000001_ в корзину 3 шт"))
        pending = self.store.pending("client")
        self.assertIsNotNone(pending)
        self.assertNotEqual(pending["id"], original["id"])
        self.assertEqual(pending["items"][0]["quantity"], 3)
        self.assertEqual(result["proposal"]["id"], pending["id"])
        rejected = await self.assistant.confirm("client", original["id"], explicit=True)
        self.assertNotEqual(rejected.get("status"), "confirmed")
        self.assert_cart_untouched()

    async def test_explicit_cancellation_removes_proposal_without_cart_mutation(self):
        original = self.propose()
        await self.bounded(self.assistant.reply("client", "Отмена"))
        self.assertIsNone(self.store.pending("client"))
        rejected = await self.assistant.confirm("client", original["id"], explicit=True)
        self.assertNotEqual(rejected.get("status"), "confirmed")
        self.assert_cart_untouched()

    async def test_tool_planning_uses_one_api_round_even_for_cart_request(self):
        create = self.install_model(response=tool_response("search_catalog", {"query": "автомат"}))
        result = await self.bounded(self.assistant.reply("client", "Подбери 2 автомата в корзину"))
        self.assertEqual(create.await_count, 1, "A second model round adds avoidable latency")
        self.assertTrue(result["text"])
        self.assertTrue(result.get("products"))
        self.assertIn("search_catalog", result["trace"])
        self.assert_cart_untouched()

    async def test_openai_sdk_retries_are_disabled(self):
        fake_client = Mock()
        with patch("app.agent.AsyncOpenAI", return_value=fake_client) as constructor:
            assistant = Assistant(self.catalog, self.store,
                                  replace(self.config, api_key="offline-placeholder-not-a-real-key"))
        self.assertIs(assistant.client, fake_client)
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        self.assertLessEqual(constructor.call_args.kwargs["timeout"], self.config.model_timeout)


if __name__ == "__main__":
    unittest.main()
