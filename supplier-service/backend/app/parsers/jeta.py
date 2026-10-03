"""Поставщик JETA (Jeta Safety, slug jeta) — прайс .xlsx, 11 листов
(СИЗОД, респираторы, комбинезоны, перчатки, защита слуха/глаз и т.д.).

Структура листа: шапка «Фото | Артикул | Наименование + Описание | Ед. изм. |
Кол-во в упаковке | Кол-во в коробке | цены в EUR | цены в рублях». Один
товар — несколько строк-размеров: название и описание написаны только в
первой строке группы (первая строка ячейки — название, остальное — описание),
у остальных размеров ячейка пустая; строка без артикула, но с текстом в колонке
«Наименование» — продолжение описания текущей группы.

Закупочная цена — «В РУБЛЯХ, с НДС / Цена со скидкой за ед. изм» (последний
ценовой столбец), в прайсе она уже со скидкой поставщика.

Данные товара берутся из вкладки «Весь каталог» (поставщик catalog-import):
если артикул найден среди товаров бренда Jeta — название, бренд, описание,
фото, ID товара/варианта и все «Параметр: …» копируются оттуда, цена продажи —
из каталога. Не найденные в каталоге остаются с данными из прайса (название и
описание из ячейки, цена продажи = закупка + 30%); на странице поставщика они
подсвечиваются красным (подсветка по артикулу уже есть в сервисе).
Служебные столбцы магазина (URL, мета-теги, «Файл: …») не копируются.

Дополнительно к каждой позиции (jeta_extra.json, ничего из каталога не
затирается): «Количество в упаковке/коробке» из самого прайса; параметры с
сайта производителя jetasafety.com (материал, покрытие, манжета, стандарты EN
и ГОСТ, температуры и т.д.) и «Реестр сертификатов» — ссылки на действующие
декларации/сертификаты ООО «АВТОГРАФ СЕЙФТИ» в реестре Росаккредитации,
где артикул прямо перечислен (pub.fsa.gov.ru). Позиции, которых нет в
документах реестра или на сайте, остаются без этих полей. У красных позиций
(нет в «Весь каталог») пока только реестр и данные прайса — остальное
оставлено пустым по решению пользователя (см. _registry_only).
"""
from typing import Dict, List, Optional

import json
import os
import re

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

BRAND = "Jeta Safety"
MARKUP = 1.30  # как у остальных поставщиков (по умолчанию +30%)
COL_ARTICLE, COL_NAME, COL_UNIT, COL_PACK, COL_BOX, COL_REC_RUB, COL_PRICE_RUB = 1, 2, 3, 4, 5, 8, 9
SERVICE_PREFIX = "Файл: "  # ключи служебных столбцов каталога (см. catalog_sync)


