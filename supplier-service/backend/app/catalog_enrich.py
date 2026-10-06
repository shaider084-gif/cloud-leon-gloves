"""Дозаполнение товаров поставщика данными из «Весь каталог» (решение пользователя, 2026-10-06, GWARD):
сопоставление по Артикулу; переносятся описание, фото, ID товара и все параметры каталога
(включая служебные «Файл: …»). Заполняются только ПУСТЫЕ поля — цены, название и размеры
из прайса поставщика не затрагиваются.

Данные — catalog_data/<slug>.json (выгрузка из каталога): {артикул: {description, images, product_id, attributes}}.
Для позиций без пары в каталоге «Товар» выводится из названия (derive_tovar).
"""
import json
import os
import re
from typing import Dict, Optional

ENRICH_SLUGS = {"gward"}
_CACHE: Dict[str, Dict] = {}

# Ключевые слова названия (после бренда) -> «Товар»; для позиций, которых нет в каталоге.
_TOVAR_WORDS = [
    ("полумаска", "Полумаска"), ("маска полнолицевая", "Полнолицевая маска"), ("полнолицевая", "Полнолицевая маска"),
    ("предфильтр", "Предфильтр"), ("фильтр", "Фильтр"), ("держатель предфильтра", "Держатель предфильтра"),
    ("пленка защитная", "Пленка защитная"), ("комплект", "Комплект"), ("краги", "Краги"),
    ("рукавицы", "Рукавицы"), ("перчатки", "Перчатки"),
]


def _data(slug: str) -> Dict:
    if slug not in _CACHE:
        path = os.path.join(os.path.dirname(__file__), "catalog_data", f"{slug}.json")
        try:
            with open(path, encoding="utf-8") as f:
                _CACHE[slug] = json.load(f)
        except (OSError, ValueError):
            _CACHE[slug] = {}
    return _CACHE[slug]


def derive_tovar(name: str) -> Optional[str]:
    """«Перчатки Gward Полумаска фильтрующая …» — в прайсе у iNEX-позиций ошибочный префикс «Перчатки»,
    поэтому смотрим на слово после бренда."""
    low = (name or "").lower()
    m = re.search(r"gward\s+(.*)", low)
    tail = m.group(1) if m else low
    for key, tovar in _TOVAR_WORDS:
        if tail.startswith(key):
            return tovar
    for key, tovar in _TOVAR_WORDS:
        if key in low[:25]:
            return tovar
    return None


def enrich(slug: str, article: Optional[str], name: str, description, image_url, product_id, attrs: Dict):
    """-> (description, image_url, product_id, attrs) с дозаполненными пустыми значениями."""
    attrs = dict(attrs or {})
    if slug in ENRICH_SLUGS:
        rec = _data(slug).get(article or "")
        if rec:
            description = description or rec.get("description")
            image_url = image_url or rec.get("images")
            product_id = product_id or rec.get("product_id")
            for k, v in (rec.get("attributes") or {}).items():
                if v and not attrs.get(k):
                    attrs[k] = v
        if not attrs.get("Товар"):
            t = derive_tovar(name)
            if t:
                attrs["Товар"] = t
    return description, image_url, product_id, attrs


def enrich_db() -> int:
    """Применяет enrich к сохранённым товарам последней загрузки; возвращает число изменённых."""
    from . import models
    from .database import SessionLocal

    db = SessionLocal()
    changed = 0
    try:
        for sup in db.query(models.Supplier).filter(models.Supplier.slug.in_(list(ENRICH_SLUGS))):
            last = (
                db.query(models.Upload)
                .filter(models.Upload.supplier_id == sup.id, models.Upload.status == "done")
                .order_by(models.Upload.created_at.desc(), models.Upload.id.desc())
                .first()
            )
            if not last:
                continue
            for p in db.query(models.Product).filter(models.Product.upload_id == last.id):
                d, i, pid, a = enrich(sup.slug, p.article, p.name, p.description, p.image_url,
                                      p.external_product_id, p.attributes)
                if (d, i, pid, a) != (p.description, p.image_url, p.external_product_id, dict(p.attributes or {})):
                    p.description, p.image_url, p.external_product_id, p.attributes = d, i, pid, a
                    changed += 1
        if changed:
            db.commit()
        return changed
    finally:
        db.close()
