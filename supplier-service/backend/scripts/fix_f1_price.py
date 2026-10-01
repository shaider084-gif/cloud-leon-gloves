"""Разовая точечная правка: Ф1 (ООО ПКФ "Спецзащита") теперь представляет
вариант "4 детали кроя" (минимальная из двух цен прайса 01.08.2026, решение
пользователя при сверке прайса) вместо прежнего "бесшовный".

Запуск на сервере:
    docker compose exec backend python fix_f1_price.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402


def main():
    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == "pkf_spetczaschita").first()
        p = db.query(models.Product).filter(
            models.Product.supplier_id == supplier.id, models.Product.article == "Ф1"
        ).first()
        if not p:
            print("Ф1 не найден — ничего не делаю")
            return
        print("было:", p.name, p.cost_price, p.sale_price)
        p.name = 'Фартук спилковый (4 детали кроя), арт. Ф1'
        p.cost_price = 1260
        p.sale_price = 1638
        db.commit()
        print("стало:", p.name, p.cost_price, p.sale_price)
    finally:
        db.close()


if __name__ == "__main__":
    main()
