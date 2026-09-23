"""SQLite sync, EKT lookup and rate limits without any external network."""

import asyncio
import json
import time

import httpx
import pytest

from app.catalog_sync import CatalogSync, SyncedCatalog, _SearchCards


def detail(pid, **changes):
    return {"id": pid, "article": f"TEST{pid:06d}_", "name": f"Изделие {pid}", "price": 100,
            "quantity": 5, "stores": [{"id": 1, "name": "Астана", "quantity": 5}],
            "properties": {}, "url": f"https://ekt.kz/catalog/accessories/item_{pid}/", "image": "", **changes}


def listing(raw):
    return {key: raw[key] for key in ("id", "article", "name", "price", "url", "image")}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("EKT_API_USER", "test")
    monkeypatch.setenv("EKT_API_PASSWORD", "test")
    monkeypatch.setenv("EKT_API_BASE", "https://ekt.kz/api")
    rows = [detail(1), detail(2), detail(3)]
    (tmp_path / "raw_details.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    (tmp_path / "raw_products.json").write_text(json.dumps([listing(row) for row in rows]), encoding="utf-8")
    result = SyncedCatalog(tmp_path)
    yield result
    result.close()


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))


def test_sqlite_seed_is_once_and_local_search_is_offline(catalog, monkeypatch):
    assert catalog.count == 3
    assert catalog.get("TEST000001")["quantity"] == 5
    assert catalog.get(1)["stock_source"] == "local_database"
    assert catalog.status()["stock_coverage_percent"] == 0
    catalog.upsert_detail(detail(1, name="Розетка уникальная", quantity=9))
    catalog.upsert_detail(detail(4, name="Новый контактор"))
    assert catalog.search("уникальная")[0]["id"] == 1
    # Raw seed may later disappear or contain outdated rows: SQLite is authoritative.
    (catalog.data_dir / "raw_details.jsonl").write_text("bad seed", encoding="utf-8")
    other = SyncedCatalog(catalog.data_dir)
    try:
        assert other.get(1)["quantity"] == 9
        assert other.get(4)["name"] == "Новый контактор"
        assert other.search("уникальная")[0]["id"] == 1
        assert other.get(1)["stock_fresh"] is False
    finally:
        other.close()


def test_changed_search_indexes_and_articles_are_replaced(catalog):
    catalog.upsert_detail(detail(1, name="Контактор фиолетовый", article="REPLACED"))
    assert catalog.get("TEST000001") is None
    assert catalog.get("REPLACED")["id"] == 1
    assert catalog.search("фиолетовый")[0]["id"] == 1
    catalog.upsert_detail(detail(1, name="Розетка зелёная", article="REPLACED"))
    assert catalog.search("фиолетовый") == []
    assert catalog.search("зелёная")[0]["id"] == 1


def test_first_sync_imports_missing_products_before_changed_seed_details(tmp_path, monkeypatch):
    monkeypatch.setenv("EKT_API_USER", "test")
    monkeypatch.setenv("EKT_API_PASSWORD", "test")
    monkeypatch.setenv("EKT_API_BASE", "https://ekt.kz/api")
    seeded = [detail(pid) for pid in range(1, 7)]
    (tmp_path / "raw_details.jsonl").write_text("\n".join(json.dumps(row) for row in seeded), encoding="utf-8")
    # Cloud seed has no raw_products; changed list prices queue every existing row.
    rows = [detail(pid, price=101) for pid in range(1, 7)] + [detail(99), detail(100)]
    requested = []

    def handler(request):
        if request.url.path == "/api/products":
            return httpx.Response(200, json={"items": [listing(row) for row in rows]})
        pid = int(request.url.params["id"])
        requested.append(pid)
        return httpx.Response(200, json=detail(pid))

    mock_client(monkeypatch, handler)
    cold = SyncedCatalog(tmp_path)
    try:
        assert cold.count == 6
        result = asyncio.run(CatalogSync(cold, detail_budget=2).run_once())
        assert result["status"] == "success"
        assert set(requested) == {99, 100}
        assert cold.get(99) is not None and cold.get(100) is not None
        assert cold.status()["pending_details"] == 6
    finally:
        cold.close()


