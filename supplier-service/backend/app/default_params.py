"""Столбцы по умолчанию у всех поставщиков (решение пользователя, 2026-10-06).

Всегда присутствуют: Артикул, Название, Закупка, Продажа, Изображения, Описание,
Размеры (размеры одежды/обуви), Бренд — фиксированные поля таблицы, плюс «Параметр: …»:
Товар, Поставщик, Страна производства, Вес (кг), Размер, см (габариты Д×Ш×В),
Размер, м3 (объём), Реестр сертификатов, Реестр Минпромторг.
Пустые столбцы показываются всегда; заполняются по команде пользователя.

apply_defaults — проставляет значения по умолчанию и то, что выводится из уже
имеющихся данных (вес, габариты, страна по реестровому номеру).
fill_db — идемпотентно делает то же для уже сохранённых товаров последней загрузки
каждого поставщика (вызывается при старте приложения).
"""
import json
import os
import re
from typing import Dict, Optional

DEFAULT_PARAM_KEYS = ["Товар", "Поставщик", "Страна производства", "Вес", "Размер, см", "Размер, м3",
                      "Реестр сертификатов", "Реестр Минпромторг"]

# Поставщики, к которым правила не применяются (зеркало сайта и тестовые).
SKIP_SLUGS = {"catalog-import", "demo", "test"}

# Известные факты о стране производства (проверено по данным поставщика/реестров).
COUNTRY_BY_SLUG = {
    "fest": "Россия",  # ООО «Предприятие «ФЭСТ», г. Кострома; ТУ и РУ РФ
    "pkf_spetczaschita": "Россия",  # правило парсера: бренд «Россия»
}

# Код страны изготовителя в номере сертификата/декларации: «ЕАЭС RU С-CN.АВ29…», «Д-RU.РА08…».
COUNTRY_CODES = {
    "RU": "Россия", "CN": "Китай", "DE": "Германия", "IT": "Италия", "BY": "Беларусь", "KZ": "Казахстан",
    "TR": "Турция", "PL": "Польша", "CZ": "Чехия", "TW": "Тайвань", "IN": "Индия", "VN": "Вьетнам",
    "KR": "Республика Корея", "JP": "Япония", "FR": "Франция", "ES": "Испания", "GB": "Великобритания",
    "US": "США", "UZ": "Узбекистан", "PK": "Пакистан", "MY": "Малайзия", "TH": "Таиланд", "FI": "Финляндия", "LK": "Шри-Ланка",
}


def _kg(value: str, default_unit: str = "кг") -> Optional[str]:
    """'150 гр.' / '0,25 кг' / '280' -> килограммы строкой с запятой ('0,15')."""
    m = re.match(r"\s*([\d]+(?:[.,]\d+)?)\s*(кг|килограмм\w*|гр?\.?|грамм\w*)?", str(value or ""), re.I)
    if not m:
        return None
    x = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or default_unit).lower()
    if not unit.startswith("кг") and not unit.startswith("килограмм"):
        x /= 1000
    s = ("%.3f" % x).rstrip("0").rstrip(".")
    return s.replace(".", ",") if s and s != "0" else None


def _weight_from(attrs: Dict, name: str) -> Optional[str]:
    for key, unit in (("Вес, кг", "кг"), ("Вес обуви, кг", "кг"), ("Вес изделия, кг", "кг"), ("Вес, г", "г"),
                      ("Масса, кг", "кг"), ("Масса, г", "г")):
        if attrs.get(key):
            w = _kg(attrs[key], unit)
            if w:
                return w
    m = re.search(r"вес\s*(\d+(?:[.,]\d+)?)\s*(кг|гр\w*|г)\b", name or "", re.I)
    if m:
        return _kg(m.group(1) + " " + m.group(2))
    return None


