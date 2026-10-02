"""Обновление вкладки "Весь каталог" из постоянной ссылки на выгрузку магазина.

Выгрузка — экспорт InSales в формате .xls (защита-про.рф: ~9,5 тыс. строк,
311 столбцов). Ссылка постоянная, файл на ней актуализируется магазином;
кнопка "Обновить" на странице /catalog скачивает его заново и заменяет
содержимое каталога.

Что переносится: ВСЕ строки (одна строка файла = один вариант товара, без
дедупликации — ID варианта в файле уникален) и ВСЕ столбцы. Основные поля
кладутся в колонки Product (артикул, название, бренд, цены, ID товара/варианта,
описание, изображения, ед. изм.), остальное — в Product.attributes:
  * "Параметр: X"  -> ключ "X" (как и раньше, фильтры по "Товар" и т.п.);
  * остальные столбцы (URL, мета-теги, "Свойство: …", "Дополнительное поле: …")
    -> ключ "Файл: <исходный заголовок>" (префикс убирает catalog_view).
Пустые ячейки не сохраняются.

Замена атомарная: новая загрузка создаётся, старые удаляются в одной
транзакции. Если скачивание или разбор упали — старые данные остаются.
"""
import os
import tempfile
import threading
from datetime import datetime
from typing import Dict, List, Optional

import httpx
import xlrd
from sqlalchemy.orm import Session
from xlrd import sheet as _xlrd_sheet

from . import models
from .catalog import IMPORT_SUPPLIER_SLUG
from .database import SessionLocal

SOURCE_URL = os.environ.get(
    "CATALOG_SOURCE_URL",
    "https://xn----7sba0ag2bgio2e.xn--p1ai/marketplace/4677177.xls",
)
SUPPLIER_NAME = "Общий каталог (импорт)"
PARAM_PREFIX = "Параметр: "
CHUNK = 500

# Основные столбцы файла -> поля Product.
COL_ARTICLE = "Артикул"
COL_NAME = "Название товара или услуги"
COL_BRAND = "Параметр: Бренд"
COL_COST = "Себестоимость"
COL_SALE = "Цена продажи"
COL_PRODUCT_ID = "ID товара"
COL_VARIANT_ID = "ID варианта"
COL_DESCRIPTION = "Описание"
COL_IMAGES = "Изображения"
COL_UNIT = "Единица измерения"
CORE_SOURCE_COLUMNS = {
    COL_ARTICLE, COL_NAME, COL_COST, COL_SALE, COL_PRODUCT_ID,
    COL_VARIANT_ID, COL_DESCRIPTION, COL_IMAGES, COL_UNIT,
}

# Порядок служебных (не "Параметр: …") столбцов в таблице — как в файле.
EXTRA_COLUMNS_ORDER = [
    "Название товара в URL", "URL", "Дополнительное описание", "Видимость на витрине",
    "Применять скидки", "Тег title", "Мета-тег keywords", "Мета-тег description",
    "Размещение на сайте", "Весовой коэффициент", "Валюта склада", "НДС", "Габариты",
    "Ссылка на видео", "Средний рейтинг", "Количество отзывов",
    "Свойство: Размер", "Свойство: Размер перчаток", "Свойство: Рост", "Свойство: Вариант",
    "Свойство: Размер одежды", "Свойство: Ростовка", "Свойство: Цвет", "Свойство: Объем",
    "Свойство: Плотность ткани", "Свойство: С хранения", "Свойство: Кол-во нитей",
    "Штрих-код", "Габариты варианта", "Старая цена", "Остаток", "Вес",
    "Изображения варианта", "Тип цен: Розничная цена",
    "Дополнительное поле: ID Большая Птица 6ee637b0b0ec", "Дополнительное поле: ID 1С d4c424157dbc",
    "Дополнительное поле: SEO-ссылка в хлебных крошках", "Дополнительное поле: FAQ",
    "Дополнительное поле: Документы", "Дополнительное поле: H1",
    "Дополнительное поле: Краткие характеристики",
]
_EXTRA_RANK = {name: i for i, name in enumerate(EXTRA_COLUMNS_ORDER)}
# Ключ в attributes для столбцов, не являющихся "Параметр: …". Префикс нужен,
# потому что имена совпадают ("Параметр: Вес" и служебный "Вес") и без него
# одно значение затирало бы другое.
SERVICE_PREFIX = "Файл: "