def test_run_refreshes_full_list_changed_details_and_respects_two_requests(catalog, monkeypatch):
    rows = [detail(1, price=101), detail(2), detail(3), detail(4)]
    active = peak = 0
    requests = []

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        requests.append(str(request.url))
        try:
            await asyncio.sleep(.01)
            if request.url.path == "/api/products":
                assert request.extensions["timeout"]["read"] == 20
                page = int(request.url.params["page"])
                # EKT repeats first page once the requested page is out of range.
                return httpx.Response(200, json={"items": [listing(row) for row in rows[(page - 1) * 2:page * 2]] if page <= 2 else [listing(row) for row in rows[:2]]})
            assert request.extensions["timeout"]["read"] == 5
            return httpx.Response(200, json=rows[int(request.url.params["id"]) - 1])
        finally:
            active -= 1

    mock_client(monkeypatch, handler)
    syncer = CatalogSync(catalog, detail_budget=4, per_page=2)
    result = asyncio.run(syncer.run_once())
    assert result["status"] == "success" and result["list_complete"] == 1
    assert result["list_count"] == 4 and result["details_updated"] == 4
    assert peak == 2
    assert catalog.get(1)["price"] == 101
    assert catalog.get(4)["quantity"] == 5
    assert catalog.status()["stock_coverage_percent"] == 100
    assert catalog.status()["last_complete_list_at"] and catalog.status()["last_success_at"]
    assert all("q=" not in url for url in requests)


def test_incomplete_list_never_deletes_cached_products(catalog, monkeypatch):
    def handler(request):
        if int(request.url.params["page"]) == 1:
            return httpx.Response(200, json={"items": [listing(detail(7)), listing(detail(8))]})
        return httpx.Response(503)

    mock_client(monkeypatch, handler)
    result = asyncio.run(CatalogSync(catalog, per_page=2).run_once())
    assert result["status"] == "partial"
    assert result["list_complete"] == 0
    assert catalog.count == 3 and catalog.get(1) is not None
    assert catalog.status()["pending_details"] == 2
    assert catalog.status()["last_complete_list_at"] is None


def test_list_fields_never_fabricate_stock_or_overwrite_details(catalog):
    catalog.upsert_list([listing(detail(1, price=500)), listing(detail(8))])
    assert catalog.get(1)["quantity"] == 5
    assert catalog.get(1)["price"] == 100
    assert catalog.get(8) is None
    assert catalog.status()["pending_details"] == 2
    assert catalog.candidates("TEST000008") == [8]


def test_rolling_batch_cursor_persists(catalog, monkeypatch):
    def handler(request):
        if request.url.path == "/api/products":
            return httpx.Response(200, json={"items": [listing(detail(pid)) for pid in (1, 2, 3)]})
        return httpx.Response(200, json=detail(int(request.url.params["id"])))

    mock_client(monkeypatch, handler)
    syncer = CatalogSync(catalog, detail_budget=1)
    asyncio.run(syncer.run_once())
    assert catalog.status()["rolling_cursor"] == 1
    asyncio.run(syncer.run_once())
    assert catalog.status()["rolling_cursor"] == 2
    assert catalog.status()["details_checked_last_5m"] == 2


def test_refresh_writes_db_but_local_get_remains_honest(catalog, monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=detail(1, quantity=17)))
    product = asyncio.run(catalog.refresh(1))
    assert product["stock_fresh"] is True and product["quantity"] == 17
    assert catalog.get(1)["quantity"] == 17 and catalog.get(1)["stock_fresh"] is False
    row = catalog._db.execute("SELECT payload,last_detail_at FROM products WHERE id=1").fetchone()
    assert json.loads(row[0])["quantity"] == 17 and row[1]


def test_refresh_rejects_wrong_product(catalog, monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=detail(2)))
    product = asyncio.run(catalog.refresh(1))
    assert product["stock_fresh"] is False and product["id"] == 1
    assert catalog.get(1)["quantity"] == 5


def test_lookup_imports_known_list_sku_and_queues_extra_sync(catalog, monkeypatch):
    catalog.upsert_list([listing(detail(9))])
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=detail(9, name="Новая клемма")))
    syncer = CatalogSync(catalog)
    result = asyncio.run(syncer.lookup("Товар TEST000009_ точно есть на сайте"))
    assert result["status"] == "found"
    assert catalog.get(9)["name"] == "Новая клемма"
    assert syncer.status()["queued"] is True
    assert catalog.search("Новая клемма")[0]["id"] == 9


def test_lookup_searches_verified_public_form_for_missing_sku(catalog, monkeypatch):
    html = '<div class="small_card_catalog product-card-out-catalog"><div class="product-card"><a href="/catalog/klemma/"><h3 class="product-title">Клемма новая</h3></a><div class="product-article">Код товара TEST000099_</div><a data-action="add2basket" data-id="99">Купить</a></div></div>'
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if request.url.path == "/catalog/":
            assert "authorization" not in request.headers
            assert request.url.params["q"] == "TEST000099_"
            return httpx.Response(200, text=html)
        return httpx.Response(200, json=detail(99, name="Клемма новая"))

    mock_client(monkeypatch, handler)
    result = asyncio.run(CatalogSync(catalog).lookup("TEST000099_ точно есть на сайте"))
    assert result["status"] == "found"
    assert catalog.get(99)["name"] == "Клемма новая"
    assert len(urls) == 2
    assert "/catalog/?q=" in urls[0] and "/api/products/detail?id=99" in urls[1]


