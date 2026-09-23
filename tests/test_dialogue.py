"""Regression for the actual repair -> lighting conversation, using real catalogue records."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.agent import Assistant
from app.catalog import Catalog
from app.config import settings
from app.grounding import DEFAULT_QUESTION
from app.store import Store


class ProjectDialogueTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog(settings.data_dir)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'dialogue.sqlite')
        self.assistant = Assistant(self.catalog, self.store, replace(settings, api_key='', data_dir=Path(self.temp.name)))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_repair_then_lighting_gives_actual_candidates_and_advances(self):
        first = await self.assistant.reply('repair', 'хочу сделать ремонт дома электрику че надо купить?')
        second = await self.assistant.reply('repair', 'для освещения')
        self.assertIn('Начнём с освещения', first['text'])
        self.assertIn('В каких комнатах', second['text'])
        for result in (first, second):
            self.assertNotIn(DEFAULT_QUESTION, result['text'])
            self.assertTrue(result['products'])
            self.assertLess(result['seconds'], 2)
            self.assertEqual(result['usage'], [])
            for product in result['products']:
                actual = self.catalog.get(product['id'])
                self.assertEqual(product['article'], actual['article'])
                self.assertEqual(product['price'], actual['price'])
                self.assertGreater(actual['quantity'], 0)
            self.assertIsNone(result['proposal'])
        third = await self.assistant.reply('repair', 'две комнаты 20 м2')
        self.assertIn('существующие светильники или новые', third['text'])
        self.assertEqual(self.store.get_cart('repair')['items'], [])

    async def test_empty_catalog_never_invents_candidates(self):
        with patch.object(self.catalog, 'search', return_value=[]):
            result = await self.assistant.reply('empty', 'хочу сделать ремонт дома электрику че надо купить?')
        self.assertEqual(result['products'], [])
        self.assertIn('позиций в локальном каталоге не нашёл', result['text'])
        self.assertEqual(self.store.get_cart('empty')['items'], [])

    async def test_concrete_unknown_article_is_not_replaced_by_generic_recommendations(self):
        result = await self.assistant.reply('unknown', 'для освещения артикул UNKNOWN-NONEXISTENT-99999999999')
        self.assertNotIn('project_guidance', result['trace'])
        self.assertEqual(result['products'], [])


if __name__ == '__main__':
    unittest.main()
