"""Перепарсивает уже сохранённый исходный файл загрузки (upload.file_path)
новым парсером TechnoaviaParser и заменяет её товары — для случая, когда
загрузка изначально прошла через неверное сопоставление колонок (визуальный
маппинг не поддерживает прямую запись sale_price/image_url/description) и
заполнила только Артикул и Название.

Сама загрузка (дата, имя файла, исходник для скачивания) не трогается —
меняются только Product-строки, привязанные к ней.

Запуск:
    python fix_technoavia_products.py <upload_id>
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402
from app.parsers.technoavia import TechnoaviaParser  # noqa: E402


def main(upload_id: int):
    db = SessionLocal()
    try:
        upload = db.get(models.Upload, upload_id)
        if not upload:
            print("Загрузка не найдена")
            return
        if not upload.file_path or not os.path.isfile(upload.file_path):
            print("Исходный файл не найден на диске:", upload.file_path)
            return

        parsed = TechnoaviaParser().parse(upload.file_path)
        if not parsed:
            print("Парсер не вернул ни одного товара — ничего не меняю")
            return

        old_count = db.query(models.Product).filter(models.Product.upload_id == upload_id).delete()
        for p in parsed:
            db.add(models.Product(
                upload_id=upload.id,
                supplier_id=upload.supplier_id,
                article=p.article, name=p.name, brand=p.brand,
                category=p.category, unit=p.unit,
                cost_price=p.cost_price, sale_price=p.sale_price,
                image_url=p.image_url,
                external_product_id=p.external_product_id,
                external_variant_id=p.external_variant_id,
                description=p.description,
                attributes=p.attributes,
            ))
        upload.products_count = len(parsed)
        db.commit()
        print(f"Было товаров: {old_count}; стало: {len(parsed)}")
    finally:
        db.close()


if __name__ == "__main__":
    main(int(sys.argv[1]))
