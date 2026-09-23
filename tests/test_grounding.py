import unittest

from app.grounding import (
    ALTERNATIVES, CATALOG_OPTIONS, DEFAULT_QUESTION, EXACT_PRODUCT,
    NO_MATCH, NO_ALTERNATIVES, grounded_narrative,
)


class GroundedNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [{"id": 1, "article": "EKT-C16", "name": "Автомат", "price": 1500}]

    def test_product_evidence_replaces_all_free_model_facts(self):
        hallucination = "Есть FAKE-999, цена 1 тенге, 500 штук. Сертификат https://fake.example/doc.pdf"
        result = grounded_narrative(hallucination, self.evidence, ["search_catalog"], "Подбери автомат")
        self.assertEqual(result, CATALOG_OPTIONS)
        for invented in ("FAKE-999", "500", "тенге", "fake.example"):
            self.assertNotIn(invented, result)

    def test_exact_article_produces_static_found_message(self):
        result = grounded_narrative("Добавил всё бесплатно", self.evidence, ["product_details"], "Есть ekt-c16?")
        self.assertEqual(result, EXACT_PRODUCT)

    def test_partial_article_does_not_count_as_exact(self):
        for query in ("EKT-C160", "EKT-C16-PRO", "X/EKT-C16", "EKT-C16.2"):
            with self.subTest(query=query):
                self.assertEqual(grounded_narrative("", self.evidence, [], query), CATALOG_OPTIONS)

    def test_sentence_punctuation_after_exact_article_is_allowed(self):
        self.assertEqual(grounded_narrative("", self.evidence, [], "Нужен EKT-C16."), EXACT_PRODUCT)

    def test_alternatives_take_priority_over_original_article_match(self):
        result = grounded_narrative("Полностью совместим без проверки", self.evidence,
                                    ["search_catalog", "product_details", "find_alternatives"], "Есть EKT-C16?")
        self.assertEqual(result, ALTERNATIVES)
        self.assertNotIn("Полностью совместим", result)

    def test_catalog_tool_without_evidence_reports_no_match(self):
        for name in ("search_catalog", "product_details", "find_alternatives"):
            with self.subTest(name=name):
                self.assertEqual(grounded_narrative("Есть товар FAKE-999", [], [name], "FAKE-999"), NO_MATCH)

    def test_zero_stock_without_suitable_analog_does_not_claim_candidates_found(self):
        product = self.evidence[0] | {'quantity': 0}
        self.assertEqual(grounded_narrative('', [product], ['search_catalog', 'find_alternatives'], 'EKT-C16'),
                         NO_ALTERNATIVES)

    def test_only_one_short_natural_question_is_preserved(self):
        for question in (
            "Для помещения или улицы нужен кабель?",
            "Какое оборудование планируешь подключать?",
            "Какие параметры указаны на старом автомате?",
        ):
            with self.subTest(question=question):
                self.assertEqual(grounded_narrative(question, [], [], "Нужен кабель"), question)

    def test_digits_prices_links_and_product_claims_are_rejected(self):
        unsafe = (
            "Подойдёт FAKE-999?",
            "Цена подходит?",
            "По какой цене ищешь?",
            "Нужен сертификат?",
            "Добавил товар, всё верно?",
            "Нашёл кабель, подойдёт?",
            "Товар в наличии, берём?",
            "Посмотри https://fake.example, подходит?",
            "Посмотри ekt.kz/cart, подходит?",
            "Нужны １２ штук?",
        )
        for text in unsafe:
            with self.subTest(text=text):
                self.assertEqual(grounded_narrative(text, [], [], ""), DEFAULT_QUESTION)

    def test_statement_multiple_questions_and_preamble_are_rejected(self):
        for text in ("Товар подобран", "Что нужно? И для чего?", "Это хороший выбор. Где установишь?",
                     "Товар подобран! Готов?", "Первая строка\nКакой цвет?", "Что нужно? Ещё текст"):
            with self.subTest(text=text):
                self.assertEqual(grounded_narrative(text, [], [], ""), DEFAULT_QUESTION)

    def test_empty_or_long_text_is_rejected(self):
        for text in ("", "   ", "Какой " + "очень " * 50 + "нужен?"):
            with self.subTest(length=len(text)):
                self.assertEqual(grounded_narrative(text, [], [], ""), DEFAULT_QUESTION)

    def test_other_tools_never_license_free_facts(self):
        for name in ("research_task", "purchase_terms", "previous_requests", "propose_cart"):
            with self.subTest(name=name):
                self.assertEqual(grounded_narrative("Какое оборудование подключаешь?", [], [name], ""), DEFAULT_QUESTION)


if __name__ == "__main__":
    unittest.main()
