"""Поставщик ФЭСТ (ООО «Предприятие «ФЭСТ», аптечки; slug fest) — прайс .xlsx.

Один лист «TDSheet»: строка-раздел («01. Аптечки автомобильные приказ 260н»)
в первой колонке, затем товары: B — артикул, C — номенклатура (первая строка
— название, далее «штрихкод …»), J — ставка НДС, K — цена, M — количество в
коробке, N — «объём м3 / вес кг» коробки. Шапка таблицы повторяется на
каждой странице — такие строки пропускаются.

Файл из учётной системы нестандартный: общие строки лежат в
`xl/SharedStrings.xml` (с заглавной S), и openpyxl его не находит —
переименовываем запись в памяти перед чтением.

Сопоставление с «Весь каталог» по артикулу для этого поставщика НЕ
делается (решение пользователя, 2026-10-05): берутся только данные прайса.
Единственное добавление — «ID варианта» из таблицы пользователя
(fest_ids.json: артикул прайса -> ID варианта в магазине).
Цена из прайса (по артикулу) записывается как закупочная; цена продажи =
закупка × 1,2 (MARKUP) без копеек (отбрасываются, не округляются). Встроенные в файл картинки (≈235) не
используются; фото, описания, параметры и новые названия — из fest_extra.json
(донор promza.ru, см. _load_extra).
"""
import io
import json
import os
import re
import zipfile
from typing import Dict, List, Optional

import openpyxl

from ..schemas import ProductIn
from .base import BaseParser

BRAND = "ФЭСТ"
MARKUP = 1.2  # цена продажи = закупка × 1,2 (решение пользователя, 2026-10-05)


def _load_variant_ids() -> Dict[str, str]:
    """Артикул прайса -> ID варианта в магазине (первая страница файла
    ФЭСТ.xlsx пользователя; 202 из 204 ID есть в «Весь каталог» как «ID варианта»)."""
    path = os.path.join(os.path.dirname(__file__), "fest_ids.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("variant_id_by_article", {})
    except (OSError, ValueError):
        return {}


def _load_extra() -> Dict[str, dict]:
    """Артикул прайса -> {name, images, description, attributes} из донора promza.ru
    (fest_extra.json; собрано из таблицы донора и страниц сайта, описания и параметры
    написаны заново; названия — по шаблону «Товар Бренд описание, приказ N, арт. X»).
    Донор нашёл 178 из 234 артикулов; у остальных — только новое название."""
    path = os.path.join(os.path.dirname(__file__), "fest_extra.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_VARIANT_IDS = _load_variant_ids()
_EXTRA = _load_extra()
COL_SECTION, COL_ARTICLE, COL_NAME, COL_VAT, COL_PRICE, COL_PACK, COL_BOX = 0, 1, 2, 9, 10, 12, 13


def _open_workbook(file_path: str):
    try:
        return openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except KeyError:  # нет xl/sharedStrings.xml — в файле он называется SharedStrings.xml
        pass
    buf = io.BytesIO()
    with zipfile.ZipFile(file_path) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data, name = zin.read(item.filename), item.filename
            if name.lower() == "xl/sharedstrings.xml":
                name = "xl/sharedStrings.xml"
            elif name == "[Content_Types].xml":
                data = re.sub(rb"/xl/sharedstrings\.xml", b"/xl/sharedStrings.xml", data, flags=re.I)
            zout.writestr(name, data)
    buf.seek(0)
    return openpyxl.load_workbook(buf, read_only=True, data_only=True)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value) -> Optional[float]:
    try:
        return round(float(str(value).replace(",", ".").replace(" ", "")), 2)
    except (TypeError, ValueError):
        return None


def _fmt_int(value) -> Optional[str]:
    n = _num(value)
    if n is None:
        return None
    return str(int(n)) if n == int(n) else str(n).replace(".", ",")


def _split_name(cell: str):
    """Название и штрихкод из ячейки номенклатуры."""
    barcode = None
    name_lines = []
    for line in cell.replace("\r", "").split("\n"):
        line = line.strip()
        m = re.match(r"штрих\s*-?\s*код\s*:?\s*(\d+)", line, re.I)
        if m:
            barcode = m.group(1)
        elif line:
            name_lines.append(line)
    return re.sub(r"\s+", " ", " ".join(name_lines)).strip(), barcode


class FestParser(BaseParser):
    display_name = "ФЭСТ (аптечки, прайс .xlsx)"

    def parse(self, file_path: str) -> List[ProductIn]:
        wb = _open_workbook(file_path)
        ws = wb.worksheets[0]
        products: List[ProductIn] = []
        seen = set()
        section = None
        for row in ws.iter_rows(values_only=True):
            row = tuple(row) + (None,) * (COL_BOX + 1 - len(row))
            article, cell = _text(row[COL_ARTICLE]), _text(row[COL_NAME])
            if not article and not cell:
                head = _text(row[COL_SECTION])
                if head and not head.lower().startswith("прайс"):
                    section = re.sub(r"^\d+\.\s*", "", head).strip()
                continue
            if article.lower() == "артикул" or not article:
                continue  # повторяющаяся шапка таблицы
            if article in seen:
                continue
            seen.add(article)
            name, barcode = _split_name(cell)
            attrs: Dict[str, str] = {}
            if barcode:
                attrs["Штрихкод"] = barcode
            vat = _fmt_int(row[COL_VAT])
            if vat:
                attrs["Ставка НДС"] = f"{vat}%"
            pack = _fmt_int(row[COL_PACK])
            if pack:
                attrs["Количество в коробке"] = f"{pack} шт"
            box = _text(row[COL_BOX])
            if box:
                parts = [p.strip() for p in box.replace("\n", " ").split("/")]
                if len(parts) == 2 and parts[0] and parts[1]:
                    attrs["Объем коробки, м3"] = parts[0]
                    attrs["Вес коробки, кг"] = parts[1]
            cost = _num(row[COL_PRICE])
            extra = _EXTRA.get(article) or {}
            if extra.get("attributes"):
                attrs = {**extra["attributes"], **attrs}
            products.append(
                ProductIn(
                    article=article,
                    name=extra.get("name") or name or article,
                    brand=BRAND,
                    category=section,
                    cost_price=cost,
                    sale_price=float(int(cost * MARKUP)) if cost is not None else None,  # копейки отрезаются
                    image_url=" ".join(extra["images"]) if extra.get("images") else None,
                    external_variant_id=_VARIANT_IDS.get(article),
                    description=extra.get("description"),
                    attributes=attrs,
                )
            )
        wb.close()
        return products
