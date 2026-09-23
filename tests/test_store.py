from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.store import Store


def item(quantity=2, product_id=1, price=100.50):
    return {"id": product_id, "article": f"ЕКТ-{product_id}", "name": "Кабель", "price": price, "quantity": quantity}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def confirm(self, proposal, stocks=None, user=1):
        return self.store.confirm(user, proposal["id"], stocks if stocks is not None else {1: 10}, explicit=True)

    def test_requires_literal_explicit_confirmation(self):
        proposal = self.store.propose(1, [item()])
        for explicit in (False, None, "yes", 1):
            self.assertEqual(self.store.confirm(1, proposal["id"], {1: 10}, explicit=explicit)["status"], "confirmation_required")
        self.assertEqual(self.store.get_cart(1)["items"], [])
        self.assertEqual(self.confirm(proposal)["cart"]["items"][0]["quantity"], 2)

    def test_foreign_confirmation_and_unknown_token(self):
        proposal = self.store.propose(1, [item()])
        self.assertEqual(self.confirm(proposal, user=2)["status"], "not_found")
        self.assertEqual(self.store.confirm(1, "invalid", {1: 10}, explicit=True)["status"], "not_found")
        self.assertEqual(self.store.get_cart(1)["items"], [])
        self.assertIsNone(self.store.cart_by_token("invalid"))

    def test_expired_and_superseded_proposals(self):
        with patch("app.store._now", return_value=1000):
            old = self.store.propose(1, [item()], ttl_seconds=10)
        with patch("app.store._now", return_value=1010):
            self.assertEqual(self.confirm(old)["status"], "expired")
            self.assertIsNone(self.store.pending(1))
        old = self.store.propose(1, [item()])
        new = self.store.propose(1, [item(3)])
        self.assertEqual(self.confirm(old)["status"], "invalidated")
        self.assertEqual(self.store.pending(1)["id"], new["id"])

    def test_replay_is_idempotent_even_when_concurrent(self):
        proposal = self.store.propose(1, [item()])
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.confirm(proposal), range(8)))
        self.assertEqual(sum(not result["replayed"] for result in results), 1)
        self.assertEqual(self.store.get_cart(1)["items"][0]["quantity"], 2)
        self.store.clear_cart(1)
        self.assertTrue(self.confirm(proposal)["replayed"])
        self.assertEqual(self.store.get_cart(1)["items"], [])

    def test_aggregate_stock_and_atomic_multi_item_failure(self):
        self.confirm(self.store.propose(1, [item(7)]))
        proposal = self.store.propose(1, [item(4), item(1, 2)])
        result = self.confirm(proposal, {1: 10, 2: 100})
        self.assertEqual(result["status"], "insufficient_stock")
        self.assertEqual(result["errors"][0]["remaining"], 3)
        cart = self.store.get_cart(1)
        self.assertEqual(len(cart["items"]), 1)
        self.assertEqual(cart["items"][0]["quantity"], 7)

    def test_unknown_zero_and_invalid_stock(self):
        proposal = self.store.propose(1, [item()])
        for stocks in ({}, {1: None}, {1: float("nan")}, {1: -2}, {1: True}):
            self.assertEqual(self.confirm(proposal, stocks)["status"], "unknown_stock")
        self.assertEqual(self.confirm(proposal, {1: 0})["status"], "insufficient_stock")
        self.assertEqual(self.store.get_cart(1)["items"], [])

    def test_rejects_invalid_quantities_and_prices(self):
        for quantity in (0, -1, None, True, "bad", float("nan"), float("inf")):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.store.propose(1, [item(quantity)])
        for price in (-1, None, True, float("nan")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.store.propose(1, [item(price=price)])
        self.assertEqual(self.store.get_cart(1)["items"], [])

    def test_deduplicates_items_and_freezes_proposal(self):
        source = [item(0.1), item(0.2)]
        proposal = self.store.propose(1, source)
        source[0]["quantity"] = 99
        source[0]["price"] = 0
        self.assertEqual(proposal["items"][0]["quantity"], 0.3)
        self.assertEqual(len(proposal["items"]), 1)
        result = self.confirm(proposal, {1: 0.3})
        self.assertEqual(result["cart"]["total"], 30.15)
        self.assertEqual(result["cart"]["items"][0]["price"], 100.5)

    def test_cart_token_tracks_current_cart_and_survives_restart(self):
        token = self.store.get_cart(1)["token"]
        self.confirm(self.store.propose(1, [item()]))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.cart_by_token(token)["total"], 201)
        self.assertNotEqual(token, self.store.get_cart(2)["token"])

    def test_new_proposal_cannot_silently_reprice_existing_cart(self):
        self.confirm(self.store.propose(1, [item(price=100)]))
        with self.assertRaises(ValueError):
            self.store.propose(1, [item(price=150)])
        self.assertEqual(self.store.get_cart(1)["total"], 200)

    def test_finite_large_totals_do_not_crash_checkout(self):
        proposal = self.store.propose(1, [item(quantity=10**15, price=10**15)])
        self.assertEqual(self.confirm(proposal, {1: 10**15})["cart"]["total"], 10**30)

    def test_history_is_persistent_bounded_and_user_scoped(self):
        for n in range(self.store.HISTORY_KEEP + 3):
            self.store.add_message(1, "user", f"Сообщение {n}")
        self.store.add_message(2, "user", "Другой клиент")
        self.store.close()
        self.store = Store(self.path)
        history = self.store.history(1, 1000)
        self.assertEqual(len(history), self.store.HISTORY_KEEP)
        self.assertEqual(history[0]["content"], "Сообщение 3")
        self.assertEqual(len(self.store.history(1)), 16)
        self.assertEqual(self.store.history(2), [{"role": "user", "content": "Другой клиент"}])

    def test_saved_requests_are_snapshots_and_idempotent(self):
        self.assertEqual(self.store.save_request(1)["status"], "empty_cart")
        self.confirm(self.store.propose(1, [item()]))
        saved = self.store.save_request(1)
        self.assertEqual(saved["status"], "request")
        self.assertEqual(saved, self.store.save_request(1))
        self.store.clear_cart(1)
        self.assertEqual(self.store.orders(1), [saved])
        self.assertEqual(self.store.orders(2), [])
        self.assertEqual(saved["items"][0]["quantity"], 2)

    def test_cancel_and_clear_invalidate_pending(self):
        proposal = self.store.propose(1, [item()])
        self.store.cancel_proposal(1)
        self.assertEqual(self.confirm(proposal)["status"], "cancelled")
        proposal = self.store.propose(1, [item()])
        self.store.clear_cart(1)
        self.assertEqual(self.confirm(proposal)["status"], "invalidated")


if __name__ == "__main__":
    unittest.main()