def _load_data() -> dict:
    """Данные JETA из jeta_extra.json: параметры с jetasafety.com и ссылки
    реестра сертификатов по артикулам, карточки производителя, правила для
    красных позиций. Собираются отдельным скриптом, парсер только читает."""
    path = os.path.join(os.path.dirname(__file__), "jeta_extra.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_DATA = _load_data()
_EXTRA: Dict[str, Dict[str, str]] = _DATA.get("by_article", {})
_ARTICLE_TO_SKU: Dict[str, str] = _DATA.get("article_to_sku", {})
_PRODUCTS: Dict[str, dict] = _DATA.get("products", {})
_TYPE_TOVAR: Dict[str, str] = _DATA.get("type_to_tovar", {})
_PREFIX_TOVAR: Dict[str, str] = _DATA.get("prefix_to_tovar", {})
_CLASS_PARAMS: Dict[str, Dict[str, str]] = _DATA.get("class_params", {})

# ---- красные позиции (нет в «Весь каталог») ----
_SIZE_SEG = r"XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|0[5-9]|1[0-2]"
_SIZE_TAIL = re.compile(rf"-({_SIZE_SEG})$", re.I)
_NUM_TO_LETTER = {"05": "XXS", "06": "XS", "07": "S", "08": "M", "09": "L", "10": "XL", "11": "XXL", "12": "XXXL"}
_LETTER_TO_NUM = {"XXS": 5, "XS": 6, "S": 7, "M": 8, "L": 9, "XL": 10, "XXL": 11, "XXXL": 12}
_SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", "4XL", "5XL"]
_TOVAR_NAME = {"Защитный комбинезон": "Комбинезон"}
_TOVAR_USE_MF_NAME = {"Запчасти респиратора", "Запчасти наушники", "Плащ влагозащитный", "Фартук сварщика##Фартук"}
_GLOVE_TOVARS = {"Перчатки", "Краги", "Нарукавники"}
# «Товар» для типов производителя, которых нет среди зелёных (ключевое слово в типе/названии)
_TOVAR_KEYWORDS = [("краг", "Краги"), ("перчат", "Перчатки"), ("нарукавник", "Нарукавники"), ("беруш", "Беруши"),
                   ("наушник", "Наушники"), ("полнолицев", "Полнолицевая маска"), ("полумаск", "Полумаска"),
                   ("респиратор", "Респиратор"), ("комбинез", "Защитный комбинезон"), ("очки", "Очки"),
                   ("щиток", "Щиток"), ("фильтр", "Фильтр")]


def _tovar_by_keywords(*texts: str) -> Optional[str]:
    blob = " ".join(t.lower() for t in texts if t)
    for key, tovar in _TOVAR_KEYWORDS:
        if key in blob:
            return tovar
    return None


def _model(article: str) -> str:
    """Артикул без размера: «JPC-60-5XL» -> «JPC-60», «JEM-832-C(bag)» -> «JEM-832-C»."""
    a = re.sub(r"\(.*?\)", "", article)
    prev = None
    while prev != a:
        prev = a
        a = _SIZE_TAIL.sub("", a)
    return a


def _site_article(article: str) -> str:
    """Артикул для сайта: скобки заменяются дефисом («JRG-9011-XL(6)» -> «JRG-9011-XL-6»)."""
    return re.sub(r"\(([^)]*)\)", r"-\1", article)


def _catalog_candidates(article: str) -> List[str]:
    """Артикул прайса может отличаться от каталожного только размером в конце
    («NATRIX-50BL-07» в прайсе = «NATRIX-50BL-07-S» в каталоге)."""
    cands = [article]
    m = re.fullmatch(r"(.+-)(0[5-9]|1[0-2])", article)
    if m:
        cands.append(f"{article}-{_NUM_TO_LETTER[m.group(2)]}")
    return cands


def _richness(p) -> int:
    return len(p.attributes or {}) + (5 if p.description else 0) + (3 if p.image_url else 0)


def _find_sibling(article: str, catalog: Dict[str, object]):
    """Тот же товар в каталоге в другом размере (kind='size') или с другой
    буквой цвета в артикуле (kind='color': JPC75g ~ JPC75b)."""
    m = _model(article)
    same = [p for a, p in catalog.items() if a != article and _model(a) == m]
    if same:
        return max(same, key=_richness), "size"
    if re.search(r"\d(-?[A-Za-z]{1,2})$", m):
        root = re.sub(r"-?[A-Za-z]{1,2}$", "", m)
        near = [p for a, p in catalog.items()
                if _model(a) != m and _model(a).startswith(root)
                and re.fullmatch(r"-?[A-Za-z]{1,2}", _model(a)[len(root):])]
        if near:
            return max(near, key=_richness), "color"
    return None, None


def _kit_contents(desc_lines: List[str]) -> Optional[str]:
    """Состав комплекта из описания прайса («В комплекте: - … - 1 шт; …») в виде
    списка через ##."""
    lines = [ln.strip() for chunk in desc_lines for ln in chunk.split("\n")]
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("в комплекте")), None)
    if start is None:
        return None
    items: List[str] = []
    for ln in lines[start + 1:]:
        if not ln:
            continue
        if not ln.startswith(("-", "–", "•")):
            break
        item = re.sub(r"\s+", " ", ln.lstrip("-–• ").strip().rstrip(";.,")).strip()
        if item:
            items.append(item[0].upper() + item[1:])
    return "##".join(items) or None


