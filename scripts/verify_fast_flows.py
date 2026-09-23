"""Live assistant scenarios, temporary baskets, read-only EKT access.

Run after configuring .env. This script never sends Telegram messages or writes
to EKT. A timeout/fallback response is recorded as an incomplete scenario.
"""

import asyncio
import argparse
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import Assistant
from app.attachments import extract_attachment
from app.catalog import Catalog
from app.config import settings
from app.store import Store


OUT = ROOT / "data" / "fast_flow_results.json"
SKU = "010500006_"
ABSENT_SKU = "310100024_"


def token_counts(usages):
    result = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for usage in usages:
        for key in result:
            result[key] += usage.get(key, 0)
    return result


def numeric_value(value):
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def matches_recipe(product):
    props = product.get("properties", {})
    current = str(props.get("NOMINALNYY_TOK", "")).replace(",", ".")
    pole = str(props.get("KOLICHESTVO_POLYUSOV", ""))
    curve = str(props.get("KHARAKTERISTIKA_SRABATYVANIYA", "")).upper().replace("С", "C")
    voltage = str(props.get("NOMINALNOE_NAPRYAZHENIE", ""))
    capacity = str(props.get("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST", "")).replace(",", ".")
    return bool(numeric_value(current) == 16 and numeric_value(pole) == 1
                and curve == "C" and "230" in voltage and "4.5" in capacity)


def first_product_in_text(catalog, text):
    """Read the first article column, including alphabetic/Cyrillic SKUs."""
    article_index = None
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        headers = [cell.casefold() for cell in cells]
        if "article" in headers or "артикул" in headers:
            article_index = headers.index("article" if "article" in headers else "артикул")
            continue
        if article_index is not None and len(cells) > article_index and cells[article_index]:
            article = cells[article_index]
            # Do not skip an unknown first row and mistakenly validate a later row.
            return catalog.get(article) or {"id": None, "article": article}
    return None


def photo_spec_checks(products, queries):
    """Validate only specifications the actual vision tool query supplied."""
    query = queries.casefold().replace(",", ".").replace("с", "c")
    checks = {}
    properties = [product.get("properties", {}) for product in products]
    if re.search(r"\bc\s*63\b", query):
        checks["all_candidates_respect_current_and_curve"] = all(
            numeric_value(props.get("NOMINALNYY_TOK", "")) == 63
            and str(props.get("KHARAKTERISTIKA_SRABATYVANIYA", "")).upper().replace("С", "C") == "C"
            for props in properties)
    if re.search(r"\b1\s*[pрп]\b|однополю", query):
        checks["all_candidates_respect_single_pole"] = all(
            re.fullmatch(r"\s*1\s*", str(props.get("KOLICHESTVO_POLYUSOV", ""))) for props in properties)
    if re.search(r"\b4500\s*[aа]\b|\b4\.5\s*[кk][aа]\b", query):
        checks["all_candidates_respect_capacity"] = all(
            "4.5" in str(props.get("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST", "")).replace(",", ".")
            for props in properties)
    return checks


