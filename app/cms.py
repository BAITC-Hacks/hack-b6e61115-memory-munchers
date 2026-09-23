"""Department CMS with aggregate-only assistant telemetry.

Mount with ``create_cms_router(CMSRepository(data_dir, assistant_db_path))``.
The assistant database is opened read-only. CMS owns only cms.sqlite.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Department = Literal["director", "marketing", "sales", "development", "support"]
TaskStatus = Literal["todo", "in_progress", "blocked", "done"]
DEPARTMENTS = [
    {"id": "director", "name": "Директор", "parent_id": None,
     "description": "Все департаменты и сквозные показатели"},
    {"id": "marketing", "name": "Маркетинг", "parent_id": "director",
     "description": "Обращения и вовлечение покупателей"},
    {"id": "sales", "name": "Продажи", "parent_id": "director",
     "description": "Подборы, подтверждённые корзины и заявки"},
    {"id": "development", "name": "Разработка", "parent_id": "director",
     "description": "Скорость ассистента, токены и работа инструментов"},
    {"id": "support", "name": "Поддержка", "parent_id": "director",
     "description": "Ошибки и подключение менеджера"},
]
DEPARTMENT_NAMES = {item["id"]: item["name"] for item in DEPARTMENTS}
DEFAULT_CONTENT = {
    "welcome": "Помогу найти товар в каталоге ЕКТ, проверить наличие и подобрать аналог.",
    "delivery_note": "Условия оплаты и доставки уточняются по данным магазина при оформлении заказа.",
}
# Telemetry never stores free text, customer identifiers, articles or chat content.
NUMERIC_EVENT_FIELDS = {"seconds", "latency_ms", "input_tokens", "output_tokens", "tokens", "count", "items_count", "cost_usd"}
BOOLEAN_EVENT_FIELDS = {"manager", "llm_connected", "success", "replayed"}
IDENTIFIER_EVENT_FIELDS = {"model", "status", "source", "error_type"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return value
    return None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


class Filters:
    def __init__(self, department="all", from_date: date | None = None, to_date: date | None = None):
        if department not in {"all", *DEPARTMENT_NAMES}:
            raise HTTPException(422, "Неизвестный департамент.")
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Начало периода не может быть позже конца.")
        if to_date == date.max:
            raise HTTPException(422, "Дата окончания выходит за допустимый диапазон.")
        self.department = department
        self.from_date, self.to_date = from_date, to_date
        self.start = datetime.combine(from_date, time.min, timezone.utc) if from_date else None
        self.end = datetime.combine(to_date + timedelta(days=1), time.min, timezone.utc) if to_date else None

    def includes(self, timestamp):
        stamp = _timestamp(timestamp)
        if stamp is None:
            return self.start is None and self.end is None
        return (self.start is None or stamp >= self.start) and (self.end is None or stamp < self.end)

    def sql(self, column="created_at", *, numeric=False):
        clauses, values = [], []
        for bound, operator in ((self.start, ">="), (self.end, "<")):
            if bound:
                clauses.append(f"{column} {operator} ?")
                values.append(bound.timestamp() if numeric else bound.isoformat())
        return clauses, values

    def as_dict(self):
        return {"department": self.department, "from": self.from_date.isoformat() if self.from_date else None,
                "to": self.to_date.isoformat() if self.to_date else None, "timezone": "UTC"}


def request_filters(department: str = "all", from_date: date | None = Query(None, alias="from"),
                    to_date: date | None = Query(None, alias="to")) -> Filters:
    return Filters(department, from_date, to_date)


def admin_access(request: Request):
    """Never trust X-Forwarded-For here; proxy deployments must configure a token."""
    expected = os.getenv("CMS_ADMIN_TOKEN", "").strip()
    if expected:
        received = request.headers.get("X-Admin-Token", "")
        authorization = request.headers.get("Authorization", "")
        if not received and authorization.lower().startswith("bearer "):
            received = authorization[7:]
        if not received or not hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(401, "Нужен токен администратора.", headers={"WWW-Authenticate": "Bearer"})
        return
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
        local = address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        local = False
    # A remote site must not use the browser to write to a tokenless local CMS.
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Запрос CMS должен приходить с этого сайта.")
    if not local:
        raise HTTPException(403, "Удалённый доступ к CMS требует CMS_ADMIN_TOKEN.")


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    department: Department
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    status: TaskStatus = "todo"
    assignee: str = Field(default="", max_length=120)
    due_date: date | None = None


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    department: Department | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: TaskStatus | None = None
    assignee: str | None = Field(default=None, max_length=120)
    due_date: date | None = None

    @model_validator(mode="after")
    def reject_empty_or_null(self):
        if not self.model_fields_set:
            raise ValueError("Укажите изменяемые поля.")
        if any(getattr(self, key) is None for key in self.model_fields_set - {"due_date"}):
            raise ValueError("Пустое значение допустимо только для дедлайна.")
        return self


class ContentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    welcome: str = Field(min_length=1, max_length=2000)
    delivery_note: str = Field(min_length=1, max_length=4000)

    @field_validator("welcome", "delivery_note")
    @classmethod
    def plain_text(cls, value):
        if re.search(r"<[^>]+>", value):
            raise ValueError("Используйте обычный текст без HTML.")
        return value


class CMSRepository:
    def __init__(self, data_dir: Path, assistant_db_path: Path):
        self.data_dir, self.assistant_db_path = Path(data_dir), Path(assistant_db_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.data_dir / "cms.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL,
                assignee TEXT NOT NULL, due_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS tasks_department ON tasks(department,created_at);
            CREATE TABLE IF NOT EXISTS content (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_department ON events(department,created_at);
        """)

    def close(self):
        with self._lock:
            self._db.close()

    def tasks(self, filters: Filters, status: str | None = None):
        clauses, values = filters.sql()
        if filters.department != "all":
            clauses.append("department=?")
            values.append(filters.department)
        if status:
            clauses.append("status=?")
            values.append(status)
        query = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        with self._lock:
            return [dict(row) for row in self._db.execute(query + " ORDER BY updated_at DESC,id DESC", values)]

    def create_task(self, body: TaskCreate):
        now, data = utc_now(), body.model_dump(mode="json")
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO tasks(department,title,description,status,assignee,due_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (*data.values(), now, now),
            )
            return dict(self._db.execute("SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)).fetchone())

    def patch_task(self, task_id: int, body: TaskPatch):
        data = body.model_dump(mode="json", exclude_unset=True)
        data["updated_at"] = utc_now()
        with self._lock, self._db:
            cursor = self._db.execute("UPDATE tasks SET " + ",".join(key + "=?" for key in data) + " WHERE id=?",
                                      (*data.values(), task_id))
            if not cursor.rowcount:
                raise HTTPException(404, "Задача не найдена.")
            return dict(self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def delete_task(self, task_id: int):
        with self._lock, self._db:
            if not self._db.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount:
                raise HTTPException(404, "Задача не найдена.")

    def public_content(self):
        with self._lock:
            rows = self._db.execute("SELECT key,value,updated_at FROM content").fetchall()
        return {**DEFAULT_CONTENT, **{row["key"]: row["value"] for row in rows},
                "updated_at": max((row["updated_at"] for row in rows), default=None)}

    def update_content(self, body: ContentUpdate):
        now = utc_now()
        with self._lock, self._db:
            self._db.executemany("INSERT INTO content VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                                 ((key, value, now) for key, value in body.model_dump().items()))
        return self.public_content()

    def record_event(self, department: str, kind: str, payload: dict | None = None):
        if department not in DEPARTMENT_NAMES or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", kind):
            raise ValueError("Неверный тип события или департамент.")
        clean = {}
        for key, value in (payload or {}).items():
            if key in NUMERIC_EVENT_FIELDS and _finite_number(value) is not None:
                clean[key] = value
            elif key in BOOLEAN_EVENT_FIELDS and isinstance(value, bool):
                clean[key] = value
            elif key in IDENTIFIER_EVENT_FIELDS and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value):
                clean[key] = value
        with self._lock, self._db:
            self._db.execute("INSERT INTO events(department,kind,payload,created_at) VALUES(?,?,?,?)",
                             (department, kind, json.dumps(clean), utc_now()))

    def departments(self, filters: Filters):
        tasks = self.tasks(filters)
        return [{**item, "tasks_total": sum(t["department"] == item["id"] for t in tasks),
                 "tasks_open": sum(t["department"] == item["id"] and t["status"] != "done" for t in tasks)}
                for item in DEPARTMENTS]

    def _assistant_counts(self, filters: Filters):
        keys = ("sessions", "questions", "proposals", "confirmed_proposals", "saved_requests")
        result = dict.fromkeys(keys)
        if not self.assistant_db_path.is_file():
            return result, "missing"
        clauses, values = filters.sql(numeric=True)
        where = " AND " + " AND ".join(clauses) if clauses else ""
        queries = {
            "sessions": "SELECT COUNT(DISTINCT user_id) FROM messages WHERE role='user'" + where,
            "questions": "SELECT COUNT(*) FROM messages WHERE role='user'" + where,
            "proposals": "SELECT COUNT(*) FROM proposals WHERE 1=1" + where,
            "confirmed_proposals": "SELECT COUNT(*) FROM proposals WHERE status='confirmed'" + where,
            "saved_requests": "SELECT COUNT(*) FROM requests WHERE 1=1" + where,
        }
        try:
            connection = sqlite3.connect(self.assistant_db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                for key, query in queries.items():
                    result[key] = connection.execute(query, values).fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error:
            return result, "unavailable"
        return result, "ready"

    def _log_metrics(self, filters: Filters):
        path = self.data_dir / "metrics.jsonl"
        counters = Counter()
        latencies, costs, days = [], [], defaultdict(lambda: {"requests": 0, "errors": 0, "tokens": 0, "seconds": []})
        if not path.is_file():
            return counters, latencies, costs, days, "missing"
        try:
            with path.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        counters["malformed"] += 1
                        continue
                    if not isinstance(row, dict):
                        counters["malformed"] += 1
                        continue
                    stamp = row.get("timestamp") or row.get("created_at") or row.get("ts")
                    parsed_stamp = _timestamp(stamp)
                    if parsed_stamp is None:
                        counters["undated"] += 1
                    if not filters.includes(stamp):
                        continue
                    counters["responses"] += 1
                    seconds = _finite_number(row.get("seconds"))
                    if seconds is not None:
                        latencies.append(seconds)
                    counters["manager"] += row.get("manager") is True
                    error = bool(row.get("error") or row.get("error_type") or row.get("status") == "error")
                    counters["errors"] += error
                    trace = row.get("tools", [])
                    if isinstance(trace, list):
                        counters["tool_calls"] += len(trace)
                    usage = row.get("usage", [])
                    if isinstance(usage, dict):
                        usage = [usage]
                    tokens = 0
                    for item in usage if isinstance(usage, list) else []:
                        if not isinstance(item, dict):
                            continue
                        for name in ("input_tokens", "output_tokens"):
                            number = _finite_number(item.get(name))
                            if number is not None:
                                counters[name] += number
                                tokens += number
                    cost = _finite_number(row.get("cost_usd"))
                    if cost is not None:
                        costs.append(cost)
                    if parsed_stamp:
                        day = days[parsed_stamp.date().isoformat()]
                        day["requests"] += 1
                        day["errors"] += error
                        day["tokens"] += tokens
                        if seconds is not None:
                            day["seconds"].append(seconds)
        except OSError:
            return counters, latencies, costs, days, "unavailable"
        return counters, latencies, costs, days, "ready"

    def summary(self, filters: Filters):
        counts, db_state = self._assistant_counts(filters)
        log, latencies, costs, days, log_state = self._log_metrics(filters)
        metrics = []

        def add(key, label, value, unit, department, source, note=""):
            if filters.department in {"all", "director", department}:
                metrics.append(dict(key=key, label=label, value=value, unit=unit, department=department, source=source, note=note))

        add("sessions", "Сессии с обращениями", counts["sessions"], "", "marketing", "assistant.sqlite", "По сохранённым сообщениям, без идентификаторов клиентов.")
        add("questions", "Вопросы покупателей", counts["questions"], "", "marketing", "assistant.sqlite")
        add("proposals", "Предложения корзины", counts["proposals"], "", "sales", "assistant.sqlite")
        add("confirmed_proposals", "Подтверждённые предложения", counts["confirmed_proposals"], "", "sales", "assistant.sqlite", "Фильтр периода по дате создания предложения.")
        add("saved_requests", "Сохранённые заявки", counts["saved_requests"], "", "sales", "assistant.sqlite", "Локальные заявки, не оплаченные заказы EKT.")
        conversion = round(100 * counts["confirmed_proposals"] / counts["proposals"], 1) if counts["proposals"] and counts["confirmed_proposals"] is not None else None
        add("confirmation_rate", "Доля подтверждённых предложений", conversion, "%", "sales", "assistant.sqlite")
        observed = log_state == "ready"
        add("responses", "Замеренные ответы", log["responses"] if observed else None, "", "development", "metrics.jsonl")
        add("latency_avg", "Среднее время ответа", round(sum(latencies) / len(latencies), 3) if latencies else None, "с", "development", "metrics.jsonl")
        add("latency_p95", "Время ответа p95", round(sorted(latencies)[math.ceil(len(latencies) * .95) - 1], 3) if latencies else None, "с", "development", "metrics.jsonl")
        add("input_tokens", "Входные токены", log["input_tokens"] if observed else None, "", "development", "metrics.jsonl")
        add("output_tokens", "Выходные токены", log["output_tokens"] if observed else None, "", "development", "metrics.jsonl")
        add("tool_calls", "Вызовы инструментов", log["tool_calls"] if observed else None, "", "development", "metrics.jsonl")
        add("cost_usd", "Учтённая стоимость API", round(sum(costs), 6) if costs and len(costs) == log["responses"] else None, "USD", "development", "metrics.jsonl", "Только явно записанная стоимость; тарифы не предполагаются.")
        add("manager_activations", "Подключения протокола менеджера", log["manager"] if observed else None, "", "support", "metrics.jsonl")
        clauses, values = filters.sql()
        if filters.department not in {"all", "director"}:
            clauses.append("department=?")
            values.append(filters.department)
        with self._lock:
            events = self._db.execute("SELECT department,kind,COUNT(*) AS total FROM events" +
                                     (" WHERE " + " AND ".join(clauses) if clauses else "") + " GROUP BY department,kind", values).fetchall()
        event_counts = [{"department": row["department"], "kind": row["kind"], "count": row["total"]} for row in events]
        errors = sum(row["total"] for row in events if row["kind"] in {"chat_error", "confirm_error", "request_error", "attachment_error"})
        add("errors", "Зарегистрированные ошибки", errors, "", "support", "cms.sqlite", "События chat_error, confirm_error, request_error, attachment_error.")
        tasks = self.tasks(filters)
        statuses = Counter(task["status"] for task in tasks)
        limitations = ["Даты фильтруются в UTC; задачи - по дате создания.",
                       "История сообщений ограничена настройками ассистента; сессии и вопросы отражают сохранённую историю."]
        if log["undated"]:
            limitations.append(f"Записей метрик без даты: {log['undated']}. При выборе периода они исключаются.")
        if log["malformed"]:
            limitations.append(f"Нечитаемых строк журнала пропущено: {log['malformed']}.")
        if db_state != "ready" or log_state != "ready":
            limitations.append("Недоступный источник показан как «Нет данных», а не нулевой результат.")
        timeseries = [{"date": day, "requests": row["requests"], "errors": row["errors"], "tokens": row["tokens"],
                       "latency_ms": round(sum(row["seconds"]) * 1000 / len(row["seconds"]), 1) if row["seconds"] else None}
                      for day, row in sorted(days.items())] if filters.department in {"all", "director", "development", "support"} else []
        return {"generated_at": utc_now(), "filters": filters.as_dict(), "metrics": metrics,
                "task_counts": {key: statuses[key] for key in ("todo", "in_progress", "blocked", "done")},
                "event_counts": event_counts, "timeseries": timeseries,
                "sources": {"assistant_db": db_state, "metrics_log": log_state}, "limitations": limitations}


def create_cms_router(repository: CMSRepository | None = None):
    """An omitted repository is resolved from app.state.cms at request time."""
    router = APIRouter(prefix="/api/admin", tags=["CMS"], dependencies=[Depends(admin_access)])

    def repo(request: Request):
        selected = repository or getattr(request.app.state, "cms", None)
        if selected is None:
            raise HTTPException(503, "CMS ещё не запущена.")
        return selected

    @router.get("/summary")
    def summary(filters: Filters = Depends(request_filters), cms=Depends(repo)):
        return cms.summary(filters)

    @router.get("/departments")
    def departments(filters: Filters = Depends(request_filters), cms=Depends(repo)):
        return {"departments": cms.departments(filters)}

    @router.get("/tasks")
    def tasks(filters: Filters = Depends(request_filters), status: TaskStatus | None = None, cms=Depends(repo)):
        return {"tasks": cms.tasks(filters, status)}

    @router.post("/tasks", status_code=201)
    def create_task(body: TaskCreate, cms=Depends(repo)):
        return cms.create_task(body)

    @router.patch("/tasks/{task_id}")
    def patch_task(task_id: int, body: TaskPatch, cms=Depends(repo)):
        return cms.patch_task(task_id, body)

    @router.delete("/tasks/{task_id}")
    def delete_task(task_id: int, cms=Depends(repo)):
        cms.delete_task(task_id)
        return {"deleted": True}

    @router.get("/content")
    def content(cms=Depends(repo)):
        return cms.public_content()

    @router.put("/content")
    def update_content(body: ContentUpdate, cms=Depends(repo)):
        return cms.update_content(body)

    @router.get("/exports/{format}")
    def export(format: Literal["xlsx", "pdf"], filters: Filters = Depends(request_filters), cms=Depends(repo)):
        from .cms_exports import export_pdf, export_xlsx
        try:
            report = cms.summary(filters)
            tasks = cms.tasks(filters)
            result = export_xlsx(report, tasks) if format == "xlsx" else export_pdf(report, tasks)
        except (ImportError, RuntimeError) as exc:
            raise HTTPException(503, "Экспорт недоступен: проверьте openpyxl, reportlab и шрифт DejaVu Sans.") from exc
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx" else "application/pdf"
        return Response(result, media_type=media, headers={"Content-Disposition": f'attachment; filename="ekt-departments.{format}"',
                                                          "Cache-Control": "no-store"})

    return router
