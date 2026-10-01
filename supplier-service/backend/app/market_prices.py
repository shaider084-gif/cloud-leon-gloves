"""Рыночные цены товаров каталога (для вкладки "Сравнение цен").

Отдельная таблица по артикулу, а не колонки в products: products пересоздаётся
при каждой загрузке прайса, а рыночные цены должны переживать перезагрузки.
Таблица создаётся сама (Base.metadata.create_all при старте), models.py не правится."""
from datetime import datetime
from typing import Dict, Iterable

from sqlalchemy import Column, DateTime, Numeric, String, Text
from sqlalchemy.orm import Session

from .database import Base


class MarketPrice(Base):
    __tablename__ = "market_prices"

    article = Column(String(255), primary_key=True)
    price1 = Column(Numeric(12, 2))  # самая низкая найденная цена
    price2 = Column(Numeric(12, 2))  # вторая по минимальности (у другого продавца)
    url1 = Column(Text)
    url2 = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_market_prices(db: Session, articles: Iterable[str]) -> Dict[str, MarketPrice]:
    """Рыночные цены по артикулам; товары без записи в словарь не попадают."""
    wanted = {a for a in articles if a}
    if not wanted:
        return {}
    return {m.article: m for m in db.query(MarketPrice).all() if m.article in wanted}
