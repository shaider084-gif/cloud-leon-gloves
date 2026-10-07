from typing import Optional

from .base import BaseParser
from .demo import DemoParser
from .fest import FestParser
from .foxweld import FoxweldParser
from .gward import GwardParser
from .jeta import JetaParser
from .mapping import MappingParser
from .spetszaschita import SpetszaschitaParser
from .technoavia import TechnoaviaParser

# Реестр парсеров: ключ — slug поставщика (как в таблице suppliers.slug),
# значение — класс парсера. Чтобы добавить нового поставщика — импортируйте
# его класс и добавьте сюда одну строку.
PARSERS = {
    "demo": DemoParser,
    "fest": FestParser,
    "foxweld": FoxweldParser,
    "gward": GwardParser,
    "jeta": JetaParser,
    "pkf_spetczaschita": SpetszaschitaParser,
    "technoavia_spetsobuv": TechnoaviaParser,
}


def get_parser(slug: str) -> Optional[BaseParser]:
    parser_cls = PARSERS.get(slug)
    if not parser_cls:
        return None
    return parser_cls()


def resolve_parser(supplier) -> BaseParser:
    """Кастомный парсер (сложный формат файла, см. get_parser) имеет приоритет
    над визуальным маппингом колонок — используется и обычной загрузкой
    (routers/suppliers.py), и сценарием "Обновление цен" (routers/price_updates.py),
    чтобы у поставщика со своим парсером сверка с текущим каталогом тоже
    проходила через его собственные правила, а не через сырой column_mapping."""
    custom_parser = get_parser(supplier.slug)
    if custom_parser:
        return custom_parser
    return MappingParser(supplier.column_mapping, supplier.markup_percent)
