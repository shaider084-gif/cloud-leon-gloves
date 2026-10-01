"""Заменяет фото товаров ООО ПКФ "Спецзащита" на обработанные AI-фото из
Торгрин-про (генератор фото, https://торгрин-про.рф/user/ai_photo_generator_preview/).
Для артикулов, которых нет в источнике (не сгенерировано или инструмент их
вообще не знает) — image_url очищается (по явному указанию пользователя:
"если какие-то не найдёшь, оставь пустую строку").

Несколько фото на один артикул — через пробел (как и для прочих товаров
каталога). У одного артикула бывает несколько строк каталога (напр. ПП7,
КС04ОТ — два разных реальных товара под одним кодом) — фото ставится на все.

Запуск:
    python update_spetszaschita_photos.py <путь_к_photos.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402


def main(photos_path: str):
    with open(photos_path, encoding="utf-8") as f:
        photos = json.load(f)

    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == "pkf_spetczaschita").first()
        if not supplier:
            print("Поставщик не найден")
            return
        products = db.query(models.Product).filter(models.Product.supplier_id == supplier.id).all()

        matched = cleared = 0
        for p in products:
            urls = photos.get(p.article) if p.article else None
            if urls:
                p.image_url = " ".join(urls)
                matched += 1
            else:
                p.image_url = None
                cleared += 1
        db.commit()
        print(f"Товаров всего: {len(products)}; с фото: {matched}; очищено (не нашли): {cleared}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1])
