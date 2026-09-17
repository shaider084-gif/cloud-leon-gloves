from abc import ABC, abstractmethod
from typing import List

from ..schemas import ProductIn


class BaseParser(ABC):
    """
    Базовый интерфейс парсера поставщика.

    Чтобы добавить нового поставщика:
      1. Создать файл app/parsers/<slug>.py с классом, унаследованным от BaseParser.
      2. Реализовать метод parse(file_path) -> List[ProductIn].
      3. Зарегистрировать его в app/parsers/registry.py (PARSERS = {"<slug>": ...}).
      4. Добавить поставщика в БД (через UI "Добавить поставщика", slug должен совпадать).
    """

    #: человекочитаемое имя, показывается в интерфейсе как заглушка при создании
    display_name: str = "Unnamed parser"

    @abstractmethod
    def parse(self, file_path: str) -> List[ProductIn]:
        """Прочитать сырой файл поставщика и вернуть список товаров в канонической схеме."""
        raise NotImplementedError
