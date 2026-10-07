"""Общая логика "текущего каталога" поставщика и сверки нового прайса с ним.

Упрощение (сознательное, MVP): "текущий каталог" поставщика — это набор
Product'ов последней успешной (status='done') загрузки. Апсерта в БД нет —
каждая загрузка создаёт новый набор строк, а "текущим" считается просто самый
свежий. Это не требует изменений схемы и уязвимостей блокировок при апдейте;
если понадобится полноценная история изменений одного товара — заменить на
модель с апсертом по (supplier_id, article).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Set

from sqlalchemy.orm import Session

from . import models
from .schemas import ProductIn

DEMO_SUPPLIER_SLUG = "demo"
IMPORT_SUPPLIER_SLUG = "catalog-import"


def get_current_products(db: Session, supplier_id: int) -> List[models.Product]:
    """Товары последней успешной загрузки поставщика — то, что считается
    "текущим каталогом" на вкладке сверки/просмотра."""
    latest_upload = (
        db.query(models.Upload)
        .filter(models.Upload.supplier_id == supplier_id, models.Upload.status == "done")
        .order_by(models.Upload.created_at.desc())
        .first()
    )
    if not latest_upload:
        return []
    return latest_upload.products


def get_all_current_products(db: Session) -> List[models.Product]:
    """Текущий каталог по ВСЕМ поставщикам сразу — для каждого поставщика
    берём товары его последней успешной загрузки и объединяем в один список.
    Поставщик "demo" — тестовый (заводится сам при старте приложения для
    проверки пайплайна) и не содержит реальных данных, поэтому не попадает.
    Не используется вкладкой "Весь каталог" (см. get_import_products) —
    там показывается только импортированный каталог, а не сумма всех
    поставщиков."""
    all_products: List[models.Product] = []
    for supplier in db.query(models.Supplier).filter(models.Supplier.slug != DEMO_SUPPLIER_SLUG).all():
        all_products.extend(get_current_products(db, supplier.id))
    return all_products


def get_import_products(db: Session) -> List[models.Product]:
    """Товары большого импортированного каталога (старый магазин
    защита-про.рф, файл "Общий каталог/shop_data-*.csv", см.
    scripts/import_master_catalog.py) — ЭТО и есть содержимое вкладки
    "Весь каталог". Данные от поставщиков (GWARD и т.п.) сюда не подмешиваются
    — у каждого поставщика своя страница; "Весь каталог" сверяется с ними
    только для подсветки (см. get_active_supplier_articles), но не показывает
    их строки."""
    supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
    if not supplier:
        return []
    return get_current_products(db, supplier.id)


def get_import_articles(db: Session) -> Set[str]:
    """Артикулы из большого импортированного каталога (старый магазин,
    см. scripts/import_master_catalog.py) — используется на странице
    поставщика, чтобы подсветить зелёным те его товары, что уже были в старом
    каталоге, и красным — новые, которых там не было."""
    supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
    if not supplier:
        return set()
    return {p.article for p in get_current_products(db, supplier.id) if p.article}


def get_import_variant_ids(db: Session) -> Set[str]:
    """«ID варианта» из «Весь каталог» (зеркало сайта) — по ним сопоставляются поставщики,
    у которых артикулов в каталоге нет (ФЭСТ)."""
    supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
    if not supplier:
        return set()
    return {str(p.external_variant_id).strip() for p in get_current_products(db, supplier.id)
            if p.external_variant_id and str(p.external_variant_id).strip()}


def get_active_supplier_articles(db: Session) -> Set[str]:
    """Артикулы по ВСЕМ реально ведущимся поставщикам (то есть кроме
    служебных demo/catalog-import) — используется на вкладке "Весь каталог",
    чтобы подсветить зелёным позиции, которые уже "в работе" у какого-то
    поставщика, и красным — те, что пока ни у кого не ведутся."""
    articles: Set[str] = set()
    for supplier in db.query(models.Supplier).filter(
        models.Supplier.slug.notin_([DEMO_SUPPLIER_SLUG, IMPORT_SUPPLIER_SLUG])
    ).all():
        articles.update(p.article for p in get_current_products(db, supplier.id) if p.article)
    return articles


@dataclass
class DiffResult:
    matched: List[tuple] = field(default_factory=list)   # (current: Product, new: ProductIn)
    new_items: List[ProductIn] = field(default_factory=list)       # 🔴 в новом прайсе, не было в каталоге
    missing_items: List[models.Product] = field(default_factory=list)  # 🟠 было в каталоге, нет в новом прайсе
    no_article: List[ProductIn] = field(default_factory=list)      # без артикула — сравнить нельзя, считаем новыми

    @property
    def counts(self):
        return {
            "matched": len(self.matched),
            "new": len(self.new_items) + len(self.no_article),
            "missing": len(self.missing_items),
        }


def diff_by_article(current: List[models.Product], new: List[ProductIn]) -> DiffResult:
    """Сверка нового прайса с текущим каталогом по Артикулу (идентификатор
    для сценария 2.2 'Обновление цен', как и было решено)."""
    current_by_article = {p.article: p for p in current if p.article}
    new_by_article: dict[str, ProductIn] = {}
    no_article: List[ProductIn] = []

    for p in new:
        if p.article:
            new_by_article[p.article] = p  # при дублях в файле побеждает последняя строка
        else:
            no_article.append(p)

    result = DiffResult(no_article=no_article)

    for article, new_p in new_by_article.items():
        current_p = current_by_article.get(article)
        if current_p is not None:
            result.matched.append((current_p, new_p))
        else:
            result.new_items.append(new_p)

    for article, current_p in current_by_article.items():
        if article not in new_by_article:
            result.missing_items.append(current_p)

    return result
