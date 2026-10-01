"""Полный экспорт каталога (Suppliers -> Uploads -> Products) + рыночных цен
в один JSON-файл — для переноса с локальной SQLite-базы на серверную Postgres
(см. import_full.py). Таблица users НЕ экспортируется (логины на проде — свои,
не трогаем).

Запуск локально (из backend/, с теми же переменными окружения, что обычно):
    py -3 scripts/export_full.py full_export.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.market_prices import MarketPrice  # noqa: E402
from app import models  # noqa: E402


def dec(v):
    return str(v) if v is not None else None


def dt(v):
    return v.isoformat() if v is not None else None


def main(out_path: str):
    db = SessionLocal()
    try:
        suppliers = []
        for s in db.query(models.Supplier).all():
            uploads = []
            for u in db.query(models.Upload).filter(models.Upload.supplier_id == s.id).all():
                products = []
                for p in db.query(models.Product).filter(models.Product.upload_id == u.id).all():
                    products.append({
                        "article": p.article, "name": p.name, "brand": p.brand,
                        "category": p.category, "unit": p.unit,
                        "cost_price": dec(p.cost_price), "sale_price": dec(p.sale_price),
                        "image_url": p.image_url,
                        "external_product_id": p.external_product_id,
                        "external_variant_id": p.external_variant_id,
                        "description": p.description,
                        "attributes": p.attributes,
                        "created_at": dt(p.created_at),
                    })
                uploads.append({
                    "original_filename": u.original_filename, "status": u.status,
                    "error_message": u.error_message, "products_count": u.products_count,
                    "created_at": dt(u.created_at), "products": products,
                })
            suppliers.append({
                "slug": s.slug, "name": s.name,
                "column_mapping": s.column_mapping,
                "markup_percent": dec(s.markup_percent),
                "created_at": dt(s.created_at), "uploads": uploads,
            })

        market_prices = [
            {
                "article": m.article, "price1": dec(m.price1), "price2": dec(m.price2),
                "url1": m.url1, "url2": m.url2, "updated_at": dt(m.updated_at),
            }
            for m in db.query(MarketPrice).all()
        ]
    finally:
        db.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"suppliers": suppliers, "market_prices": market_prices}, f, ensure_ascii=False)

    total_products = sum(len(u["products"]) for s in suppliers for u in s["uploads"])
    print(f"Поставщиков: {len(suppliers)}, товаров: {total_products}, рыночных цен: {len(market_prices)}")
    print(f"Сохранено в {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "full_export.json")
