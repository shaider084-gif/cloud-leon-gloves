from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Numeric, Text, JSON
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base

# На проде (Postgres) реально используется JSONB, на SQLite (локальный запуск
# без Docker, см. README) — обычный JSON. with_variant позволяет одной
# колонке работать на обоих диалектах без дублирования моделей.
JSONType = JSON().with_variant(JSONB, "postgresql")


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
    # slug matches a key in the parser registry (app/parsers/registry.py) —
    # only used for suppliers with a hand-written custom parser (complex файлы).
    slug = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Сохранённая настройка визуального маппинга колонок (для простых файлов —
    # один заголовок, один товар на строку). Формат:
    # {"<заголовок_в_файле_lower>": "<канонич.поле или 'attr:Имя' или '__ignore__'>", ...}
    # Если задано — повторные загрузки этого поставщика используют его автоматически
    # (сценарий "Обновление цен"), без похода к разработчику за парсером.
    column_mapping = Column(JSONType, nullable=True)
    markup_percent = Column(Numeric(5, 2), nullable=True, default=30)

    # Свободный текст — формат контактов/адресов у поставщиков слишком разный,
    # чтобы заводить под них жёсткую структуру полей.
    contacts = Column(Text, nullable=True)           # Контакты поставщика
    pickup_addresses = Column(Text, nullable=True)   # Адреса складов для самовывоза

    uploads = relationship("Upload", back_populates="supplier", cascade="all, delete-orphan")


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    original_filename = Column(String(500))
    status = Column(String(32), default="done")  # done / error
    error_message = Column(Text, nullable=True)
    products_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Путь к сохранённому исходному файлу прайса (None у загрузок, сделанных
    # до появления этой возможности — старые временные файлы не хранились).
    file_path = Column(String(500), nullable=True)

    supplier = relationship("Supplier", back_populates="uploads")
    products = relationship("Product", back_populates="upload", cascade="all, delete-orphan")


class Product(Base):
    """Canonical (target-template) representation of a single product row."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    upload_id = Column(Integer, ForeignKey("uploads.id"), nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)

    article = Column(String(255))       # Артикул
    name = Column(String(1000))         # Название
    brand = Column(String(255))         # Бренд
    category = Column(String(255))      # Категория
    unit = Column(String(64))           # Ед. изм.
    cost_price = Column(Numeric(12, 2))  # Закупочная цена (из прайса поставщика)
    sale_price = Column(Numeric(12, 2))  # Цена продажи (с наценкой)
    # Text, не String(1000): у части товаров (импорт старого каталога) тут
    # несколько ссылок через пробел и значение доходит до ~5000 символов —
    # на Postgres строгий лимит длины VARCHAR валит вставку целиком (на SQLite
    # не проверяется, поэтому раньше проблема была не видна).
    image_url = Column(Text, nullable=True)

    # Поля для будущего наполнения (пока не парсятся ни одним поставщиком) —
    # заведены заранее по запросу пользователя, чтобы структура каталога уже
    # была готова принять эти данные.
    external_product_id = Column(String(255), nullable=True)   # ID товара (во внешней системе)
    external_variant_id = Column(String(255), nullable=True)   # ID варианта товара
    description = Column(Text, nullable=True)                  # Описание

    # Гибкие параметры товара (Цвет, Размер, Покрытие и т.п.) —
    # разные категории/поставщики имеют разный набор параметров.
    attributes = Column(JSONType, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)

    upload = relationship("Upload", back_populates="products")
