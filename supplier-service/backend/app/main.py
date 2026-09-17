import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import Base, engine, SessionLocal
from .config import settings
from .security import hash_password
from . import models
from .routers import auth, suppliers, users

app = FastAPI(title="Сервис управления поставщиками и прайс-листами")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth.router)
app.include_router(suppliers.router)
app.include_router(users.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

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
