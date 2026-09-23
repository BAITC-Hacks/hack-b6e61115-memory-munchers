"""Read-only EKT catalogue, local retrieval and bounded live stock refresh."""

from __future__ import annotations

import asyncio
import copy
import html
import json
import math
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


_STOP = set("есть наличие нужен нужна нужно нужны товар товары мне для на по и или в из с со пожалуйста покажи подбери хочу купить артикул цена сколько стоит можете ли у вас".split())
_LABELS = {
    "KOLICHESTVO_POLYUSOV": "полюса", "NOMINALNYY_TOK": "номинальный ток",
    "KHARAKTERISTIKA_SRABATYVANIYA": "характеристика срабатывания",
    "NOMINALNOE_NAPRYAZHENIE": "напряжение",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "отключающая способность",
    "MATERIAL_ZHILY": "материал жилы", "KOLICHESTVO_ZHIL": "число жил",
    "SECHENIE_MM2": "сечение", "NAPRYAZHENIE": "напряжение",
    "MATERIAL_IZOLYATSII_I_OBOLOCHKI": "изоляция и пожарное исполнение",
    "NALICHIE_METALLICHESKOY_BRONI": "броня", "GIBKOST": "гибкость",
    "TORGOVAYA_MARKA": "бренд", "TIP_TSOKOLYA": "цоколь",
    "MOSHCHNOST_W": "мощность", "TSVETOVAYA_TEMPERATURA": "цветовая температура",
}
_BREAKER = ("KOLICHESTVO_POLYUSOV", "NOMINALNYY_TOK", "KHARAKTERISTIKA_SRABATYVANIYA",
            "NOMINALNOE_NAPRYAZHENIE", "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST")
_CABLE = ("MATERIAL_ZHILY", "KOLICHESTVO_ZHIL", "SECHENIE_MM2", "NAPRYAZHENIE",
          "MATERIAL_IZOLYATSII_I_OBOLOCHKI", "NALICHIE_METALLICHESKOY_BRONI", "GIBKOST")
_LAMP = ("TIP_TSOKOLYA", "MOSHCHNOST_W", "TSVETOVAYA_TEMPERATURA")
# Observed on the EKT product page for 010500006_ on 2026-09-23.
# Every URL returned HEAD 200 application/pdf. These are documents published
# for this card, not URLs guessed from the numeric Bitrix file IDs.
_VERIFIED_DOCUMENTS = {
    21449: {
        "article": "010500006_",
        "file_ids": {"192137", "192138", "192139", "192140"},
        "urls": [
            "https://ekt.kz/upload/iblock/d73/cg4iz2mfi5d8v86yygje3moxllyoq0w2/Sertifikat-004-MVA20_1_016_C.pdf",
            "https://ekt.kz/upload/iblock/6c6/olvnj39a03ztrg0jmvmto1gpd6po3r9h/Deklaratsiya-037-MVA20_1_016_C.pdf",
            "https://ekt.kz/upload/iblock/d16/mfgpxsn36rskuho1o23tolrgvu0852ew/Otkaznoe-pismo-043-MVA20_1_016_C.pdf",
            "https://ekt.kz/upload/iblock/d6c/nyypp4htt4yz5dk7y6vfsjo30whxvmjj/Otkaznoe-pismo-123_FZ-MVA20_1_016_C.pdf",
        ],
    },
}


class _DocumentLinks(HTMLParser):
    """Read links only inside a labelled certificate section of the product page."""

    def __init__(self):
        super().__init__()
        self.section = ""
        self.reading_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if "doc_item__block__title" in classes:
            self.section = ""
            self.reading_title = True
        if tag == "a" and "doc_item" in classes and "сертифик" in self.section.casefold():
            href = attributes.get("href", "")
            absolute = urljoin("https://ekt.kz/", href)
            parsed = urlparse(absolute)
            if parsed.scheme == "https" and parsed.hostname == "ekt.kz" and parsed.path.startswith("/upload/"):
                self.links.append(absolute)

    def handle_data(self, data):
        if self.reading_title:
            self.section += data

    def handle_endtag(self, tag):
        if tag == "div":
            self.reading_title = False


