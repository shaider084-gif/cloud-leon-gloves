import os


class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    )
    secret_key: str = os.environ.get("SECRET_KEY", "dev-insecure-secret")
    admin_username: str = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "admin")
    upload_dir: str = os.environ.get("UPLOAD_DIR", "/app/uploads")


settings = Settings()
