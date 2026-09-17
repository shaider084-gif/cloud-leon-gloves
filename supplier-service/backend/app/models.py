from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Numeric, Text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, default=0)  # 0/1, simple flag
    created_at = Column(DateTime, default=datetime.utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    # slug matches a key in the parser registry (app/parsers/registry.py)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    uploads = relationship("Upload", back_populates="supplier", cascade="all, delete-orphan")


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    original_filename = Column(String(500))
    status = Column(String(32), default="done")  # done / error
    error_message = Column(Text, nullable=True)
    products_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    supplier = relationship("Supplier", back_populates="uploads")
    products = relationship("Product", back_populates="upload", cascade="all, delete-orphan")


class Product(Base):
    """Canonical (target-template) representation of a single product row."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    upload_id = Column(Integer, ForeignKey("uploads.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)

    article = Column(String(255))       # Артикул
    name = Column(String(1000))         # Название
    brand = Column(String(255))         # Бренд
    category = Column(String(255))      # Категория
    unit = Column(String(64))           # Ед. изм.
    cost_price = Column(Numeric(12, 2))  # Закупочная цена (из прайса поставщика)
    sale_price = Column(Numeric(12, 2))  # Цена продажи (с наценкой)
    image_url = Column(String(1000), nullable=True)

    # Гибкие параметры товара (Цвет, Размер, Покрытие и т.п.) —
    # разные категории/поставщики имеют разный набор параметров.
    attributes = Column(JSONB, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)

    upload = relationship("Upload", back_populates="products")
