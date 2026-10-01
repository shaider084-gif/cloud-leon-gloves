import re
from typing import Dict, List, Optional, Tuple

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

MARKUP_PERCENT = 30.0

# Канонический порядок размеров — только для сортировки при склейке
# нескольких размеров одного товара в одну ячейку (см. SIZE_DELIMITER).
SIZE_ORDER = [
    "5 (XXS)", "6 (XS)", "7 (S)", "8 (M)", "9 (L)",
    "10 (XL)", "11 (XXL)", "12 (XXXL)", "Универсальный",
]
SIZE_ORDER_INDEX = {label: i for i, label in enumerate(SIZE_ORDER)}
SIZE_DELIMITER = "##"

# Материалы, для которых известна форма родительного падежа — нужно только
# чтобы превратить "спилок/кожа" в "из спилка и кожи" в названии (пример из
# ТЗ). Для материалов не из этого словаря запись "материал1/материал2"
# оставляем как есть — так безопаснее, чем гадать со склонением.
MATERIAL_GENITIVE = {
    "спилок": "спилка",
    "кожа": "кожи",
}

_ARTICLE_RE = re.compile(r"\(\s*арт\.?\s*[^()]*\)", re.IGNORECASE)
# Обрабатывает один уровень вложенных скобок — реальные значения выглядят
# как "(размер 11 (XXL))" или "(размер 8 (M) цвет зеленый)".
_SIZE_CLAUSE_RE = re.compile(r"\(\s*размер\b(?:[^()]|\([^()]*\))*\)", re.IGNORECASE)
_PACK_QTY_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_PAIRS_QTY_RE = re.compile(r"\b\d+\s*пар\.?", re.IGNORECASE)
_PACKAGING_PHRASE_RE = re.compile(
    r"индивидуальн(?:ая|ой)\s+упаковк\w*|инд\.?\s*упаковка", re.IGNORECASE
)
# То, что остаётся от "(Упак.100 пар)" после вырезания количества (см. _PAIRS_QTY_RE) —
# пустая скобка с одним словом "Упак." внутри, тоже вырезаем.
_EMPTY_PACK_PAREN_RE = re.compile(r"\(\s*[Уу]пак\.?\s*\)")
_GWARD_TOKEN_RE = re.compile(r"\bgward\b", re.IGNORECASE)
_LEADING_LINE_RE = re.compile(r"^(?P<line>.+?)\s+перчатки\s+(?P<rest>.+)$", re.IGNORECASE)
_SLASH_MATERIAL_RE = re.compile(r"\b([а-яё]+)/([а-яё]+)\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s{2,}")
_SIZE_PREFIX_RE = re.compile(r"^размер\s+", re.IGNORECASE)


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip(" ,")


