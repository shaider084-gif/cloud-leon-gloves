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
документах реестра или на сайте, остаются без этих полей.
"""
from typing import Dict, List, Optional

import json
import os

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

BRAND = "Jeta Safety"
MARKUP = 1.30  # как у остальных поставщиков (по умолчанию +30%)
COL_ARTICLE, COL_NAME, COL_UNIT, COL_PACK, COL_BOX, COL_PRICE_RUB = 1, 2, 3, 4, 5, 9
SERVICE_PREFIX = "Файл: "  # ключи служебных столбцов каталога (см. catalog_sync)


def _load_extra() -> Dict[str, Dict[str, str]]:
    """Параметры с jetasafety.com и ссылки реестра сертификатов по артикулам
    (jeta_extra.json). Собираются отдельным скриптом, парсер только читает."""
    path = os.path.join(os.path.dirname(__file__), "jeta_extra.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("by_article", {})
    except (OSError, ValueError):
        return {}


_EXTRA = _load_extra()


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


def _load_catalog_rows(articles: List[str]) -> Dict[str, object]:
    """article -> товар бренда Jeta из «Весь каталог». Если у артикула в
    каталоге несколько строк — берём самую заполненную."""
    from ..catalog import IMPORT_SUPPLIER_SLUG, get_current_products
    from ..database import SessionLocal
    from .. import models

    found: Dict[str, object] = {}
    wanted = set(articles)
    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
        if not supplier:
            return found
        for p in get_current_products(db, supplier.id):
            art = (p.article or "").strip()
            if art not in wanted or "jeta" not in (p.brand or "").lower():
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
                        pack=_qty(row[COL_PACK], unit_txt),
                        box=_qty(row[COL_BOX], unit_txt),
                    )
                )
        wb.close()

        catalog = _load_catalog_rows([r["article"] for r in records])

        products: List[ProductIn] = []
        for r in records:
            article, cost = r["article"], r["cost"]
            cat = catalog.get(article)
            group = r["group"]
            from_price = {k: v for k, v in (("Количество в упаковке", r["pack"]),
                                            ("Количество в коробке", r["box"])) if v}
            if cat is not None:
                attributes = {k: v for k, v in (cat.attributes or {}).items()
                              if not k.startswith(SERVICE_PREFIX)}
                attributes.update(from_price)
                _merge_extra(attributes, article)
                products.append(
                    ProductIn(
                        article=article,
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
                desc = "\n".join(group["desc"]) if group and group["desc"] else None
                products.append(
                    ProductIn(
                        article=article,
                        name=(group["name"] if group and group["name"] else article),
                        brand=BRAND,
                        unit=r["unit"],
                        cost_price=cost,
                        sale_price=round(cost * MARKUP, 2) if cost is not None else None,
                        description=desc,
                        attributes=_merge_extra(dict(from_price), article),
                    )
                )
        return products
