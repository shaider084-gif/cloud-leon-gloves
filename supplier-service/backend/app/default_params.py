"""Столбцы по умолчанию у всех поставщиков (решение пользователя, 2026-10-06).

Всегда присутствуют: Артикул, Название, Закупка, Продажа, Изображения, Описание,
Размер(ы), Бренд — это фиксированные поля таблицы, плюс «Параметр: …»:
Товар, Поставщик, Страна производства, Вес, Реестр сертификатов, Реестр Минпромторг.
Пустые столбцы показываются всегда; заполняются по команде пользователя.

DEFAULT_PARAM_KEYS — порядок параметров в таблице и в экспорте.
apply_defaults — проставляет «Поставщик» (= название поставщика), если он не задан.
fill_db — идемпотентно делает то же для уже сохранённых товаров последней загрузки
каждого поставщика (вызывается при старте приложения).
"""
from typing import Dict

DEFAULT_PARAM_KEYS = ["Товар", "Поставщик", "Страна производства", "Вес", "Реестр сертификатов", "Реестр Минпромторг"]

# Поставщики, к которым правила не применяются (зеркало сайта и тестовые).
SKIP_SLUGS = {"catalog-import", "demo", "test"}

# Известные факты о стране производства (проверено по данным поставщика/реестров).
COUNTRY_BY_SLUG = {
    "fest": "Россия",  # ООО «Предприятие «ФЭСТ», г. Кострома; ТУ и РУ РФ
    "pkf_spetczaschita": "Россия",  # правило парсера: бренд «Россия»
}


def apply_defaults(attributes: Dict, supplier) -> Dict:
    """Возвращает attributes с обязательными значениями по умолчанию."""
    attrs = dict(attributes or {})
    if not attrs.get("Поставщик"):
        attrs["Поставщик"] = supplier.name
    country = COUNTRY_BY_SLUG.get(supplier.slug)
    if country and not attrs.get("Страна производства"):
        attrs["Страна производства"] = country
    return attrs


def fill_db() -> int:
    """Проставляет значения по умолчанию в товарах текущей (последней успешной) загрузки
    каждого поставщика. Возвращает число изменённых товаров."""
    from . import models
    from .database import SessionLocal

    db = SessionLocal()
    changed = 0
    try:
        for sup in db.query(models.Supplier).all():
            if sup.slug in SKIP_SLUGS:
                continue
            last = (
                db.query(models.Upload)
                .filter(models.Upload.supplier_id == sup.id, models.Upload.status == "done")
                .order_by(models.Upload.created_at.desc(), models.Upload.id.desc())
                .first()
            )
            if not last:
                continue
            for p in db.query(models.Product).filter(models.Product.upload_id == last.id):
                new = apply_defaults(p.attributes, sup)
                if new != (p.attributes or {}):
                    p.attributes = new  # новый dict — чтобы SQLAlchemy заметил изменение JSON
                    changed += 1
        if changed:
            db.commit()
        return changed
    finally:
        db.close()
