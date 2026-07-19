"""
config.py — центральный файл конфигурации парсера Яндекс Карт
Все настройки хранятся здесь. Скрипты только импортируют.
"""

import json
import os
import random
import socket
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


# ══════════════════════════════════════════════════════════════════
# РАЗДЕЛ 1 — ПУТИ К ФАЙЛАМ
# ══════════════════════════════════════════════════════════════════

# Корневая папка проекта и директории данных.
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
BASE_DIR = PROJECT_ROOT


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return Path(raw).expanduser().resolve()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y", "да"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DATA_DIR = _env_path("YANDEX_SCRAPER_DATA_DIR", PROJECT_ROOT / "data")

INPUT_DIR = DATA_DIR / "input"
STATE_DIR = DATA_DIR / "state"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_DIR = DATA_DIR / "output"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"
TMP_DIR = DATA_DIR / "tmp"
ANALYTICS_DIR = DATA_DIR / "analytics"

# Входные данные
SOURCE_POLYGON_FILE = INPUT_DIR / "spb_polygon.geojson"      # детальный контур города
POLYGON_FILE        = INPUT_DIR / "spb_polygon.json"         # контур города (подготовленный)
FIXED_GRID_FILE     = OUTPUT_DIR / "grid_visualization.geojson" # fixed-grid разметка
QUEUE_FILE          = STATE_DIR / "parsing_queue.csv"        # очередь URL для парсинга

