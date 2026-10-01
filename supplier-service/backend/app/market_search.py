"""Сбор рыночных цен: поиск в Яндексе через API XMLRiver -> открываем найденные
страницы магазинов -> читаем цену со страницы -> две самые низкие цены с РАЗНЫХ
доменов -> таблица market_prices (см. market_prices.py).

Ключи XMLRIVER_USER / XMLRIVER_KEY лежат в supplier-service/.env (в git не
попадает). Пробный XML-ответ XMLRiver с additional=y_of цен не содержит, поэтому
цену читаем с самой страницы (JSON-LD Offer / itemprop=price / meta price)."""
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import dotenv_values

# Локальный запуск без Docker (см. README): supplier-service/.env лежит на диске
# рядом с проектом. В контейнере на проде такого файла нет — там ключи приходят
# через переменные окружения (docker-compose.yml), поэтому os.environ в приоритете.
_ENV = {
    **dotenv_values(os.path.join(os.path.dirname(__file__), "..", "..", ".env")),
    **{k: v for k, v in os.environ.items() if k.startswith("XMLRIVER_")},
}
XMLRIVER_URL = "http://xmlriver.com/search_yandex/xml"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PAGES = 2
FETCH_TIMEOUT = 15
WORKERS = 8
# Наш магазин защита-про.рф: цена в статистику входит, на странице помечается фиолетовым.
OWN_DOMAINS = {"защита-про.рф", "xn----7sba0ag2bgio2e.xn--p1ai"}
# Агрегаторы и доски объявлений — не магазины, их цены в статистику не берём.
AGGREGATOR_DOMAINS = {
    "market.yandex.ru", "yandex.ru", "avito.ru", "uteka.ru", "price.ru",
    "aport.ru", "e-katalog.ru", "irr.ru", "youla.ru",
}
LOOKALIKE = str.maketrans("АВЕКМНОРСТХаветкмнорстх", "ABEKMHOPCTXabetkmhopctx")  # кириллица -> латиница


def norm(text: str) -> str:
    """Верхний регистр, кириллица-двойники -> латиница, пробелы схлопнуты
    (границы слов сохраняем — по ним ищем модель как отдельный токен)."""
    return re.sub(r"[\s_]+", " ", text.translate(LOOKALIKE)).upper()


def model_token(name: str, brand: str = "") -> Optional[str]:
    """Модель из названия: "95HK", "JPC65", "JEM 137", "JM-7612", "5950".
    Ищем после бренда (если он есть в названии), иначе по всему названию."""
    text = name
    if brand:
        i = name.lower().find(brand.lower())
        if i >= 0:
            text = name[i + len(brand):]
    m = re.search(
        r"(?<![A-Za-zА-Яа-я0-9])(?:[A-Za-zА-Яа-я]{1,5}[-\s]?\d{1,6}|\d{2,6})[A-Za-zА-Яа-я]{0,3}(?![\wА-Яа-я])",
        text,
    )
    return re.sub(r"[\s\-]+", "", norm(m.group(0))) if m else None


def _model_regex(model: str) -> "re.Pattern":
    """Модель как отдельный токен; между буквами и цифрами допускаем пробел/дефис."""
    letters, digits = re.match(r"([A-Z]*)(.*)", model).groups()
    sep = r"[\s\-]?" if letters else ""
    return re.compile(rf"(?<![0-9A-Z]){re.escape(letters)}{sep}{re.escape(digits)}(?![0-9A-Z])")


BUNDLE_WORDS = ("КОМПЛЕКТ", "KIT", "НАБОР")


def has_model(soup: BeautifulSoup, model: Optional[str], name: str = "") -> bool:
    """Страница про этот товар: модель есть в title/h1/og:title как отдельный токен.
    Если страница — комплект/набор, а наш товар нет (или наоборот), это другой товар."""
    if not model:
        return True
    parts = [soup.title.get_text() if soup.title else ""]
    parts += [h.get_text() for h in soup.find_all("h1")]
    og = soup.find("meta", attrs={"property": "og:title"})
    parts.append(og.get("content", "") if og else "")
    text = norm(" ".join(parts))
    if not _model_regex(model).search(text):
        return False
    if name:
        ours = norm(name)
        return all((w in text) == (w in ours) for w in BUNDLE_WORDS)
    return True


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _in(domain: str, domains: set) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def is_own(url: str) -> bool:
    return _in(domain_of(url or ""), OWN_DOMAINS)


def search_urls(query: str, pages: int = PAGES) -> List[str]:
    """Ссылки органической выдачи Яндекса (1–2 страницы)."""
    urls: List[str] = []
    for page in range(1, pages + 1):
        params = {
            "user": _ENV["XMLRIVER_USER"], "key": _ENV["XMLRIVER_KEY"],
            "query": query, "domain": "ru", "groupby": 10, "page": page,
        }
        for attempt in range(3):  # XMLRiver иногда отвечает долго или просит "перезапрос"
            try:
                r = httpx.get(XMLRIVER_URL, params=params, timeout=60)
                root = ET.fromstring(r.content)
            except (httpx.TimeoutException, ET.ParseError):
                if attempt == 2:
                    raise
                continue
            err = root.find(".//error")
            if err is None:
                break
            retryable = "перезапрос" in (err.text or "") or "свободных каналов" in (err.text or "")
            if attempt == 2 or not retryable:
                raise RuntimeError(err.text)
            time.sleep(5)
        for doc in root.iter("doc"):
            url = doc.findtext("url")
            if url and url not in urls:
                urls.append(url)
    return urls


def _to_float(v) -> Optional[float]:
    try:
        f = float(re.sub(r"[^\d.,]", "", str(v)).replace(",", "."))
    except ValueError:
        return None
    return f if f > 0 else None


