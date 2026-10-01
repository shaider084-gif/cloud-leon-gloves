from typing import Optional, Dict, Any
from pydantic import BaseModel


class ProductIn(BaseModel):
    """What a parser must produce for each source row — canonical schema."""
    article: Optional[str] = None
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    cost_price: Optional[float] = None
    sale_price: Optional[float] = None
    image_url: Optional[str] = None
    external_product_id: Optional[str] = None
    external_variant_id: Optional[str] = None
    description: Optional[str] = None
    attributes: Dict[str, Any] = {}


# Канонические поля, в которые можно смаппить колонку файла поставщика через
# визуальный маппер (вкладка "Новые прайс-листы"). Используется и в UI
# маппинга, и в экспорте (export.py), чтобы не дублировать список.
CANONICAL_FIELDS = [
    ("article", "Артикул"),
    ("name", "Название"),
    ("brand", "Бренд"),
    ("category", "Категория"),
    ("unit", "Ед. изм."),
    ("cost_price", "Закупочная цена"),
]

IGNORE_FIELD = "__ignore__"
ATTRIBUTE_PREFIX = "attr:"  # "attr:Цвет" -> кладётся в attributes["Цвет"]
