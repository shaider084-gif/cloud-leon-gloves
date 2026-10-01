import os
import re
from html import unescape
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str) -> str:
    """Убирает HTML-теги и раскрывает сущности (&mdash; и т.п.) — поле
    "Описание" хранит готовую разметку для карточки товара (см. SEO-описания),
    но в таблицах нужен читаемый превью-текст, а не сырые теги."""
    text = _TAG_RE.sub(" ", value or "")
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def static_version() -> int:
    """Время изменения style.css — используется как ?v= в base.html, чтобы
    браузер не показывал закэшированные старые стили после правок (иначе
    пользователю приходится делать hard refresh вручную)."""
    try:
        return int(os.path.getmtime(os.path.join(STATIC_DIR, "style.css")))
    except OSError:
        return 0


templates.env.globals["static_version"] = static_version
# Нужен для catalog_view.html — там набор колонок динамический (зависит от
# фильтров), поэтому колонка адресуется по имени поля модели, а не жёстко
# захардкожена в шаблоне.
templates.env.globals["getattr"] = getattr
templates.env.filters["strip_html"] = strip_html