# Лимиты длины колонок Product (на Postgres превышение валит вставку целиком).
_LIMITS = {"article": 255, "name": 1000, "brand": 255, "unit": 64,
           "external_product_id": 255, "external_variant_id": 255}


def is_service_key(key: str) -> bool:
    """True, если ключ attributes — служебный столбец файла (не "Параметр: X")."""
    return key.startswith(SERVICE_PREFIX)


def attr_label(key: str) -> str:
    """Подпись столбца в таблице: исходный заголовок из файла."""
    return key[len(SERVICE_PREFIX):] if is_service_key(key) else PARAM_PREFIX + key


def attr_sort_key(key: str):
    """Служебные столбцы — первыми, в порядке файла; затем параметры по алфавиту."""
    if is_service_key(key):
        name = key[len(SERVICE_PREFIX):]
        return (0, _EXTRA_RANK.get(name, len(_EXTRA_RANK)), name)
    return (1, 0, key)


# xls допускает 256 столбцов, а магазин выгружает 311: xlrd на таком листе
# падает на assert. Лимит — атрибут листа, поднимаем его перед чтением.
_orig_sheet_read = _xlrd_sheet.Sheet.read


def _sheet_read_wide(self, bk):
    self.utter_max_cols = 4096
    return _orig_sheet_read(self, bk)


_xlrd_sheet.Sheet.read = _sheet_read_wide

# ---- состояние фонового обновления (один процесс сервера) ----
_state_lock = threading.Lock()
_state: Dict[str, object] = {"status": "idle", "message": "", "started_at": None, "finished_at": None}


def get_state() -> Dict[str, object]:
    with _state_lock:
        return dict(_state)


def try_start() -> bool:
    """Помечает обновление запущенным. False — если оно уже идёт."""
    with _state_lock:
        if _state["status"] == "running":
            return False
        _state.update(status="running", message="Скачиваю файл…",
                      started_at=datetime.utcnow().isoformat(), finished_at=None)
        return True


def _set(**kw) -> None:
    with _state_lock:
        _state.update(kw)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else repr(v)
    return str(v).replace("\x00", "").strip()


