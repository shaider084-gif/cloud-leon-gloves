"""Вкладка "Весь каталог" — агрегированный просмотр товаров по ВСЕМ
поставщикам сразу (сценарий описан пользователем в сессии 2026-09-23:
большая таблица + фильтры по бренду и виду продукции + набор видимых
столбцов сужается/расширяется в зависимости от применённых фильтров)."""
from collections import Counter
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Request, Depends, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..templating import templates
from ..catalog import get_import_products, get_active_supplier_articles, IMPORT_SUPPLIER_SLUG
from .. import catalog_sync, models

router = APIRouter()

# Базовые колонки — показываются всегда, независимо от фильтров.
CORE_COLUMNS = [
    ("article", "Артикул"),
    ("name", "Название товара или услуги"),
    ("brand", "Параметр: Бренд"),
    ("unit", "Ед. изм."),
    ("cost_price", "Закупка"),
    ("sale_price", "Продажа"),
    ("external_product_id", "ID товара"),
    ("external_variant_id", "ID варианта"),
    ("description", "Описание"),
    ("image_url", "Изображения"),
]

TOVAR_ATTR_KEY = "Товар"
PAGE_SIZE = 200


@router.get("/catalog", response_class=HTMLResponse)
def catalog_view(
    request: Request,
    brand: str = Query(""),
    tovar: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    all_products = get_import_products(db)
    active_supplier_articles = get_active_supplier_articles(db)

    all_brands = sorted({p.brand for p in all_products if p.brand})
    tovar_counts = Counter(
        (p.attributes or {}).get(TOVAR_ATTR_KEY) for p in all_products if (p.attributes or {}).get(TOVAR_ATTR_KEY)
    )
    all_tovar_types = sorted(tovar_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    filtered = all_products
    if brand:
        filtered = [p for p in filtered if p.brand == brand]
    if tovar:
        filtered = [p for p in filtered if (p.attributes or {}).get(TOVAR_ATTR_KEY) == tovar]
    q = q.strip()
    if q:  # поиск по артикулу (часть артикула), а также по названию и ID варианта
        ql = q.lower()
        filtered = [
            p for p in filtered
            if ql in (p.article or "").lower() or ql in (p.name or "").lower()
            or ql == str(p.external_variant_id or "").strip()
        ]

    # Набор "Параметр: X" столбцов сужается до реально заполненных хотя бы у
    # одного товара в ТЕКУЩЕЙ (отфильтрованной) выборке — без фильтра это
    # весь набор параметров по всем категориям, с фильтром — только те, что
    # использует выбранный бренд/вид продукции.
    param_keys = set()
    for p in filtered:
        for k, v in (p.attributes or {}).items():
            if v not in (None, "") and k != "Бренд":  # бренд — базовый столбец «Параметр: Бренд»
                param_keys.add(k)
    # Служебные столбцы файла (URL, мета-теги, "Свойство: …") идут первыми в
    # порядке файла, затем "Параметр: …" по алфавиту; подпись — исходный заголовок.
    param_columns = [(k, catalog_sync.attr_label(k)) for k in sorted(param_keys, key=catalog_sync.attr_sort_key)]

    last_upload = (
        db.query(models.Upload)
        .join(models.Supplier, models.Supplier.id == models.Upload.supplier_id)
        .filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG, models.Upload.status == "done")
        .order_by(models.Upload.created_at.desc())
        .first()
    )
    # created_at хранится в UTC; показываем по Москве.
    last_update = (last_upload.created_at + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M") if last_upload else None

    # Без пагинации страница с тысячами строк x десятками колонок весит
    # десятки МБ HTML и вешает браузер (проверено: 7351 строк x 269 колонок
    # → 82 МБ, ~2 млн ячеек DOM) — поэтому показываем порциями.
    total_count = len(filtered)
    matched_count = sum(1 for p in filtered if p.article and p.article in active_supplier_articles)
    unmatched_count = total_count - matched_count
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    page_products = filtered[start:start + PAGE_SIZE]

    return templates.TemplateResponse(
        "catalog_view.html",
        {
            "request": request,
            "user": user,
            "products": page_products,
            "total_count": total_count,
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "active_supplier_articles": active_supplier_articles,
            "all_brands": all_brands,
            "all_tovar_types": all_tovar_types,
            "selected_brand": brand,
            "selected_tovar": tovar,
            "search_q": q,
            "core_columns": CORE_COLUMNS,
            "param_columns": param_columns,
            "last_update": last_update,
            "page": page,
            "total_pages": total_pages,
            "page_size": PAGE_SIZE,
        },
    )


@router.post("/catalog/refresh")
def catalog_refresh(background: BackgroundTasks, user: models.User = Depends(get_current_user)):
    """Кнопка "Обновить": скачивает выгрузку по постоянной ссылке и заменяет
    каталог. Работает в фоне (скачивание + разбор ~30 с) — страница опрашивает
    /catalog/refresh/status."""
    if not user:
        return JSONResponse({"error": "Требуется вход"}, status_code=401)
    if not catalog_sync.try_start():
        return JSONResponse(catalog_sync.get_state(), status_code=409)
    background.add_task(catalog_sync.run_refresh)
    return JSONResponse(catalog_sync.get_state(), status_code=202)


@router.get("/catalog/refresh/status")
def catalog_refresh_status(user: models.User = Depends(get_current_user)):
    if not user:
        return JSONResponse({"error": "Требуется вход"}, status_code=401)
    return JSONResponse(catalog_sync.get_state())