def _size_token(letter: str, numeric: bool, clothing: bool) -> str:
    if numeric and letter in _LETTER_TO_NUM:
        return f"{_LETTER_TO_NUM[letter]} ({letter})"
    if clothing:
        letter = {"XXL": "2XL", "XXXL": "3XL"}.get(letter, letter)
    return letter


def _family_sizes(model: str, records: List[dict], tovar: Optional[str]) -> Optional[str]:
    letters = []
    for r in records:
        if _model(r["article"]) != model:
            continue
        m = _SIZE_TAIL.search(re.sub(r"\(.*?\)", "", r["article"]))
        if m:
            s = m.group(1).upper()
            s = _NUM_TO_LETTER.get(s, s)
            letters.append(s)
    if not letters:
        return None
    ordered = sorted(set(letters), key=lambda s: _SIZE_ORDER.index(s) if s in _SIZE_ORDER else 99)
    numeric = tovar in _GLOVE_TOVARS
    clothing = tovar == "Защитный комбинезон"
    return "##".join(_size_token(s, numeric, clothing) for s in ordered)


def _mf_feature(mf_name: str) -> str:
    m = re.search(r"\b(?:с|со|из|для|на|от)\s+.+$", mf_name, re.I)
    return (m.group(0)[0].lower() + m.group(0)[1:]) if m else ""


def _build_red(r: dict, catalog: Dict[str, object], records: List[dict], kit: Optional[str]) -> ProductIn:
    """Позиция из прайса, которой нет в «Весь каталог» (красная). Заполняем по
    образцу зелёных: 1) тот же товар в каталоге в другом размере/цвете —
    копируем его карточку; 2) иначе — карточка производителя jetasafety.com,
    «Товар» и классовые параметры по тому, как в каталоге заполнены зелёные
    того же типа. Недостающее остаётся пустым. Цена продажи — рекомендованная
    цена прайса (как у зелёных)."""
    article, cost = r["article"], r["cost"]
    group = r["group"]
    price_name = (group["name"] if group and group["name"] else article)
    sib, kind = _find_sibling(article, catalog)
    sku = _ARTICLE_TO_SKU.get(article)
    mf = _PRODUCTS.get(sku) if sku else None
    attrs: Dict[str, str] = {}
    name, desc, images, product_id = None, None, None, None

    if sib is not None:
        attrs = {k: v for k, v in (sib.attributes or {}).items() if not k.startswith(SERVICE_PREFIX)}
        name = sib.name
        desc = sib.description
        images = sib.image_url
        if kind == "size":
            product_id = sib.external_product_id
        else:  # другой цвет: цвет по букве артикула ненадёжен — оставляем пустым
            attrs.pop("Цвет", None)
            old_model, new_model = _model(sib.article), _model(article)
            name = name.replace(old_model, new_model)
            desc = desc.replace(old_model, new_model) if desc else desc

    # «Товар»: тип производителя -> как в каталоге у зелёных; иначе по префиксу артикула
    tovar = attrs.get("Товар")
    if not tovar and mf:
        tovar = _TYPE_TOVAR.get(mf["type"])
    if not tovar:
        pre = re.match(r"[A-Za-z]+", article)
        tovar = _PREFIX_TOVAR.get(pre.group(0).upper()) if pre else None
    if not tovar and kit and "дыхан" in price_name.lower():
        tovar = "Полумаска"
    if not tovar:
        tovar = _tovar_by_keywords(mf["type"] if mf else "", mf["name"] if mf else "", price_name)

    if name is None and kit:
        name = re.sub(r"\s+", " ", price_name).strip()  # комплект: название из прайса
    if name is None:
        if mf and tovar:
            display = sku if sku and "/" not in sku else _model(article)
            if tovar in _TOVAR_USE_MF_NAME:
                name = f"{mf['name']} Jeta Safety {display}"
            else:
                name = f"{_TOVAR_NAME.get(tovar, tovar.split('##')[0])} Jeta Safety {display}"
                feature = _mf_feature(mf["name"])
                if feature:
                    name += f" {feature}"
        else:
            name = price_name
    if desc is None:
        if mf:
            desc = ((mf.get("short") or "") + " " + (mf.get("desc") or "")).strip() or None
        if not desc and group and group["desc"]:
            desc = "\n".join(group["desc"])
    if images is None and mf and mf.get("images"):
        images = " ".join(mf["images"])

    # параметры: образец (братья) -> производитель -> классовые по зелёным
    for k, v in _EXTRA.get(article, {}).items():
        if v and not attrs.get(k):
            attrs[k] = v
    if mf:
        for k, v in _CLASS_PARAMS.get(mf["type"], {}).items():
            if v and not attrs.get(k):
                attrs[k] = v
    if tovar and not attrs.get("Товар"):
        attrs["Товар"] = tovar
    attrs.setdefault("Бренд", BRAND)
    attrs.setdefault("Продажа оптом", "Оптом##От производителя")
    sizes = _family_sizes(_model(article), records, tovar)
    if sizes:
        existing = attrs.get("Размер", "")
        if not existing:
            attrs["Размер"] = sizes
        else:  # размер из прайса, которого ещё нет в списке брата
            tokens = existing.split("##")
            for tok in sizes.split("##"):
                if tok not in tokens and tok.split(" ")[-1].strip("()") not in " ".join(tokens):
                    tokens.append(tok)
            attrs["Размер"] = "##".join(tokens)
    if kit:
        attrs["Комплектация"] = kit
    for k, v in (("Количество в упаковке", r["pack"]), ("Количество в коробке", r["box"])):
        if v:
            attrs[k] = v

    rec = r["rec"]
    return ProductIn(
        article=_site_article(article),
        name=name,
        brand=BRAND,
        unit=r["unit"],
        cost_price=cost,
        sale_price=float(round(rec)) if rec is not None else None,
        image_url=dedupe_images(images),
        external_product_id=product_id,
        external_variant_id=None,
        description=desc,
        attributes=attrs,
    )