# Выходные данные
JSONL_FILE = RAW_DIR / "raw_data.jsonl"       # сырые результаты (одна строка = один объект)
FIELD_AUDIT_FILE = RAW_DIR / "field_audit.jsonl"  # диагностический снимок видимых XHR/DOM данных
ENRICHED_JSONL_FILE = RAW_DIR / "enriched_data.jsonl"  # расширенные результаты XHR + DOM
CSV_FILE   = OUTPUT_DIR / "result.csv"        # финальная таблица
ENRICHED_CSV_FILE = OUTPUT_DIR / "enriched_result.csv"  # расширенная финальная таблица
EXCEL_FILE = OUTPUT_DIR / "raw_data.xlsx"     # Excel-экспорт
DB_FILE    = STATE_DIR / "seen_ids.db"        # SQLite база дедупликации
ORGANIZATIONS_DB_FILE = OUTPUT_DIR / "organizations.db"  # SQLite read-model для API/Flutter
REVIEWS_ANALYTICS_SOURCE_FILE = _env_path(
    "YANDEX_SCRAPER_REVIEWS_ANALYTICS_SOURCE_FILE",
    OUTPUT_DIR / "reviews.csv",
)
ORGANIZATION_DETAILS_JSONL_FILE = _env_path(
    "YANDEX_SCRAPER_ORGANIZATION_DETAILS_JSONL_FILE",
    RAW_DIR / "organization_details.jsonl",
)
ORGANIZATION_SERVICES_JSONL_FILE = _env_path(
    "YANDEX_SCRAPER_ORGANIZATION_SERVICES_JSONL_FILE",
    RAW_DIR / "organization_services.jsonl",
)
REVIEW_AI_CACHE_DIR = _env_path(
    "YANDEX_SCRAPER_REVIEW_AI_CACHE_DIR",
    ANALYTICS_DIR / "review_ai",
)
REVIEW_AI_RADIUS_CACHE_DIR = _env_path(
    "YANDEX_SCRAPER_REVIEW_AI_RADIUS_CACHE_DIR",
    ANALYTICS_DIR / "review_ai_radius",
)
REVIEW_DYNAMICS_START_DATE = (
    os.environ.get("YANDEX_SCRAPER_REVIEW_DYNAMICS_START_DATE", "2026-01-01").strip()
    or "2026-01-01"
)
REVIEW_DYNAMICS_OUTPUT_DIR = _env_path(
    "YANDEX_SCRAPER_REVIEW_DYNAMICS_OUTPUT_DIR",
    OUTPUT_DIR / "analytics",
)
REVIEW_DYNAMICS_PERIODS = [7, 30, 90]
# Reviews parser date filter. Empty value disables date filtering.
# Keeps reviews with date >= REVIEWS_DATE_FROM.
REVIEWS_DATE_FROM = os.environ.get("YANDEX_SCRAPER_REVIEWS_DATE_FROM", "2026-01-01").strip() or None
REVIEWS_MAX_REVIEWS = max(1, _env_int("YANDEX_SCRAPER_REVIEWS_MAX_REVIEWS", 500))
REVIEWS_SCROLL_STEPS = max(1, _env_int("YANDEX_SCRAPER_REVIEWS_SCROLL_STEPS", 120))
REVIEWS_SCROLL_NO_GROWTH_LIMIT = max(1, _env_int("YANDEX_SCRAPER_REVIEWS_SCROLL_NO_GROWTH_LIMIT", 8))
REVIEWS_STORE_ORGANIZATION_REPLY_TEXT = _env_bool("YANDEX_SCRAPER_REVIEWS_STORE_ORGANIZATION_REPLY_TEXT", False)
ORGANIZATION_DETAILS_ENABLED = _env_bool("YANDEX_SCRAPER_ORGANIZATION_DETAILS_ENABLED", True)
ORGANIZATION_DETAILS_MISSING_TEXT = (
    os.environ.get("YANDEX_SCRAPER_ORGANIZATION_DETAILS_MISSING_TEXT", "данные отсутствуют").strip()
    or "данные отсутствуют"
)
ORGANIZATION_DETAILS_MAX_ITEMS = max(1, _env_int("YANDEX_SCRAPER_ORGANIZATION_DETAILS_MAX_ITEMS", 300))
ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS = max(
    500,
    _env_int("YANDEX_SCRAPER_ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS", 40000),
)
REVIEW_AI_PROVIDER = os.environ.get("YANDEX_SCRAPER_REVIEW_AI_PROVIDER", "gemini").strip().casefold() or "gemini"
REVIEW_AI_MODEL = os.environ.get("YANDEX_SCRAPER_REVIEW_AI_MODEL", "").strip()
REVIEW_AI_TIMEOUT_SEC = max(1, _env_int("YANDEX_SCRAPER_REVIEW_AI_TIMEOUT_SEC", 30))
REVIEW_AI_MAX_REVIEWS = max(1, _env_int("YANDEX_SCRAPER_REVIEW_AI_MAX_REVIEWS", 500))
REVIEW_AI_MAX_REVIEW_TEXT_CHARS = max(80, _env_int("YANDEX_SCRAPER_REVIEW_AI_MAX_REVIEW_TEXT_CHARS", 120))
OPENROUTER_MODEL = (
    os.environ.get(
        "YANDEX_SCRAPER_OPENROUTER_MODEL",
        REVIEW_AI_MODEL if REVIEW_AI_PROVIDER == "openrouter" and REVIEW_AI_MODEL else "openrouter/free",
    ).strip()
    or "openrouter/free"
)
OLLAMA_MODEL = (
    os.environ.get(
        "YANDEX_SCRAPER_OLLAMA_MODEL",
        REVIEW_AI_MODEL if REVIEW_AI_PROVIDER == "ollama" and REVIEW_AI_MODEL else "qwen2.5:7b",
    ).strip()
    or "qwen2.5:7b"
)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/") or "http://127.0.0.1:11434"
LMSTUDIO_MODEL = (
    os.environ.get(
        "YANDEX_SCRAPER_LMSTUDIO_MODEL",
        REVIEW_AI_MODEL if REVIEW_AI_PROVIDER == "lmstudio" and REVIEW_AI_MODEL else "local-model",
    ).strip()
    or "local-model"
)
LMSTUDIO_BASE_URL = (
    os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").strip().rstrip("/")
    or "http://127.0.0.1:1234/v1"
)
GEMINI_MODEL = (
    os.environ.get(
        "YANDEX_SCRAPER_GEMINI_MODEL",
        REVIEW_AI_MODEL if REVIEW_AI_PROVIDER == "gemini" and REVIEW_AI_MODEL else "gemini-3-flash-preview",
    ).strip()
    or "gemini-3-flash-preview"
)
GEMINI_TIMEOUT_SEC = max(1, _env_int("YANDEX_SCRAPER_GEMINI_TIMEOUT_SEC", REVIEW_AI_TIMEOUT_SEC))

