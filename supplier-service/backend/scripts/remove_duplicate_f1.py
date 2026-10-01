"""Удаляет дублирующую строку Ф1 ("бесшовный", старая цена) — оставляем только
исправленную ("4 детали кроя", минимальная цена по решению пользователя).

Запуск на сервере:
    docker compose exec backend python remove_duplicate_f1.py
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
        rows = db.query(models.Product).filter(
            models.Product.supplier_id == supplier.id, models.Product.article == "Ф1"
        ).all()
        stale = [r for r in rows if "бесшовный" in (r.name or "")]
        if not stale:
            print("дубль не найден — нечего удалять")
            return
        for r in stale:
            print("удаляю:", r.id, r.name, r.cost_price)
            db.delete(r)
        db.commit()
        print("готово")
    finally:
        db.close()


if __name__ == "__main__":
    main()
