"""SQLite-backed EKT catalogue and bounded, non-blocking synchronization.

Only the observed /products?page=&per_page= and /products/detail?id= API
contracts are used. A list response never supplies stock or specifications.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time as clock_time
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .catalog import Catalog, _clean, _normal, _tokens


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(raw):
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class _SearchCards(HTMLParser):
    """Observed in дизайн/catalog-source.html: form 566, cards from 2107."""
    def __init__(self):
        super().__init__()
        self.cards, self.current, self.stack = [], None, []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if "product-card-out-catalog" in classes and self.current is None:
            self.current = {"id": None, "name": "", "article": "", "url": ""}
            self.stack = []
        if self.current is None:
            return
        field = "name" if "product-title" in classes else "article" if "product-article" in classes and "product-post_article" not in classes else None
        if tag not in {"img", "input", "br", "hr", "meta", "link", "source", "wbr"}:
            self.stack.append((tag, field))
        if attributes.get("data-action") in {"add2basket", "add2basketPreOrder"} and attributes.get("data-id", "").isdigit():
            self.current["id"] = int(attributes["data-id"])
        if tag == "a" and attributes.get("href", "").startswith("/catalog/"):
            self.current["url"] = urljoin("https://ekt.kz/", attributes["href"])

    def handle_data(self, text):
        if self.current:
            field = next((field for _, field in reversed(self.stack) if field), None)
            if field:
                self.current[field] += text

    def handle_endtag(self, tag):
        if self.current is None or not self.stack:
            return
        if any(item[0] == tag for item in self.stack):
            while self.stack:
                closed, _ = self.stack.pop()
                if closed == tag:
                    break
        if not self.stack:
            if self.current["id"] and self.current["name"] and self.current["url"]:
                self.current["name"] = _clean(self.current["name"])
                self.current["article"] = re.sub(r"^Код\s*товара\s*", "", _clean(self.current["article"]), flags=re.I)
                self.cards.append(self.current)
            self.current = None


class SyncedCatalog(Catalog):
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._network_gate = asyncio.Semaphore(2)
        self._refresh_locks = {}
        self._items, self._articles = {}, {}
        self._aliases, self._index, self._categories = defaultdict(set), defaultdict(dict), defaultdict(set)
        self._listing_items, self._listing_articles, self._listing_urls = {}, {}, {}
        self._listing_index = defaultdict(set)
        self._priority = {}
        self._last_version_poll = 0
        self._db = sqlite3.connect(self.data_dir / "catalog.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL, last_detail_at TEXT,
                version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
                last_list_at TEXT, detail_pending INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sync_lease (id INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS detail_attempts (id INTEGER PRIMARY KEY, attempted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                list_count INTEGER NOT NULL DEFAULT 0, list_complete INTEGER NOT NULL DEFAULT 0,
                details_updated INTEGER NOT NULL DEFAULT 0, details_failed INTEGER NOT NULL DEFAULT 0,
                error_type TEXT
            );
        """)
        for table in ("products", "listings"):
            if "version" not in {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        self._db.commit()
        if self._meta("seeded") != "1":
            self._seed()
        # Capture before reading rows: a concurrent commit must never be skipped.
        self._seen_version = int(self._meta("version", "0"))
        for row in self._db.execute("SELECT payload FROM products"):
            self._install(json.loads(row["payload"]))
        for row in self._db.execute("SELECT payload FROM listings"):
            self._index_listing(json.loads(row["payload"]))

    def _meta(self, key, default=None):
        row = self._db.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key, value):
        self._db.execute("INSERT INTO sync_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def _next_version(self):
        self._db.execute("INSERT INTO sync_meta VALUES('version','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        return int(self._meta("version"))

    def _reload_if_changed(self, force=False):
        now = clock_time.monotonic()
        if not force and now - self._last_version_poll < 5:
            return
        self._last_version_poll = now
        version = int(self._meta("version", "0"))
        if version <= self._seen_version:
            return
        for row in self._db.execute("SELECT payload FROM products WHERE version>?", (self._seen_version,)):
            self._install(json.loads(row["payload"]))
        for row in self._db.execute("SELECT payload FROM listings WHERE version>?", (self._seen_version,)):
            self._index_listing(json.loads(row["payload"]))
        self._seen_version = version

    def _seed(self):
        source = self.data_dir / "raw_details.jsonl"
        skipped = 0
        with self._db:
            if source.is_file():
                with source.open(encoding="utf-8-sig") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        try:
                            raw = json.loads(line)
                            product = self._normalize(raw, fresh=False)
                            self._db.execute("INSERT OR IGNORE INTO products(id,payload,last_detail_at) VALUES(?,?,NULL)",
                                             (product["id"], json.dumps(product, ensure_ascii=False)))
                        except (ValueError, TypeError, KeyError):
                            skipped += 1
            raw_list = self.data_dir / "raw_products.json"
            if raw_list.is_file():
                try:
                    rows = json.loads(raw_list.read_text(encoding="utf-8-sig"))
                    if isinstance(rows, dict):
                        rows = rows.get("items", [])
                    for raw in rows:
                        try:
                            pid = int(raw["id"])
                            pending = not self._db.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone()
                            self._db.execute("INSERT OR IGNORE INTO listings(id,payload,fingerprint,last_list_at,detail_pending) VALUES(?,?,?,NULL,?)",
                                             (pid, json.dumps(raw, ensure_ascii=False), _fingerprint(raw), int(pending)))
                        except (ValueError, TypeError, KeyError):
                            skipped += 1
                except (ValueError, TypeError):
                    skipped += 1
            # A details-only snapshot remains searchable without requiring a list file.
            for row in self._db.execute("SELECT id,payload FROM products WHERE id NOT IN (SELECT id FROM listings)").fetchall():
                product = json.loads(row["payload"])
                raw = {key: product[key] for key in ("id", "article", "name", "price", "url", "image")}
                self._db.execute("INSERT INTO listings(id,payload,fingerprint,last_list_at,detail_pending) VALUES(?,?,?,NULL,0)",
                                 (row["id"], json.dumps(raw, ensure_ascii=False), _fingerprint(raw)))
            self._set_meta("seeded", "1")
            self._set_meta("seed_skipped", skipped)

    def close(self):
        with self._lock:
            self._db.close()

    @staticmethod
    def _product_tokens(product):
        text = " ".join(str(product.get(key, "")) for key in ("article", "name", "properties", "description"))
        return _tokens(text + " автомат выключатель кабель провод медный медь")

    def _install(self, product):
        pid = product["id"]
        previous = self._items.get(pid)
        if previous:
            for token in self._product_tokens(previous):
                self._index[token].pop(pid, None)
            old_article = _normal(previous["article"])
            if self._articles.get(old_article) == pid:
                self._articles.pop(old_article, None)
            self._aliases[old_article.rstrip("_")].discard(pid)
            old_vendor = previous["properties"].get("ARTIKULPOSTAVSHCHIKA")
            if old_vendor:
                self._aliases[_normal(old_vendor).rstrip("_")].discard(pid)
            self._categories[previous["category"]].discard(pid)
        self._items[pid] = copy.deepcopy(product)
        article = _normal(product["article"])
        self._articles[article] = pid
        self._aliases[article.rstrip("_")].add(pid)
        vendor = product["properties"].get("ARTIKULPOSTAVSHCHIKA")
        if vendor:
            self._aliases[_normal(vendor).rstrip("_")].add(pid)
        self._categories[product["category"]].add(pid)
        self._index_product(product)

    def _index_listing(self, raw):
        pid = int(raw["id"])
        old = self._listing_items.get(pid)
        if old:
            self._listing_articles.pop(_normal(old.get("article")).rstrip("_"), None)
            self._listing_urls.pop(str(old.get("url", "")).rstrip("/"), None)
            for token in _tokens(str(old.get("name", ""))):
                self._listing_index[token].discard(pid)
        self._listing_items[pid] = raw
        self._listing_articles[_normal(raw.get("article")).rstrip("_")] = pid
        self._listing_urls[str(raw.get("url", "")).rstrip("/")] = pid
        for token in _tokens(str(raw.get("name", ""))):
            self._listing_index[token].add(pid)

    def _local(self, product):
        if product is not None:
            product.update(stock_fresh=False, stock_source="local_database")
            self._priority[product["id"]] = _now()
            if len(self._priority) > 500:
                self._priority.pop(next(iter(self._priority)))
        return product

    def get(self, product_id_or_article):
        with self._lock:
            self._reload_if_changed()
            return self._local(super().get(product_id_or_article))

    def search(self, query, limit=5):
        with self._lock:
            self._reload_if_changed()
            return [self._local(product) for product in super().search(query, limit)]

    def alternatives(self, product_id, limit=3):
        with self._lock:
            self._reload_if_changed()
            return [self._local(product) for product in super().alternatives(product_id, limit)]

    def upsert_detail(self, raw, *, expected_id=None):
        product = self._normalize(raw, fresh=True)
        if product["id"] <= 0 or (expected_id is not None and product["id"] != expected_id):
            raise ValueError("Ответ EKT содержит другой идентификатор товара.")
        with self._lock, self._db:
            version = self._next_version()
            self._db.execute("INSERT INTO products VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_detail_at=excluded.last_detail_at,version=excluded.version",
                             (product["id"], json.dumps(product, ensure_ascii=False), product["stock_checked_at"], version))
            self._db.execute("UPDATE listings SET detail_pending=0 WHERE id=?", (product["id"],))
            if product["id"] not in self._listing_items:
                listing = {key: raw.get(key) for key in ("id", "article", "name", "price", "url", "image")}
                self._db.execute("INSERT OR IGNORE INTO listings VALUES(?,?,?,NULL,0,?)",
                                 (product["id"], json.dumps(listing, ensure_ascii=False), _fingerprint(listing), version))
                self._index_listing(listing)
            self._install(product)
        return copy.deepcopy(product)

    def upsert_list(self, rows):
        now = _now()
        with self._lock, self._db:
            version = self._next_version()
            for raw in rows:
                if not isinstance(raw, dict) or not raw.get("article") or not raw.get("name"):
                    raise ValueError("EKT вернул неполную строку списка.")
                pid = int(raw["id"])
                if pid <= 0:
                    raise ValueError("EKT вернул неверный id.")
                fingerprint = _fingerprint(raw)
                self._db.execute("""INSERT INTO listings VALUES(?,?,?,?,1,?)
                    ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_list_at=excluded.last_list_at,
                    detail_pending=CASE WHEN listings.fingerprint!=excluded.fingerprint THEN 1 ELSE listings.detail_pending END,
                    version=CASE WHEN listings.fingerprint!=excluded.fingerprint THEN excluded.version ELSE listings.version END,
                    fingerprint=excluded.fingerprint""", (pid, json.dumps(raw, ensure_ascii=False), fingerprint, now, version))
                self._index_listing(raw)

    def credentials_configured(self):
        return bool((os.getenv("EKT_API_USER") or os.getenv("EKT_API_USERNAME")) and os.getenv("EKT_API_PASSWORD"))

    def client(self, timeout=5):
        username = os.getenv("EKT_API_USER") or os.getenv("EKT_API_USERNAME") or ""
        password = os.getenv("EKT_API_PASSWORD") or ""
        return httpx.AsyncClient(timeout=timeout, auth=httpx.BasicAuth(username, password), follow_redirects=False,
                                 limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    async def request_json(self, client, path, params, *, request_timeout=None):
        base = os.getenv("EKT_API_BASE", "https://ekt.kz/api").rstrip("/")
        for attempt in range(2):
            try:
                async with self._network_gate:
                    timeout_args = {"timeout": request_timeout} if request_timeout is not None else {}
                    response = await client.get(base + path, params=params, **timeout_args)
                    response.raise_for_status()
                    return response.json()
            except (httpx.HTTPError, ValueError):
                if attempt:
                    raise
                await asyncio.sleep(.1)

    async def refresh(self, product_id):
        with self._lock:
            pid = self._resolve(product_id)
        if pid is None:
            raise KeyError("Товар отсутствует в локальном каталоге")
        if not self.credentials_configured():
            return self._fallback(pid, "Не настроен доступ к обновлению остатков")
        lock = self._refresh_locks.setdefault(pid, asyncio.Lock())
        try:
            async with asyncio.timeout(1.6):
                async with lock, self.client(timeout=.65) as client:
                    raw = await self.request_json(client, "/products/detail", {"id": pid})
                    if _normal(raw.get("article")) != _normal(self._items[pid]["article"]):
                        raise ValueError("Артикул изменился; выберите товар заново.")
                    return self.upsert_detail(raw, expected_id=pid)
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError):
            return self._fallback(pid, "API остатков временно недоступен; показана локальная БД")

    async def resolve_certificates(self, product_id):
        async with self._network_gate:
            product = await super().resolve_certificates(product_id)
        with self._lock, self._db:
            version = self._next_version()
            self._db.execute("UPDATE products SET payload=?,version=? WHERE id=?", (json.dumps(self._items[product["id"]], ensure_ascii=False), version, product["id"]))
        return product

    def candidates(self, text):
        with self._lock:
            self._reload_if_changed()
            for identifier in re.findall(r"[\w][\w./-]*", text, flags=re.UNICODE):
                pid = self._listing_articles.get(_normal(identifier).rstrip("_"))
                if pid:
                    return [pid]
            for url in re.findall(r"https?://[^\s<>]+", text):
                parsed = urlparse(url.rstrip(".,)"))
                if parsed.hostname != "ekt.kz":
                    continue
                pid = self._listing_urls.get(url.rstrip("/.,)"))
                if pid:
                    return [pid]
                values = parse_qs(parsed.query).get("id", [])
                if values and values[0].isdigit() and "/products/detail" in parsed.path:
                    return [int(values[0])]
            explicit_id = re.search(r"\bid\s*[:=#]?\s*(\d{1,10})\b", text, re.I)
            if explicit_id:
                return [int(explicit_id.group(1))]
            exact_name = [pid for pid, raw in self._listing_items.items() if _normal(raw.get("name")) == _normal(text)]
            if exact_name:
                return exact_name[:5]
            scores = defaultdict(int)
            for token in _tokens(text):
                for pid in self._listing_index.get(token, set()):
                    scores[pid] += 1
            if not scores:
                return []
            best = max(scores.values())
            return sorted(pid for pid, score in scores.items() if score == best)[:5]

    def detail_batch(self, budget):
        with self._lock:
            pending = [row[0] for row in self._db.execute("SELECT l.id FROM listings l LEFT JOIN detail_attempts a ON a.id=l.id LEFT JOIN products p ON p.id=l.id WHERE detail_pending=1 ORDER BY (p.id IS NOT NULL),COALESCE(a.attempted_at,''),l.id LIMIT ?", (budget,))]
            priority = [pid for pid, _ in sorted(self._priority.items(), key=lambda pair: pair[1], reverse=True) if pid not in pending]
            cursor = int(self._meta("rolling_cursor", "0"))
            ordered = [row[0] for row in self._db.execute("SELECT id FROM listings ORDER BY id")]
        # Keep a rolling quota even under a constant stream of viewed products.
        priority_quota = min(len(priority), budget // 4)
        rolling_quota = budget // 4 if len(ordered) > len(pending) else 0
        selected = pending[:max(1, budget - priority_quota - rolling_quota)] + priority[:priority_quota]
        rolling = [pid for pid in ordered if pid > cursor] + [pid for pid in ordered if pid <= cursor]
        rolling_selected = []
        for pid in rolling:
            if len(selected) >= budget:
                break
            if pid not in selected:
                selected.append(pid)
                rolling_selected.append(pid)
        return selected, rolling_selected

    def status(self):
        since = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            listed = self._db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            fresh = self._db.execute("SELECT COUNT(*) FROM products WHERE last_detail_at>=?", (since,)).fetchone()[0]
            pending = self._db.execute("SELECT COUNT(*) FROM listings WHERE detail_pending=1").fetchone()[0]
            last = self._db.execute("SELECT MAX(last_detail_at) FROM products").fetchone()[0]
            run = self._db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
            return {"storage": "sqlite", "local_products": total, "listed_products": listed,
                    "pending_details": pending, "details_checked_last_5m": fresh,
                    "stock_coverage_percent": round(100 * fresh / listed, 2) if listed else 0,
                    "last_detail_at": last, "last_complete_list_at": self._meta("last_complete_list_at"),
                    "last_success_at": self._meta("last_success_at"),
                    "rolling_cursor": int(self._meta("rolling_cursor", "0")),
                    "seed_skipped": int(self._meta("seed_skipped", "0")),
                    "last_run": dict(run) if run else None,
                    "stock_note": "Каждые 5 минут обновляется список. Остатки обновляются партиями и при подтверждении корзины."}


class CatalogSync:
    def __init__(self, catalog: SyncedCatalog, cms=None, *, interval_seconds=300, detail_budget=200, per_page=1000):
        self.catalog, self.cms = catalog, cms
        self.interval_seconds = max(.05, float(interval_seconds))
        self.detail_budget = max(1, min(int(detail_budget), 200))
        self.per_page = max(1, min(int(per_page), 1000))
        self._wake = asyncio.Event()
        self._reason = "startup"
        self._task = None
        self._run_lock = asyncio.Lock()
        self._next_run_at = None
        self._lease_owner = secrets.token_hex(16)

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="ekt-catalog-sync")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def trigger(self, reason="manual"):
        # Coalesce repeated requests instead of creating unbounded network tasks.
        self._reason = reason if re.fullmatch(r"[a-z0-9_.-]{1,60}", reason) else "manual"
        self._wake.set()
        return {"queued": True, "running": self._task is not None and not self._task.done()}

    def status(self):
        return {**self.catalog.status(), "interval_seconds": self.interval_seconds, "detail_budget": self.detail_budget,
                "running": self._task is not None and not self._task.done(), "queued": self._wake.is_set(),
                "next_run_at": self._next_run_at}

    async def _loop(self):
        while True:
            reason = self._reason
            self._wake.clear()
            started = asyncio.get_running_loop().time()
            self._next_run_at = (datetime.now(timezone.utc) + timedelta(seconds=self.interval_seconds)).isoformat()
            await self.run_once(reason)
            remaining = max(.01, self.interval_seconds - (asyncio.get_running_loop().time() - started))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except TimeoutError:
                self._reason = "scheduled"

    async def run_once(self, reason="manual"):
        async with self._run_lock:
            with self.catalog._lock, self.catalog._db:
                now = clock_time.time()
                acquired = self.catalog._db.execute("""INSERT INTO sync_lease VALUES(1,?,?)
                    ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
                    WHERE sync_lease.expires_at<? OR sync_lease.owner=?""", (self._lease_owner, now + 300, now, self._lease_owner)).rowcount
            if not acquired:
                return {"status": "lease_busy"}
            try:
                return await self._run_once(reason)
            finally:
                with self.catalog._lock, self.catalog._db:
                    self.catalog._db.execute("DELETE FROM sync_lease WHERE id=1 AND owner=?", (self._lease_owner,))

    async def _run_once(self, reason):
        catalog = self.catalog
        with catalog._lock, catalog._db:
            cursor = catalog._db.execute("INSERT INTO sync_runs(reason,status,started_at) VALUES(?,'running',?)", (reason, _now()))
            run_id = cursor.lastrowid
        result = {"status": "success", "list_count": 0, "list_complete": 0, "details_updated": 0, "details_failed": 0, "error_type": None}
        cancelled = False
        try:
            if not catalog.credentials_configured():
                result["status"] = "not_configured"
            else:
                async with asyncio.timeout(min(240, max(10, self.interval_seconds - 1))):
                    # Bulk list pages may take longer than a chat response. This
                    # background-only allowance remains inside the 240s run budget.
                    async with catalog.client(timeout=20) as client:
                        seen, page = set(), 1
                        while page <= 1000:
                            payload = await catalog.request_json(client, "/products", {"page": page, "per_page": self.per_page})
                            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                                raise ValueError("Неверный формат списка EKT")
                            batch = payload["items"]
                            new = [raw for raw in batch if int(raw["id"]) not in seen]
                            if not new:
                                result["list_complete"] = 1
                                break
                            catalog.upsert_list(new)
                            seen.update(int(raw["id"]) for raw in new)
                            result["list_count"] = len(seen)
                            if len(batch) < self.per_page:
                                result["list_complete"] = 1
                                break
                            page += 1
                        if not result["list_complete"]:
                            raise ValueError("Список EKT превысил предел страниц")
                        with catalog._lock, catalog._db:
                            catalog._set_meta("last_complete_list_at", _now())
                        selected, rolling = catalog.detail_batch(self.detail_budget)
                        queue = asyncio.Queue()
                        for pid in selected:
                            queue.put_nowait(pid)
                        attempted_rolling = []

                        async def worker():
                            while not queue.empty():
                                pid = queue.get_nowait()
                                try:
                                    raw = await catalog.request_json(client, "/products/detail", {"id": pid}, request_timeout=5)
                                    catalog.upsert_detail(raw, expected_id=pid)
                                    result["details_updated"] += 1
                                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                                    result["details_failed"] += 1
                                finally:
                                    with catalog._lock, catalog._db:
                                        catalog._db.execute("INSERT INTO detail_attempts VALUES(?,?) ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at", (pid, _now()))
                                    if pid in rolling:
                                        attempted_rolling.append(pid)
                                    queue.task_done()

                        # TaskGroup cancels both workers on a run timeout or shutdown.
                        async with asyncio.TaskGroup() as group:
                            for _ in range(2):
                                group.create_task(worker())
                        if attempted_rolling:
                            with catalog._lock, catalog._db:
                                catalog._set_meta("rolling_cursor", next(pid for pid in reversed(rolling) if pid in attempted_rolling))
                        if result["details_failed"]:
                            result["status"] = "partial"
        except asyncio.CancelledError:
            result.update(status="cancelled", error_type="CancelledError")
            cancelled = True
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, ExceptionGroup) as exc:
            result.update(status="partial" if result["list_count"] or result["details_updated"] else "failed", error_type=type(exc).__name__)
        finally:
            with catalog._lock, catalog._db:
                catalog._db.execute("UPDATE sync_runs SET status=?,finished_at=?,list_count=?,list_complete=?,details_updated=?,details_failed=?,error_type=? WHERE id=?",
                                    (result["status"], _now(), result["list_count"], result["list_complete"], result["details_updated"], result["details_failed"], result["error_type"], run_id))
                if result["status"] == "success":
                    catalog._set_meta("last_success_at", _now())
            if self.cms is not None:
                self.cms.record_event("development", "catalog_sync", {"status": result["status"], "count": result["details_updated"],
                                                                       "error_type": result["error_type"] or "none"})
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _site_search(self, text):
        # The public HTML form is GET /catalog/?q=. This is not an API search parameter.
        identifier = re.search(r"(?<!\w)[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_.-]*\d[A-Za-zА-Яа-я0-9_.-]*(?!\w)", text)
        query = identifier.group() if identifier else text[:255]
        async with httpx.AsyncClient(timeout=.7, follow_redirects=False) as client:
            for attempt in range(2):
                try:
                    async with self.catalog._network_gate:
                        response = await client.get("https://ekt.kz/catalog/", params={"q": query})
                        response.raise_for_status()
                    parser = _SearchCards()
                    parser.feed(response.text[:3_000_000])
                    exact = [row for row in parser.cards if _normal(row["article"]).rstrip("_") == _normal(query).rstrip("_")]
                    return exact or parser.cards[:5]
                except httpx.HTTPError:
                    if attempt:
                        raise
                    await asyncio.sleep(.05)

    async def lookup(self, text):
        self.trigger("customer_assertion")
        if not self.catalog.credentials_configured():
            return {"status": "unavailable", "sync_triggered": True}
        pid = None
        try:
            async with asyncio.timeout(1.9):
                text = str(text)[:4000]
                candidates = self.catalog.candidates(text)
                rows = [self.catalog._listing_items[pid] for pid in candidates if pid in self.catalog._listing_items]
                if not candidates:
                    rows = await self._site_search(text)
                    candidates = list(dict.fromkeys(row["id"] for row in rows))
                if not candidates:
                    return {"status": "not_found", "sync_triggered": True, "site_checked": True,
                            "message": "Поиск на сайте не вернул карточку. Уточните артикул или ссылку EKT; синхронизация запущена."}
                if len(candidates) > 1:
                    return {"status": "ambiguous", "sync_triggered": True,
                            "candidates": [{key: row.get(key) for key in ("id", "article", "name", "url")} for row in rows]}
                pid = candidates[0]
                async with self.catalog.client(timeout=.75) as client:
                    raw = await self.catalog.request_json(client, "/products/detail", {"id": pid})
                    product = self.catalog.upsert_detail(raw, expected_id=pid)
                    self.trigger("lookup_import")
                    return {"status": "found", "product": product, "sync_triggered": True, "source": "ekt_api"}
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError):
            return {"status": "pending", "sync_triggered": True, "product_id": pid,
                    "message": "EKT не успел вернуть карточку; синхронизация продолжится в фоне."}
