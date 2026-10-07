"""Кнопка «Обновить» у поставщиков, чей прайс — не файл, а несколько ссылок (FoxWeld: 3 таблицы).

Что делает синхронизация:
  1. скачивает каждый источник (публичная ссылка Яндекс.Диска на .xlsx или прямая http-ссылка);
  2. читает цены (парсер parsers/foxweld.py: «Артикул», «Наименование», «VIP, руб», «Розн., руб»);
  3. сверяет с текущим каталогом поставщика по артикулу:
       * у существующих товаров обновляются параметры «РРЦ» и «Цена VIP» (остальное не трогается);
       * новые товары добавляются и помечаются «Новый = Да» + «Дата добавления» (метка «Новый»
         у прошлой партии снимается);
       * товары, которых нет в прайсах, остаются как есть (ничего не удаляется);
  4. сохраняет результат новой загрузкой (видна в «Истории загрузок»).
Закупка (cost_price) здесь не меняется: правило «какая цена — закупка» пользователь ещё не задал.

Ссылки на источники хранятся в таблице supplier_sources (создаётся при старте приложения).
Яндекс.Документы (docs.yandex.ru) роботам отдают капчу — поэтому нужны публичные ссылки Диска на файлы.
"""
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session

from . import models
from .catalog import get_current_products
from .database import Base, SessionLocal
from .schemas import ProductIn

# поставщики, у которых включена синхронизация по ссылкам
SYNC_SLUGS = {"foxweld"}
MAX_SOURCE_SIZE = 40 * 1024 * 1024
SOURCE_SLOTS = 3


class SupplierSource(Base):
    __tablename__ = "supplier_sources"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    position = Column(Integer, default=0)
    url = Column(Text, nullable=False)
    last_status = Column(String(500), nullable=True)
    last_fetched = Column(DateTime, nullable=True)


def get_sources(db: Session, supplier_id: int) -> List[SupplierSource]:
    return (db.query(SupplierSource).filter(SupplierSource.supplier_id == supplier_id)
            .order_by(SupplierSource.position, SupplierSource.id).all())


def save_sources(db: Session, supplier_id: int, urls: List[str]) -> None:
    """Заменяет набор ссылок поставщика (пустые строки пропускаются)."""
    db.query(SupplierSource).filter(SupplierSource.supplier_id == supplier_id).delete()
    for i, u in enumerate(u.strip() for u in urls if u and u.strip()):
        db.add(SupplierSource(supplier_id=supplier_id, position=i, url=u))
    db.commit()


# ---------------------------------------------------------------- скачивание

def _is_disk_url(url: str) -> bool:
    return bool(re.match(r"https?://(disk\.yandex\.\w+|yadi\.sk)/", url))


def download_source(url: str, dest: str) -> int:
    """Скачивает источник в dest, возвращает размер. Бросает ValueError с понятным текстом."""
    url = url.strip()
    if "docs.yandex." in url:
        raise ValueError("Ссылка на Яндекс.Документы открывается только в браузере (капча). "
                         "Положите таблицу на Яндекс.Диск как .xlsx и дайте публичную ссылку disk.yandex.ru/…")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; supplier-service sync)"}
    with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True, headers=headers) as cli:
        href = url
        if _is_disk_url(url):
            r = cli.get("https://cloud-api.yandex.net/v1/disk/public/resources/download", params={"public_key": url})
            if r.status_code == 404:
                raise ValueError("Файл по публичной ссылке Диска не найден (проверьте, что доступ по ссылке включён)")
            r.raise_for_status()
            href = r.json().get("href")
            if not href:
                raise ValueError("Диск не вернул ссылку на скачивание (это папка, а не файл?)")
        size = 0
        with cli.stream("GET", href) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > MAX_SOURCE_SIZE:
                        raise ValueError("Файл больше 40 МБ")
                    f.write(chunk)
    with open(dest, "rb") as f:
        if f.read(2) != b"PK":
            raise ValueError("По ссылке не файл .xlsx")
    return size


# ---------------------------------------------------------------- состояние фоновой задачи

_lock = threading.Lock()
_state: Dict[int, Dict] = {}


def get_state(supplier_id: int) -> Dict:
    with _lock:
        return dict(_state.get(supplier_id) or {"status": "idle", "message": ""})


def try_start(supplier_id: int) -> bool:
    with _lock:
        if (_state.get(supplier_id) or {}).get("status") == "running":
            return False
        _state[supplier_id] = {"status": "running", "message": "Скачиваю прайсы…", "result": None}
        return True


def _set(supplier_id: int, **kw) -> None:
    with _lock:
        _state.setdefault(supplier_id, {}).update(kw)


# ---------------------------------------------------------------- сверка и сохранение

def _copy(p: models.Product) -> ProductIn:
    return ProductIn(
        article=p.article, name=p.name, brand=p.brand, category=p.category, unit=p.unit,
        cost_price=float(p.cost_price) if p.cost_price is not None else None,
        sale_price=float(p.sale_price) if p.sale_price is not None else None,
        image_url=p.image_url, external_product_id=p.external_product_id,
        external_variant_id=p.external_variant_id, description=p.description,
        attributes=dict(p.attributes or {}),
    )


