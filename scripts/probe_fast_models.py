"""Small live planner latency probe; never prints credentials or changes app config."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import time

from dotenv import dotenv_values
import httpx
from openai import AsyncOpenAI


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fast_model_probe.json"
MODELS = ("gpt-6-luna", "gpt-5.4-mini")
PROMPT = "Нужен однополюсный автомат C16 на230В,4.5кА с сертификатом"
INSTRUCTIONS = (
    "Ты планировщик поиска ограниченного каталога электротехники. "
    "Вызови search_catalog ровно один раз. В query сохрани тип товара и ВСЕ "
    "указанные параметры, включая сертификат. Не отвечай клиенту, не выдумывай товар."
)
TOOL = {
    "type": "function", "name": "search_catalog", "strict": True,
    "description": "Поиск по локальному каталогу электротехники.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                   "required": ["query"], "additionalProperties": False},
}


def safe_error(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    return {
        "error_type": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None),
        "error_code": body.get("code") if isinstance(body, dict) else None,
        "error_parameter": body.get("param") if isinstance(body, dict) else None,
    }


def retention(query):
    normalized = query.casefold().replace(",", ".")
    return {
        "single_pole": bool(re.search(r"однополюс|1\s*[pрп]", normalized)),
        "curve_and_current": bool(re.search(r"[cс]\s*16", normalized)),
        "voltage": "230" in normalized,
        "breaking_capacity": "4.5" in normalized,
        "certificate": "сертификат" in normalized,
    }


async def main():
    config = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
    key = config.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is missing; no API calls were made")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "One forced planner tool call only; excludes catalog refresh and Telegram delivery.",
        "prompt": PROMPT, "instructions": INSTRUCTIONS,
        "request_settings": {"reasoning": {"effort": "none"}, "max_output_tokens": 400,
                             "max_retries": 0, "http_timeout_seconds": 6.0,
                             "outer_deadline_seconds": 6.0, "store": False,
                             "tool_choice": {"type": "function", "name": "search_catalog"},
                             "parallel_tool_calls": False},
        "documentation": [
            "https://developers.openai.com/api/docs/models/gpt-6-luna",
            "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
        ],
        "tool": TOOL,
        "availability": {}, "attempts": [],
        "limitations": "Three repeated prompts per model are not an SLA or a p95 estimate.",
    }

    def save():
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async with AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1",
                           timeout=httpx.Timeout(6.0), max_retries=0) as client:
        try:
            async with asyncio.timeout(6.0):
                models = await client.models.list()
            available = {model.id for model in models.data}
            report["availability"] = {"status": "ok", "listed": {model: model in available for model in MODELS}}
        except Exception as exc:
            report["availability"] = {"status": "error", **safe_error(exc)}
        save()
        # Alternate models so neither receives all later/warmed measurements.
        for attempt in range(1, 4):
            for model in MODELS:
                started = time.perf_counter()
                row = {"requested_model": model, "attempt": attempt}
                try:
                    async with asyncio.timeout(6.0):
                        response = await client.responses.create(
                            model=model, instructions=INSTRUCTIONS, input=PROMPT,
                            reasoning={"effort": "none"}, max_output_tokens=400,
                            tools=[TOOL], tool_choice={"type": "function", "name": "search_catalog"},
                            parallel_tool_calls=False, store=False,
                        )
                    elapsed = time.perf_counter() - started
                    calls = [part for part in response.output if part.type == "function_call"]
                    queries = []
                    for call in calls:
                        args = json.loads(call.arguments)
                        if call.name == "search_catalog" and set(args) == {"query"} and isinstance(args["query"], str):
                            queries.append(args["query"])
                    row.update(
                        status="ok", elapsed_seconds=round(elapsed, 4), actual_model=response.model,
                        response_status=response.status,
                        valid_tool_call=len(calls) == 1 and len(queries) == 1 and bool(queries[0].strip()),
                        queries=queries, parameter_retention=[retention(query) for query in queries],
                        output=[part.model_dump() for part in response.output],
                        usage=response.usage.model_dump() if response.usage else None,
                    )
                except Exception as exc:
                    row.update(status="error", elapsed_seconds=round(time.perf_counter() - started, 4), **safe_error(exc))
                report["attempts"].append(row)
                save()
                print(json.dumps({k: v for k, v in row.items() if k not in {"output"}}, ensure_ascii=True), flush=True)
    report["summary"] = {}
    for model in MODELS:
        rows = [row for row in report["attempts"] if row["requested_model"] == model]
        successful = [row for row in rows if row["status"] == "ok" and row["valid_tool_call"]]
        times = [row["elapsed_seconds"] for row in successful]
        report["summary"][model] = {
            "attempts": len(rows), "successful_tool_calls": len(successful),
            "complete_parameter_calls": sum(all(flags.values()) for row in successful for flags in row["parameter_retention"]),
            "min_seconds": min(times) if times else None,
            "max_seconds": max(times) if times else None,
            "median_seconds": statistics.median(times) if times else None,
        }
    save()
    print(json.dumps(report["summary"], ensure_ascii=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
