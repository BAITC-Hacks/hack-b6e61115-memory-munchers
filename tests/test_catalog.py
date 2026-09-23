"""Regression checks against the actual EKT snapshot; no external requests."""

import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.catalog import Catalog, _DocumentLinks, _clean, _urls


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(Path(__file__).resolve().parents[1] / "data")

    def test_real_snapshot_and_exact_article(self):
        self.assertEqual(self.catalog.count, 15035)
        product = self.catalog.get("050300004_")
        self.assertEqual(product["id"], 20505)
        self.assertEqual(product["quantity"], 110160)
        self.assertEqual(product["price"], 816)
        self.assertEqual(product["properties"]["SECHENIE_MM2"], "2,5")
        self.assertEqual(self.catalog.get("050300004")["id"], 20505)
        self.assertFalse(product["stock_fresh"])

    def test_missing_and_defensive_copy(self):
        self.assertIsNone(self.catalog.get("NONEXISTENT-SKU-99999999999"))
        self.assertEqual(self.catalog.search("NONEXISTENT-SKU-99999999999"), [])
        first = self.catalog.get(20505)
        first["quantity"] = 99999999999
        self.assertEqual(self.catalog.get(20505)["quantity"], 110160)

    def test_search_is_local_and_fast(self):
        start = time.perf_counter()
        self.assertEqual(self.catalog.search("Есть 050300004?")[0]["id"], 20505)
        products = self.catalog.search("кабель ВВГ 3х2,5")
        elapsed = time.perf_counter() - start
        self.assertTrue(products)
        self.assertIn(20505, [product["id"] for product in products])
        self.assertLess(elapsed, 0.3)
        self.assertTrue(all(self.catalog.get(product["id"]) for product in products))

    def test_search_respects_ampere_and_cable_cross_section(self):
        for query in ("автомат 16А", "автомат C16"):
            products = self.catalog.search(query)
            self.assertTrue(products)
            for product in products:
                self.assertIn(product["properties"]["NOMINALNYY_TOK"].replace(" ", "").lower(), ("16а", "16a"))
        cables = self.catalog.search("кабель 3х2.5")
        self.assertTrue(cables)
        for product in cables:
            self.assertEqual(product["properties"]["SECHENIE_MM2"].replace(",", "."), "2.5")
            self.assertEqual(product["properties"]["KOLICHESTVO_ZHIL"], "3")

    def test_two_real_zero_stock_examples_have_parameter_matches(self):
        for zero_id, expected_id in [(21182, 18427), (21185, 18430)]:
            self.assertEqual(self.catalog.get(zero_id)["quantity"], 0)
            alternatives = self.catalog.alternatives(zero_id, limit=30)
            self.assertIn(expected_id, [item["id"] for item in alternatives])
            for item in alternatives:
                self.assertGreater(item["quantity"], 0)
                self.assertEqual(item["category"], self.catalog.get(zero_id)["category"])
                self.assertFalse(item["compatibility_verified"])
                self.assertTrue(item["explanation"])

    def test_recommendations_are_not_automatic_analogs(self):
        terminal = self.catalog.get(22978)
        self.assertTrue(terminal["properties"]["RECOMMEND"])
        self.assertEqual(self.catalog.alternatives(22978), [])

    def test_file_ids_are_not_fake_certificate_links(self):
        product = self.catalog.get(21449)
        self.assertEqual(product["certificate_file_ids"], ["192137", "192138", "192139", "192140"])
        self.assertEqual(len(product["certificates"]), 4)
        self.assertTrue(product["certificates"][0].endswith("Sertifikat-004-MVA20_1_016_C.pdf"))
        self.assertEqual(product["certificate_source"], product["url"])
        self.assertEqual(_urls(["192137", "https://ekt.kz/upload/cert.pdf"]), ["https://ekt.kz/upload/cert.pdf"])

    def test_certificate_parser_keeps_document_scope(self):
        parser = _DocumentLinks()
        parser.feed('<div class="doc_item__block__title">Сертификаты</div>'
                    '<a class="doc_item" href="/upload/cert.pdf">Сертификат</a>'
                    '<a class="doc_item" href="https://external.invalid/fake.pdf">Чужой</a>'
                    '<div class="doc_item__block__title">Паспорта и РЭ</div>'
                    '<a class="doc_item" href="/upload/manual.pdf">Инструкция</a>')
        self.assertEqual(parser.links, ["https://ekt.kz/upload/cert.pdf"])

    def test_html_cleanup_and_no_credentials_fallback(self):
        self.assertEqual(_clean("<b>Кабель</b><script>bad()</script>&nbsp;3х2,5"), "Кабель 3х2,5")
        with patch.dict(os.environ, {}, clear=True):
            product = asyncio.run(self.catalog.refresh(20505))
        self.assertEqual(product["id"], 20505)
        self.assertFalse(product["stock_fresh"])
        self.assertEqual(product["stock_source"], "cached_fallback")

    def test_photo_label_breaking_capacity_is_not_nominal_current(self):
        result = self.catalog.search('автоматический выключатель IEK ВА47-29 KARAT C63 230/400V 4500A 1P по фото')
        self.assertTrue(result)
        self.assertIn(21451, [item['id'] for item in result])
        for item in result:
            self.assertIn('63', str(item['properties']['NOMINALNYY_TOK']))
            self.assertNotIn('4500', str(item['properties']['NOMINALNYY_TOK']))
            self.assertEqual(item['properties']['KOLICHESTVO_POLYUSOV'], '1')
            self.assertEqual(item['properties']['NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST'], '4,5кА')
        self.assertEqual(self.catalog.search('010500012 2P'), [])
        self.assertEqual(self.catalog.search('010500012 400V'), [])
        self.assertEqual(self.catalog.search('010500012 1P')[0]['id'], 21451)
        unitless = self.catalog.search('автоматический выключатель EKF KARAT C63 230/400V 4500 1P по фото')
        self.assertTrue(unitless)
        for item in unitless:
            self.assertEqual(item['properties']['KOLICHESTVO_POLYUSOV'], '1')
            self.assertIn('63', str(item['properties']['NOMINALNYY_TOK']))
            self.assertEqual(item['properties']['NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST'], '4,5кА')

    def test_explicit_poles_capacity_and_voltage_exclude_unproven_candidates(self):
        base = self.catalog.get(21451)
        raw = {key: base[key] for key in ('id', 'article', 'name', 'description', 'price', 'quantity', 'stores', 'image', 'url', 'properties')}
        fixtures = [
            ('1', '230В', '4,5кА'),
            ('1', '230-400В', '4,5кА'),
            ('2', '400В', '4,5кА'),
            ('1', None, '4,5кА'),
            ('1', '230В', '6кА'),
            ('1+N', '230В', '4,5кА'),
            ('1', '400В', '4500A'),
            (None, '230В', '4,5кА'),
            ('1', '230В', None),
            ('3', '400В', '4,5кА'),
            ('4', '400В', '4,5кА'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            records = []
            for index, (poles, voltage, capacity) in enumerate(fixtures, 1):
                item = copy.deepcopy(raw)
                item.update(id=9000000 + index, article=f'FIXTURE-{index}')
                item['properties'].update(KOLICHESTVO_POLYUSOV=poles, NOMINALNOE_NAPRYAZHENIE=voltage,
                                          NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST=capacity)
                records.append(json.dumps(item, ensure_ascii=False))
            Path(directory, 'raw_details.jsonl').write_text('\n'.join(records), encoding='utf-8')
            catalog = Catalog(Path(directory))

            def ids(query):
                return {item['id'] - 9000000 for item in catalog.search(query, limit=30)}

            for spelling in ('1P', '1полюс', 'однополюсный'):
                self.assertEqual(ids(f'автомат C63 230/400V 4500A {spelling}'), {1, 2, 7})
            self.assertEqual(ids('автомат C63 1P 230V 4.5кА'), {1, 2})
            self.assertEqual(ids('автомат C63 1P 400V 4,5кА'), {2, 7})
            self.assertEqual(ids('автомат C63 2P 400V 4.5kA'), {3})
            self.assertEqual(ids('автомат C63 двухполюсный 400V 4500A'), {3})
            self.assertEqual(ids('автомат C63 3P 400V 4500A'), {10})
            self.assertEqual(ids('автомат C63 трёхполюсный 400V 4500A'), {10})
            self.assertEqual(ids('автомат C63 4P 400V 4500A'), {11})
            self.assertEqual(ids('автомат C63 четырёхполюсный 400V 4500A'), {11})
            self.assertEqual(ids('автомат C63 5P 400V 4500A'), set())
            self.assertEqual(ids('автомат C63 1P 690V 4500A'), set())
            self.assertEqual(ids('автомат C63 1P 230V 10кА'), set())
            self.assertEqual(ids('автомат C63 40A 1P'), set())

    def test_refresh_rejects_unknown_ids(self):
        with self.assertRaises(KeyError):
            asyncio.run(self.catalog.refresh(99999999999))

    def test_refresh_success_and_bounded_failure(self):
        import httpx

        original_client = httpx.AsyncClient
        saved = self.catalog.get(20505)
        raw = {key: saved[key] for key in ("id", "article", "name", "description", "price", "quantity", "stores", "image", "url", "properties")}
        raw["quantity"] = 17.5
        seen = []

        def success(request):
            seen.append(request)
            return httpx.Response(200, json=raw)

        try:
            with patch.dict(os.environ, {"EKT_API_USER": "test-user", "EKT_API_PASSWORD": "test-only"}, clear=True):
                with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(success))):
                    result = asyncio.run(self.catalog.refresh(20505))
                self.assertTrue(result["stock_fresh"])
                self.assertEqual(result["quantity"], 17.5)
                self.assertEqual(str(seen[0].url), "https://ekt.kz/api/products/detail?id=20505")
                self.assertEqual(seen[0].method, "GET")

                failures = []

                def failure(request):
                    failures.append(request)
                    return httpx.Response(503)

                with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(failure))):
                    fallback = asyncio.run(self.catalog.refresh(20505))
                self.assertEqual(len(failures), 2)
                self.assertFalse(fallback["stock_fresh"])
                self.assertEqual(fallback["quantity"], 17.5)
                self.assertFalse(self.catalog.get(20505)["stock_fresh"])
        finally:
            self.catalog._items[20505] = saved


if __name__ == "__main__":
    unittest.main()
