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
"""
from typing import Dict, List, Optional

import json
import os

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

BRAND = "Jeta Safety"
MARKUP = 1.30  # как у остальных поставщиков (по умолчанию +30%)
COL_ARTICLE, COL_NAME, COL_UNIT, COL_PRICE_RUB = 1, 2, 3, 9
SERVICE_PREFIX = "Файл: "  # ключи служебных столбцов каталога (см. catalog_sync)


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
                        unit=_text(row[COL_UNIT]) or None,
                        cost=_num(row[COL_PRICE_RUB]),
                    )
                )
        wb.close()

        catalog = _load_catalog_rows([r["article"] for r in records])

        products: List[ProductIn] = []
        for r in records:
            article, cost = r["article"], r["cost"]
            cat = catalog.get(article)
            group = r["group"]
            if cat is not None:
                attributes = {k: v for k, v in (cat.attributes or {}).items()
                              if not k.startswith(SERVICE_PREFIX)}
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
                        attributes={},
                    )
                )
        return products
