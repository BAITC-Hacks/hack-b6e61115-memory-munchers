"""Read customer attachments as untrusted data; never execute their contents.

The caller must keep extracted text separate from assistant instructions. This
module does not fetch linked files, calculate formulas, execute macros or OCR.
"""

from __future__ import annotations

import base64
from contextlib import closing
from io import BytesIO
from pathlib import PurePath
import posixpath
import re
from typing import TypedDict
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_XLSX_ROWS = 100
MAX_XLSX_SHEETS = 4
MAX_XLSX_XML_BYTES = 8 * 1024 * 1024
MAX_XLSX_METADATA_BYTES = 256 * 1024
MAX_PDF_PAGES = 20
MAX_EXPANDED_BYTES = 100 * 1024 * 1024


class AttachmentResult(TypedDict):
    text: str
    image_data_url: str | None
    warning: str | None


class _Text:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.truncated = False

    def add(self, value: str) -> bool:
        value = value.strip()
        if not value:
            return True
        separator = "\n" if self.parts else ""
        available = MAX_TEXT_CHARS - self.length
        addition = separator + value
        if len(addition) > available:
            self.parts.append(addition[:available])
            self.length = MAX_TEXT_CHARS
            self.truncated = True
            return False
        self.parts.append(addition)
        self.length += len(addition)
        return True

    def result(self, warnings: list[str]) -> AttachmentResult:
        if self.truncated:
            warnings.append("Текст сокращён до 20 000 символов. Отправьте оставшуюся часть отдельным файлом.")
        return {"text": "".join(self.parts), "image_data_url": None,
                "warning": " ".join(warnings) or None}


def _failure(message: str) -> AttachmentResult:
    return {"text": "", "image_data_url": None, "warning": message}


