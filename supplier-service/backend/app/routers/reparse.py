"""«Пересобрать из сохранённого прайса»: берёт исходник последней успешной
загрузки поставщика, заново прогоняет его через ТЕКУЩИЙ парсер и сохраняет
как новую загрузку. Нужно, когда парсер/справочники обновились (новые фото,
описания, реестровые номера), а сам прайс не менялся — файл заново грузить
не надо."""
import os
import shutil
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..parsers.registry import resolve_parser
from ..upload_utils import save_products, save_upload_error

router = APIRouter()


@router.post("/suppliers/{supplier_id}/reparse")
def reparse_latest_upload(
    supplier_id: int,
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    supplier = db.get(models.Supplier, supplier_id)
    if not supplier:
        return RedirectResponse("/", status_code=303)

    last = (
        db.query(models.Upload)
        .filter(models.Upload.supplier_id == supplier_id, models.Upload.status == "done")
        .order_by(models.Upload.created_at.desc(), models.Upload.id.desc())
        .first()
    )
    if not last or not last.file_path or not os.path.isfile(last.file_path):
        return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)

    os.makedirs(settings.upload_dir, exist_ok=True)
    tmp_path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{os.path.splitext(last.file_path)[1]}")
    shutil.copyfile(last.file_path, tmp_path)
    try:
        parsed = resolve_parser(supplier).parse(tmp_path)
        save_products(db, supplier, last.original_filename or "", parsed, tmp_path=tmp_path)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        save_upload_error(db, supplier, last.original_filename or "", str(exc))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)