def _price(raw) -> Optional[float]:
    s = _cell(raw).replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def download(url: str, dest: str) -> int:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; supplier-service catalog sync)"}
    size = 0
    with httpx.stream("GET", url, headers=headers, timeout=httpx.Timeout(30, read=180),
                      follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                size += len(chunk)
    with open(dest, "rb") as f:
        if f.read(4) != b"\xd0\xcf\x11\xe0":
            raise ValueError("По ссылке не файл .xls (ожидался Excel 97–2003)")
    return size


def parse_xls(path: str):
    """Возвращает (список dict'ов для Product, список заголовков, счётчик обрезок)."""
    wb = xlrd.open_workbook(path, on_demand=True, ragged_rows=True)
    try:
        ws = wb.sheet_by_index(0)
        header = [_cell(h) for h in ws.row_values(0)]
        idx = {h: i for i, h in enumerate(header) if h}
        for need in (COL_ARTICLE, COL_NAME):
            if need not in idx:
                raise ValueError(f"В файле нет столбца «{need}»")
        # Бренд ("Параметр: Бренд") кладём и в колонку brand, и (как раньше) в параметры.
        attr_cols = [(i, h[len(PARAM_PREFIX):] if h.startswith(PARAM_PREFIX) else SERVICE_PREFIX + h)
                     for i, h in enumerate(header)
                     if h and h not in CORE_SOURCE_COLUMNS]
        clipped = 0
        rows: List[dict] = []
        total = ws.nrows - 1
        for r in range(1, ws.nrows):
            row = ws.row_values(r)

            def get(col):
                i = idx.get(col)
                return _cell(row[i]) if i is not None and i < len(row) else ""

            name = get(COL_NAME)
            if not name:
                continue
            attrs = {}
            for i, key in attr_cols:
                if i < len(row):
                    val = _cell(row[i])
                    if val:
                        attrs[key] = val
            data = dict(
                article=get(COL_ARTICLE) or None,
                name=name,
                brand=get(COL_BRAND) or None,
                category=None,
                unit=get(COL_UNIT) or None,
                cost_price=_price(row[idx[COL_COST]]) if COL_COST in idx and idx[COL_COST] < len(row) else None,
                sale_price=_price(row[idx[COL_SALE]]) if COL_SALE in idx and idx[COL_SALE] < len(row) else None,
                image_url=get(COL_IMAGES) or None,
                external_product_id=get(COL_PRODUCT_ID) or None,
                external_variant_id=get(COL_VARIANT_ID) or None,
                description=get(COL_DESCRIPTION) or None,
                attributes=attrs,
            )
            for field, limit in _LIMITS.items():
                v = data[field]
                if v and len(v) > limit:
                    data[field] = v[:limit]
                    clipped += 1
            rows.append(data)
            if r % 1000 == 0:
                _set(message=f"Читаю файл: {r} из {total} строк…")
        return rows, header, clipped
    finally:
        wb.release_resources()


def replace_catalog(db: Session, rows: List[dict], filename: str) -> int:
    """Сохраняет rows новой загрузкой и удаляет прежние загрузки каталога —
    одной транзакцией."""
    supplier = db.query(models.Supplier).filter(models.Supplier.slug == IMPORT_SUPPLIER_SLUG).first()
    if not supplier:
        supplier = models.Supplier(name=SUPPLIER_NAME, slug=IMPORT_SUPPLIER_SLUG)
        db.add(supplier)
        db.flush()
    old_ids = [u.id for u in db.query(models.Upload.id).filter(models.Upload.supplier_id == supplier.id)]

    upload = models.Upload(supplier_id=supplier.id, original_filename=filename,
                           status="done", products_count=len(rows))
    db.add(upload)
    db.flush()
    for start in range(0, len(rows), CHUNK):
        db.bulk_insert_mappings(
            models.Product,
            [dict(upload_id=upload.id, supplier_id=supplier.id, **d) for d in rows[start:start + CHUNK]],
        )
        _set(message=f"Сохраняю в базу: {min(start + CHUNK, len(rows))} из {len(rows)}…")
    if old_ids:
        db.query(models.Product).filter(models.Product.upload_id.in_(old_ids)).delete(synchronize_session=False)
        db.query(models.Upload).filter(models.Upload.id.in_(old_ids)).delete(synchronize_session=False)
    db.commit()
    return upload.id


def run_refresh(url: str = SOURCE_URL) -> None:
    """Скачать, разобрать, заменить каталог. Вызывается после try_start()
    (из фоновой задачи) — ошибки пишет в состояние, а не пробрасывает."""
    tmp = None
    db = SessionLocal()
    try:
        fd, tmp = tempfile.mkstemp(suffix=".xls", prefix="catalog_")
        os.close(fd)
        size = download(url, tmp)
        _set(message=f"Файл скачан ({size // (1024 * 1024)} МБ), читаю…")
        rows, header, clipped = parse_xls(tmp)
        if not rows:
            raise ValueError("В файле нет ни одной строки с названием товара — каталог не изменён")
        replace_catalog(db, rows, os.path.basename(url.split("?")[0]) or "catalog.xls")
        note = f"Готово: {len(rows)} строк, {len(header)} столбцов."
        if clipped:
            note += f" Обрезано слишком длинных значений: {clipped}."
        _set(status="done", message=note, finished_at=datetime.utcnow().isoformat())
    except Exception as e:  # noqa: BLE001 — любая ошибка должна дойти до пользователя
        db.rollback()
        _set(status="error", message=f"Не удалось обновить, прежние данные сохранены: {e}",
             finished_at=datetime.utcnow().isoformat())
    finally:
        db.close()
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
