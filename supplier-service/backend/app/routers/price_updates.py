"""Вкладка 2 «Обновление цен»: сценарий 2.2 из плана — для поставщика, у
которого уже есть сохранённый маппинг колонок, загружаем новый прайс,
сверяем его с текущим каталогом (последняя успешная загрузка) по Артикулу
и показываем предпросмотр (🟢 совпало / 🔴 новое / 🟠 пропало) перед тем,
как реально сохранить его как новую загрузку."""
import os

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..templating import templates
from ..parsers.registry import get_parser, resolve_parser
from ..upload_utils import (
    save_upload_error as _save_upload_error,
    save_products as _save_products,
    validate_and_save_upload as _validate_and_save_upload,
    safe_tmp_path as _safe_tmp_path,
)
from ..catalog import get_current_products, diff_by_article
from .. import models

router = APIRouter()


def _require_user(request: Request, user):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return None


@router.post("/suppliers/{supplier_id}/price-update/preview")
def price_update_preview(
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

    # Без сохранённого маппинга колонок и без кастомного парсера сверка
    # невозможна — у нас нет способа понять, что означают колонки файла
    # (это сценарий вкладки 3).
    if not supplier.column_mapping and not get_parser(supplier.slug):
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    saved = _validate_and_save_upload(db, supplier, file)
    if saved is None:
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)
    tmp_path, original_name = saved

    try:
        parser = resolve_parser(supplier)
        parsed_products = parser.parse(tmp_path)
    except Exception as exc:  # noqa: BLE001 — показываем ошибку парсинга пользователю
        _save_upload_error(db, supplier, original_name, str(exc))
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    current_products = get_current_products(db, supplier_id)
    diff = diff_by_article(current_products, parsed_products)

    # Временный файл НЕ удаляем — понадобится на шаге "Применить", чтобы не
    # заставлять пользователя загружать файл заново.
    return templates.TemplateResponse(
        "price_update_preview.html",
        {
            "request": request,
            "user": user,
            "supplier": supplier,
            "tmp": os.path.basename(tmp_path),
            "original": original_name,
            "diff": diff,
            "counts": diff.counts,
        },
    )


@router.post("/suppliers/{supplier_id}/price-update/apply")
def price_update_apply(
    supplier_id: int,
    request: Request,
    tmp: str = Form(""),
    original: str = Form(""),
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
        parser = resolve_parser(supplier)
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