def test_lookup_timeout_is_bounded_and_does_not_invent_a_product(catalog, monkeypatch):
    async def slow(request):
        await asyncio.sleep(4)
        return httpx.Response(200, json=detail(55))

    mock_client(monkeypatch, slow)
    started = time.perf_counter()
    result = asyncio.run(CatalogSync(catalog).lookup("https://ekt.kz/api/products/detail?id=55"))
    assert time.perf_counter() - started < 2.2
    assert result["status"] == "pending" and result["sync_triggered"] is True
    assert catalog.get(55) is None


def test_ambiguous_lookup_does_not_import_an_arbitrary_match(catalog, monkeypatch):
    mock_client(monkeypatch, lambda request: pytest.fail("Ambiguous local match must not make a network call"))
    result = asyncio.run(CatalogSync(catalog).lookup("Изделие"))
    assert result["status"] == "ambiguous" and len(result["candidates"]) == 3


def test_multiprocess_memory_reloads_only_committed_versions(catalog):
    other = SyncedCatalog(catalog.data_dir)
    try:
        assert other.get(1)["quantity"] == 5
        catalog.upsert_detail(detail(1, quantity=42))
        catalog.upsert_detail(detail(77, name="Новая лампа"))
        other._last_version_poll = 0
        assert other.get(1)["quantity"] == 42
        assert other.get(77)["name"] == "Новая лампа"
        assert other.search("Новая лампа")[0]["id"] == 77
    finally:
        other.close()


def test_sqlite_lease_prevents_two_background_scans(catalog, monkeypatch):
    scans = []

    async def handler(request):
        scans.append(str(request.url))
        await asyncio.sleep(.03)
        if request.url.path == "/api/products":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json=detail(int(request.url.params["id"])))

    mock_client(monkeypatch, handler)
    other = SyncedCatalog(catalog.data_dir)
    try:
        async def concurrent():
            return await asyncio.gather(CatalogSync(catalog, detail_budget=1).run_once(), CatalogSync(other, detail_budget=1).run_once())
        result = asyncio.run(concurrent())
        assert sorted(row["status"] for row in result) == ["lease_busy", "success"]
        assert sum("/api/products?" in url for url in scans) == 1
    finally:
        other.close()


def test_background_start_trigger_stop_are_nonblocking_and_persist_runs(catalog, monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"items": []}) if request.url.path == "/api/products" else httpx.Response(200, json=detail(int(request.url.params["id"]))))
    syncer = CatalogSync(catalog, interval_seconds=.1, detail_budget=1)

    async def scenario():
        await syncer.start()
        syncer.trigger("customer_assertion")
        await asyncio.sleep(.15)
        await syncer.stop()

    asyncio.run(scenario())
    assert syncer.status()["running"] is False
    assert catalog._db.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] >= 1
    assert catalog._db.execute("SELECT COUNT(*) FROM sync_lease").fetchone()[0] == 0


def test_not_configured_does_not_contact_network(catalog, monkeypatch):
    monkeypatch.delenv("EKT_API_PASSWORD")
    mock_client(monkeypatch, lambda request: pytest.fail("Missing credentials must not make a network call"))
    assert asyncio.run(CatalogSync(catalog).run_once())["status"] == "not_configured"
    assert asyncio.run(CatalogSync(catalog).lookup("TEST000001"))["status"] == "unavailable"


def test_failed_new_card_does_not_starve_other_pending_cards(catalog, monkeypatch):
    catalog.upsert_list([listing(detail(4)), listing(detail(5))])

    def handler(request):
        if request.url.path == "/api/products":
            return httpx.Response(200, json={"items": [listing(detail(pid)) for pid in (1, 2, 3, 4, 5)]})
        pid = int(request.url.params["id"])
        return httpx.Response(503) if pid == 4 else httpx.Response(200, json=detail(pid))

    mock_client(monkeypatch, handler)
    syncer = CatalogSync(catalog, detail_budget=1)
    assert asyncio.run(syncer.run_once())["status"] == "partial"
    assert catalog.get(4) is None
    assert asyncio.run(syncer.run_once())["status"] == "success"
    assert catalog.get(5) is not None
    assert catalog.status()["pending_details"] == 1
