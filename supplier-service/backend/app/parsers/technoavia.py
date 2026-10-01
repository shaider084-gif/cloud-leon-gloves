"""Поставщик "Обувь ТЕХНОАВИА" (slug technoavia_spetsobuv) — прайс приходит
уже в каноническом формате самого сервиса: заголовки колонок буквально
"Название товара", "Артикул", "Изображения", "Цена продажи, ₽", "Описание" и
набор "Параметр: X" (значения-списки — уже через "##", как и везде в проекте).
Поэтому парсер — простое прямое сопоставление, без визуального маппинга:
каждый "Параметр: X" идёт в attributes[X], кроме "Параметр: Бренд" — он же
Product.brand (нужен для фильтра по бренду на вкладках каталога).

Цена в файле — уже цена ПРОДАЖИ (не закупочная), наценка не применяется:
cost_price остаётся пустым, sale_price берётся из файла как есть.

В дальнейшем данные этого поставщика будут обновляться не этим файлом, а
скрейпингом https://www.technoavia.ru (отдельный скрипт, см. scripts/) —
этот парсер остаётся на случай, если придёт ещё один файл в этом же формате.
"""
from typing import List, Optional

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

PARAM_PREFIX = "Параметр: "
BRAND_HEADER = "Параметр: Бренд"


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class TechnoaviaParser(BaseParser):
    display_name = 'Обувь ТЕХНОАВИА (файл в каноническом формате сервиса)'

    def parse(self, file_path: str) -> List[ProductIn]:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        products: List[ProductIn] = []

        for row in rows[1:]:
            if row is None or all(v is None for v in row):
                continue
            values = dict(zip(headers, row))

            name = _clean(values.get("Название товара"))
            if not name:
                continue  # без названия строка бесполезна

            attributes = {}
            brand = None
            for header, value in values.items():
                if not header.startswith(PARAM_PREFIX):
                    continue
                cleaned = _clean(value)
                if not cleaned:
                    continue
                if header == BRAND_HEADER:
                    brand = cleaned
                else:
                    attributes[header[len(PARAM_PREFIX):]] = cleaned

            sale_price_raw = values.get("Цена продажи, ₽")
            try:
                sale_price = float(sale_price_raw) if sale_price_raw not in (None, "") else None
            except (TypeError, ValueError):
                sale_price = None

            products.append(
                ProductIn(
                    article=_clean(values.get("Артикул")),
                    name=name,
                    brand=brand,
                    cost_price=None,
                    sale_price=sale_price,
                    image_url=_clean(values.get("Изображения")),
                    description=_clean(values.get("Описание")),
                    attributes=attributes,
                )
            )

        return products
