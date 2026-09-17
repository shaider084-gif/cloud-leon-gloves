from typing import List

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

# Соответствие "человеческое имя колонки в файле поставщика" -> поле канонической схемы.
# Сопоставление регистронезависимое, лишние пробелы обрезаются.
COLUMN_MAP = {
    "артикул": "article",
    "название": "name",
    "наименование": "name",
    "бренд": "brand",
    "цена": "cost_price",
    "закупочная цена": "cost_price",
    "ед": "unit",
    "ед.изм": "unit",
    "ед. изм.": "unit",
}

MARKUP = 1.3  # наценка по умолчанию 30%, как в ранее согласованной бизнес-логике


class DemoParser(BaseParser):
    """
    Демонстрационный/тестовый парсер.

    Ожидает простой xlsx с шапкой в первой строке и колонками вида
    'Артикул' / 'Название' / 'Цена' (порядок и регистр не важны).
    Служит для проверки всего пайплайна (загрузка -> разбор -> сохранение ->
    выгрузка в шаблон) до того, как будут написаны реальные парсеры под
    конкретных поставщиков.
    """

    display_name = "Демо-поставщик (тестовый xlsx)"

    def parse(self, file_path: str) -> List[ProductIn]:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
        field_by_col = {}
        for idx, col_name in enumerate(header):
            field = COLUMN_MAP.get(col_name)
            if field:
                field_by_col[idx] = field

        products: List[ProductIn] = []
        for row in rows[1:]:
            if row is None or all(v is None for v in row):
                continue

            values = {}
            for idx, field in field_by_col.items():
                if idx < len(row):
                    values[field] = row[idx]

            name = values.get("name")
            if not name:
                continue  # без названия строка бесполезна — пропускаем

            cost_price = values.get("cost_price")
            try:
                cost_price = float(cost_price) if cost_price is not None else None
            except (TypeError, ValueError):
                cost_price = None

            sale_price = round(cost_price * MARKUP, 2) if cost_price is not None else None

            products.append(
                ProductIn(
                    article=str(values.get("article")) if values.get("article") is not None else None,
                    name=str(name),
                    brand=str(values.get("brand")) if values.get("brand") else None,
                    category="Демо",
                    unit=str(values.get("unit")) if values.get("unit") else None,
                    cost_price=cost_price,
                    sale_price=sale_price,
                    attributes={},
                )
            )

        return products