def _clean(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normal(value: object) -> str:
    return unicodedata.normalize("NFKC", _clean(value)).casefold().replace("ё", "е")


def _property_value(value: object) -> str:
    return re.sub(r"\s+", "", _normal(value)).replace(",", ".")


def _tokens(value: object) -> set[str]:
    text = _normal(value).replace(",", ".")
    text = re.sub(r"\b[сc](?=\d)", "c", text)
    text = re.sub(r"\b[вb](?=\d)", "b", text)
    text = re.sub(r"\b[дd](?=\d)", "d", text)
    text = re.sub(r"(?<=\d)[xх×](?=\s*\d)", " ", text)
    text = re.sub(r"(?<=\d)(?=[a-zа-я])|(?<=[a-zа-я])(?=\d)", " ", text)
    tokens = re.findall(r"[a-zа-я0-9]+(?:\.\d+)?", text)
    return {word[:5] if len(word) > 5 and re.fullmatch(r"[а-я]+", word) else word
            for word in tokens if (word not in _STOP and len(word) > 1) or word.isdigit() or word in {"b", "c", "d"}}


def _number(value: object, field: str) -> int | float:
    try:
        result = float(str(value).replace(",", "."))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Каталог: поле {field} не содержит число") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Каталог: некорректное значение {field}")
    return int(result) if result.is_integer() else result


def _urls(value: object) -> list[str]:
    """Numeric Bitrix file IDs are deliberately not converted to guessed URLs."""
    values = value if isinstance(value, list) else [value]
    found = []
    for item in values:
        if isinstance(item, dict):
            found.extend(_urls(list(item.values())))
        elif isinstance(item, list):
            found.extend(_urls(item))
        elif isinstance(item, str):
            for url in re.findall(r"https?://[^\s<>\"']+", html.unescape(item)):
                if urlparse(url).hostname:
                    found.append(url)
    return list(dict.fromkeys(found))


