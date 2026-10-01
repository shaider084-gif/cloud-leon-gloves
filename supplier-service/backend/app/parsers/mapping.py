import csv as csv_module
import os
from typing import List, Optional

import openpyxl

from ..schemas import ProductIn, IGNORE_FIELD, ATTRIBUTE_PREFIX
from .base import BaseParser


def _normalize_header(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def _read_csv_rows(file_path: str) -> List[tuple]:
    """Разбирает .csv — кодировка и разделитель заранее не известны (частый
    случай для выгрузок из 1С/Excel с русской локалью: cp1251 + ';'), поэтому
    пытаемся угадать оба."""
    with open(file_path, "rb") as f:
        raw = f.read()

    text = None
    for enc in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:4096]
    try:
        dialect = csv_module.Sniffer().sniff(sample, delimiters=",;\t")
    except csv_module.Error:
        dialect = csv_module.excel  # по умолчанию — запятая

    reader = csv_module.reader(text.splitlines(), dialect)
    return [tuple(row) for row in reader]


def _read_rows(file_path: str) -> List[tuple]:
    """Читает строки файла независимо от формата — .xlsx/.xlsm через openpyxl,
    .xls через xlrd (старый бинарный формат Excel), .csv — вручную (см. выше).
    Список допустимых расширений см. upload_utils.ALLOWED_UPLOAD_EXTENSIONS."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return _read_csv_rows(file_path)
    if ext == ".xls":
        import xlrd  # локальный импорт: нужен только для этого редкого формата

        book = xlrd.open_workbook(file_path)
        sheet = book.sheet_by_index(0)
        return [tuple(sheet.row_values(r)) for r in range(sheet.nrows)]

    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    return list(ws.iter_rows(values_only=True))


def read_header_row(file_path: str) -> List[str]:
    """Читает первую непустую строку файла как список заголовков колонок —
    используется мастером маппинга, чтобы показать пользователю, что выбирать.
    Работает только с простым случаем: один заголовок, один товар на строку."""
    for row in _read_rows(file_path):
        if row is None:
            continue
        if any(v is not None and str(v).strip() != "" for v in row):
            return [str(v).strip() if v is not None else "" for v in row]
    return []


class MappingParser(BaseParser):
    """
    Универсальный парсер для "простого случая": один заголовок в первой
    непустой строке, дальше — ровно один товар на строку.

    Настройка (column_mapping) собирается один раз через визуальный мастер
    маппинга и сохраняется за поставщиком — при повторной загрузке того же
    поставщика парсинг происходит автоматически, без нового кода.
    """

    display_name = "Маппинг колонок (визуальный)"

    def __init__(self, column_mapping: dict, markup_percent: Optional[float] = 30):
        self.column_mapping = column_mapping or {}
        self.markup_percent = float(markup_percent) if markup_percent is not None else 30.0

    def parse(self, file_path: str) -> List[ProductIn]:
        rows = _read_rows(file_path)
        header_idx = None
        for idx, row in enumerate(rows):
            if row is not None and any(v is not None and str(v).strip() != "" for v in row):
                header_idx = idx
                break
        if header_idx is None:
            return []

        header = [_normalize_header(c) for c in rows[header_idx]]

        field_by_col = {}
        attr_by_col = {}
        for col_idx, col_name in enumerate(header):
            target = self.column_mapping.get(col_name)
            if not target or target == IGNORE_FIELD:
                continue
            if target.startswith(ATTRIBUTE_PREFIX):
                attr_by_col[col_idx] = target[len(ATTRIBUTE_PREFIX):]
            else:
                field_by_col[col_idx] = target

        products: List[ProductIn] = []
        for row in rows[header_idx + 1:]:
            if row is None or all(v is None for v in row):
                continue

            values = {}
            for idx, field in field_by_col.items():
                if idx < len(row):
                    values[field] = row[idx]

            attributes = {}
            for idx, attr_name in attr_by_col.items():
                if idx < len(row) and row[idx] is not None and str(row[idx]).strip() != "":
                    attributes[attr_name] = row[idx]

            name = values.get("name")
            if not name or str(name).strip() == "":
                continue  # без названия строка бесполезна — пропускаем

            cost_price = values.get("cost_price")
            try:
                cost_price = float(cost_price) if cost_price is not None else None
            except (TypeError, ValueError):
                cost_price = None

            sale_price = (
                round(cost_price * (1 + self.markup_percent / 100), 2)
                if cost_price is not None
                else None
            )

            products.append(
                ProductIn(
                    article=str(values.get("article")).strip() if values.get("article") not in (None, "") else None,
                    name=str(name).strip(),
                    brand=str(values.get("brand")).strip() if values.get("brand") not in (None, "") else None,
                    category=str(values.get("category")).strip() if values.get("category") not in (None, "") else None,
                    unit=str(values.get("unit")).strip() if values.get("unit") not in (None, "") else None,
                    cost_price=cost_price,
                    sale_price=sale_price,
                    attributes=attributes,
                )
            )

        return _dedupe_products(products)


def _dedupe_products(products: List[ProductIn]) -> List[ProductIn]:
    """Некоторые выгрузки поставщиков дают отдельную строку на каждый склад/остаток —
    один и тот же товар (тот же артикул, а если его нет — то же название) повторяется
    несколько раз подряд с одинаковой ценой. Каталогу нужна одна строка на товар,
    поэтому оставляем первое вхождение, остальные (дубли) отбрасываем."""
    seen = set()
    result: List[ProductIn] = []
    for p in products:
        key = (p.article or "").strip().lower() if p.article else f"__name__:{p.name.strip().lower()}"
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
    return result
