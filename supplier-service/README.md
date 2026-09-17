# Сервис управления поставщиками и прайс-листами

Веб-сервис: список поставщиков → загрузка прайса поставщика → парсер конкретного
поставщика приводит данные к единой (канонической) схеме товара → результат можно
скачать в виде .xlsx (или позже — увидеть в общем каталоге).

## Архитектура

- **backend/** — FastAPI-приложение (Python), server-rendered UI на Jinja2.
- **PostgreSQL** — хранит поставщиков, историю загрузок, товары (канонические поля +
  гибкий JSON `attributes` под параметры, которые отличаются от категории к категории).
- **Docker Compose** — поднимает backend + Postgres.
- **Nginx** (на хосте, не в Docker) — принимает HTTPS-трафик по домену и проксирует
  на `127.0.0.1:8000`, где слушает backend-контейнер.

## Как добавить нового поставщика (парсер)

1. Создать файл `backend/app/parsers/<slug>.py` с классом, унаследованным от
   `BaseParser` (см. `backend/app/parsers/base.py` и пример
   `backend/app/parsers/demo.py`).
2. Метод `parse(self, file_path: str) -> List[ProductIn]` должен прочитать сырой
   файл поставщика и вернуть список товаров в канонической схеме
   (`backend/app/schemas.py: ProductIn`).
3. Зарегистрировать парсер в `backend/app/parsers/registry.py` (добавить строку в
   словарь `PARSERS`).
4. В интерфейсе (`/`) добавить поставщика с тем же `slug`.
5. Задеплоить обновление (см. ниже).

## Первый деплой на сервер

Выполняется на сервере (по SSH), из папки, куда склонирован репозиторий:

```bash
cd ~
git clone https://github.com/shaider084-gif/cloud-leon-gloves.git
cd cloud-leon-gloves/supplier-service

cp .env.example .env
nano .env   # заполнить пароли/секреты (см. комментарии в файле)

docker compose up -d --build
```

Проверка, что backend поднялся и слушает локально:
```bash
curl http://127.0.0.1:8000/health
# должно вернуть {"status":"ok"}
```

Подключить Nginx (чтобы домен показывал приложение, а не заглушку):
```bash
cp nginx/supplier-service.conf /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

После этого сайт должен открываться на `https://прайс-поставщики.рф` с формой входа.
Логин/пароль — те, что указаны в `.env` (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).

## Обновление после изменений в коде (все следующие разы)

```bash
cd ~/cloud-leon-gloves
git pull
cd supplier-service
docker compose up -d --build
```

## Бэкапы БД

```bash
docker compose exec db pg_dump -U <POSTGRES_USER> <POSTGRES_DB> > backup_$(date +%F).sql
```
Рекомендуется добавить это в cron и хранить копии отдельно от сервера.