async def main(only=None):
    selected = set(only or [])
    if not settings.api_key:
        raise SystemExit("OPENAI_API_KEY missing; live scenarios were not run")
    if settings.model != "gpt-5.4-mini":
        raise SystemExit("Runtime is not gpt-5.4-mini; this fast probe refuses to run the older slow model")
    suite_started = time.perf_counter()
    catalog = Catalog(settings.data_dir)
    product = catalog.get(SKU)
    absent = catalog.get(ABSENT_SKU)
    if not product or not absent:
        raise SystemExit("Required catalogue samples are missing")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_end_to_end_assistant_reply",
        "model": settings.model, "research_model": settings.research_model,
        "model_timeout_seconds": settings.model_timeout,
        "response_timeout_seconds": settings.response_timeout,
        "catalog_products": catalog.count,
        "scope": "Assistant.reply plus attachment parsing; excludes Telegram delivery and user upload transfer.",
        "store": "Temporary SQLite only; deleted after verification.",
        "external_writes": False, "telegram_messages": False,
        "runs": [], "setup": {},
        "limitations": "One pass per different scenario is not a p95 estimate or a latency SLA.",
    }
    invocation = {
        "started_at": report["created_at"],
        "model": settings.model, "research_model": settings.research_model,
        "model_timeout_seconds": settings.model_timeout,
        "response_timeout_seconds": settings.response_timeout,
        "code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in ("app/agent.py", "app/catalog.py", "app/attachments.py", "app/config.py")},
        "catalog_source": {"file": str(settings.data_dir / "raw_details.jsonl"),
                           "size_bytes": (settings.data_dir / "raw_details.jsonl").stat().st_size,
                           "products": catalog.count},
    }
    report["current_invocation"] = invocation
    if selected and OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8"))
        report["runs"] = [row for row in previous["runs"] if row["scenario"] not in selected]
        report["previous_attempts"] = previous.get("previous_attempts", []) + [
            row for row in previous["runs"] if row["scenario"] in selected]
        report["setup"] = previous.get("setup", {})
        report["initial_suite_wall_seconds"] = previous.get("initial_suite_wall_seconds", previous.get("suite_wall_seconds"))
    report["current_invocation_selection"] = sorted(selected) if selected else "all"

    def save():
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="ekt-fast-flow-") as directory:
        temporary = Path(directory)
        store = Store(temporary / "state.sqlite3")
        assistant = Assistant(catalog, store, replace(settings, data_dir=temporary,
                                                    db_path=temporary / "state.sqlite3"))
        tool_events = []
        original_run_tool = assistant.run_tool

        async def observed_run_tool(user_id, name, args, manager=False):
            event = {"name": name, "arguments": args, "user_id": str(user_id)}
            tool_events.append(event)
            try:
                value = await original_run_tool(user_id, name, args, manager)
                if isinstance(value, list):
                    event["result_count"] = len(value)
                elif isinstance(value, dict) and value.get("error"):
                    event["error"] = value["error"]
                return value
            except Exception as exc:
                event["exception"] = type(exc).__name__
                raise

        assistant.run_tool = observed_run_tool
        metrics_path = temporary / "metrics.jsonl"

        def metrics():
            if not metrics_path.exists():
                return []
            return [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines() if line]

        async def run_case(name, user, prompt, check, *, attachment_factory=None, extra=None, extra_after=None):
            if selected and name not in selected:
                return {"scenario": name, "status": "not_selected"}
            previous_metrics = len(metrics())
            previous_tools = len(tool_events)
            started = time.perf_counter()
            attachment = None
            row = {"scenario": name, "prompt": prompt,
                   "observed_at": datetime.now(timezone.utc).isoformat(), "runtime": invocation}
            if extra:
                row.update(extra)
            try:
                if attachment_factory:
                    attachment = attachment_factory()
                    row["attachment_processing_seconds"] = round(time.perf_counter() - started, 4)
                    row["attachment"] = {
                        "text_characters": len(attachment.get("text", "")),
                        "has_image": bool(attachment.get("image_data_url")),
                        "warning": attachment.get("warning"),
                    }
                result = await asyncio.wait_for(assistant.reply(user, prompt, attachment=attachment), timeout=8.25)
                elapsed = time.perf_counter() - started
                new_metrics = metrics()[previous_metrics:]
                metric = new_metrics[-1] if new_metrics else {}
                status = result.get("status") or metric.get("status") or "unknown"
                products = result.get("products", [])
                trace = result.get("trace", [])
                usages = list(result.get("usage", []))
                usages += [entry.get("usage", {}) for entry in metric.get("research_models", [])]
                facts = check(result)
                runtime_ok = status in {"completed", "confirmed"} and not metric.get("error")
                row.update(
                    wall_seconds=round(elapsed, 4), reported_seconds=result.get("seconds"), status=status,
                    product_ids=[p["id"] for p in products], product_count=len(products),
                    trace=trace, proposal=bool(result.get("proposal")),
                    tool_calls=tool_events[previous_tools:],
                    proposal_items=result.get("proposal", {}).get("items", []) if result.get("proposal") else [],
                    tokens=token_counts(usages), llm_used=metric.get("llm_used", False),
                    token_usage_available=bool(usages) or not metric.get("llm_used"),
                    manager=result.get("manager", False), criteria=facts,
                    completed_bool=bool(runtime_ok and all(facts.values())),
                    under_8_seconds=elapsed <= 8.0,
                    text=result.get("text", ""),
                )
                if "full_input_processed" in facts:
                    row["partial_processing_succeeded"] = bool(
                        runtime_ok and attachment and attachment.get("text")
                        and facts.get("first_product_found") and facts.get("bounded_parser_output"))
                if extra_after:
                    row.update(extra_after())
                if metric.get("error"):
                    row["error"] = metric["error"]
            except Exception as exc:
                row.update(wall_seconds=round(time.perf_counter() - started, 4), status="exception",
                           error=type(exc).__name__, product_ids=[], product_count=0, trace=[], proposal=False,
                           tokens=token_counts([]), completed_bool=False)
            report["runs"].append(row)
            save()
            print(json.dumps({k: row.get(k) for k in (
                "scenario", "wall_seconds", "status", "product_ids", "trace", "proposal", "completed_bool",
                "partial_processing_succeeded", "attachment_processing_seconds", "criteria"
            )}, ensure_ascii=True), flush=True)
            return row

        try:
            await run_case("natural_selection", "flow:natural",
                           "Нужен однополюсный автомат C16 на 230 В, 4.5 кА с сертификатом. Подбери из каталога.",
                           lambda r: {"catalog_products_returned": bool(r.get("products")),
                                      "at_least_one_matches_requested_specs": any(matches_recipe(p) for p in r.get("products", []))})
            await run_case("exact_sku_details", "flow:exact",
                           f"Есть ли {SKU}? Нужны наличие, характеристики и сертификат.",
                           lambda r: {"exact_product_returned": product["id"] in [p["id"] for p in r.get("products", [])],
                                      "specifications_present": "Характеристики:" in r.get("text", ""),
                                      "certificate_status_present": "сертификат" in r.get("text", "").lower()})
            await run_case("zero_stock_alternative", "flow:alternative",
                           f"{ABSENT_SKU} нет в наличии? Предложи подходящий аналог и объясни различия.",
                           lambda r: {"source_is_zero_stock": absent["quantity"] == 0,
                                      "in_stock_alternative_with_reason": any(p["id"] != absent["id"] and p.get("quantity", 0) > 0
                                          and p.get("explanation") for p in r.get("products", []))})
            await run_case("purchase_terms", "flow:terms",
                           "Какие условия оплаты, доставки и минимальной партии?",
                           lambda r: {"terms_tool": "purchase_terms" in r.get("trace", []),
                                      "source_link": "https://ekt.kz/checkout-delivery/" in r.get("text", ""),
                                      "substantive_answer": len(r.get("text", "")) > 250})
            await run_case("prepare_cart", "flow:cart",
                           f"Подготовь 2 штуки {SKU} в корзину. Пока не добавляй.",
                           lambda r: {"proposal_prepared": bool(r.get("proposal")),
                                      "cart_stays_empty": not store.get_cart("flow:cart")["items"],
                                      "quantity_two": sum(p["quantity"] for p in (r.get("proposal") or {}).get("items", [])) == 2})
            await run_case("explicit_confirm_live_stock", "flow:cart", "да, добавь",
                           lambda r: {"confirmed": r.get("status") == "confirmed",
                                      "quantity_two": sum(p["quantity"] for p in store.get_cart("flow:cart")["items"]) == 2,
                                      "cart_link_present": bool(r.get("cart_url")),
                                      "token_matches_cart": store.cart_by_token(store.get_cart("flow:cart")["token"])["items"] == store.get_cart("flow:cart")["items"]})

            started = time.perf_counter()
            request = store.save_request("flow:cart")
            saved_ok = request.get("status") == "request"
            saved_row = {
                "scenario": "save_local_request", "wall_seconds": round(time.perf_counter() - started, 4),
                "status": request.get("status"), "product_ids": [p["id"] for p in request.get("items", [])],
                "product_count": len(request.get("items", [])), "trace": ["store.save_request"],
                "proposal": False, "tokens": token_counts([]), "completed_bool": saved_ok,
                "under_8_seconds": True, "scope": "Local request persistence; not an EKT purchase.",
            }
            if not selected or "save_local_request" in selected:
                report["runs"].append(saved_row)
            store.clear_cart("flow:cart")
            await run_case("repeat_saved_request", "flow:cart",
                           "Повтори мой прошлый заказ: подготовь ту же корзину с тем же количеством, без добавления до подтверждения.",
                           lambda r: {"previous_request_exists": saved_ok,
                                      "new_proposal": bool(r.get("proposal")),
                                      "cart_stays_empty": not store.get_cart("flow:cart")["items"],
                                      "same_items": (r.get("proposal") or {}).get("items") == request.get("items")})

            await run_case("indecision_first_turn", "flow:manager", "Не знаю, что выбрать для домашней мастерской.",
                           lambda r: {"clarifying_question": "?" in r.get("text", ""),
                                      "not_yet_manager": not r.get("manager")})
            await run_case("indecision_manager_turn", "flow:manager", "Всё ещё сомневаюсь и не могу выбрать.",
                           lambda r: {"manager_protocol_active": bool(r.get("manager")),
                                      "clarifying_question": "?" in r.get("text", "")})
            await run_case("manager_real_web_research", "flow:web",
                           "Поищи в интернете: чем отличается дифференциальный автомат от УЗО? Нужен общий принцип, без подбора номиналов и цен.",
                           lambda r: {"manager_protocol_active": bool(r.get("manager")),
                                      "research_tool_ran": "research_task" in r.get("trace", []),
                                      "web_context_rendered": "Технический контекст из интернета" in r.get("text", ""),
                                      "external_source_link": any(urlparse(u).hostname not in {"ekt.kz", "www.ekt.kz", "127.0.0.1", None}
                                          for u in re.findall(r"https?://[^\s<>]+", r.get("text", "")))})

            # This small in-memory XLSX is a parser test fixture using real
            # catalogue rows, not a rewritten customer workbook.
            from openpyxl import Workbook
            fixture = Workbook()
            sheet = fixture.active
            sheet.title = "Тестовая спецификация"
            sheet.append(["Артикул", "Наименование", "Количество"])
            sheet.append([product["article"], product["name"], 2])
            sheet.append([absent["article"], absent["name"], 1])
            buffer = BytesIO()
            fixture.save(buffer)
            fixture.close()
            excel_bytes = buffer.getvalue()
            await run_case("excel_attachment", "flow:excel",
                           "Найди в ограниченном каталоге первую товарную строку приложенного Excel и покажи её карточку. Не добавляй в корзину.",
                           lambda r: {"first_product_found": product["id"] in [p["id"] for p in r.get("products", [])],
                                      "cart_stays_empty": not store.get_cart("flow:excel")["items"]},
                           attachment_factory=lambda: extract_attachment("specification.xlsx", excel_bytes),
                           extra={"input": "Two-row XLSX parser fixture made from actual catalogue products."})
            workbook = settings.data_dir / "catalog.xlsx"
            large_context = {}

            def prepare_large_attachment():
                # Reading and parsing happen inside the run_case wall clock.
                attachment = extract_attachment(workbook.name, workbook.read_bytes())
                large_context["attachment"] = attachment
                large_context["first_product"] = first_product_in_text(catalog, attachment["text"])
                return attachment

            def check_large_attachment(result):
                attachment = large_context["attachment"]
                first = large_context["first_product"]
                return {"bounded_parser_output": len(attachment["text"]) <= 20000,
                        "parser_warning_present": bool(attachment.get("warning")),
                        "first_product_found": bool(first and first["id"] in [p["id"] for p in result.get("products", [])]),
                        "full_input_processed": not attachment.get("warning")}

            await run_case("large_excel_bounded_partial", "flow:large-excel",
                           "Проверь приложенный каталог Excel и найди первую товарную строку. Если файл прочитан не весь, не утверждай обратное.",
                           check_large_attachment,
                           attachment_factory=prepare_large_attachment,
                           extra={"source_file": str(workbook),
                                  "timing_scope": "File read, parse, first-row verification and Assistant.reply.",
                                  "expected_limitation": "A parser rejection or bounded partial read is not counted as full task completion."},
                           extra_after=lambda: {
                               "parser_handling": "rejected" if not large_context["attachment"]["text"] else "partial",
                               "expected_first_article": (large_context["first_product"] or {}).get("article"),
                               "expected_first_product_id": (large_context["first_product"] or {}).get("id"),
                           })

            image_url = product.get("image", "")
            image_started = time.perf_counter()
            image_bytes = None
            image_meta = {"source_product_id": product["id"], "source_article": SKU, "url": image_url}
            if (not selected or "jpeg_vision" in selected) and urlparse(image_url).hostname in {"ekt.kz", "www.ekt.kz"}:
                try:
                    async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                        response = await client.get(image_url)
                        response.raise_for_status()
                        if response.content.startswith(b"\xff\xd8\xff"):
                            image_bytes = response.content
                        image_meta.update(http_status=response.status_code, bytes=len(response.content), jpeg=bool(image_bytes))
                except Exception as exc:
                    image_meta["error"] = type(exc).__name__
            image_meta["download_seconds"] = round(time.perf_counter() - image_started, 4)
            if not selected or "jpeg_vision" in selected:
                report["setup"]["vision_image"] = image_meta
            if image_bytes:
                def check_photo(result):
                    products = result.get("products", [])
                    queries = " ".join(event["arguments"].get("query", "") for event in tool_events
                                       if event["name"] == "search_catalog" and event["user_id"] == "flow:vision")
                    return {"catalog_product_found": bool(products),
                            "model_was_used": bool(result.get("usage")),
                            "cart_stays_empty": not store.get_cart("flow:vision")["items"],
                            **photo_spec_checks(products, queries)}

                await run_case("jpeg_vision", "flow:vision",
                               "Определи тип товара по фотографии и найди подходящие позиции в каталоге. Не угадывай нечитаемые параметры.",
                               check_photo,
                               attachment_factory=lambda: extract_attachment("ekt-product.jpg", image_bytes),
                               extra={"fixture_note": "Generic C63 image hosted on a C16 SKU page. Verify candidates against visible specifications, not the hosting SKU."})
            elif not selected or "jpeg_vision" in selected:
                report["runs"].append({"scenario": "jpeg_vision", "status": "image_unavailable", "completed_bool": False,
                                       "wall_seconds": image_meta["download_seconds"], "product_ids": [], "product_count": 0,
                                       "trace": [], "proposal": False, "tokens": token_counts([])})

            await run_case("unknown_sku_catalog_boundary", "flow:unknown",
                           "Есть ли товар с точным артикулом ZZZ-NONEXISTENT-999999999999? Покажи только точное совпадение, без аналогов.",
                           lambda r: {"no_invented_products": not r.get("products"),
                                      "explicit_no_match": "Точного совпадения" in r.get("text", ""),
                                      "cart_stays_empty": not store.get_cart("flow:unknown")["items"]})
        finally:
            if assistant.client:
                await assistant.client.close()
            store.close()
    report["suite_wall_seconds"] = round(time.perf_counter() - suite_started, 4)
    report["summary"] = {
        "scenarios": len(report["runs"]),
        "completed": sum(row["completed_bool"] for row in report["runs"]),
        "incomplete": [row["scenario"] for row in report["runs"] if not row["completed_bool"]],
        "all_observed_under_8_seconds": all(row["wall_seconds"] <= 8 for row in report["runs"]),
        "max_wall_seconds": max(row["wall_seconds"] for row in report["runs"]),
        "total_tokens": sum(row["tokens"]["total_tokens"] for row in report["runs"]),
        "useful_partial_processing": [row["scenario"] for row in report["runs"] if row.get("partial_processing_succeeded")],
        "token_usage_note": "Sum of reported usage for current result rows only. Timed-out calls may have unreported usage; prior attempts are stored separately.",
        "unreported_usage_scenarios": [row["scenario"] for row in report["runs"]
                                       if row.get("llm_used") and row["status"] in {"error", "deadline", "exception"}
                                       and not row["tokens"]["total_tokens"]],
    }
    save()
    print(json.dumps({"summary": report["summary"], "suite_wall_seconds": report["suite_wall_seconds"]}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", help="Rerun selected independent cases, retaining previous report rows.")
    asyncio.run(main(parser.parse_args().only))
