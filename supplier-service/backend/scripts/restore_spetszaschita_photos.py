"""Откатывает фото Спецзащиты ТОЛЬКО у товаров, у которых сейчас пусто
(image_url is None/""), на значения из старой полной выгрузки (full_export.json,
сделанной до замены фото на AI-фото из Торгрин-про). Товары, которым уже
подставили новое AI-фото, не трогает — пользователь попросил оставить их как есть.

Запуск:
    python restore_spetszaschita_photos.py <путь_к_full_export.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402


def main(export_path: str):
    with open(export_path, encoding="utf-8") as f:
        data = json.load(f)

    # Старые фото по (артикул, название) — на случай совпадающих артикулов
    # (ПП7, КС04ОТ — два разных товара под одним кодом) совпадение только по
    # артикулу перепутало бы их местами.
    old_photos = {}
    for s in data["suppliers"]:
        if s["slug"] != "pkf_spetczaschita":
            continue
        for u in s["uploads"]:
            for p in u["products"]:
                if p["image_url"]:
                    old_photos[(p["article"], p["name"])] = p["image_url"]

    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == "pkf_spetczaschita").first()
        products = db.query(models.Product).filter(models.Product.supplier_id == supplier.id).all()

        restored = still_empty = kept_new = 0
        for p in products:
            if p.image_url:
                kept_new += 1
                continue
            old = old_photos.get((p.article, p.name))
            if old:
                p.image_url = old
                restored += 1
            else:
                still_empty += 1
                print(f"  не нашёл старое фото для: {p.article!r} | {p.name}")
        db.commit()
        print(f"Оставлено новое AI-фото: {kept_new}; восстановлено старое: {restored}; "
              f"осталось пустых (старого фото тоже не было): {still_empty}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1])
