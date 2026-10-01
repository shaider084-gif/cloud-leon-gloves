"""Импорт рыночных цен из JSON (см. export_market_prices.py) в текущую БД
(DATABASE_URL из окружения) — на сервере запускается внутри контейнера backend,
там DATABASE_URL уже указывает на Postgres. Образ собирается только из app/
(см. Dockerfile), поэтому сам скрипт и файл с данными копируются в контейнер
отдельно, а не через git pull.

Запуск на сервере (из supplier-service/, после git pull):
    docker compose cp backend/scripts/import_market_prices.py backend:/app/import_market_prices.py
    docker compose cp market_prices.json backend:/app/market_prices.json
    docker compose exec backend python import_market_prices.py /app/market_prices.json
"""
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.market_prices import MarketPrice  # noqa: E402


def main(in_path: str):
    Base.metadata.create_all(bind=engine)
    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        for row in data:
            m = db.get(MarketPrice, row["article"]) or MarketPrice(article=row["article"])
            m.price1 = Decimal(row["price1"]) if row["price1"] is not None else None
            m.price2 = Decimal(row["price2"]) if row["price2"] is not None else None
            m.url1 = row["url1"]
            m.url2 = row["url2"]
            m.updated_at = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
            db.merge(m)
        db.commit()
    finally:
        db.close()
    print(f"Загружено {len(data)} строк")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "market_prices.json")
