from typing import Optional

from .base import BaseParser
from .demo import DemoParser

# Реестр парсеров: ключ — slug поставщика (как в таблице suppliers.slug),
# значение — класс парсера. Чтобы добавить нового поставщика — импортируйте
# его класс и добавьте сюда одну строку.
PARSERS = {
    "demo": DemoParser,
}


def get_parser(slug: str) -> Optional[BaseParser]:
    parser_cls = PARSERS.get(slug)
    if not parser_cls:
        return None
    return parser_cls()
