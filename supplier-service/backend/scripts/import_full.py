"""Импорт полного экспорта (см. export_full.py) в текущую БД (DATABASE_URL из
окружения) — запускается внутри контейнера backend на сервере, там
DATABASE_URL уже указывает на Postgres.

Поставщики находятся/создаются по slug (как и везде в проекте — id могут не
совпадать между базами). Загрузка пропускается, если у этого поставщика уже
есть загрузка с тем же именем файла и временем создания — это делает скрипт
безопасным для повторного/прерванного запуска (например, после сбоя на
середине переноса).

Запуск на сервере:
    docker compose exec backend python import_full.py /app/full_export.json
"""
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.market_prices import MarketPrice  # noqa: E402
from app import models  # noqa: E402


def num(v):
    return Decimal(v) if v is not None else None


def parse_dt(v):
    return datetime.fromisoformat(v) if v else None


def main(in_path: str):
    Base.metadata.create_all(bind=engine)
    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        total_products = 0
        for s_data in data["suppliers"]:
            supplier = db.query(models.Supplier).filter(models.Supplier.slug == s_data["slug"]).first()
            if supplier is None:
                supplier = models.Supplier(slug=s_data["slug"], name=s_data["name"])
                db.add(supplier)
                db.flush()
            supplier.name = s_data["name"]
            supplier.column_mapping = s_data["column_mapping"]
            supplier.markup_percent = num(s_data["markup_percent"])

            for u_data in s_data["uploads"]:
                created_at = parse_dt(u_data["created_at"])
                exists = db.query(models.Upload).filter(
                    models.Upload.supplier_id == supplier.id,
                    models.Upload.original_filename == u_data["original_filename"],
                    models.Upload.created_at == created_at,
                ).first()
                if exists:
                    print(f"  пропуск (уже есть): {s_data['slug']} / {u_data['original_filename']}")
                    continue

                upload = models.Upload(
                    supplier_id=supplier.id,
                    original_filename=u_data["original_filename"],
                    status=u_data["status"],
                    error_message=u_data["error_message"],
                    products_count=u_data["products_count"],
                    created_at=created_at,
                )
                db.add(upload)
                db.flush()

                for p_data in u_data["products"]:
                    db.add(models.Product(
                        upload_id=upload.id,
                        supplier_id=supplier.id,
                        article=p_data["article"], name=p_data["name"], brand=p_data["brand"],
                        category=p_data["category"], unit=p_data["unit"],
                        cost_price=num(p_data["cost_price"]), sale_price=num(p_data["sale_price"]),
                        image_url=p_data["image_url"],
                        external_product_id=p_data["external_product_id"],
                        external_variant_id=p_data["external_variant_id"],
                        description=p_data["description"],
                        attributes=p_data["attributes"],
                        created_at=parse_dt(p_data["created_at"]),
                    ))
                    total_products += 1
            db.commit()
        print(f"Поставщиков: {len(data['suppliers'])}, товаров: {total_products}")

        for m_data in data["market_prices"]:
            m = db.get(MarketPrice, m_data["article"]) or MarketPrice(article=m_data["article"])
            m.price1 = num(m_data["price1"])
            m.price2 = num(m_data["price2"])
            m.url1 = m_data["url1"]
            m.url2 = m_data["url2"]
            m.updated_at = parse_dt(m_data["updated_at"])
            db.merge(m)
        db.commit()
        print(f"Рыночных цен: {len(data['market_prices'])}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "full_export.json")
