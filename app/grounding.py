"""Keep product facts in server-rendered catalogue cards, not model prose."""

import re
import unicodedata


DEFAULT_QUESTION = "Для какой задачи подбираешь товар и какие параметры уже известны?"
EXACT_PRODUCT = "Нашёл товар. Ниже данные каталога."
ALTERNATIVES = "Вот кандидаты на замену. Сравни характеристики; различия указаны в карточках."
NO_ALTERNATIVES = "Товар отсутствует. Подходящего аналога по известным параметрам не нашёл. Уточни допустимые отличия."
CATALOG_OPTIONS = "Нашёл варианты в каталоге. Уточни нужное количество или главный параметр."
NO_MATCH = "Точного совпадения в каталоге не нашёл. Уточни артикул или ключевые параметры."

_CATALOG_TOOLS = {"search_catalog", "product_details", "find_alternatives"}
_FORBIDDEN = re.compile(
    r"в\s+наличи[еи]|\bцен\w*|\bсертификат\w*|\bдобав\w*|"
    r"\bнаш[её]л\w*|\bнайден\w*|\bподобрал\w*|\bпроверил\w*",
    re.IGNORECASE,
)
_URL = re.compile(
    r"://|www\.|mailto:|data:|\b[\w-]+\.[a-zа-я]{2,}(?=[/\s?:#]|$)",
    re.IGNORECASE,
)


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")


def _exact_article(evidence: list[dict], user_text: str) -> bool:
    query = _normal(user_text)
    for product in evidence:
        article = _normal(str(product.get("article") or "").strip())
        if article and re.search(r"(?<![\w./-])" + re.escape(article) + r"(?![\w/-]|\.\w)", query):
            return True
    return False


def grounded_narrative(text: str, evidence: list[dict], tool_names: list[str], user_text: str) -> str:
    """Return deterministic product prose or one tightly bounded clarification.

    Terms, saved requests and prepared basket items must be rendered separately
    by the caller from their source records. Nothing from model prose is used
    as a product fact, a link, or confirmation of an action.
    """
    names = set(tool_names)
    if evidence:
        if "find_alternatives" in names:
            if all(product.get("quantity") == 0 for product in evidence):
                return NO_ALTERNATIVES
            return ALTERNATIVES
        if _exact_article(evidence, user_text):
            return EXACT_PRODUCT
        return CATALOG_OPTIONS
    if names & _CATALOG_TOOLS:
        return NO_MATCH
    if names:
        return DEFAULT_QUESTION
    candidate = text.strip() if isinstance(text, str) else ""
    if not candidate or len(candidate) > 280:
        return DEFAULT_QUESTION
    # A factual preamble followed by a question is not a single clarification.
    if candidate.count("?") != 1 or not candidate.endswith("?"):
        return DEFAULT_QUESTION
    if re.search(r"[\d\n\r.!;]", candidate[:-1]) or _URL.search(candidate) or _FORBIDDEN.search(candidate):
        return DEFAULT_QUESTION
    return candidate