# Файл кеша водных объектов (создаётся автоматически при первом запуске)
WATER_CACHE_FILE = CACHE_DIR / "water_mask_cache.geojson"
GRID_LOG_FILE = LOGS_DIR / "grid_generator.log"


def ensure_data_dirs() -> None:
    """Create runtime data directories expected by the pipeline."""
    for directory in (
        INPUT_DIR,
        STATE_DIR,
        RAW_DIR,
        OUTPUT_DIR,
        CACHE_DIR,
        LOGS_DIR,
        TMP_DIR,
        ANALYTICS_DIR,
        REVIEW_AI_CACHE_DIR,
        REVIEW_AI_RADIUS_CACHE_DIR,
        REVIEW_DYNAMICS_OUTPUT_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


ensure_data_dirs()


# ══════════════════════════════════════════════════════════════════
# РАЗДЕЛ 2 — ПРОКСИ И БРАУЗЕР
# ══════════════════════════════════════════════════════════════════

# Гибридная схема прокси:
# 1) PROXIES_PRIMARY  — основной (обычно дешевле: ISP/DC)
# 2) PROXIES_FALLBACK — fallback (обычно надежнее: Residential)
#
# Секреты не хранятся в репозитории. Каждый пул задаётся тремя переменными
# окружения: *_SERVER, *_USERNAME и *_PASSWORD. Пустой пул допускается на
# этапе импорта конфигурации, но запуск прокси-зависимого прохода завершится
# понятной ошибкой, если обязательные переменные не заданы.


def _load_proxy_pool(env_prefix: str) -> list[dict[str, str]]:
    """Load one proxy pool from environment variables without exposing secrets."""
    variable_names = {
        "server": f"{env_prefix}_SERVER",
        "username": f"{env_prefix}_USERNAME",
        "password": f"{env_prefix}_PASSWORD",
    }
    values = {
        field: os.environ.get(variable_name, "").strip()
        for field, variable_name in variable_names.items()
    }

    if not any(values.values()):
        return []

    missing = [
        variable_name
        for field, variable_name in variable_names.items()
        if not values[field]
    ]
    if missing:
        raise RuntimeError(
            f"Incomplete proxy configuration for {env_prefix}. "
            f"Missing environment variables: {', '.join(missing)}"
        )

    return [values]


PROXIES_PRIMARY = _load_proxy_pool("YANDEX_PROXY_PRIMARY")
PROXIES_FALLBACK = _load_proxy_pool("YANDEX_PROXY_FALLBACK")

# Диапазоны портов для ротации у провайдера (включительно).
# Пример: (10000, 10099) -> случайный порт из 100 значений.
PROXY_PRIMARY_PORT_RANGE = (10000, 10099)
PROXY_FALLBACK_PORT_RANGE = (10000, 10099)

# Резервный порт, если выбранный из диапазона недоступен.
PROXY_PORT_FALLBACK = 10000

# Проверка доступности порта перед использованием.
PROXY_PORT_PROBE_ENABLED = True
PROXY_PORT_PROBE_TIMEOUT_SEC = 0.35
PROXY_PORT_PROBE_SAMPLES = 5

# Обратная совместимость со старым именем списка прокси
PROXIES = PROXIES_PRIMARY


def get_random_proxy_primary() -> dict:
    """Возвращает случайный прокси для primary-прохода."""
    if not PROXIES_PRIMARY:
        raise RuntimeError(
            "Primary proxy is not configured. Set "
            "YANDEX_PROXY_PRIMARY_SERVER, YANDEX_PROXY_PRIMARY_USERNAME, "
            "and YANDEX_PROXY_PRIMARY_PASSWORD."
        )
    proxy = dict(random.choice(PROXIES_PRIMARY))
    proxy["server"] = _apply_random_port(
        str(proxy["server"]),
        PROXY_PRIMARY_PORT_RANGE,
    )
    return proxy


def get_random_proxy_fallback() -> dict:
    """Возвращает случайный прокси для fallback-прохода."""
    if not PROXIES_FALLBACK:
        raise RuntimeError(
            "Fallback proxy is not configured. Set "
            "YANDEX_PROXY_FALLBACK_SERVER, YANDEX_PROXY_FALLBACK_USERNAME, "
            "and YANDEX_PROXY_FALLBACK_PASSWORD."
        )
    proxy = dict(random.choice(PROXIES_FALLBACK))
    proxy["server"] = _apply_random_port(
        str(proxy["server"]),
        PROXY_FALLBACK_PORT_RANGE,
    )
    return proxy


def get_random_proxy() -> dict:
    """Обратная совместимость: возвращает primary-прокси."""
    return get_random_proxy_primary()


def _apply_random_port(server: str, port_range: tuple[int, int] | None) -> str:
    """Возвращает server URL с подставленным рабочим портом."""
    parsed = urlsplit(server)
    if not parsed.scheme or not parsed.hostname:
        return server

    fallback_port = parsed.port or PROXY_PORT_FALLBACK
    if not port_range:
        return _format_server_with_port(parsed, fallback_port)

    min_port, max_port = port_range
    if min_port > max_port:
        min_port, max_port = max_port, min_port

    all_ports = list(range(min_port, max_port + 1))
    if not all_ports:
        return _format_server_with_port(parsed, fallback_port)

    sample_size = min(max(1, PROXY_PORT_PROBE_SAMPLES), len(all_ports))
    candidates = random.sample(all_ports, sample_size)
    if fallback_port not in candidates:
        candidates.append(fallback_port)

    if not PROXY_PORT_PROBE_ENABLED:
        return _format_server_with_port(parsed, random.choice(candidates))

    for port in candidates:
        if _is_tcp_port_open(parsed.hostname, port, PROXY_PORT_PROBE_TIMEOUT_SEC):
            return _format_server_with_port(parsed, port)

    return _format_server_with_port(parsed, fallback_port)


def _format_server_with_port(parsed, port: int) -> str:
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _is_tcp_port_open(host: str, port: int, timeout_sec: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


# Типы ресурсов которые НЕ блокируем (нужны для работы карт)
BLOCKED_RESOURCE_TYPES = {
    "image", "media", "font", "stylesheet",
    "texttrack", "eventsource", "websocket",
    "manifest", "other",
}

# Домены которые блокируем полностью (реклама, аналитика)
BLOCKED_DOMAINS = [
    "mc.yandex.ru",
    "an.yandex.ru",
    "yastatic.net/metrika",
    "google-analytics.com",
    "googletagmanager.com",
    "amplitude.com",
    "sentry.io",
    "surveys.yandex",
    "static-mon.yandex",
    "maps/api/taxi",
    "taxi.yandex",
]

# Блокировка тайлов карт Яндекс (экономия 40-60% трафика)
BLOCKED_MAP_PATTERNS = [
    "maps.yandex.net/tiles",
    "maps.yandex.net/tiles.lbs",
    "/tiles?",
    "/tile?",
    "vec01.maps.yandex.net",
    "core-renderer-tiles.maps.yandex.net",
    "sat01.maps.yandex.net",
    "traffic.maps.yandex.net",
    "layer01.maps.yandex.net",
]

# HTTP-заголовки браузера — имитируем живого пользователя
BROWSER_HEADERS = {
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "DNT":             "1",
}

# Headful mode for manual visual checks:
# set YANDEX_SCRAPER_HEADFUL=1 to open Chromium windows during parsing.
BROWSER_HEADLESS = not _env_bool("YANDEX_SCRAPER_HEADFUL", False)

# Признаки капчи — CSS-селекторы
CAPTCHA_SELECTORS = [
    '[class*="captcha"]',
    '[id*="captcha"]',
    'form[action*="captcha"]',
    '[class*="CheckboxCaptcha"]',
    '[class*="AdvancedCaptcha"]',
]

# Признаки капчи — фрагменты URL
CAPTCHA_URL_MARKERS = [
    "showcaptcha",
    "checkcaptcha",
    "/captcha/",
    "captcha=1",
]

# Функция санитизации URL (убирает лишние параметры)
def sanitize_url(url: str) -> str:
    """Возвращает URL без параметров отслеживания."""
    # Оставляем URL как есть — он уже сформирован корректно генератором сетки
    return url.strip()


# ══════════════════════════════════════════════════════════════════
# РАЗДЕЛ 3 — ПАРАМЕТРЫ СЕТКИ
# ══════════════════════════════════════════════════════════════════

# Размер одной ячейки сетки в метрах
CELL_SIZE_METERS = 1500

# Фильтрация водных объектов (Невская губа, реки, каналы).
# True  — скачивает водные полигоны из OSM и убирает ячейки,
#         чей центр находится на воде. Требует: pip install osmnx
# False — поведение v1, вода остаётся в очереди
FILTER_WATER = True

# Минимальная доля суши в ячейке (0.0..1.0), при которой
# "пограничная" ячейка (центр на воде) всё равно остаётся в очереди.
# Пример: 0.05 = оставить ячейку, если суши >= 5% её городской части.
WATER_MIN_LAND_SHARE = 0.05

def _env_search_queries(default: list[str]) -> list[str]:
    raw = os.environ.get("YANDEX_SCRAPER_SEARCH_QUERIES", "").strip()
    if not raw:
        return default

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        queries = [str(item).strip() for item in parsed if str(item).strip()]
        return queries or default

    separator = "|" if "|" in raw else ","
    queries = [part.strip() for part in raw.split(separator) if part.strip()]
    return queries or default


# Поисковые запросы — по каждому создаётся отдельный URL для каждой ячейки
SEARCH_QUERIES = _env_search_queries([
    "стоматология",
])


# ══════════════════════════════════════════════════════════════════
# РАЗДЕЛ 4 — ПАРАМЕТРЫ ПАРСЕРА
# ══════════════════════════════════════════════════════════════════

# Паузы между ячейками (используются напрямую, случайно)
PAUSE_BETWEEN_CELLS = (40, 81)  # (мин, макс) в секундах

# Паузы скроллинга (используются напрямую, случайно)
SCROLL_PAUSE_MIN = 0.114141   # секунд
SCROLL_PAUSE_MAX = 0.654356   # секунд

# Порядок ожидания загрузки страницы:
# сначала быстрый вариант, затем более "тяжёлый" fallback
NAV_WAIT_UNTIL_SEQUENCE = ("domcontentloaded", "networkidle")

# Лимиты скроллинга (умеренно сокращают сетевые догрузки)
SCROLL_MAX_STEPS = 30
SCROLL_NO_GROWTH_LIMIT = 2
SCROLL_NO_GROWTH_MIN_STEP = 1

# Блокировать Service Workers (снижает фоновый сетевой шум)
BLOCK_SERVICE_WORKERS = True

# Строгая фильтрация XHR/FETCH:
# если True, пропускаются только запросы, совпавшие с паттернами ниже
STRICT_XHR_FETCH_FILTER = True
ALLOWED_XHR_FETCH_PATTERNS = [
    "yandex.ru/maps/",
    "yandex.ru/maps-api/",
    "api-maps.yandex.ru",
    "api/search",
    "api/business",
    "sprav.yandex",
    "/search?",
    "textsearch",
    "search-snippet",
    "features=",
]

# Авто-fallback: если экономный проход выглядит "рискованным",
# запускаем повторный мягкий проход для снижения потери карточек.
ENABLE_SAFE_FALLBACK_PASS = True
FALLBACK_MIN_RESULTS = 3

# Диагностический режим: пишет отдельный JSONL со срезом данных, видимых во время скролла.
# Не влияет на raw_data.jsonl, FINAL_COLUMNS, очередь и дедупликацию.
FIELD_AUDIT_ENABLED = _env_bool("YANDEX_SCRAPER_FIELD_AUDIT_ENABLED", False)
FIELD_AUDIT_MAX_RECORDS_PER_CELL = max(
    0,
    _env_int("YANDEX_SCRAPER_FIELD_AUDIT_MAX_RECORDS_PER_CELL", 50),
)
FIELD_AUDIT_RAW_PREVIEW_MAX_CHARS = max(
    500,
    _env_int("YANDEX_SCRAPER_FIELD_AUDIT_RAW_PREVIEW_MAX_CHARS", 20000),
)

# Расширенный сбор: отдельный контракт XHR + DOM без изменения legacy FINAL_COLUMNS.
ENRICHED_DATA_ENABLED = _env_bool("YANDEX_SCRAPER_ENRICHED_DATA_ENABLED", False)

# Перемешивать порядок pending-ячеек перед запуском.
# Это снижает вероятность последовательного обхода соседних ячеек.
RANDOMIZE_PENDING_QUEUE = True

# Разносить pending-ячейки по пространственным корзинам перед выдачей воркерам.
# Это помогает 2-4 воркерам стартовать и продолжать работу в разных частях сетки.
SPREAD_PENDING_QUEUE = True
PENDING_QUEUE_SPREAD_BUCKETS_PER_AXIS = 8

# Максимум загрузок страницы с капчей за один проход ячейки.
# 1 = не тратим трафик на повторную загрузку той же капчи в той же сессии.
CAPTCHA_MAX_RETRIES = 1

# Сколько отложенных captcha-проходов выполнить после основного pending-прохода.
# 1 = сначала пройти остальные ячейки, затем один раз вернуться к captcha-ячейкам.
CAPTCHA_DEFERRED_PASSES = 1

# Сколько раз дополнительно сменить прокси при статусе "captcha" на ячейке.
# 1 = один раз сменить прокси и повторить captcha-ячейку в финальном проходе.
CAPTCHA_PROXY_ROTATIONS = 1

# Сколько раз дополнительно сменить прокси при статусе "error" на ячейке.
# 0 = старое поведение без ротации по ошибке.
ERROR_PROXY_ROTATIONS = 2

# Колонки итогового CSV — порядок важен
FINAL_COLUMNS = [
    "title",
    "shortTitle",
    "fullAddress",
    "categories_0_name",
    "phones_0_number",
    "coordinates_0",
    "coordinates_1",
    "permalink",
    "ratingData_ratingCount",
    "ratingData_ratingValue",
    "source_query",
    "source_bbox",
]

ENRICHED_COLUMNS = [
    "captured_at",
    "source_query",
    "source_bbox",
    "cell_url",
    "search_result_index",
    "yandex_id",
    "permalink",
    "org_url",
    "title",
    "fullAddress",
    "categories_0_name",
    "raw_categories_json",
    "phones_0_number",
    "raw_phones_json",
    "coordinates_0",
    "coordinates_1",
    "rating_count",
    "rating_value",
    "review_count",
    "raw_ratingData_json",
    "raw_features_json",
    "raw_urls_json",
    "website_url",
    "photos_count",
    "first_photo_url",
    "raw_photos_json",
    "business_verified_owner",
    "dom_category",
    "dom_visibleText",
    "open_status_text",
    "awards_text",
    "offer_text",
    "gallery_url",
    "reviews_url",
    "dom_image_url",
]


# ══════════════════════════════════════════════════════════════════
# РАЗДЕЛ 5 — ВОРКЕРЫ И RATE LIMITER
# ══════════════════════════════════════════════════════════════════

# Количество параллельных воркеров (каждый = отдельный браузер)
# 1 воркер ≈ 300–500 МБ RAM
# Рекомендуется: 2 при 8 ГБ RAM, 3–4 при 16 ГБ RAM
WORKERS_COUNT = 2

# Максимум browser-сессий в минуту (суммарно по всем воркерам)
# При превышении воркер уходит в паузу до освобождения окна
MAX_REQUESTS_PER_MINUTE = 3

