"""Разово прикрепить файл-исходник к уже существующей (и проверенной) загрузке
поставщика — для случаев вроде "Спецзащита, 01.08.2026": файл сверили построчно
с каталогом вручную (расхождений не нашли), поэтому заново прогонять через
парсер и пересохранять товары не нужно — только прикрепить сам файл, чтобы он
был виден и скачивался на странице поставщика.

Запуск (локально или docker compose exec backend python ...):
    python attach_original_file.py <slug_поставщика> <путь_к_файлу>
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.upload_utils import ORIGINALS_SUBDIR  # noqa: E402


def main(slug: str, src_path: str):
    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == slug).first()
        if not supplier:
            print(f"Поставщик {slug!r} не найден")
            return
        upload = (
            db.query(models.Upload)
            .filter(models.Upload.supplier_id == supplier.id, models.Upload.status == "done")
            .order_by(models.Upload.created_at.desc())
            .first()
        )
        if not upload:
            print("Нет ни одной успешной загрузки — прикреплять некуда")
            return

        originals_dir = os.path.join(settings.upload_dir, ORIGINALS_SUBDIR)
        os.makedirs(originals_dir, exist_ok=True)
        ext = os.path.splitext(src_path)[1]
        dest_path = os.path.join(originals_dir, f"{upload.id}{ext}")
        shutil.copy2(src_path, dest_path)
        upload.file_path = dest_path
        db.commit()
        print(f"Прикреплено к загрузке #{upload.id} ({upload.original_filename}, {upload.created_at}): {dest_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
