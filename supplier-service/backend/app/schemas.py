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
    attributes: Dict[str, Any] = {}
