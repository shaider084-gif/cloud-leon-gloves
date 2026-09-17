import os
import uuid

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..templating import templates
from ..config import settings
from ..parsers.registry import get_parser, PARSERS
from ..export import export_products_to_xlsx
from .. import models

router = APIRouter()


def _require_user(request: Request, user):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return None


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


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_detail(
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
    uploads = (
        db.query(models.Upload)
        .filter(models.Upload.supplier_id == supplier_id)
        .order_by(models.Upload.created_at.desc())
        .all()
    )
    parser = get_parser(supplier.slug)
    return templates.TemplateResponse(
        "supplier_detail.html",
        {
            "request": request,
            "user": user,
            "supplier": supplier,
            "uploads": uploads,
            "parser_available": parser is not None,
        },
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

    parser = get_parser(supplier.slug)
    if not parser:
        upload = models.Upload(
            supplier_id=supplier.id,
            original_filename=file.filename,
            status="error",
            error_message=f"Для поставщика '{supplier.slug}' ещё не написан парсер.",
        )
        db.add(upload)
        db.commit()
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    os.makedirs(settings.upload_dir, exist_ok=True)
    tmp_path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}_{file.filename}")
    with open(tmp_path, "wb") as f:
        f.write(file.file.read())

    upload = models.Upload(supplier_id=supplier.id, original_filename=file.filename)

    try:
        parsed_products = parser.parse(tmp_path)
        upload.products_count = len(parsed_products)
        db.add(upload)
        db.flush()  # получить upload.id

        for p in parsed_products:
            db.add(
                models.Product(
                    upload_id=upload.id,
                    supplier_id=supplier.id,
                    article=p.article,
                    name=p.name,
                    brand=p.brand,
                    category=p.category,
                    unit=p.unit,
                    cost_price=p.cost_price,
                    sale_price=p.sale_price,
                    image_url=p.image_url,
                    attributes=p.attributes,
                )
            )
        upload.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — показываем ошибку парсинга пользователю
        db.rollback()
        upload = models.Upload(
            supplier_id=supplier.id,
            original_filename=file.filename,
            status="error",
            error_message=str(exc),
        )
        db.add(upload)
        db.commit()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)


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