def _normalize_material_slash(text: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        a, b = m.group(1).lower(), m.group(2).lower()
        ga, gb = MATERIAL_GENITIVE.get(a), MATERIAL_GENITIVE.get(b)
        if ga and gb:
            return f"из {ga} и {gb}"
        return m.group(0)

    return _SLASH_MATERIAL_RE.sub(repl, text)


def transform_name(raw_name: str, base_article: str) -> Tuple[str, bool]:
    """Переделывает сырое название поставщика под формат ТЗ:
    "Перчатки Gward {линейка} {описание}, арт. {артикул}".

    Возвращает (новое_название, matched_pattern) — matched_pattern=False
    значит, что в исходном тексте не нашлось слова "перчатки" рядом с
    названием линейки, и результат стоит перепроверить глазами.
    """
    text = raw_name
    text = _SIZE_CLAUSE_RE.sub("", text)
    text = _ARTICLE_RE.sub("", text)
    text = _PACK_QTY_RE.sub("", text)
    text = _PAIRS_QTY_RE.sub("", text)
    text = _EMPTY_PACK_PAREN_RE.sub("", text)
    text = _PACKAGING_PHRASE_RE.sub("", text)
    text = _GWARD_TOKEN_RE.sub("", text)
    text = _collapse_ws(text)
    text = _normalize_material_slash(text)
    text = _collapse_ws(text)

    m = _LEADING_LINE_RE.match(text)
    if m:
        line = m.group("line").strip(" ,")
        rest = m.group("rest").strip(" ,")
        body = f"{line} {rest}".strip()
        matched = True
    else:
        body = text
        matched = False

    body = _collapse_ws(body)
    if body:
        new_name = f"Перчатки Gward {body}, арт. {base_article}"
    else:
        new_name = f"Перчатки Gward, арт. {base_article}"
    return new_name, matched


def normalize_size(raw_characteristic: str) -> List[str]:
    """"Размер 11 (XXL)" -> ["11 (XXL)"]. Не-размерные значения (цвет облива,
    упаковка и т.п.) возвращают [] — они не подходят под формат размера."""
    raw = (raw_characteristic or "").strip()
    if not raw:
        return []
    labels: List[str] = []
    for part in raw.split(";"):
        part = part.strip()
        if _SIZE_PREFIX_RE.match(part):
            label = _SIZE_PREFIX_RE.sub("", part).strip()
            if label and label not in labels:
                labels.append(label)
    return labels


class GwardParser(BaseParser):
    """Поставщик GWARD — сложный формат файла: одна строка на комбинацию
    товар+размер+склад (артикул содержит суффикс #NNNN — техническая метка
    склада/варианта в системе поставщика, не значимая для нашего каталога).

    Правила ниже зафиксированы по прямому ТЗ пользователя (сессия 2026-09-22):
      - Артикул обрезается до "#".
      - Бренд всегда "GWARD", категория пока не заполняется.
      - Размеры одного товара склеиваются в одну ячейку через "##".
      - Название переписывается в формат "Перчатки Gward {линейка} {описание}, арт. {артикул}".
      - Товары "Перчатки ХБ" (хлопчатобумажная основа) этому поставщику не нужны — исключаются полностью.
    Товары считаются одинаковыми (и их размеры склеиваются в одну строку),
    если совпадает обрезанный артикул И итоговое название без размера — один
    и тот же артикул поставщика реально покрывает разные товары (разный
    материал/цвет/цена), поэтому механическая склейка только по артикулу
    была бы небезопасна (см. переписку с пользователем)."""

    display_name = "GWARD (кастомный парсер)"

    def parse(self, file_path: str) -> List[ProductIn]:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        idx = {name: i for i, name in enumerate(header)}

        def get(row, key):
            i = idx.get(key)
            return row[i] if i is not None and i < len(row) else None

        seen_full_articles = set()
        groups: Dict[Tuple[str, str], dict] = {}
        order: List[Tuple[str, str]] = []
        self.price_conflicts: List[dict] = []
        self.unmatched_names: List[dict] = []

        for row in rows[1:]:
            if row is None or all(v is None for v in row):
                continue

            full_article = get(row, "артикул")
            if not full_article:
                continue
            full_article = str(full_article).strip()
            if full_article in seen_full_articles:
                continue  # дубль склада — та же комбинация товар+размер уже учтена
            seen_full_articles.add(full_article)

            raw_name = str(get(row, "название") or "").strip()
            if not raw_name:
                continue
            if "хб" in raw_name.lower():
                continue  # по ТЗ — этому поставщику "Перчатки ХБ" не нужны

            base_article = full_article.split("#")[0].strip()

            cost_raw = get(row, "ваша цена")
            try:
                cost_price = float(cost_raw) if cost_raw is not None else None
            except (TypeError, ValueError):
                cost_price = None

            new_name, matched = transform_name(raw_name, base_article)
            if not matched:
                self.unmatched_names.append(
                    {"article": full_article, "raw_name": raw_name, "new_name": new_name}
                )

            raw_characteristic = str(get(row, "характеристики") or "").strip()
            size_labels = normalize_size(raw_characteristic)

            key = (base_article, new_name)
            if key not in groups:
                groups[key] = {
                    "article": base_article,
                    "name": new_name,
                    "cost_price": cost_price,
                    "sizes": [],
                    "other_characteristic": None,
                }
                order.append(key)
            group = groups[key]

            if cost_price is not None and group["cost_price"] is not None and cost_price != group["cost_price"]:
                self.price_conflicts.append(
                    {
                        "article": base_article,
                        "name": new_name,
                        "kept_price": group["cost_price"],
                        "conflicting_price": cost_price,
                        "source_row_article": full_article,
                    }
                )
            elif group["cost_price"] is None:
                group["cost_price"] = cost_price

            for label in size_labels:
                if label not in group["sizes"]:
                    group["sizes"].append(label)
            if not size_labels and raw_characteristic and not group["other_characteristic"]:
                group["other_characteristic"] = raw_characteristic

        products: List[ProductIn] = []
        for key in order:
            g = groups[key]
            sizes_sorted = sorted(
                g["sizes"], key=lambda s: SIZE_ORDER_INDEX.get(s, len(SIZE_ORDER))
            )
            attributes = {}
            if sizes_sorted:
                attributes["Размер"] = SIZE_DELIMITER.join(sizes_sorted)
            if g["other_characteristic"]:
                attributes["Характеристики (не размер)"] = g["other_characteristic"]

            cost_price = g["cost_price"]
            sale_price = (
                round(cost_price * (1 + MARKUP_PERCENT / 100), 2)
                if cost_price is not None
                else None
            )

            products.append(
                ProductIn(
                    article=g["article"],
                    name=g["name"],
                    brand="GWARD",
                    category=None,
                    unit=None,
                    cost_price=cost_price,
                    sale_price=sale_price,
                    attributes=attributes,
                )
            )

        return products