def _check_office_archive(content: bytes) -> None:
    with ZipFile(BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise ValueError("Слишком большой объём распакованного документа.")
        if any(item.flag_bits & 1 for item in archive.infolist()):
            raise ValueError("Документ защищён паролем.")


class _XlsxLimit(Exception):
    """The bounded XML reader stopped; already extracted rows remain useful."""


class _XmlBudget:
    def __init__(self):
        self.remaining = MAX_XLSX_XML_BYTES

    def read(self, stream, size):
        if self.remaining <= 0:
            raise _XlsxLimit
        chunk = stream.read(min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk


def _reject_xml_declarations(chunk: bytes) -> None:
    # OOXML does not need DTDs. Also cover UTF-16/32 encodings before parsing.
    plain = chunk.replace(b"\x00", b"").lower()
    if b"<!doctype" in plain or b"<!entity" in plain:
        raise ValueError("DTD и объявления сущностей в Excel не поддерживаются.")


def _local_tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _small_xml(archive, path, budget):
    size = archive.getinfo(path).file_size
    if size > min(MAX_XLSX_METADATA_BYTES, budget.remaining):
        raise _XlsxLimit
    with archive.open(path) as stream:
        content = budget.read(stream, size)
    _reject_xml_declarations(content)
    return ET.fromstring(content)


def _xlsx_elements(archive, path, budget, wanted):
    """Stream rows/string entries and release completed elements immediately."""
    parser = ET.XMLPullParser(events=("start", "end"))
    root = None
    tail = b""
    with archive.open(path) as stream:
        remaining_part = archive.getinfo(path).file_size
        while remaining_part:
            chunk = budget.read(stream, min(16 * 1024, remaining_part))
            if not chunk:
                raise ValueError("Оборванная XML-часть Excel.")
            remaining_part -= len(chunk)
            _reject_xml_declarations(tail + chunk)
            tail = chunk[-64:]
            parser.feed(chunk)
            for event, element in parser.read_events():
                if root is None:
                    root = element
                if event == "end" and _local_tag(element) == wanted:
                    yield element
                    element.clear()
                    root.clear()
        parser.close()


def _xlsx_part(target):
    if "\\" in target or ":" in target:
        raise ValueError("Неверный путь XML-части Excel.")
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
    if not path.startswith("xl/"):
        raise ValueError("Неверный путь XML-части Excel.")
    return path


def _xlsx_row(element):
    cells = {}
    previous = 0
    for cell in element:
        if _local_tag(cell) != "c":
            continue
        reference = re.match(r"[A-Z]+", cell.get("r", ""))
        column = 0
        if reference:
            if len(reference[0]) > 3:
                raise ValueError("Недопустимый номер столбца Excel.")
            for char in reference[0]:
                column = column * 26 + ord(char) - 64
        else:
            column = previous + 1
        if not 1 <= column <= 16384:
            raise ValueError("Недопустимый номер столбца Excel.")
        previous = column
        kind = cell.get("t", "n")
        # Formula source <f> is never read or evaluated; only cached <v> is used.
        if kind == "inlineStr":
            value = "".join(child.text or "" for child in cell.iter() if _local_tag(child) == "t")
        else:
            value = next((child.text or "" for child in cell if _local_tag(child) == "v"), "")
        cells[column] = (kind, value)
    return [cells.get(index, ("n", "")) for index in range(1, max(cells, default=0) + 1)]


def _xlsx(content: bytes) -> AttachmentResult:
    # Unlike load_workbook, this never loads the entire sharedStrings table.
    text, warnings, budget = _Text(), [], _XmlBudget()
    rows, strings, needed = [], {}, set()
    rows_read = 0
    shared_path = None
    with ZipFile(BytesIO(content)) as archive:
        if any(item.flag_bits & 1 for item in archive.infolist()):
            raise ValueError("Документ защищён паролем.")
        try:
            workbook = _small_xml(archive, "xl/workbook.xml", budget)
            relationships = _small_xml(archive, "xl/_rels/workbook.xml.rels", budget)
            parts = {}
            for relation in relationships:
                if relation.get("TargetMode") == "External":
                    continue
                kind = relation.get("Type", "").rsplit("/", 1)[-1]
                if kind in {"worksheet", "sharedStrings"}:
                    path = _xlsx_part(relation.get("Target", ""))
                    parts[relation.get("Id")] = path
                    if kind == "sharedStrings":
                        shared_path = path
            sheets = [sheet for sheet in workbook.iter() if _local_tag(sheet) == "sheet"]
            if len(sheets) > MAX_XLSX_SHEETS:
                warnings.append("Прочитаны только первые 4 листа Excel.")
            for sheet in sheets[:MAX_XLSX_SHEETS]:
                relation_id = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), None)
                sheet_rows = []
                rows.append((sheet.get("name", "Лист"), sheet_rows))
                with closing(_xlsx_elements(archive, parts[relation_id], budget, "row")) as elements:
                    for element in elements:
                        row = _xlsx_row(element)
                        sheet_rows.append(row)
                        rows_read += 1
                        needed.update(int(value) for kind, value in row if kind == "s" and value)
                        if rows_read >= MAX_XLSX_ROWS:
                            break
                if rows_read >= MAX_XLSX_ROWS:
                    warnings.append("Прочитаны только первые 100 строк Excel суммарно по листам; остальная часть файла не прочитана.")
                    break
            if needed and shared_path:
                with closing(_xlsx_elements(archive, shared_path, budget, "si")) as elements:
                    for index, element in enumerate(elements):
                        if index in needed:
                            strings[index] = "".join(child.text or "" for child in element.iter() if _local_tag(child) == "t")
                            if len(strings) == len(needed):
                                break
        except _XlsxLimit:
            warnings.append("Прочитана только часть Excel: достигнут лимит чтения XML (8 МБ, включая общие строки; структура до 256 КБ). Отправьте нужный фрагмент отдельно.")
    missing = needed - strings.keys()
    if missing:
        warnings.append("Часть текстовых ячеек не прочитана: нужные общие строки находятся за лимитом чтения или отсутствуют.")
    has_values = False
    for name, sheet_rows in rows:
        if not text.add(f"Лист: {name}"):
            break
        for row in sheet_rows:
            values = [strings.get(int(value), "[текст ячейки не прочитан]") if kind == "s" and value else value for kind, value in row]
            has_values = has_values or any(values)
            if not text.add(" | ".join(values).strip(" |")):
                break
        if text.truncated:
            break
    if not has_values:
        warnings.append("В Excel не найдено текста. Формулы не вычисляются: доступны только сохранённые значения.")
    return text.result(warnings)


