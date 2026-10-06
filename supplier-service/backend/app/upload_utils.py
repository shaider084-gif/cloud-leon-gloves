"""Общие для routers/suppliers.py и routers/price_updates.py куски: валидация
и сохранение загружаемого файла на диск, безопасное построение пути к temp-файлу,
сохранение распарсенных товаров в БД. Вынесено, чтобы не дублировать между
обычной загрузкой и сценарием "Обновление цен" (preview/apply)."""
import os
import uuid
from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .schemas import ProductIn

ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf"}
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25MB — совпадает с client_max_body_size в nginx


def save_upload_error(db: Session, supplier: models.Supplier, filename: str, message: str) -> None:
    upload = models.Upload(
        supplier_id=supplier.id,
        original_filename=filename,
        status="error",
        error_message=message,
    )
    db.add(upload)
    db.commit()


ORIGINALS_SUBDIR = "originals"  # внутри settings.upload_dir — постоянное хранилище исходников прайсов


def save_products(
    db: Session,
    supplier: models.Supplier,
    filename: str,
    parsed_products: List[ProductIn],
    tmp_path: Optional[str] = None,
) -> None:
    """tmp_path (если передан) — временный файл исходника (см.
    validate_and_save_upload); переносится в постоянное хранилище и
    привязывается к загрузке, чтобы на странице поставщика можно было
    скачать именно присланный файл, а не только распарсенные товары."""
    upload = models.Upload(supplier_id=supplier.id, original_filename=filename)
    upload.products_count = len(parsed_products)
    db.add(upload)
    db.flush()  # получить upload.id

    if tmp_path and os.path.isfile(tmp_path):
        originals_dir = os.path.join(settings.upload_dir, ORIGINALS_SUBDIR)
        os.makedirs(originals_dir, exist_ok=True)
        extension = os.path.splitext(tmp_path)[1]
        dest_path = os.path.join(originals_dir, f"{upload.id}{extension}")
        os.replace(tmp_path, dest_path)
        upload.file_path = dest_path

    from .default_params import SKIP_SLUGS, apply_defaults  # столбцы по умолчанию у всех поставщиков

    for p in parsed_products:
        if supplier.slug not in SKIP_SLUGS:
            p.attributes = apply_defaults(p.attributes, supplier, p.article)
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
                external_product_id=p.external_product_id,
                external_variant_id=p.external_variant_id,
                description=p.description,
                attributes=p.attributes,
            )
        )
    upload.status = "done"
    db.commit()


def validate_and_save_upload(
    db: Session, supplier: models.Supplier, file: UploadFile
) -> Optional[Tuple[str, str]]:
    """Общая валидация (расширение/размер) + сохранение на диск под uuid-именем.
    Возвращает (tmp_path, original_name) либо None, если записал Upload с ошибкой
    и дальше обрабатывать нечего."""
    original_name = file.filename or ""
    extension = os.path.splitext(original_name)[1].lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        save_upload_error(
            db, supplier, original_name,
            f"Недопустимый тип файла: {extension or '(без расширения)'}",
        )
        return None

    raw_bytes = file.file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        save_upload_error(db, supplier, original_name, "Файл слишком большой (максимум 25MB).")
        return None

    os.makedirs(settings.upload_dir, exist_ok=True)
    # Имя файла на диске не зависит от исходного (uuid) — исходное имя нигде
    # не используется как путь, поэтому path traversal через file.filename исключён.
    tmp_path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{extension}")
    with open(tmp_path, "wb") as f:
        f.write(raw_bytes)

    return tmp_path, original_name


def safe_tmp_path(tmp_filename: str) -> str:
    """Разрешает только простое uuid-имя файла внутри upload_dir — защита от
    path traversal через параметр tmp (например, '../../etc/passwd')."""
    safe_name = os.path.basename(tmp_filename)
    return os.path.join(settings.upload_dir, safe_name)
