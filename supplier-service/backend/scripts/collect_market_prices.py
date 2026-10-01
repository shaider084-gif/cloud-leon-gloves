"""Сбор рыночных цен для вкладки "Сравнение цен".

Использование (из backend/, с теми же переменными окружения, что и сервер):
    py -3 scripts/collect_market_prices.py 5                  # первые 5 товаров каталога
    py -3 scripts/collect_market_prices.py --brand "Jeta Safety"   # весь бренд

Размеры одной модели (JPC-65-S/M/L) имеют одинаковое название, поэтому ищем
ОДИН раз на уникальное название, а результат записываем всем артикулам с ним.
Товары обрабатываются в несколько потоков; ход работы печатается построчно.
"""
import argparse
from datetime import datetime
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.catalog import get_import_products  # noqa: E402
from app.market_prices import MarketPrice  # noqa: E402
from app.market_search import find_prices  # noqa: E402

PARALLEL = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, help="первые N товаров каталога")
    ap.add_argument("--brand", help="все товары бренда")
    ap.add_argument("--updated-before", help="только товары, чья рыночная цена не обновлялась после "
                    "этого момента (UTC, ISO, напр. 2026-09-25T06:40) или ещё не собиралась — "
                    "чтобы дособрать упавшие при сбое")
    args = ap.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    t0 = time.time()
    try:
        products = get_import_products(db)
        if args.brand:
            products = [p for p in products if p.brand == args.brand]
        elif args.n:
            products = products[:args.n]

        if args.updated_before:
            cutoff = datetime.fromisoformat(args.updated_before)
            fresh = {m.article for m in db.query(MarketPrice).all() if m.updated_at and m.updated_at >= cutoff}
            products = [p for p in products if p.article not in fresh]

        # уникальное название -> (бренд, все артикулы с ним)
        groups = defaultdict(lambda: ["", []])
        for p in products:
            if p.article:
                groups[p.name][0] = p.brand or ""
                groups[p.name][1].append(p.article)
        print(f"Товаров {len(products)}, уникальных названий {len(groups)}", flush=True)

        done = failed = 0
        with ThreadPoolExecutor(PARALLEL) as ex:
            futures = {ex.submit(find_prices, name, brand): (name, articles)
                       for name, (brand, articles) in groups.items()}
            for fut in as_completed(futures):
                name, articles = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:  # сбой поиска — товар остаётся без изменений
                    failed += 1
                    print(f"ОШИБКА {name[:50]}: {e}", flush=True)
                    continue
                top = res["top"] + [(None, None)] * (2 - len(res["top"]))
                for article in articles:
                    row = db.get(MarketPrice, article) or MarketPrice(article=article)
                    row.price1, row.url1 = top[0]
                    row.price2, row.url2 = top[1]
                    db.merge(row)
                db.commit()
                done += 1
                print(f"[{done + failed}/{len(groups)}] {name[:55]} | ссылок {res['urls']}, "
                      f"с ценой {res['with_price']}, {res['seconds']} с -> {top[0][0]} / {top[1][0]}",
                      flush=True)
    finally:
        db.close()
    print(f"Готово: {done} названий, ошибок {failed}, {round(time.time() - t0)} с", flush=True)


if __name__ == "__main__":
    main()