def _guess_brand(name: str, brands: Dict[str, str]) -> Optional[str]:
    low = (name or "").lower()
    for key, brand in brands.items():
        if re.search(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", low):
            return brand
    return None


def apply_rows(db: Session, supplier: models.Supplier, rows: List[Dict], label: str) -> Dict:
    """rows — строки прайсов (parsers.foxweld.parse_price_rows). Возвращает отчёт."""
    from .parsers.foxweld import price_attributes

    by_article: Dict[str, Dict] = {}
    dups = 0
    for r in rows:
        if r["article"] in by_article:
            dups += 1
            continue
        by_article[r["article"]] = r

    current = get_current_products(db, supplier.id)
    cur_by_article = {p.article: p for p in current if p.article}
    brands: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    for p in current:
        if p.brand:
            counts[p.brand] = counts.get(p.brand, 0) + 1
    for b, n in counts.items():
        if n >= 2:
            brands[b.lower()] = b

    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y")
    out: List[ProductIn] = []
    changed: List[Dict] = []
    new_items: List[Dict] = []
    for p in current:
        pi = _copy(p)
        pi.attributes.pop("Новый", None)  # метка прошлой партии снимается
        row = by_article.get(p.article) if p.article else None
        if row:
            new_attrs = price_attributes(row)
            old = {k: (p.attributes or {}).get(k) for k in new_attrs}
            if any((old[k] or "") != v for k, v in new_attrs.items()):
                changed.append({"article": p.article, "name": p.name, "old": old, "new": new_attrs})
            pi.attributes.update(new_attrs)
        out.append(pi)

    for art, row in by_article.items():
        if art in cur_by_article:
            continue
        attrs = price_attributes(row)
        if row.get("warranty"):
            attrs["Гарантия, мес"] = row["warranty"]
        attrs["Новый"] = "Да"
        attrs["Дата добавления"] = today
        out.append(ProductIn(article=art, name=row["name"], brand=_guess_brand(row["name"], brands),
                             description=row.get("brief"), attributes=attrs))
        new_items.append({"article": art, "name": row["name"], "retail": row.get("retail"), "vip": row.get("vip")})

    from .upload_utils import save_products
    summary = f"Обновление из прайсов {today}: +{len(new_items)} новых, цены изменились у {len(changed)}"
    if new_items or changed:  # без изменений новую загрузку в историю не пишем
        save_products(db, supplier, summary, out)
    not_in_price = sum(1 for a in cur_by_article if a not in by_article)
    return {"new": new_items, "changed": changed, "not_in_price": not_in_price,
            "rows": len(by_article), "dups": dups, "total": len(out), "summary": summary}


def run_sync(supplier_id: int) -> None:
    """Фоновая задача кнопки «Обновить»."""
    from .parsers.foxweld import parse_price_rows

    db = SessionLocal()
    try:
        supplier = db.get(models.Supplier, supplier_id)
        sources = get_sources(db, supplier_id)
        if not sources:
            _set(supplier_id, status="error", message="Ссылки на прайсы не заданы — вставьте их и нажмите «Сохранить ссылки».")
            return
        rows: List[Dict] = []
        errors: List[str] = []
        per_source: List[str] = []
        for i, s in enumerate(sources, 1):
            _set(supplier_id, message=f"Скачиваю прайс {i} из {len(sources)}…")
            tmp = os.path.join(tempfile.gettempdir(), f"src_{supplier_id}_{i}.xlsx")
            try:
                size = download_source(s.url, tmp)
                got = parse_price_rows(tmp)
                if not got:
                    raise ValueError("в таблице не нашлись строки с «Артикул», «Наименование» и ценами")
                rows.extend(got)
                s.last_status = f"ок: {len(got)} строк, {size // 1024} КБ"
                per_source.append(f"прайс {i}: {len(got)} строк")
            except Exception as exc:  # noqa: BLE001 — текст ошибки показываем пользователю
                msg = str(exc)[:300]
                s.last_status = "ошибка: " + msg
                errors.append(f"прайс {i}: {msg}")
            finally:
                s.last_fetched = datetime.utcnow()
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        db.commit()
        if not rows:
            _set(supplier_id, status="error", message="Ничего не скачалось. " + " | ".join(errors))
            return
        _set(supplier_id, message="Сверяю с каталогом…")
        res = apply_rows(db, supplier, rows, "")
        res["errors"] = errors
        msg = (f"Готово: новых товаров {len(res['new'])}, цены изменились у {len(res['changed'])}, "
               f"в прайсах нет {res['not_in_price']} из имеющихся. " + ("; ".join(per_source)))
        if errors:
            msg += ". Ошибки: " + " | ".join(errors)
        _set(supplier_id, status="done", message=msg, result={k: res[k] for k in ("new", "changed", "not_in_price", "errors")})
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _set(supplier_id, status="error", message=f"Не удалось обновить: {exc}")
    finally:
        db.close()