def _qty(value, unit) -> Optional[str]:
    """«12» + «пар» -> «12 пар» (количество в упаковке/коробке из прайса)."""
    try:
        n = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    n_txt = str(int(n)) if n == int(n) else str(n)
    return f"{n_txt} {unit}" if unit else n_txt


def _merge_extra(attributes: Dict[str, str], article: str) -> Dict[str, str]:
    """Добавляет параметры производителя. Данные из «Весь каталог» не затираются:
    берём только те ключи, которых нет или которые пусты."""
    for key, value in _EXTRA.get(article, {}).items():
        if value and not attributes.get(key):
            attributes[key] = value
    return attributes


def _load_smaller_to_larger() -> Dict[str, str]:
    path = os.path.join(os.path.dirname(__file__), "jeta_image_dupes.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("smaller_to_larger", {})
    except (OSError, ValueError):
        return {}


# Мелкие копии тех же фото (сверено по содержимому, см. jeta_image_dupes.json).
_SMALLER_TO_LARGER = _load_smaller_to_larger()


def dedupe_images(image_url: Optional[str]) -> Optional[str]:
    """Убирает дубли фото в карточке. В выгрузке магазина каждый размер
    (вариант) загружал свои копии одних и тех же картинок, и в карточке их
    десятки. Правила: 1) повтор имени файла (21.jpg, 22.jpg …) — оставляем
    первую; 2) мелкая копия фото, чья крупная версия есть в той же карточке
    (то же изображение под другим именем) — убираем мелкую. Порядок остальных
    фото сохраняется."""
    if not image_url:
        return image_url
    seen_names = set()
    kept: List[str] = []
    for url in image_url.split():
        name = url.rsplit("/", 1)[-1].lower()
        if name in seen_names:
            continue
        seen_names.add(name)
        kept.append(url)
    kept_set = set(kept)
    kept = [u for u in kept if _SMALLER_TO_LARGER.get(u) not in kept_set]
    return " ".join(kept) or None


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(str(value).replace(",", ".").replace(" ", "")), 2)
    except ValueError:
        return None


