"""Замена ссылок на реестры номерами реестровых записей.

В параметрах «Реестр сертификатов» и «Реестр Минпромторг» должен стоять не URL,
а номер записи (решение пользователя, 2026-10-05):
  * Росаккредитация (pub.fsa.gov.ru): «ЕАЭС RU С-CN.АВ29.В.01903/23»,
    «ЕАЭС N RU Д-CN.РА11.В.04345/24» (номер сертификата/декларации);
  * ГИСП (gisp.gov.ru, реестр российской промышленной продукции): номер
    реестровой записи, например «10878619».
Соответствие «ссылка -> номер» лежит в registry_numbers.json. Ссылка, для
которой номера в справочнике нет, остаётся как есть (ничего не выдумываем).

convert_attributes() вызывается парсером при разборе файла; convert_db()
вызывается при старте приложения и однократно заменяет ссылки в уже
сохранённых товарах (идемпотентно: когда ссылок не осталось, ничего не делает).
"""
import json
import os
import re
from typing import Dict, Iterable

REGISTRY_KEYS = ("Реестр сертификатов", "Реестр Минпромторг")
# поставщики, у которых ссылки на реестры могли быть сохранены раньше
MIGRATE_SLUGS = ("technoavia_spetsobuv", "jeta")

_FSA_RE = re.compile(r"https://pub\.fsa\.gov\.ru/(rss/certificate|rds/declaration)/view/(\d+)/(?:baseInfo|common)/?")
_GISP_RE = re.compile(r"https://gisp\.gov\.ru/goods/#/product/(\d+)/?")


def _load() -> dict:
    path = os.path.join(os.path.dirname(__file__), "registry_numbers.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_DATA = _load()
_FSA: Dict[str, str] = _DATA.get("fsa", {})
_GISP: Dict[str, str] = _DATA.get("gisp", {})


def _number(part: str) -> str:
    part = part.strip()
    m = _FSA_RE.fullmatch(part)
    if m:
        return _FSA.get(("c" if m.group(1) == "rss/certificate" else "d") + m.group(2), part)
    m = _GISP_RE.fullmatch(part)
    if m:
        return _GISP.get(m.group(1), part)
    return part


def convert_value(value: str) -> str:
    """Значения-списки (через ##) обрабатываются по частям; повторы убираются."""
    out = []
    for part in value.split("##"):
        n = _number(part)
        if n and n not in out:
            out.append(n)
    return "##".join(out)


def convert_attributes(attributes: Dict[str, str]) -> bool:
    """Меняет значения реестровых параметров на месте. True — если что-то изменилось."""
    changed = False
    for key in REGISTRY_KEYS:
        value = attributes.get(key)
        if isinstance(value, str) and ("http://" in value or "https://" in value):
            new = convert_value(value)
            if new != value:
                attributes[key] = new
                changed = True
    return changed


def convert_db(slugs: Iterable[str] = MIGRATE_SLUGS) -> int:
    """Заменяет ссылки на номера в сохранённых товарах указанных поставщиков.
    Загружаются только строки, где ссылки ещё есть. Возвращает число изменённых."""
    from sqlalchemy import Text, cast, or_

    from . import models
    from .database import SessionLocal

    db = SessionLocal()
    try:
        ids = [s.id for s in db.query(models.Supplier).filter(models.Supplier.slug.in_(list(slugs)))]
        if not ids:
            return 0
        as_text = cast(models.Product.attributes, Text)
        rows = (
            db.query(models.Product)
            .filter(models.Product.supplier_id.in_(ids),
                    or_(as_text.like("%pub.fsa.gov.ru%"), as_text.like("%gisp.gov.ru/goods%")))
            .all()
        )
        changed = 0
        for p in rows:
            attrs = dict(p.attributes or {})
            if convert_attributes(attrs):
                p.attributes = attrs  # новый dict — чтобы SQLAlchemy заметил изменение JSON
                changed += 1
        if changed:
            db.commit()
        return changed
    finally:
        db.close()