def _dims_cm(value: str, unit: str = "мм") -> Optional[str]:
    """'266x220x80' (мм) -> '26,6x22x8' (см); одиночное значение тоже переводится."""
    parts = re.findall(r"\d+(?:[.,]\d+)?", str(value or ""))
    if not parts:
        return None
    out = []
    for p in parts[:3]:
        x = float(p.replace(",", "."))
        if unit == "мм":
            x /= 10
        s = ("%.1f" % x).rstrip("0").rstrip(".")
        out.append(s.replace(".", ","))
    return "x".join(out)


def _country_from_registry(attrs: Dict) -> Optional[str]:
    codes = set()
    for key in ("Реестр сертификатов",):
        for num in str(attrs.get(key) or "").split("##"):
            m = re.search(r"[СCД]-([A-Z]{2})[.\s]", num)
            if m:
                codes.add(m.group(1))
    if len(codes) == 1:
        return COUNTRY_CODES.get(next(iter(codes)))
    return None


_OVERLAY_CACHE: Dict[str, Dict] = {}


def _overlay(slug: str) -> Dict:
    """registry_data/<slug>.json: {артикул: {«Реестр сертификатов»: …, «Реестр Минпромторг»: …}} —
    реестровые номера, найденные в реестрах (Росаккредитация, ГИСП) и сопоставленные с артикулами."""
    if slug not in _OVERLAY_CACHE:
        path = os.path.join(os.path.dirname(__file__), "registry_data", f"{slug}.json")
        try:
            with open(path, encoding="utf-8") as f:
                _OVERLAY_CACHE[slug] = json.load(f)
        except (OSError, ValueError):
            _OVERLAY_CACHE[slug] = {}
    return _OVERLAY_CACHE[slug]


def apply_defaults(attributes: Dict, supplier, article=None, name: str = "") -> Dict:
    """Возвращает attributes с обязательными значениями по умолчанию."""
    attrs = dict(attributes or {})
    if article:
        for k, v in _overlay(supplier.slug).get(article, {}).items():
            if v and not attrs.get(k):
                attrs[k] = v
    if not attrs.get("Поставщик"):
        attrs["Поставщик"] = supplier.name
    if not attrs.get("Страна производства"):
        country = COUNTRY_BY_SLUG.get(supplier.slug) or _country_from_registry(attrs)
        if country:
            attrs["Страна производства"] = country
    if not attrs.get("Вес"):
        w = _weight_from(attrs, name)
        if w:
            attrs["Вес"] = w
    if not attrs.get("Размер, см"):
        for key, unit in (("Размер футляра, мм", "мм"), ("Длина, мм", "мм"), ("Размер, мм", "мм")):
            if attrs.get(key):
                d = _dims_cm(attrs[key], unit)
                if d:
                    attrs["Размер, см"] = d
                    break
    if not attrs.get("Размер, м3") and attrs.get("Объем коробки, м3"):
        attrs["Размер, м3"] = attrs["Объем коробки, м3"]
    return attrs


def fill_db() -> int:
    """Проставляет значения по умолчанию в товарах текущей (последней успешной) загрузки
    каждого поставщика. Возвращает число изменённых товаров."""
    from . import models
    from .database import SessionLocal

    db = SessionLocal()
    changed = 0
    try:
        for sup in db.query(models.Supplier).all():
            if sup.slug in SKIP_SLUGS:
                continue
            last = (
                db.query(models.Upload)
                .filter(models.Upload.supplier_id == sup.id, models.Upload.status == "done")
                .order_by(models.Upload.created_at.desc(), models.Upload.id.desc())
                .first()
            )
            if not last:
                continue
            for p in db.query(models.Product).filter(models.Product.upload_id == last.id):
                new = apply_defaults(p.attributes, sup, p.article, p.name)
                if new != (p.attributes or {}):
                    p.attributes = new  # новый dict — чтобы SQLAlchemy заметил изменение JSON
                    changed += 1
        if changed:
            db.commit()
        return changed
    finally:
        db.close()
