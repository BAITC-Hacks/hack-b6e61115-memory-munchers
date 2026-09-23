"""Persistent conversation memory and explicitly confirmed local baskets.

The caller must derive ``explicit=True`` from a real customer confirmation,
never from model-generated tool arguments. Saved requests are local records,
not purchases or orders placed on ekt.kz.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json
from pathlib import Path
import secrets
import sqlite3
import threading


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _number(value, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Количество и цена должны быть числами.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Количество и цена должны быть числами.") from None
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise ValueError("Количество должно быть положительным, цена — неотрицательной.")
    if number > Decimal("1e15"):
        raise ValueError("Число выходит за допустимый диапазон.")
    return number


def _plain(number: Decimal):
    return int(number) if number == number.to_integral_value() else float(number)


class Store:
    HISTORY_KEEP = 200
    HISTORY_CONTENT_LIMIT = 24_000

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, id);
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                items TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS proposals_user ON proposals(user_id, status);
            CREATE TABLE IF NOT EXISTS carts (
                user_id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cart_items (
                user_id TEXT NOT NULL REFERENCES carts(user_id),
                product_id INTEGER NOT NULL, article TEXT NOT NULL,
                name TEXT NOT NULL, price TEXT NOT NULL, quantity TEXT NOT NULL,
                PRIMARY KEY(user_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                cart_revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(user_id, cart_revision)
            );
        """)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def close(self):
        with self._lock:
            self._db.close()

    def history(self, user_id, limit=16) -> list[dict]:
        limit = max(0, min(int(limit), self.HISTORY_KEEP))
        with self._lock:
            rows = self._db.execute(
                "SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (str(user_id), limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_message(self, user_id, role, content):
        if role not in {"user", "assistant", "system", "developer", "tool"}:
            raise ValueError("Неизвестная роль сообщения.")
        if not isinstance(content, str):
            raise ValueError("Текст сообщения должен быть строкой.")
        user = str(user_id)
        with self._transaction():
            self._db.execute(
                "INSERT INTO messages(user_id,role,content,created_at) VALUES(?,?,?,?)",
                (user, role, content[:self.HISTORY_CONTENT_LIMIT], _now()),
            )
            self._db.execute(
                "DELETE FROM messages WHERE user_id=? AND id NOT IN "
                "(SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                (user, user, self.HISTORY_KEEP),
            )

    @staticmethod
    def _items(items) -> list[dict]:
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ValueError("Выберите от 1 до 100 позиций.")
        merged = {}
        for source in items:
            if not isinstance(source, dict):
                raise ValueError("Неверный формат позиции.")
            product_id = source.get("id")
            if isinstance(product_id, bool) or not str(product_id).isdigit():
                raise ValueError("У товара должен быть целочисленный идентификатор.")
            product_id = int(product_id)
            if product_id <= 0 or product_id > 2**63 - 1:
                raise ValueError("Неверный идентификатор товара.")
            quantity = _number(source.get("quantity"), positive=True)
            price = _number(source.get("price"))
            if product_id in merged:
                item = merged[product_id]
                if price != Decimal(str(item["price"])):
                    raise ValueError("У одинаковых товаров указаны разные цены.")
                item["quantity"] = _plain(_number(Decimal(str(item["quantity"])) + quantity, positive=True))
            else:
                merged[product_id] = {
                    "id": product_id,
                    "article": str(source.get("article", "")),
                    "name": str(source.get("name", "")),
                    "price": _plain(price),
                    "quantity": _plain(quantity),
                }
        return list(merged.values())

    @staticmethod
    def _proposal(row) -> dict:
        return {
            "id": row["id"], "items": json.loads(row["items"]),
            "status": row["status"], "created_at": _utc(row["created_at"]),
            "expires_at": _utc(row["expires_at"]),
        }

    def propose(self, user_id, items: list[dict], ttl_seconds=900) -> dict:
        normalized = self._items(items)
        ttl = _number(ttl_seconds, positive=True)
        if ttl > 86400:
            raise ValueError("Предложение может действовать не дольше суток.")
        user, now, token = str(user_id), _now(), secrets.token_urlsafe(18)
        with self._transaction():
            # A new line must not silently reprice quantities confirmed earlier.
            current_prices = {r["product_id"]: Decimal(r["price"]) for r in self._db.execute(
                "SELECT product_id,price FROM cart_items WHERE user_id=?", (user,)
            ).fetchall()}
            for item in normalized:
                old_price = current_prices.get(item["id"])
                if old_price is not None and old_price != Decimal(str(item["price"])):
                    raise ValueError("Цена товара в корзине изменилась. Очистите корзину и подтвердите её по новой цене.")
            self._db.execute(
                "UPDATE proposals SET status='invalidated' WHERE user_id=? AND status='pending'", (user,)
            )
            self._db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?)", (
                token, user, json.dumps(normalized, ensure_ascii=False), "pending", now, now + float(ttl)
            ))
            row = self._db.execute("SELECT * FROM proposals WHERE id=?", (token,)).fetchone()
            return self._proposal(row)

    def pending(self, user_id) -> dict | None:
        user = str(user_id)
        with self._transaction():
            self._db.execute(
                "UPDATE proposals SET status='expired' WHERE user_id=? AND status='pending' AND expires_at<=?",
                (user, _now()),
            )
            row = self._db.execute(
                "SELECT * FROM proposals WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (user,),
            ).fetchone()
            return self._proposal(row) if row else None

    def _cart(self, user: str) -> dict:
        self._db.execute("INSERT OR IGNORE INTO carts(user_id,token) VALUES(?,?)", (user, secrets.token_urlsafe(24)))
        token = self._db.execute("SELECT token FROM carts WHERE user_id=?", (user,)).fetchone()["token"]
        rows = self._db.execute("SELECT * FROM cart_items WHERE user_id=? ORDER BY product_id", (user,)).fetchall()
        items = [{
            "id": row["product_id"], "article": row["article"], "name": row["name"],
            "price": _plain(Decimal(row["price"])), "quantity": _plain(Decimal(row["quantity"])),
        } for row in rows]
        with localcontext() as context:
            context.prec = 50
            total = sum((Decimal(row["price"]) * Decimal(row["quantity"]) for row in rows), Decimal(0))
            total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"token": token, "items": items, "total": _plain(total)}

    def get_cart(self, user_id) -> dict:
        with self._transaction():
            return self._cart(str(user_id))

    def cart_by_token(self, token) -> dict | None:
        with self._transaction():
            row = self._db.execute("SELECT user_id FROM carts WHERE token=?", (str(token),)).fetchone()
            return self._cart(row["user_id"]) if row else None

    def confirm(self, user_id, proposal_id, stocks: dict[int, float], explicit: bool = False) -> dict:
        if explicit is not True:
            return {"status": "confirmation_required"}
        user = str(user_id)
        with self._transaction():
            row = self._db.execute("SELECT * FROM proposals WHERE id=? AND user_id=?", (proposal_id, user)).fetchone()
            if not row:
                return {"status": "not_found"}
            if row["status"] == "confirmed":
                return {"status": "confirmed", "replayed": True, "cart": self._cart(user)}
            if row["status"] != "pending":
                return {"status": row["status"]}
            if row["expires_at"] <= _now():
                self._db.execute("UPDATE proposals SET status='expired' WHERE id=?", (proposal_id,))
                return {"status": "expired"}
            items = json.loads(row["items"])
            existing = {r["product_id"]: Decimal(r["quantity"]) for r in self._db.execute(
                "SELECT product_id,quantity FROM cart_items WHERE user_id=?", (user,)
            ).fetchall()}
            errors = []
            for item in items:
                product_id = item["id"]
                previous = existing.get(product_id, Decimal(0))
                needed = previous + Decimal(str(item["quantity"]))
                try:
                    stock = _number(stocks.get(product_id))
                except (ValueError, AttributeError):
                    errors.append({"id": product_id, "reason": "unknown_stock"})
                    continue
                if needed > stock:
                    errors.append({
                        "id": product_id, "reason": "insufficient_stock", "stock": _plain(stock),
                        "requested": item["quantity"], "already_in_cart": _plain(previous),
                        "remaining": _plain(max(Decimal(0), stock - previous)),
                    })
            if errors:
                status = "unknown_stock" if any(e["reason"] == "unknown_stock" for e in errors) else "insufficient_stock"
                return {"status": status, "errors": errors}
            self._cart(user)
            for item in items:
                quantity = existing.get(item["id"], Decimal(0)) + Decimal(str(item["quantity"]))
                self._db.execute("""
                    INSERT INTO cart_items VALUES(?,?,?,?,?,?)
                    ON CONFLICT(user_id,product_id) DO UPDATE SET
                      article=excluded.article, name=excluded.name,
                      price=excluded.price, quantity=excluded.quantity
                """, (user, item["id"], item["article"], item["name"], str(item["price"]), str(quantity)))
            self._db.execute("UPDATE carts SET revision=revision+1 WHERE user_id=?", (user,))
            self._db.execute("UPDATE proposals SET status='confirmed' WHERE id=?", (proposal_id,))
            return {"status": "confirmed", "replayed": False, "cart": self._cart(user)}

    def save_request(self, user_id) -> dict:
        user = str(user_id)
        with self._transaction():
            cart = self._cart(user)
            if not cart["items"]:
                return {"status": "empty_cart"}
            revision = self._db.execute("SELECT revision FROM carts WHERE user_id=?", (user,)).fetchone()[0]
            existing = self._db.execute(
                "SELECT snapshot FROM requests WHERE user_id=? AND cart_revision=?", (user, revision)
            ).fetchone()
            if existing:
                return json.loads(existing["snapshot"])
            now = _now()
            request = {
                "id": secrets.token_urlsafe(12), "status": "request", "created_at": _utc(now),
                "items": cart["items"], "total": cart["total"],
            }
            self._db.execute("INSERT INTO requests VALUES(?,?,?,?,?)", (
                request["id"], user, revision, json.dumps(request, ensure_ascii=False), now
            ))
            return request

    def orders(self, user_id, limit=5) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT snapshot FROM requests WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                (str(user_id), max(0, min(int(limit), 100))),
            ).fetchall()
            return [json.loads(row["snapshot"]) for row in rows]

    def clear_cart(self, user_id):
        user = str(user_id)
        with self._transaction():
            self._db.execute("DELETE FROM cart_items WHERE user_id=?", (user,))
            self._db.execute("UPDATE carts SET revision=revision+1 WHERE user_id=?", (user,))
            self._db.execute(
                "UPDATE proposals SET status='invalidated' WHERE user_id=? AND status='pending'", (user,)
            )

    def cancel_proposal(self, user_id):
        with self._transaction():
            self._db.execute(
                "UPDATE proposals SET status='cancelled' WHERE user_id=? AND status='pending'", (str(user_id),)
            )
