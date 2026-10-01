"""Поставщик ООО ПКФ "Спецзащита" — прайс приходит PDF-файлом (не таблицей в
привычном смысле: pdfplumber режет визуальные таблицы на много "сырых" строк
из-за переноса текста внутри ячеек и объединённых заголовков). Правила ниже
зафиксированы по прямому ТЗ пользователя (сессия 2026-09-23):

  - Артикул = "Модель" из прайса.
  - Иногда одна строка прайса на самом деле описывает НЕСКОЛЬКО товаров сразу
    (модель/название/цена через " / ") — см. _split_slash_variants.
  - Бренд всегда "Россия".
  - "Параметр: Товар" — первое слово названия (Костюм/Плащ/Перчатки/Краги/...).
  - "Параметр: Поставщик" — всегда "ООО ПКФ \"Спецзащита\"".
  - "Класс защиты" и "Климатический пояс" — вытаскиваются из прайса по
    содержимому ячейки (координаты столбцов в pdfplumber ненадёжны — колонки
    "плывут" между строками одной физической таблицы), а не по позиции.
  - Название переписывается в стиле GWARD: "{Товар} Россия {остальное}, арт. {модель}".
"""
import re
from typing import Dict, List, Optional, Tuple

import pdfplumber

from ..schemas import ProductIn
from .base import BaseParser

MARKUP_PERCENT = 30.0
SUPPLIER_LABEL = 'ООО ПКФ "Спецзащита"'
BRAND = "Россия"

_PRICE_RE = re.compile(r"^[\d\s]+(?:/[\d\s]+)*$")
_CLASS_RE = re.compile(r"Тр\d|Тн\d")
_CLIMATE_RE = re.compile(r"^(I|II|III|IV|V)\b[,\s]|Особый")
_DENSITY_RE = re.compile(r"г/м")
_WS_RE = re.compile(r"\s{2,}")
_MODEL_TOKEN_RE = re.compile(r"^[А-Я]{1,4}\d{1,3}[А-Я]{0,3}$")
_MODEL_PREFIX_RE = re.compile(r"^([А-Я]{1,4}\d{1,3}[А-Я]{0,3})\s+(?=[А-ЯЁ])")


