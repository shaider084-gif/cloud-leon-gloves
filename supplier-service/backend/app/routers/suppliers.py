import os
from collections import Counter
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, Query
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..templating import templates
from ..parsers.registry import get_parser, PARSERS
from ..parsers.mapping import MappingParser, read_header_row
from ..schemas import CANONICAL_FIELDS, IGNORE_FIELD, ATTRIBUTE_PREFIX
from ..export import export_products_to_xlsx
from ..catalog_sync import attr_label, attr_sort_key, is_service_key
from ..default_params import DEFAULT_PARAM_KEYS
from ..catalog import (get_current_products, get_import_articles, get_import_variant_ids, get_import_name_articles,
                       IMPORT_SUPPLIER_SLUG, DEMO_SUPPLIER_SLUG)
from ..upload_utils import (
    save_upload_error as _save_upload_error,
    save_products as _save_products,
    validate_and_save_upload as _validate_and_save_upload,
    safe_tmp_path as _safe_tmp_path,
)
from .. import models

router = APIRouter()

ATTR_TARGET = "attr"  # значение <select>, означающее "свой параметр" (имя — в соседнем поле)
MAX_ATTR_NAME_LEN = 100


def _require_user(request: Request, user):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return None


def _canonical_field_keys():
    return {field for field, _ in CANONICAL_FIELDS}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    suppliers = db.query(models.Supplier).order_by(models.Supplier.name).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "user": user, "suppliers": suppliers, "available_parsers": PARSERS},
    )