def _docx(content: bytes) -> AttachmentResult:
    from docx import Document
    from docx.table import Table

    _check_office_archive(content)
    document = Document(BytesIO(content))
    text = _Text()
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                if not text.add(" | ".join(cell.text for cell in row.cells)):
                    break
        else:
            text.add(block.text)
        if text.truncated:
            break
    warnings = [] if text.parts else ["В Word не найдено текста. Если документ состоит из сканов, отправьте страницы как фото."]
    return text.result(warnings)


def _pdf(content: bytes) -> AttachmentResult:
    from pypdf import PdfReader

    if not content.startswith(b"%PDF-"):
        return _failure("Файл не похож на PDF. Проверьте формат и отправьте его снова.")
    reader = PdfReader(BytesIO(content), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        return _failure("PDF защищён паролем. Отправьте копию без пароля или фото нужных страниц.")
    text = _Text()
    warnings: list[str] = []
    if len(reader.pages) > MAX_PDF_PAGES:
        warnings.append("Прочитаны только первые 20 страниц PDF.")
    pages_without_text: list[int] = []
    for index, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            pages_without_text.append(index)
        if page_text and not text.add(f"Страница {index}:\n{page_text}"):
            break
    if not text.parts:
        warnings.append("В PDF нет извлекаемого текста: возможно, это скан. Требуется фото нужных страниц в JPEG или PNG.")
    elif pages_without_text:
        pages = ", ".join(map(str, pages_without_text))
        warnings.append(f"На страницах {pages} нет извлекаемого текста. Если это сканы, отправьте их как фото JPEG/PNG.")
    return text.result(warnings)


def extract_attachment(filename: str, content: bytes) -> AttachmentResult:
    """Extract bounded text or a validated JPEG/PNG data URL without disk writes.

    Errors are returned as Russian warnings suitable for a customer response.
    XLSX streams at most 100 rows total from the first four sheets, reading no
    more than 8 MiB of XML including shared strings. Its formulas
    are never calculated; only values previously cached by Excel are read.
    """
    if len(content) > MAX_FILE_BYTES:
        return _failure("Файл больше 15 МБ. Разделите его на части и отправьте снова.")
    if not content:
        return _failure("Файл пустой. Отправьте файл с товарами или фото товара.")
    extension = PurePath(filename).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png"}:
        mime = "image/png" if extension == ".png" else "image/jpeg"
        magic = b"\x89PNG\r\n\x1a\n" if extension == ".png" else b"\xff\xd8\xff"
        if not content.startswith(magic):
            return _failure("Содержимое не соответствует формату JPEG/PNG. Отправьте исходное фото.")
        return {"text": "", "image_data_url": f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}",
                "warning": None}
    if extension in {".doc", ".xls"}:
        target = "DOCX" if extension == ".doc" else "XLSX"
        return _failure(f"Старый формат {extension} не поддерживается. Сохраните файл как {target} или PDF и отправьте снова.")
    readers = {".xlsx": _xlsx, ".docx": _docx, ".pdf": _pdf}
    if extension not in readers:
        return _failure("Поддерживаются Excel XLSX, Word DOCX, PDF и фотографии JPEG/PNG.")
    try:
        return readers[extension](content)
    except (BadZipFile, ValueError, KeyError):
        return _failure("Не удалось прочитать документ: он повреждён, защищён или слишком велик после распаковки. Отправьте другую копию или фото.")
    except Exception:
        # Library parser errors contain internals and must not reach Telegram.
        return _failure("Не удалось прочитать этот файл. Пересохраните его в исходной программе или отправьте фото нужных страниц.")