def _unique(items) -> List[str]:
    out: List[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def _is_price(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    return bool(_PRICE_RE.match(text)) and any(c.isdigit() for c in text)


def _rightmost_nonempty(row) -> Optional[str]:
    for cell in reversed(row):
        if cell and cell.strip():
            return cell.strip()
    return None


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip(" ,")


def _group_rows(rows: List[list]) -> List[List[list]]:
    """Находит первую "настоящую" строку с данными (там, где справа стоит
    цена) и дальше режет строки на группы — новая группа начинается там, где
    в крайнем правом заполненном столбце снова стоит цена."""
    start_idx = None
    for i, row in enumerate(rows):
        rn = _rightmost_nonempty(row)
        if rn and _is_price(rn):
            start_idx = i
            break
    if start_idx is None:
        return []

    groups: List[List[list]] = []
    current: Optional[List[list]] = None
    for row in rows[start_idx:]:
        rn = _rightmost_nonempty(row)
        if rn and _is_price(rn):
            if current is not None:
                groups.append(current)
            current = [row]
        elif current is not None:
            current.append(row)
    if current is not None:
        groups.append(current)
    return groups


def _extract_fields(group: List[list]) -> dict:
    model_cell = None
    for row in group:
        if row[0] and row[0].strip():
            model_cell = row[0].strip()
            break

    all_cells = [c.strip() for row in group for c in row if c and c.strip()]

    price = None
    for c in reversed(all_cells):
        if _is_price(c):
            price = c
            break

    class_parts: List[str] = []
    climate_parts: List[str] = []
    density_parts: List[str] = []
    name_parts: List[str] = []

    for c in all_cells:
        if c == model_cell:
            continue
        if _is_price(c) and c == price:
            continue
        if _CLASS_RE.search(c):
            class_parts.append(c)
        elif _CLIMATE_RE.match(c):
            climate_parts.append(c)
        elif _DENSITY_RE.search(c):
            density_parts.append(c)
        else:
            name_parts.append(c)

    # "Модель" в исходнике иногда занимает две строки — код и уточнение
    # ("КС43ОТ" / "с капюшоном"), а иногда предлог уже прилип к первой строке
    # ("КС07ОТ с" / "капюшоном"). В обоих случаях артикул — только код, а
    # уточнение дописываем в КОНЕЦ названия (не в начало — иначе оно съедает
    # роль первого слова, по которому определяется "Параметр: Товар").
    if model_cell and "\n" in model_cell:
        first_line, rest = model_cell.split("\n", 1)
        tokens = first_line.strip().split()
        if len(tokens) > 1 and tokens[-1] in ("с", "со", "без"):
            model_cell = " ".join(tokens[:-1])
            rest = f"{tokens[-1]} {rest.strip()}"
        else:
            model_cell = first_line.strip()
        if rest.strip():
            name_parts.append(rest.strip())

    name = _collapse_ws(" ".join(p.replace("\n", " ") for p in name_parts))

    # Иногда код модели вообще не попадает в свою колонку и остаётся приклеен
    # к началу названия ("КП2 Краги спилковые...") — подхватываем его оттуда,
    # только когда своей модели ещё нет.
    if not model_cell:
        m = _MODEL_PREFIX_RE.match(name)
        if m:
            model_cell = m.group(1)
            name = name[m.end():].strip()
    name = _collapse_ws(name)

    return {
        "model": model_cell,
        "name": name,
        "class_zaschity": _collapse_ws(" ".join(class_parts)) if class_parts else None,
        "climate": _collapse_ws(" ".join(climate_parts)) if climate_parts else None,
        "density": " / ".join(density_parts) if density_parts else None,
        "price": price,
    }


def _split_slash_variants(field: dict) -> Tuple[List[dict], bool]:
    """Строка вида "Б2 / Б1  Вачеги цельнокроёные / П-образные  585 / 570" —
    это на самом деле 2+ разных товара в одной строке прайса. Разбиваем,
    только когда число моделей ЧЁТКО совпадает с числом цен — иначе (например
    "Ф1  Фартук спилковый / бесшовный  1260 / 1750", где артикул только один
    на 2 цены) не гадаем новый код, а возвращаем строку как есть с пометкой
    needs_review=True, чтобы пользователь проверил её сам."""
    price = field["price"] or ""

    # Н4: в прайсе одна строка "Рукава спилковые", у поставщика (сайт ksnn.ru)
    # это 2 отдельных товара — жёлтые и чёрные (свои фото, цена одна).
    if field["model"] == "Н4" and "/" not in price:
        return [
            {**field, "model": f"Н4 {color}", "name": f"Рукава спилковые {color}",
             "extra_attrs": {"Цвет": color.capitalize()}}
            for color in ("жёлтые", "чёрные")
        ], False

    if "/" not in price:
        return [field], False

    prices = [p.strip() for p in price.split("/")]
    models = [m.strip() for m in (field["model"] or "").split("/")] if field["model"] else []

    # Н2: в прайсе один код и две цены, но у поставщика (сайт ksnn.ru) это 4
    # артикула — малые/увеличенные x жёлтые/чёрные; по указанию пользователя
    # малые = первая цена (756), увеличенные = вторая (980).
    if models == ["Н2"] and len(prices) == 2:
        result = []
        for size, price_i in zip(("малые", "увеличенные"), prices):
            for color in ("жёлтые", "чёрные"):
                result.append({
                    **field,
                    "model": f"Н2 {size} {color}",
                    "name": f"Нарукавники спилковые {size} {color}",
                    "price": price_i,
                    "extra_attrs": {"Размер": size.capitalize(), "Цвет": color.capitalize()},
                })
        return result, False

    if len(models) != len(prices) or len(models) < 2:
        return [field], True

    names = [n.strip() for n in field["name"].split("/")] if field["name"] and "/" in field["name"] else None
    if names and len(names) != len(prices):
        names = None  # число вариантов названия не совпало — не рискуем сопоставлять

    # Осторожно: сегменты через "/" бывают и вариантами ОДНОГО товара
    # ("Вачеги цельнокроёные / П-образные"), и СОВСЕМ разными товарами в одной
    # строке прайса ("Чехол для шуруповёрта / Крепёж для молотка"). Надёжного
    # правила различить их нет, поэтому каждый сегмент берём как есть, без
    # попытки "дописать" к нему слово первого варианта — так короткие названия
    # вариантов выглядят лаконично, зато никогда не станут ошибочно слепленным
    # названием совсем другого товара.
    result = []
    for i, (model, price_i) in enumerate(zip(models, prices)):
        name_i = names[i].strip() if names else field["name"]
        result.append({**field, "model": model, "name": _collapse_ws(name_i), "price": price_i})
    return result, False


def _tovar_category(name: str) -> Optional[str]:
    if not name:
        return None
    low = name.lower()
    if "костюм сварщика и металлурга" in low:
        return "Костюм металлурга##Костюм сварщика"
    if "костюм сварщика" in low:
        return "Костюм сварщика"
    if "костюм металлурга" in low:
        return "Костюм металлурга"
    first_word = name.split(" ", 1)[0].strip("«»\"'.,()")
    if first_word == "Костюм":
        return "Костюм сварщика"
    return first_word or None


def _build_name(field: dict) -> str:
    body = _collapse_ws(field["name"])
    article = field["model"] or ""
    if article:
        return f"{body}, арт. {article}"
    return body


class SpetszaschitaParser(BaseParser):
    display_name = 'ООО ПКФ "Спецзащита" (кастомный PDF-парсер)'

    def __init__(self):
        self.needs_review: List[dict] = []

    def parse(self, file_path: str) -> List[ProductIn]:
        self.needs_review = []
        raw_fields: List[dict] = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if len(table) < 4 or len(table[0]) < 4:
                        continue  # шум — мелкие декоративные "таблицы" без данных
                    for group in _group_rows(table):
                        field = _extract_fields(group)
                        if not field["name"] or not field["price"]:
                            continue
                        raw_fields.append(field)

        products: List[ProductIn] = []
        for field in raw_fields:
            split_result, needs_review = _split_slash_variants(field)
            if needs_review:
                self.needs_review.append(field)
            for item in split_result:
                try:
                    cost_price = float(item["price"].replace(" ", ""))
                except (ValueError, AttributeError):
                    cost_price = None
                sale_price = (
                    round(cost_price * (1 + MARKUP_PERCENT / 100), 2)
                    if cost_price is not None
                    else None
                )
                attributes = {"Товар": _tovar_category(item["name"]) or "", "Поставщик": SUPPLIER_LABEL}
                classes = _unique(re.findall(r"Т[рн]\d", item.get("class_zaschity") or ""))
                # По указанию пользователя отдельного параметра "Класс защиты" нет —
                # классы (Тр3, Тн3, Тн4) дописываются в "Защитные свойства".
                if classes:
                    attributes["Защитные свойства"] = "##".join(classes)
                climate_src = re.sub(r"\([^)]*\)", "", item.get("climate") or "")
                zones = _unique(
                    "Особый" if z.lower() == "особый" else z
                    for z in re.findall(r"\b(?:IV|III|II|I|V)\b|[Оо]собый", climate_src)
                )
                if zones:
                    attributes["Климатический пояс"] = "##".join(zones)
                dens = [re.sub(r"\s+", " ", x).strip() for x in re.findall(r"\d+\s*г/м²", item.get("density") or "")]
                if dens:
                    attributes["Плотность ткани"] = dens[0]
                    if len(dens) > 1:
                        attributes["Плотность ткани накладок"] = dens[1]

                attributes.update(item.get("extra_attrs", {}))

                products.append(
                    ProductIn(
                        article=item["model"] or None,
                        name=_build_name(item),
                        brand=BRAND,
                        category=None,
                        unit=None,
                        cost_price=cost_price,
                        sale_price=sale_price,
                        attributes=attributes,
                    )
                )

        return products