@router.post("/suppliers")
def create_supplier(
    request: Request,
    name: str = Form(...),
    slug: str = Form(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    existing = db.query(models.Supplier).filter(models.Supplier.slug == slug).first()
    if not existing:
        db.add(models.Supplier(name=name, slug=slug))
        db.commit()
    return RedirectResponse("/", status_code=303)


TOVAR_ATTR_KEY = "Товар"
BASE_PARAM_KEYS = list(DEFAULT_PARAM_KEYS)  # столбцы по умолчанию у всех поставщиков (default_params.py)


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_detail(
    supplier_id: int,
    request: Request,
    tovar: str = Query(""),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)
    uploads = (
        db.query(models.Upload)
        .filter(models.Upload.supplier_id == supplier_id)
        .order_by(models.Upload.created_at.desc())
        .all()
    )
    custom_parser = get_parser(supplier.slug)
    all_products = get_current_products(db, supplier_id)

    # Если у поставщика в прайсе несколько видов продукции (например, у
    # "Спецзащита" — костюмы, перчатки, краги, ремни...) — даём отфильтровать
    # таблицу по "Параметр: Товар", как и на вкладке "Весь каталог".
    tovar_counts = Counter(
        (p.attributes or {}).get(TOVAR_ATTR_KEY) for p in all_products if (p.attributes or {}).get(TOVAR_ATTR_KEY)
    )
    # Сверху — вид продукции с наибольшим числом товаров.
    all_tovar_types = sorted(tovar_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    current_products = all_products
    if tovar:
        current_products = [p for p in current_products if (p.attributes or {}).get(TOVAR_ATTR_KEY) == tovar]

    # Колонки "Параметр: X": базовый шаблон (показывается всегда, даже пустой)
    # + любые другие параметры, заполненные хотя бы у одного товара выборки.
    used_keys = set()
    for p in current_products:
        for k, v in (p.attributes or {}).items():
            if v not in (None, ""):
                used_keys.add(k)
    used_keys.discard("Размер")  # у него своя колонка "Размеры"
    used_keys.discard("Бренд")  # бренд — фиксированный столбец «Параметр: Бренд»
    # Служебные столбцы файла магазина («Файл: URL», «Файл: Тег title» …) — в конце,
    # с подписью без префикса «Параметр:» (как на вкладке «Весь каталог»).
    service_keys = sorted((k for k in used_keys if is_service_key(k)), key=attr_sort_key)
    used_keys = {k for k in used_keys if not is_service_key(k)}
    param_keys = list(BASE_PARAM_KEYS) + sorted(used_keys - set(BASE_PARAM_KEYS)) + service_keys
    param_columns = [(k, attr_label(k)) for k in param_keys]

    # Подсветка "уже был в старом каталоге / это новая позиция" не имеет
    # смысла для самого импортированного каталога и для тестового поставщика.
    # У ФЭСТ в каталоге артикул есть не у всех — см. _catalog_matcher (артикул, «арт. N» в названии, ID варианта).
    show_match_highlight = supplier.slug not in (IMPORT_SUPPLIER_SLUG, DEMO_SUPPLIER_SLUG)
    in_catalog = _catalog_matcher(db, supplier) if show_match_highlight else (lambda p: False)
    matched_ids = {p.id for p in current_products if in_catalog(p)}
    matched_count = len(matched_ids)

    return templates.TemplateResponse(
        "supplier_detail.html",
        {
            "request": request,
            "user": user,
            "supplier": supplier,
            "uploads": uploads,
            "has_custom_parser": custom_parser is not None,
            "has_mapping": bool(supplier.column_mapping),
            "products": current_products,
            "show_match_highlight": show_match_highlight,
            "matched_ids": matched_ids,
            "matched_count": matched_count,
            "all_tovar_types": all_tovar_types,
            "selected_tovar": tovar,
            "param_keys": param_keys,
            "param_columns": param_columns,
            "latest_upload": uploads[0] if uploads else None,
            "previous_upload": uploads[1] if len(uploads) > 1 else None,
        },
    )


NAME_ARTICLE_SLUGS = {"fest"}
VARIANT_ID_MATCH_SLUGS = {"fest"}  # у этих поставщиков товар «на сайте» и если совпал «ID варианта» (артикул в каталоге есть не у всех)


def _catalog_matcher(db: Session, supplier):
    """Возвращает функцию «товар уже есть в Весь каталог (на сайте)»: по артикулу;
    для ФЭСТ ещё и по «арт. N» из названия и по ID варианта."""
    articles = get_import_articles(db)
    if supplier.slug in NAME_ARTICLE_SLUGS:  # у части товаров артикул в «Весь каталог» только в названии: «…, арт. 3738»
        articles = articles | get_import_name_articles(db)
    ids = get_import_variant_ids(db) if supplier.slug in VARIANT_ID_MATCH_SLUGS else set()

    def matched(p) -> bool:
        if p.article and p.article in articles:
            return True
        return bool(ids and p.external_variant_id and str(p.external_variant_id).strip() in ids)

    return matched


@router.get("/suppliers/{supplier_id}/export")
def export_supplier_catalog(
    supplier_id: int,
    request: Request,
    tovar: str = Query(""),
    green: int = Query(0),
    red: int = Query(0),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)
    products = get_current_products(db, supplier_id)
    if tovar:
        products = [p for p in products if (p.attributes or {}).get(TOVAR_ATTR_KEY) == tovar]
    if green or red:
        # «Зелёные» — товары, артикул которых уже есть в «Весь каталог», «красные» —
        # остальные (та же логика, что у подсветки строк на странице поставщика).
        in_catalog = _catalog_matcher(db, supplier)
        products = [p for p in products if in_catalog(p) == bool(green)]
    buf = export_products_to_xlsx(products)
    suffix = " - ".join(x for x in (tovar, "зелёные" if green else "", "красные" if red and not green else "") if x)
    filename = quote(f"{supplier.name} - каталог{' - ' + suffix if suffix else ''}.xlsx".replace('"', ""))
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/suppliers/{supplier_id}/upload")
def upload_price_file(
    supplier_id: int,
    request: Request,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)

    saved = _validate_and_save_upload(db, supplier, file)
    if saved is None:
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)
    tmp_path, original_name = saved

    # 1. Поставщик со сложным файлом — под него написан кастомный Python-парсер.
    custom_parser = get_parser(supplier.slug)
    if custom_parser:
        try:
            parsed_products = custom_parser.parse(tmp_path)
            _save_products(db, supplier, original_name, parsed_products, tmp_path=tmp_path)
        except Exception as exc:  # noqa: BLE001 — показываем ошибку парсинга пользователю
            db.rollback()
            _save_upload_error(db, supplier, original_name, str(exc))
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    # 2. У поставщика уже сохранён маппинг колонок (не первая загрузка) — это
    # сценарий 2.2 «Обновление цен» и он должен идти через сверку с текущим
    # каталогом (вкладка 2 / routers/price_updates.py), а не тихую перезапись.
    # UI ведёт такого поставщика на форму /price-update/preview напрямую
    # (см. supplier_detail.html), поэтому сюда этот случай попасть не должен —
    # но если всё же попал (например, прямой вызов API), не перезаписываем
    # каталог молча, а отправляем пользователя на сверку.
    if supplier.column_mapping:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    # 3. Первая загрузка для этого поставщика и без кастомного парсера —
    # временный файл НЕ удаляем, ведём пользователя на мастер маппинга колонок.
    tmp_filename = os.path.basename(tmp_path)
    return RedirectResponse(
        f"/suppliers/{supplier_id}/map?tmp={tmp_filename}&original={original_name}",
        status_code=303,
    )


@router.get("/suppliers/{supplier_id}/map", response_class=HTMLResponse)
def map_columns_form(
    supplier_id: int,
    request: Request,
    tmp: str,
    original: str = "",
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)

    tmp_path = _safe_tmp_path(tmp)
    if not os.path.isfile(tmp_path):
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    try:
        headers = read_header_row(tmp_path)
    except Exception as exc:  # noqa: BLE001
        _save_upload_error(db, supplier, original, f"Не удалось прочитать файл: {exc}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    return templates.TemplateResponse(
        "map_columns.html",
        {
            "request": request,
            "user": user,
            "supplier": supplier,
            "tmp": os.path.basename(tmp_path),
            "original": original,
            "headers": list(enumerate(headers)),
            "canonical_fields": CANONICAL_FIELDS,
            "default_markup": supplier.markup_percent or 30,
        },
    )


@router.post("/suppliers/{supplier_id}/map")
async def map_columns_submit(
    supplier_id: int,
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)

    form = await request.form()
    tmp = form.get("tmp", "")
    original = form.get("original", "")
    markup_raw = form.get("markup_percent", "30")

    tmp_path = _safe_tmp_path(tmp)
    if not os.path.isfile(tmp_path):
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    try:
        markup_percent = float(markup_raw)
    except (TypeError, ValueError):
        markup_percent = 30.0

    allowed_fields = _canonical_field_keys()
    column_mapping = {}
    idx = 0
    while f"header_{idx}" in form:
        header_text = str(form.get(f"header_{idx}", "")).strip().lower()
        target = str(form.get(f"target_{idx}", IGNORE_FIELD))
        if header_text:
            if target == ATTR_TARGET:
                attr_name = str(form.get(f"attr_name_{idx}", "")).strip()[:MAX_ATTR_NAME_LEN]
                if attr_name:
                    column_mapping[header_text] = f"{ATTRIBUTE_PREFIX}{attr_name}"
            elif target in allowed_fields:
                column_mapping[header_text] = target
            # иначе (IGNORE_FIELD или неизвестное значение) — колонка игнорируется
        idx += 1

    supplier.column_mapping = column_mapping
    supplier.markup_percent = markup_percent
    db.add(supplier)
    db.commit()
    db.refresh(supplier)

    try:
        parser = MappingParser(supplier.column_mapping, supplier.markup_percent)
        parsed_products = parser.parse(tmp_path)
        _save_products(db, supplier, original, parsed_products, tmp_path=tmp_path)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _save_upload_error(db, supplier, original, str(exc))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)


@router.post("/suppliers/{supplier_id}/info")
def update_supplier_info(
    supplier_id: int,
    request: Request,
    contacts: str = Form(""),
    pickup_addresses: str = Form(""),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)
    supplier.contacts = contacts.strip() or None
    supplier.pickup_addresses = pickup_addresses.strip() or None
    db.add(supplier)
    db.commit()
    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)


@router.get("/uploads/{upload_id}/original")
def download_original_upload(
    upload_id: int,
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Скачать именно присланный поставщиком файл (не пересобранный из БД) —
    доступно только для загрузок, сделанных после появления этой возможности."""
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    upload = db.get(models.Upload, upload_id)
    if not upload or not upload.file_path or not os.path.isfile(upload.file_path):
        return RedirectResponse("/", status_code=303)
    return FileResponse(
        upload.file_path,
        filename=upload.original_filename or os.path.basename(upload.file_path),
    )


@router.get("/uploads/{upload_id}/download")
def download_upload(
    upload_id: int,
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_user(request, user)
    if redirect:
        return redirect
    upload = db.get(models.Upload, upload_id)
    if not upload:
        return RedirectResponse("/", status_code=303)

    buf = export_products_to_xlsx(upload.products)
    filename = f"upload_{upload.id}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
