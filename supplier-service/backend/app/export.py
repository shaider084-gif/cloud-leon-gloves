from io import BytesIO
from typing import List

import openpyxl
from openpyxl.styles import Font, PatternFill

from . import models
from .catalog_sync import attr_label, attr_sort_key, is_service_key
from .schemas import CANONICAL_FIELDS

# Заголовки по решению пользователя (2026-10-07): название — всегда «Название товара или услуги»,
# бренд — «Параметр: Бренд» (как в файле магазина).
_FIXED_LABELS = {"name": "Название товара или услуги", "brand": "Параметр: Бренд"}
FIXED_COLUMNS = [(f, _FIXED_LABELS.get(f, label)) for f, label in CANONICAL_FIELDS] + [
    ("sale_price", "Цена продажи"),
    ("external_product_id", "ID товара"),
    ("external_variant_id", "ID варианта"),
    ("description", "Описание"),
    ("image_url", "Изображения"),
]

# Порядок столбцов "Параметр: X" — как в таблице на сайте: сначала базовый шаблон.
BASE_PARAM_ORDER = ["Товар", "Размер", "Поставщик", "Страна производства", "Вес", "Размер, см", "Размер, м3", "Реестр сертификатов", "Реестр Минпромторг"]  # столбцы по умолчанию (default_params.py)

HEADER_FILL = PatternFill(start_color="FF92D050", end_color="FF92D050", fill_type="solid")


def export_products_to_xlsx(products: List[models.Product]) -> BytesIO:
    """Собирает products в xlsx: фиксированные колонки + динамические 'Параметр: X'
    из объединения ключей attributes по всем товарам (аналог структуры
    'Шаблон сиз рук')."""

    used = set()
    for p in products:
        for key, val in (p.attributes or {}).items():
            if val not in (None, "") and key != "Бренд":  # бренд — фиксированный столбец «Параметр: Бренд»
                used.add(key)
    # Служебные столбцы файла магазина («Файл: URL» …) — в конце, с исходным заголовком.
    service_keys = sorted((k for k in used if is_service_key(k)), key=attr_sort_key)
    used = {k for k in used if not is_service_key(k)}
    attribute_keys: List[str] = [k for k in BASE_PARAM_ORDER] + sorted(used - set(BASE_PARAM_ORDER)) + service_keys

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Товары"

    headers = [label for _, label in FIXED_COLUMNS] + [attr_label(k) for k in attribute_keys]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL

    for p in products:
        row = [getattr(p, field) for field, _ in FIXED_COLUMNS]
        row += [(p.attributes or {}).get(k, "") for k in attribute_keys]
        ws.append(row)

    for col in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 60)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