def _prices_from_jsonld(node, in_product: bool = False) -> List[float]:
    """Цены только внутри узла @type=Product: у страниц-листингов lowPrice —
    это цена самого дешёвого товара раздела, а не искомого."""
    found: List[float] = []
    if isinstance(node, list):
        for n in node:
            found += _prices_from_jsonld(n, in_product)
    elif isinstance(node, dict):
        t = node.get("@type")
        in_product = in_product or "Product" in (t if isinstance(t, list) else [t])
        if in_product:
            for key in ("price", "lowPrice"):
                if key in node and (f := _to_float(node[key])):
                    found.append(f)
        for v in node.values():
            if isinstance(v, (dict, list)):
                found += _prices_from_jsonld(v, in_product)
    return found


_RUB_AMOUNT = re.compile(r"(\d[\d  ]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)")


def _squash_digits(text: str) -> str:
    """"2 430" / "1 909" (пробел или nbsp как разделитель тысяч) -> "2430" / "1909"."""
    return re.sub(r"(?<=\d)[  ](?=\d{3}(?!\d))", "", text)


def _main_price(soup: BeautifulSoup) -> Optional[float]:
    """Цена самого товара: первая сумма в рублях сразу после заголовка h1
    (ниже обычно идут блоки "похожие товары" с чужими ценами)."""
    h1 = soup.find("h1")
    if not h1:
        return None
    chunks: List[str] = []
    size = 0
    for node in h1.find_all_next(string=True):
        if node.parent.name in ("script", "style", "noscript"):
            continue
        chunks.append(str(node))
        size += len(chunks[-1])
        if size > 2500:
            break
    m = _RUB_AMOUNT.search(_squash_digits("\n".join(c.strip() for c in chunks if c.strip())))
    return _to_float(re.sub(r"[  ]", "", m.group(1))) if m else None


def extract_price(html: str, model: Optional[str] = None, name: str = "") -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")
    if not has_model(soup, model, name):
        return None
    prices: List[float] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            prices += _prices_from_jsonld(json.loads(tag.string or ""))
        except ValueError:
            pass
    if not prices:
        for tag in soup.find_all(attrs={"itemprop": "price"}):
            if f := _to_float(tag.get("content") or tag.get_text()):
                prices.append(f)
        for prop in ("product:price:amount", "og:price:amount"):
            tag = soup.find("meta", attrs={"property": prop})
            if tag and (f := _to_float(tag.get("content"))):
                prices.append(f)
        if not prices:
            # Магазины на Shop-Script кладут цену только в аналитику:
            # [{"id":..,"name":"..","price":58.75,...}]
            for m in re.finditer(r'"name":"[^"]*","price":(\d+(?:\.\d+)?)', html):
                if f := _to_float(m.group(1)):
                    prices.append(f)
    # Эталон — цена, которую видит покупатель у заголовка товара. Разметка
    # (JSON-LD и т.п.) у части магазинов врёт (price 7 вместо 751, цена
    # аксессуара вместо товара), поэтому при расхождении верим видимой цене.
    main = _main_price(soup)
    if main:
        near = [p for p in prices if abs(p - main) <= max(1.0, main * 0.02)]
        return near[0] if near else main
    return prices[0] if prices else None


MODEL_LINKS_LIMIT = 3


def _get(url: str) -> Optional[str]:
    for attempt in range(2):  # один повтор при таймауте/сбое соединения
        try:
            r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "ru"},
                          timeout=FETCH_TIMEOUT, follow_redirects=True)
            return r.text if r.status_code == 200 else None
        except httpx.TransportError:
            continue
        except Exception:
            return None
    return None


def _model_links(html: str, base_url: str, model: str) -> List[str]:
    """Страница-раздел магазина: ссылки на карточки, у которых модель есть в
    тексте ссылки или в адресе (тот же домен, не больше MODEL_LINKS_LIMIT)."""
    soup = BeautifulSoup(html, "html.parser")
    pattern = _model_regex(model)
    domain = domain_of(base_url)
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        if domain_of(url) != domain or url == base_url or url in links:
            continue
        if pattern.search(norm(a.get_text(" ") + " " + unquote(a["href"]))):
            links.append(url)
            if len(links) == MODEL_LINKS_LIMIT:
                break
    return links


def fetch_price(url: str, model: Optional[str] = None, name: str = "") -> Tuple[str, Optional[float]]:
    """(адрес, цена). Если выдача привела на раздел магазина, а не на карточку,
    ищем в нём карточки с нужной моделью и берём цену там (возвращается адрес
    карточки)."""
    if _in(domain_of(url), AGGREGATOR_DOMAINS):
        return url, None
    html = _get(url)
    if html is None:
        return url, None
    price = extract_price(html, model, name)
    if price or not model:
        return url, price
    for link in _model_links(html, url, model):
        card = _get(link)
        if card and (price := extract_price(card, model, name)):
            return link, price
    return url, None


def find_prices(query: str, brand: str = "") -> dict:
    """Две самые низкие цены с разных доменов + статистика для замеров."""
    t0 = time.time()
    urls = search_urls(query)
    with ThreadPoolExecutor(WORKERS) as ex:
        model = model_token(query, brand)
        results = list(ex.map(lambda u: fetch_price(u, model, query), urls))
    with_price = [(p, u) for u, p in results if p]
    best_by_domain: dict = {}
    for p, u in sorted(with_price):
        best_by_domain.setdefault(domain_of(u), (p, u))
    top = sorted(best_by_domain.values())[:2]
    return {
        "top": top,  # [(price, url), ...]
        "urls": len(urls),
        "with_price": len(with_price),
        "seconds": round(time.time() - t0, 1),
    }
