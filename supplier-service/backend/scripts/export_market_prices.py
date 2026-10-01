"""Экспорт собранных рыночных цен (таблица market_prices) в JSON — для переноса
с локальной SQLite-базы на серверную Postgres (см. import_market_prices.py).

Запуск локально (из backend/, с теми же переменными окружения, что обычно):
    py -3 scripts/export_market_prices.py market_prices.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.market_prices import MarketPrice  # noqa: E402


def main(out_path: str):
    db = SessionLocal()
    try:
        rows = db.query(MarketPrice).all()
        data = [
            {
                "article": r.article,
                "price1": str(r.price1) if r.price1 is not None else None,
                "price2": str(r.price2) if r.price2 is not None else None,
                "url1": r.url1,
                "url2": r.url2,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Выгружено {len(data)} строк в {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "market_prices.json")