def _load_catalog_rows() -> Dict[str, object]:
    """article -> товар бренда Jeta из «Весь каталог» (все товары бренда: по
    ним ищутся и совпадения, и «братья» в другом размере/цвете). Если у
    артикула в каталоге несколько строк — берём самую заполненную."""
    from ..catalog import IMPORT_SUPPLIER_SLUG, get_current_products
    from ..database import SessionLocal
    from .. import models

    found: Dict[str, object] = {}
    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
        if not supplier:
            return found
        for p in get_current_products(db, supplier.id):
            art = (p.article or "").strip()
            if not art or "jeta" not in (p.brand or "").lower():
                continue
            richness = len(p.attributes or {}) + (5 if p.description else 0) + (3 if p.image_url else 0)
            best = found.get(art)
            if best is None or richness > best[0]:
                found[art] = (richness, p)
        return {a: v[1] for a, v in found.items()}
    finally:
        db.close()


class JetaParser(BaseParser):
    display_name = "JETA (Jeta Safety, прайс .xlsx)"

    def parse(self, file_path: str) -> List[ProductIn]:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        records: List[dict] = []
        seen = set()

        for ws in wb.worksheets:
            header_found = False
            group: Optional[dict] = None
            for row in ws.iter_rows(values_only=True):
                if not header_found:
                    header_found = len(row) > COL_ARTICLE and _text(row[COL_ARTICLE]) == "Артикул"
                    continue
                if len(row) <= COL_PRICE_RUB:
                    row = tuple(row) + (None,) * (COL_PRICE_RUB + 1 - len(row))
                unit_txt = _text(row[COL_UNIT])
                article = _text(row[COL_ARTICLE])
                cell = _text(row[COL_NAME])

                if not article:
                    # продолжение описания текущей группы
                    if cell and group is not None:
                        group["desc"].append(cell)
                    continue

                if cell:
                    first, _, rest = cell.partition("\n")
                    group = {"name": first.strip(), "desc": [rest.strip()] if rest.strip() else []}
                if article in seen:
                    continue  # один артикул может повторяться на нескольких листах
                seen.add(article)
                records.append(
                    dict(
                        article=article,
                        group=group,
                        unit=unit_txt or None,
                        cost=_num(row[COL_PRICE_RUB]),
                        rec=_num(row[COL_REC_RUB]),
                        pack=_qty(row[COL_PACK], unit_txt),
                        box=_qty(row[COL_BOX], unit_txt),
                    )
                )
        wb.close()

        catalog = _load_catalog_rows()

        products: List[ProductIn] = []
        for r in records:
            article, cost = r["article"], r["cost"]
            cat = next((catalog[c] for c in _catalog_candidates(article) if c in catalog), None)
            group = r["group"]
            kit = _kit_contents(group["desc"]) if group and group["desc"] else None
            from_price = {k: v for k, v in (("Количество в упаковке", r["pack"]),
                                            ("Количество в коробке", r["box"])) if v}
            if kit:
                from_price["Комплектация"] = kit
            if cat is not None:
                attributes = {k: v for k, v in (cat.attributes or {}).items()
                              if not k.startswith(SERVICE_PREFIX)}
                attributes.update(from_price)
                _merge_extra(attributes, article)
                products.append(
                    ProductIn(
                        article=cat.article or article,
                        name=cat.name,
                        brand=cat.brand or BRAND,
                        unit=r["unit"] or cat.unit,
                        cost_price=cost,
                        sale_price=float(cat.sale_price) if cat.sale_price is not None else (
                            round(cost * MARKUP, 2) if cost is not None else None),
                        image_url=dedupe_images(cat.image_url),
                        external_product_id=cat.external_product_id,
                        external_variant_id=cat.external_variant_id,
                        description=cat.description,
                        attributes=attributes,
                    )
                )
            else:
                products.append(_build_red(r, catalog, records, kit))
        return products
