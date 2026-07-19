from patchright.async_api import BrowserContext, Page

from yandex_scraper.config import (
    ALLOWED_XHR_FETCH_PATTERNS,
    BLOCKED_DOMAINS,
    BLOCKED_MAP_PATTERNS,
    CAPTCHA_SELECTORS,
    CAPTCHA_URL_MARKERS,
    STRICT_XHR_FETCH_FILTER,
)
from yandex_scraper.constants import _ALLOWED_RESOURCE_TYPES

RequestBlockingState = dict[str, object]


async def setup_request_blocking(
    context: BrowserContext,
    strict_xhr_fetch_filter: bool | None = None,
    blocked_resource_types: set[str] | None = None,
    block_map_tiles: bool = True,
) -> RequestBlockingState:
    if strict_xhr_fetch_filter is None:
        strict_xhr_fetch_filter = STRICT_XHR_FETCH_FILTER
    state: RequestBlockingState = {
        "strict_xhr_fetch_filter": strict_xhr_fetch_filter,
        "blocked_resource_types": blocked_resource_types,
        "block_map_tiles": block_map_tiles,
    }

    async def handle_request(route, request):
        url_lower = request.url.lower()
        current_blocked_resource_types = state.get("blocked_resource_types")

        # Блокировка по типу ресурса
        if current_blocked_resource_types is None:
            if request.resource_type not in _ALLOWED_RESOURCE_TYPES:
                await route.abort()
                return
        elif request.resource_type in current_blocked_resource_types:
            await route.abort()
            return

        # Блокировка по доменам (реклама, аналитика)
        for pattern in BLOCKED_DOMAINS:
            if pattern in url_lower:
                await route.abort()
                return

        # БЛОКИРОВКА КАРТЫ (экономия трафика)
        if state.get("block_map_tiles"):
            for pattern in BLOCKED_MAP_PATTERNS:
                if pattern in url_lower:
                    await route.abort()
                    return

        # Строгая фильтрация XHR/FETCH (дополнительная экономия)
        if state.get("strict_xhr_fetch_filter") and request.resource_type in {"xhr", "fetch"}:
            if not any(pat in url_lower for pat in ALLOWED_XHR_FETCH_PATTERNS):
                await route.abort()
                return

        await route.continue_()

    await context.route("**/*", handle_request)
    return state


async def check_captcha(page: Page) -> bool:
    url_lower = page.url.lower()
    for marker in CAPTCHA_URL_MARKERS:
        if marker in url_lower:
            return True
    for selector in CAPTCHA_SELECTORS:
        try:
            if await page.query_selector(selector):
                return True
        except Exception:
            pass
    return False