class Catalog:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._items: dict[int, dict] = {}
        self._articles: dict[str, int] = {}
        self._aliases: dict[str, set[int]] = defaultdict(set)
        self._index: dict[str, dict[int, int]] = defaultdict(dict)
        self._categories: dict[str, set[int]] = defaultdict(set)
        self._refresh_locks: dict[int, asyncio.Lock] = {}
        source = self.data_dir / "raw_details.jsonl"
        with source.open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    product = self._normalize(raw, fresh=False)
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"Не удалось прочитать каталог, строка {line_no}: {exc}") from exc
                if product["id"] in self._items:
                    raise ValueError(f"Повтор ID в каталоге: {product['id']}")
                self._items[product["id"]] = product
        for product in self._items.values():
            pid = product["id"]
            article = _normal(product["article"])
            self._articles[article] = pid
            self._aliases[article.rstrip("_")].add(pid)
            vendor = product["properties"].get("ARTIKULPOSTAVSHCHIKA")
            if vendor:
                self._aliases[_normal(vendor).rstrip("_")].add(pid)
            self._categories[product["category"]].add(pid)
            self._index_product(product)

    @property
    def count(self) -> int:
        return len(self._items)

    def _normalize(self, raw: dict, *, fresh: bool) -> dict:
        pid = int(raw["id"])
        properties = copy.deepcopy(raw.get("properties") or {})
        # Catalogue strings are data. No property is used as an executable instruction.
        for key, value in properties.items():
            if isinstance(value, str):
                properties[key] = _clean(value)
            elif isinstance(value, list):
                properties[key] = [_clean(v) if isinstance(v, str) else v for v in value]
        url = str(raw.get("url") or "")
        path = [p for p in urlparse(url).path.split("/") if p]
        category_path = path[1:-1] if path and path[0] == "catalog" else []
        category = category_path[1] if len(category_path) > 1 else (category_path[0] if category_path else "")
        certificates = _urls(properties.get("FILES_CERTIFICATES"))
        known = _VERIFIED_DOCUMENTS.get(pid)
        file_ids = properties.get("FILES_CERTIFICATES") or []
        file_ids = file_ids if isinstance(file_ids, list) else [file_ids]
        if known and raw["article"] == known["article"] and set(map(str, file_ids)) == known["file_ids"]:
            certificates = list(known["urls"])
        return {
            "id": pid, "article": _clean(raw["article"]), "name": _clean(raw["name"]),
            "price": _number(raw["price"], "price"), "quantity": _number(raw["quantity"], "quantity"),
            "stores": [{"id": int(s["id"]), "name": _clean(s.get("name")),
                        "quantity": _number(s["quantity"], "stores.quantity")} for s in raw.get("stores", [])],
            "properties": properties, "certificates": certificates,
            "certificate_file_ids": properties.get("FILES_CERTIFICATES", []),
            "certificate_source": url if certificates else None,
            "url": url, "image": str(raw.get("image") or ""), "description": _clean(raw.get("description")),
            "category": category, "category_path": category_path,
            "stock_fresh": fresh, "stock_source": "ekt_api" if fresh else "local_snapshot",
            "stock_checked_at": datetime.now(timezone.utc).isoformat() if fresh else None,
        }

    def _index_product(self, product: dict) -> None:
        props = product["properties"]
        extra = []
        if props.get("KOLICHESTVO_POLYUSOV") and props.get("NOMINALNYY_TOK"):
            extra.append("автомат выключатель")
        if props.get("MATERIAL_ZHILY") and props.get("KOLICHESTVO_ZHIL"):
            extra.append("кабель провод")
            if "медь" in _normal(props.get("MATERIAL_ZHILY")):
                extra.append("медный медь")
        for value, weight in [(product["article"], 20), (product["name"], 8),
                              (" ".join(extra), 8), (str(props), 3), (product["description"], 1)]:
            for token in _tokens(value):
                index = self._index[token]
                index[product["id"]] = index.get(product["id"], 0) + weight

    def _resolve(self, value: object) -> int | None:
        if isinstance(value, int):
            return value if value in self._items else None
        text = _normal(value).strip()
        if text in self._articles:
            return self._articles[text]
        aliases = self._aliases.get(text.rstrip("_"), set())
        if len(aliases) == 1:
            return next(iter(aliases))
        if text.isdigit() and int(text) in self._items:
            return int(text)
        return None

    def get(self, product_id_or_article: object) -> dict | None:
        pid = self._resolve(product_id_or_article)
        return copy.deepcopy(self._items[pid]) if pid is not None else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        limit = max(0, min(int(limit), 30))
        if not limit or not query.strip():
            return []
        exact = self._resolve(query)
        if exact is not None:
            return [copy.deepcopy(self._items[exact])]
        # Preserve whole identifiers (including vendor punctuation and trailing underscore).
        identifiers = re.findall(r"[\w][\w./-]*", query, flags=re.UNICODE)
        exact_ids = []
        for identifier in identifiers:
            if len(identifier) >= 5 and any(c.isdigit() for c in identifier):
                pid = self._resolve(identifier)
                if pid is not None and pid not in exact_ids:
                    exact_ids.append(pid)
        scores: dict[int, float] = defaultdict(float)
        matches: dict[int, int] = defaultdict(int)
        tokens = _tokens(query)
        for token in tokens:
            posting = self._index.get(token, {})
            rarity = math.log(2 + self.count / max(1, len(posting)))
            for pid, weight in posting.items():
                scores[pid] += weight * rarity
                matches[pid] += 1
        if exact_ids:
            # A known SKU inside a sentence must still satisfy that sentence's
            # explicit parameters; it cannot bypass the engineering filters.
            scores = {pid: scores.get(pid, 0) for pid in exact_ids}
        # Explicit engineering values are filters, not weak textual hints. A
        # controller with "16" in its model number is not a 16 A circuit breaker.
        normalized = _normal(query)
        breaker = bool(re.search(r"\bавтомат", normalized))
        cable = bool(re.search(r"\b(кабел|провод)", normalized))
        currents = [float(value.replace(",", ".")) for value in
                    re.findall(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:а|a|ампер\w*)(?!\w)", normalized)]
        curve_text = re.sub(r"\b[всд](?=\d)", lambda match: match.group().translate(str.maketrans({"в": "b", "с": "c", "д": "d"})), normalized)
        curve = re.search(r"\b([bcd])\s*(\d+(?:[.,]\d+)?)\b", curve_text)
        dimensions = re.search(r"(\d+)\s*[xх×*]\s*(\d+(?:[.,]\d+)?)", normalized)
        # 4500A/6000A on an MCB label is breaking capacity, not its rated current.
        # In a label such as C63 4500A, C63 determines the 63 A nominal rating.
        capacities = {float(value.replace(",", ".")) * 1000 for value in
                      re.findall(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*[kк]\s*[aа](?!\w)", normalized)}
        nominal_currents = {value for value in currents if not (curve and value >= 1500)}
        if curve:
            nominal_currents.add(float(curve.group(2).replace(",", ".")))
            capacities.update(value for value in currents if value >= 1500)
        if curve and breaker and "по фото" in normalized:
            # OCR may omit A from the standard breaking-capacity label. Only
            # infer known ratings in this explicit photo + circuit-breaker context.
            capacities.update(float(value) for value in
                              re.findall(r"(?<![\w.,])(?:1500|4500|6000|10000)(?![\w.,])", normalized))
        # Contradictory explicit labels cannot identify a safe candidate.
        if len(nominal_currents) > 1 or len(capacities) > 1:
            return []
        expected_current = next(iter(nominal_currents), None)
        expected_capacity = next(iter(capacities), None)
        expected_curve = curve.group(1).translate(str.maketrans({"в": "b", "с": "c", "д": "d"})) if curve else None
        poles = {number + ("+n" if neutral else "") for number, neutral in
                 re.findall(r"(?<!\w)(\d+)\s*(?:p|р|полюс\w*)\b(?:\s*\+\s*([nн]))?", normalized)}
        pole_words = {"одно": "1", "дву": "2", "двух": "2", "трех": "3", "четырех": "4"}
        poles.update(pole_words[word] for word in
                     re.findall(r"\b(одно|двух|дву|трех|четырех)полюс\w*", normalized))
        if len(poles) > 1:
            return []
        expected_poles = next(iter(poles), None)

        def voltage_values(value: object, *, property_field: bool = False) -> set[float]:
            text = _normal(value)
            readings = re.findall(r"(?<![\w.,/\-])(\d+(?:[.,]\d+)?(?:\s*[/\-]\s*\d+(?:[.,]\d+)?)*)\s*([kк]?)(?:v|в|вольт\w*)(?!\w)", text)
            # The typed catalogue field also contains values like 230-380 AC/DC.
            if not readings and property_field:
                plain = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?(?:\s*[/\-]\s*\d+(?:[.,]\d+)?)*)\s*(?:ac(?:/dc)?|dc)\s*", text)
                readings = [(plain.group(1), "")] if plain else []
            return {float(number.replace(",", ".")) * (1000 if kilo else 1)
                    for numbers, kilo in readings for number in re.findall(r"\d+(?:[.,]\d+)?", numbers)}

        expected_voltages = voltage_values(normalized)

        def capacity_amps(value: object) -> float | None:
            rating = re.fullmatch(r"(\d+(?:[.,]\d+)?)([kк]?)[aа]", _property_value(value))
            return float(rating.group(1).replace(",", ".")) * (1000 if rating.group(2) else 1) if rating else None

        def numeric_property(properties: dict, key: str) -> float | None:
            value = re.search(r"\d+(?:[.,]\d+)?", str(properties.get(key, "")))
            return float(value.group().replace(",", ".")) if value else None

        def eligible(pid: int) -> bool:
            product = self._items[pid]
            props = product["properties"]
            if breaker and not ("avtomaticheskie_vyklyuchateli" in product["category"] or
                                "автоматический выключатель" in _normal(props.get("TIP_USTROYSTVA")) or
                                "автоматический выключатель" in _normal(props.get("OBYEM"))):
                return False
            if cable and not (product["category_path"] and product["category_path"][0] == "kabel_provod"):
                return False
            if expected_current is not None and numeric_property(props, "NOMINALNYY_TOK") != expected_current:
                return False
            if expected_curve and _property_value(props.get("KHARAKTERISTIKA_SRABATYVANIYA")) != expected_curve:
                return False
            if expected_poles and _property_value(props.get("KOLICHESTVO_POLYUSOV")) != expected_poles:
                return False
            if expected_capacity is not None and capacity_amps(props.get("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST")) != expected_capacity:
                return False
            if expected_voltages:
                rated_voltages = voltage_values(props.get("NOMINALNOE_NAPRYAZHENIE") or props.get("NAPRYAZHENIE"), property_field=True)
                # A 230/400 V product label contains multiple nominal ratings;
                # the EKT card may expose only 230 V for its single-pole SKU.
                # Require an explicitly shared rating, never infer missing ones.
                if not expected_voltages.intersection(rated_voltages):
                    return False
            if dimensions and (numeric_property(props, "KOLICHESTVO_ZHIL") != float(dimensions.group(1)) or
                               numeric_property(props, "SECHENIE_MM2") != float(dimensions.group(2).replace(",", "."))):
                return False
            return True

        scores = {pid: score for pid, score in scores.items() if eligible(pid)}
        ranked = sorted(scores, key=lambda pid: (matches[pid], scores[pid], self._items[pid]["quantity"] > 0), reverse=True)
        return [copy.deepcopy(self._items[pid]) for pid in ranked[:limit]]

    def alternatives(self, product_id: object, limit: int = 3) -> list[dict]:
        source = self.get(product_id)
        if source is None or not source["category"] or limit <= 0:
            return []
        props = source["properties"]
        if props.get("KOLICHESTVO_POLYUSOV") and props.get("NOMINALNYY_TOK"):
            required = _BREAKER
        elif props.get("MATERIAL_ZHILY") or props.get("KOLICHESTVO_ZHIL"):
            required = _CABLE
        elif props.get("TIP_TSOKOLYA"):
            required = _LAMP
        else:
            # Unknown product families need additional specifications before safe matching.
            return []
        if not all(props.get(k) for k in required):
            return []
        candidates = []
        for pid in self._categories[source["category"]]:
            candidate = self._items[pid]
            if pid == source["id"] or candidate["quantity"] <= 0:
                continue
            other = candidate["properties"]
            if not all(other.get(k) and _property_value(props[k]) == _property_value(other[k]) for k in required):
                continue
            item = copy.deepcopy(candidate)
            item["matched_parameters"] = {_LABELS.get(k, k): props[k] for k in required}
            item["differences"] = [{"parameter": _LABELS.get(k, k), "original": props.get(k), "alternative": other.get(k)}
                                   for k in ("TORGOVAYA_MARKA", "SERIYA", "SPOSOB_MONTAZHA", "TIP_USTANOVKI", "GOST")
                                   if _property_value(props.get(k)) != _property_value(other.get(k))]
            item["explanation"] = ("Кандидат той же категории. Совпадают данные каталога: " +
                                   "; ".join(f"{_LABELS.get(k, k)}: {props[k]}" for k in required) +
                                   ". Монтажные размеры и пригодность для конкретной задачи нужно проверить.")
            item["compatibility_verified"] = False
            candidates.append(item)
        candidates.sort(key=lambda item: (len(item["differences"]), abs(item["price"] - source["price"]), item["id"]))
        return candidates[:max(0, min(int(limit), 30))]

    async def resolve_certificates(self, product_id: object) -> dict:
        """Lazy public-page lookup, only for catalogue cards declaring documents."""
        product = self.get(product_id)
        if product is None:
            raise KeyError("Товар отсутствует в ограниченном каталоге")
        if product["certificates"] or not product["certificate_file_ids"]:
            return product
        parsed = urlparse(product["url"])
        if parsed.scheme != "https" or parsed.hostname != "ekt.kz" or not parsed.path.startswith("/catalog/"):
            return product
        import httpx

        try:
            async with asyncio.timeout(1.6):
                async with httpx.AsyncClient(timeout=0.7, follow_redirects=False) as client:
                    response = await client.get(product["url"])
                    response.raise_for_status()
                    parser = _DocumentLinks()
                    parser.feed(response.text)
                    product["certificates"] = list(dict.fromkeys(parser.links))
                    product["certificate_source"] = product["url"] if parser.links else None
                    self._items[product["id"]].update(certificates=product["certificates"], certificate_source=product["certificate_source"])
        except (TimeoutError, httpx.HTTPError, ValueError):
            pass
        return product

    async def refresh(self, product_id: object) -> dict:
        pid = self._resolve(product_id)
        if pid is None:
            raise KeyError("Товар отсутствует в ограниченном каталоге")
        lock = self._refresh_locks.setdefault(pid, asyncio.Lock())
        try:
            # One wall-clock budget covers queueing, both attempts and the retry
            # pause. Per-phase HTTP timeouts alone do not bound total latency.
            async with asyncio.timeout(1.6):
                async with lock:
                    username = os.getenv("EKT_API_USER") or os.getenv("EKT_API_USERNAME")
                    password = os.getenv("EKT_API_PASSWORD")
                    if not username or not password:
                        return self._fallback(pid, "Не настроен доступ к обновлению остатков")
                    import httpx

                    base = os.getenv("EKT_API_BASE", "https://ekt.kz/api").rstrip("/")
                    try:
                        async with httpx.AsyncClient(timeout=0.7, auth=httpx.BasicAuth(username, password), follow_redirects=False) as client:
                            for attempt in range(2):
                                try:
                                    response = await client.get(f"{base}/products/detail", params={"id": pid})
                                    response.raise_for_status()
                                    raw = response.json()
                                    if not isinstance(raw, dict) or int(raw.get("id", -1)) != pid:
                                        raise ValueError("Ответ API не совпадает с выбранным товаром")
                                    fresh = self._normalize(raw, fresh=True)
                                    if _normal(fresh["article"]) != _normal(self._items[pid]["article"]):
                                        raise ValueError("В ответе API изменился артикул товара")
                                    self._items[pid] = fresh
                                    return copy.deepcopy(fresh)
                                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                                    if attempt == 0:
                                        await asyncio.sleep(0.1)
                    except (httpx.HTTPError, ValueError, TypeError):
                        pass
        except TimeoutError:
            return self._fallback(pid, "Обновление остатков превысило 1,6 секунды; показана сохранённая выгрузка")
        return self._fallback(pid, "API остатков временно недоступен; показана сохранённая выгрузка")

    def _fallback(self, pid: int, reason: str) -> dict:
        result = copy.deepcopy(self._items[pid])
        result.update(stock_fresh=False, stock_source="cached_fallback", stock_warning=reason)
        self._items[pid] = copy.deepcopy(result)
        return result
