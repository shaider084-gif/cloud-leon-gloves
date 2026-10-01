"""Вкладка "Сравнение цен" — все товары из "Весь каталог" одной таблицей
(Артикул, Название, Бренд, Цена продажи, Параметр: Товар) с фильтрами
по Бренду и по виду продукции (в списках — количество товаров)."""
from collections import Counter
from decimal import Decimal
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..templating import templates
from ..catalog import get_import_products
from ..market_prices import get_market_prices
from ..market_search import domain_of, is_own
from .. import models

router = APIRouter()

TOVAR_ATTR_KEY = "Товар"
PAGE_SIZE = 200
# Рыночная цена ниже этой доли нашей — подозрительная (часто это цена
# расходника, упаковки или ошибка разметки), помечается красным "!".
SUSPICIOUS_RATIO = Decimal("0.3")


def is_suspicious(market_price, our_price) -> bool:
    return bool(market_price is not None and our_price and market_price < our_price * SUSPICIOUS_RATIO)


def _tovar(p: models.Product) -> str:
    return (p.attributes or {}).get(TOVAR_ATTR_KEY) or ""


@router.get("/price-compare", response_class=HTMLResponse)
def price_compare(
    request: Request,
    brand: str = Query(""),
    tovar: str = Query(""),
    filled: int = Query(0),
    page: int = Query(1, ge=1),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    products = get_import_products(db)

    # Счётчики "фасетные": у брендов — с учётом выбранного вида продукции,
    # у видов продукции — с учётом выбранного бренда.
    brand_counts = Counter(p.brand for p in products if p.brand and (not tovar or _tovar(p) == tovar))
    tovar_counts = Counter(_tovar(p) for p in products if _tovar(p) and (not brand or p.brand == brand))
    if brand:
        brand_counts.setdefault(brand, 0)
    if tovar:
        tovar_counts.setdefault(tovar, 0)
    all_brands = sorted(brand_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    all_tovar_types = sorted(tovar_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    if brand:
        products = [p for p in products if p.brand == brand]
    if tovar:
        products = [p for p in products if _tovar(p) == tovar]

    # Рыночные цены — по артикулам отфильтрованной выборки. "Заполненная"
    # строка — та, где уже есть хотя бы одна рыночная цена.
    market = get_market_prices(db, (p.article for p in products))
    if filled:
        # sorted стабилен: внутри групп сохраняется прежний порядок
        products = sorted(products, key=lambda p: p.article not in market)

    total_count = len(products)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE

    return templates.TemplateResponse(
        "price_compare.html",
        {
            "request": request,
            "user": user,
            "products": products[start:start + PAGE_SIZE],
            "tovar_key": TOVAR_ATTR_KEY,
            "all_brands": all_brands,
            "all_tovar_types": all_tovar_types,
            "selected_brand": brand,
            "selected_tovar": tovar,
            "filled": filled,
            "market": market,
            "domain_of": domain_of,
            "is_own": is_own,
            "is_suspicious": is_suspicious,
            "total_count": total_count,
            "page": page,
            "total_pages": total_pages,
            "page_size": PAGE_SIZE,
        },
    )

