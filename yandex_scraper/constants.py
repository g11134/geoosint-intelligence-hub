import re

from yandex_scraper.config import BLOCKED_RESOURCE_TYPES

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r'^\d{5,}$')
_KNOWN_RESOURCE_TYPES = {
    "document", "stylesheet", "image", "media", "font", "script",
    "texttrack", "xhr", "fetch", "eventsource", "websocket",
    "manifest", "other",
}
_ALLOWED_RESOURCE_TYPES = _KNOWN_RESOURCE_TYPES - BLOCKED_RESOURCE_TYPES

EMPTY_SCROLL_LIMIT = 3
QUEUE_SAVE_BATCH_SIZE = 10
QUEUE_SAVE_MAX_DELAY_SEC = 60.0
_SENTINEL = object()

