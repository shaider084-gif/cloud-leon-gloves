"""Ссылки на прайсы и кнопка «Обновить» у поставщиков с несколькими онлайн-прайсами (FoxWeld).
Логика — в app/source_sync.py; карточка на странице поставщика — templates/_sync_card.html."""
import os
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models, source_sync
from ..database import get_db
from ..deps import get_current_user

router = APIRouter()

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_data")
SEEDS = {"foxweld": ("foxweld_base.xlsx", "FoxWeld прайс — все товары ГОТОВО редакт.xlsx")}


def _supplier_or_none(db: Session, supplier_id: int):
    s = db.get(models.Supplier, supplier_id)
    return s if s and s.slug in source_sync.SYNC_SLUGS else None


@router.post("/suppliers/{supplier_id}/sources")
def save_sources(
    supplier_id: int,
    url1: str = Form(""), url2: str = Form(""), url3: str = Form(""),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not _supplier_or_none(db, supplier_id):
        return RedirectResponse("/", status_code=303)
    source_sync.save_sources(db, supplier_id, [url1, url2, url3])
    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)


@router.post("/suppliers/{supplier_id}/sync", status_code=202)
def start_sync(
    supplier_id: int,
    background: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _supplier_or_none(db, supplier_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    if not source_sync.try_start(supplier_id):
        return JSONResponse(source_sync.get_state(supplier_id), status_code=202)
    background.add_task(source_sync.run_sync, supplier_id)
    return JSONResponse(source_sync.get_state(supplier_id), status_code=202)


@router.get("/suppliers/{supplier_id}/sync/status")
def sync_status(supplier_id: int, user: models.User = Depends(get_current_user)):
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    st = source_sync.get_state(supplier_id)
    res = st.get("result")
    if res:  # в ответ — не больше 300 позиций каждого списка
        st = {**st, "result": {**res, "new": res["new"][:300], "changed": res["changed"][:300]}}
    return JSONResponse(st)


@router.post("/suppliers/{supplier_id}/seed-base")
def seed_base(
    supplier_id: int,
    force: int = Query(0),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Однократная загрузка базы товаров из приложенной итоговой таблицы (если загрузок ещё нет)."""
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    supplier = _supplier_or_none(db, supplier_id)
    seed = SEEDS.get(supplier.slug) if supplier else None
    if not seed:
        return JSONResponse({"error": "not found"}, status_code=404)
    has_uploads = db.query(models.Upload).filter(models.Upload.supplier_id == supplier_id,
                                                 models.Upload.status == "done").count()
    if has_uploads and not force:
        return JSONResponse({"error": "у поставщика уже есть загрузки (force=1 — загрузить поверх)"}, status_code=409)
    from ..parsers.registry import get_parser
    from ..upload_utils import save_products
    path = os.path.join(SEED_DIR, seed[0])
    products = get_parser(supplier.slug).parse(path)
    save_products(db, supplier, seed[1], products)
    return JSONResponse({"loaded": len(products)})
