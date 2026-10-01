"""Импорт большого мульти-брендового каталога (экспорт из текущего магазина
защита-про.рф, формат InSales: UTF-16, TAB-разделитель, ~309 колонок) во
вкладку "Весь каталог".

Использование (из backend/, с теми же переменными окружения, что и сервер):
    py -3 scripts/import_master_catalog.py "<путь_к_csv>"

Создаёт (если его ещё нет) служебного поставщика со slug "catalog-import" и
складывает туда все строки одной загрузкой (Upload) — вкладка "Весь каталог"
(routers/catalog_view.py, catalog.get_all_current_products) агрегирует товары
по ВСЕМ поставщикам, так что эти данные автоматически попадут туда наравне
с GWARD и другими.

Убраны служебные поля магазина (URL, мета-теги, штрих-код, остаток, раздел
сайта, видимость на витрине и т.п. — см. переписку с пользователем, сессия
2026-09-23) — оставлены только ID товара/варианта, артикул, название,
описание, изображения, цены, бренд (Параметр: Бренд) и параметры
(Параметр: *).

Дедупликация — по (Артикул, Название): один и тот же артикул в этом экспорте
иногда ошибочно расшарен между СОВСЕМ разными товарами (см. пример
"886356208" — 9 разных комбинезонов под одним кодом), поэтому дедуп только
по артикулу небезопасен. Товары без артикула дедуплицируются по названию.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal, engine, Base  # noqa: E402
from app import models  # noqa: E402

PARAM_PREFIX = "Параметр: "


def _parse_price(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = raw.replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _join_images(*parts: str) -> str:
    urls = []
    for part in parts:
        for url in (part or "").split():
            if url and url not in urls:
                urls.append(url)
    return " ".join(urls)


def import_catalog(
    csv_path: str,
    supplier_name: str = "Общий каталог (импорт)",
    supplier_slug: str = "catalog-import",
) -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        supplier = db.query(models.Supplier).filter(models.Supplier.slug == supplier_slug).first()
        if not supplier:
            supplier = models.Supplier(name=supplier_name, slug=supplier_slug)
            db.add(supplier)
            db.flush()

        rows_data = []
        total_rows = 0
        with open(csv_path, "r", encoding="utf-16", newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader)
            idx = {name: i for i, name in enumerate(header)}
            param_cols = [
                (i, name[len(PARAM_PREFIX):])
                for i, name in enumerate(header)
                if name.startswith(PARAM_PREFIX)
            ]

            def get(row, key):
                i = idx.get(key)
                if i is None or i >= len(row):
                    return ""
                return row[i].strip()

            seen = set()
            for row in reader:
                total_rows += 1
                article = get(row, "Артикул")
                name = get(row, "Название товара или услуги")
                if not name:
                    continue
                key = (article.lower(), name.lower()) if article else (None, name.lower())
                if key in seen:
                    continue
                seen.add(key)

                attributes = {}
                for col_idx, attr_name in param_cols:
                    if col_idx < len(row):
                        val = row[col_idx].strip()
                        if val:
                            attributes[attr_name] = val

                rows_data.append(
                    dict(
                        article=article or None,
                        name=name,
                        brand=get(row, "Параметр: Бренд") or None,
                        category=None,
                        unit=None,
                        cost_price=_parse_price(get(row, "Себестоимость")),
                        sale_price=_parse_price(get(row, "Цена продажи")),
                        image_url=_join_images(get(row, "Изображения"), get(row, "Изображения варианта")) or None,
                        external_product_id=get(row, "ID товара") or None,
                        external_variant_id=get(row, "ID варианта") or None,
                        description=get(row, "Описание") or None,
                        attributes=attributes,
                    )
                )

        upload = models.Upload(
            supplier_id=supplier.id,
            original_filename=os.path.basename(csv_path),
            status="done",
        )
        db.add(upload)
        db.flush()

        for data in rows_data:
            db.add(models.Product(upload_id=upload.id, supplier_id=supplier.id, **data))

        upload.products_count = len(rows_data)
        db.commit()
        print(f"raw_rows={total_rows} imported={len(rows_data)} upload_id={upload.id} supplier_id={supplier.id}")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: py -3 scripts/import_master_catalog.py "<path_to_csv>"')
        sys.exit(1)
    import_catalog(sys.argv[1])
