import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .database import Base, engine, SessionLocal
from .config import settings
from .security import hash_password
from . import models
from .routers import auth, suppliers, users, price_updates, catalog_view, price_compare

app = FastAPI(title="Сервис управления поставщиками и прайс-листами")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth.router)
app.include_router(suppliers.router)
app.include_router(price_updates.router)
app.include_router(catalog_view.router)
app.include_router(price_compare.router)
app.include_router(users.router)


def _lightweight_migrate():
    """Проект без Alembic (осознанное упрощение для внутреннего инструмента) —
    Base.metadata.create_all создаёт только отсутствующие таблицы, но не новые
    колонки в уже существующих. Для пары простых ADD COLUMN этого достаточно;
    если миграций станет больше — переходить на Alembic.
    Синтаксис ALTER TABLE ... ADD COLUMN IF NOT EXISTS специфичен для Postgres.
    На SQLite (локальный запуск без Docker, см. README) для новой БД create_all
    и так создаёт таблицы сразу с актуальными колонками — но у уже существующей
    локальной БД (local_dev.db с накопленными тестовыми данными) их всё равно
    нужно добавлять точечно, поэтому там используем ADD COLUMN с try/except
    вместо IF NOT EXISTS (SQLite его не поддерживает)."""
    is_postgres = engine.dialect.name == "postgresql"

    if is_postgres:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS column_mapping JSONB"
            ))
            conn.execute(text(
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS markup_percent NUMERIC(5,2) DEFAULT 30"
            ))
            conn.execute(text(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS external_product_id VARCHAR(255)"
            ))
            conn.execute(text(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS external_variant_id VARCHAR(255)"
            ))
            conn.execute(text(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT"
            ))
            conn.execute(text(
                "ALTER TABLE products ALTER COLUMN image_url TYPE TEXT"
            ))
        return

    for stmt in (
        "ALTER TABLE products ADD COLUMN external_product_id VARCHAR(255)",
        "ALTER TABLE products ADD COLUMN external_variant_id VARCHAR(255)",
        "ALTER TABLE products ADD COLUMN description TEXT",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass  # колонка уже существует — повторный запуск на той же локальной БД


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _lightweight_migrate()

    db = SessionLocal()
    try:
        # Бутстрап: первый админ-пользователь из переменных окружения, если ещё нет ни одного.
        if db.query(models.User).count() == 0:
            db.add(
                models.User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    is_admin=1,
                )
            )

        # Демо-поставщик для проверки пайплайна.
        if not db.query(models.Supplier).filter(models.Supplier.slug == "demo").first():
            db.add(models.Supplier(name="Демо-поставщик (тест)", slug="demo"))

        db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
