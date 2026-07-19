import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from patchright.async_api import Page

from yandex_scraper.browser import check_captcha
from yandex_scraper.features.reviews.records import (
    base_url_from_reviews_url,
    extract_org_id,
    safe_name_part,
)


DEFAULT_NAV_TIMEOUT_MS = 60_000
DEFAULT_NAV_RELOAD_TIMEOUT_MS = 90_000
NAV_NETWORKIDLE_TIMEOUT_MS = 15_000
NAV_BODY_TIMEOUT_MS = 10_000
NAV_RELOAD_PAUSE_MS = 2_000

REVIEW_READY_SELECTORS = [
    '[class*="business-review-view"]',
    '[class*="business-reviews-card-view"]',
    '[class*="review-snippet"]',
]
REVIEW_WAIT_SELECTOR = ", ".join(REVIEW_READY_SELECTORS)
ORG_READY_SELECTORS = [
    '[class*="business-card"]',
    '[class*="business-card-title"]',
    '[class*="orgpage-header"]',
    '[href*="/reviews"]',
]
TEXT_REVIEWS = "\u041e\u0442\u0437\u044b\u0432\u044b"


@dataclass
class NavigationCheck:
    ready: bool
    reason: str
    signal: str = ""


@dataclass
@dataclass
class NavigationResult:
    status: str
    error: str = ""




def elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def short_exception(exc: Exception, limit: int = 240) -> str:
    return f"{type(exc).__name__}: {str(exc)[:limit]}"


def is_yandex_maps_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return "yandex." in lowered and "/maps" in lowered


async def safe_page_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


async def body_preview(page: Page, limit: int = 500) -> str:
    try:
        text = await page.locator("body").inner_text(timeout=3_000)
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


async def selector_count(page: Page, selector: str) -> int:
    try:
        return await page.locator(selector).count()
    except Exception:
        return 0


async def text_count(page: Page, text: str) -> int:
    try:
        return await page.get_by_text(text, exact=True).count()
    except Exception:
        return 0


async def wait_for_body(page: Page, *, timeout_ms: int, debug_nav: bool, label: str) -> bool:
    try:
        await page.wait_for_selector("body", timeout=timeout_ms)
        if debug_nav:
            print(f"[Reviews] [Nav] label={label} body=ok")
        return True
    except Exception as exc:
        if debug_nav:
            print(f"[Reviews] [Nav] label={label} body=timeout error={short_exception(exc, 140)}")
        return False


async def wait_for_networkidle_soft(page: Page, *, debug_nav: bool, label: str) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=NAV_NETWORKIDLE_TIMEOUT_MS)
        if debug_nav:
            print(f"[Reviews] [Nav] label={label} networkidle=ok")
    except Exception as exc:
        if debug_nav:
            print(f"[Reviews] [Nav] label={label} networkidle=soft-timeout error={short_exception(exc, 140)}")


async def diagnose_loaded_page(page: Page, row: dict, *, label: str) -> NavigationCheck:
    current_url = str(page.url or "")
    if not current_url or current_url == "about:blank":
        return NavigationCheck(False, "blank_page")

    if await check_captcha(page):
        return NavigationCheck(False, "captcha")

    if not is_yandex_maps_url(current_url):
        return NavigationCheck(False, "unexpected_redirect")

    expected_org_id = extract_org_id(row.get("org_id"), row.get("reviews_url"), row.get("org_url"))
    if expected_org_id and expected_org_id not in current_url and "/maps/org/" not in current_url.lower():
        return NavigationCheck(False, "unexpected_redirect")

    body = await body_preview(page)
    if not body:
        return NavigationCheck(False, "empty_body")

    if label == "reviews":
        for selector in REVIEW_READY_SELECTORS:
            count = await selector_count(page, selector)
            if count > 0:
                return NavigationCheck(True, "ready", f"{selector}={count}")

        reviews_text_count = await text_count(page, TEXT_REVIEWS)
        if reviews_text_count > 0:
            return NavigationCheck(True, "ready_fallback", f"text:{TEXT_REVIEWS}={reviews_text_count}")

        body_lower = body.lower()
        if "\u043e\u0442\u0437\u044b\u0432" in body_lower or "review" in body_lower:
            return NavigationCheck(True, "ready_fallback", "body_has_review_text")

        return NavigationCheck(False, "missing_reviews_dom", body)

    for selector in ORG_READY_SELECTORS:
        count = await selector_count(page, selector)
        if count > 0:
            return NavigationCheck(True, "ready", f"{selector}={count}")

    return NavigationCheck(True, "ready_fallback", "body_loaded")


async def save_debug_screenshot(
    page: Page,
    row: dict,
    *,
    label: str,
    attempt: int,
    reason: str,
    debug_dir: Path,
) -> str:
    if page.is_closed():
        return ""

    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        org_part = safe_name_part(extract_org_id(row.get("org_id"), row.get("reviews_url"), row.get("org_url")))
        label_part = safe_name_part(label)
        reason_part = safe_name_part(reason)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = debug_dir / f"{timestamp}_{org_part}_{label_part}_attempt{attempt}_{reason_part}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as exc:
        return f"screenshot_failed:{short_exception(exc, 120)}"


def nav_error_text(
    *,
    reason: str,
    page_url: str,
    title: str,
    elapsed: int,
    screenshot_path: str = "",
    detail: str = "",
) -> str:
    parts = [
        "navigation_failed",
        f"reason={reason}",
        f"elapsed_ms={elapsed}",
    ]
    if page_url:
        parts.append(f"final_url={page_url}")
    if title:
        parts.append(f"title={title[:120]}")
    if detail:
        parts.append(f"detail={detail[:240]}")
    if screenshot_path:
        parts.append(f"screenshot={screenshot_path}")
    return " | ".join(parts)


def browser_diagnostics_text(console_errors: list[str], network_failures: list[str]) -> str:
    parts = []
    if console_errors:
        parts.append("console=" + " || ".join(console_errors[-3:]))
    if network_failures:
        parts.append("network=" + " || ".join(network_failures[-3:]))
    return " | ".join(parts)


async def goto_url_soft(
    page: Page,
    row: dict,
    url: str,
    *,
    label: str,
    debug_nav: bool,
    proxy_label: str,
    proxy_pool: str,
    attempt: int,
    nav_timeout_ms: int,
    nav_reload_timeout_ms: int,
    debug_dir: Path,
    debug_screenshot: bool,
) -> NavigationResult:
    started_at = time.perf_counter()
    if debug_nav:
        print(
            f"[Reviews] [Nav] attempt={attempt} pool={proxy_pool} proxy={proxy_label} "
            f"label={label} target_url={url} timeout_ms={nav_timeout_ms}"
        )

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        if debug_nav:
            status = response.status if response else "none"
            print(
                f"[Reviews] [Nav] label={label} domcontentloaded=ok "
                f"status={status} current_url={page.url}"
            )
    except Exception as exc:
        error_raw = str(exc)
        if debug_nav:
            print(
                f"[Reviews] [Nav] label={label} error_class={type(exc).__name__} "
                f"current_url={page.url} error={error_raw[:180]}"
            )
        if "ERR_ABORTED" not in error_raw and "frame was detached" not in error_raw:
            screenshot_path = await save_debug_screenshot(
                page,
                row,
                label=label,
                attempt=attempt,
                reason=type(exc).__name__,
                debug_dir=debug_dir,
            )
            title = await safe_page_title(page)
            return NavigationResult(
                "error",
                nav_error_text(
                    reason="timeout" if "Timeout" in type(exc).__name__ else "goto_error",
                    page_url=str(page.url or ""),
                    title=title,
                    elapsed=elapsed_ms(started_at),
                    screenshot_path=screenshot_path,
                    detail=short_exception(exc),
                ),
            )

        try:
            await page.wait_for_timeout(NAV_RELOAD_PAUSE_MS)
            current_url = str(page.url or "")
            if current_url and current_url != "about:blank" and await wait_for_body(
                page,
                timeout_ms=NAV_BODY_TIMEOUT_MS,
                debug_nav=debug_nav,
                label=label,
            ):
                if debug_nav:
                    print(f"[Reviews] [Nav] label={label} soft-ok current_url={current_url}")
            else:
                raise exc
        except Exception:
            screenshot_path = await save_debug_screenshot(
                page,
                row,
                label=label,
                attempt=attempt,
                reason=type(exc).__name__,
                debug_dir=debug_dir,
            )
            title = await safe_page_title(page)
            return NavigationResult(
                "error",
                nav_error_text(
                    reason="goto_error",
                    page_url=str(page.url or ""),
                    title=title,
                    elapsed=elapsed_ms(started_at),
                    screenshot_path=screenshot_path,
                    detail=short_exception(exc),
                ),
            )

    await wait_for_networkidle_soft(page, debug_nav=debug_nav, label=label)
    await wait_for_body(page, timeout_ms=NAV_BODY_TIMEOUT_MS, debug_nav=debug_nav, label=label)

    check = await diagnose_loaded_page(page, row, label=label)
    if debug_nav:
        title = await safe_page_title(page)
        print(
            f"[Reviews] [Nav] label={label} check={check.reason} signal={check.signal[:180]} "
            f"current_url={page.url} title={title[:120]} elapsed_ms={elapsed_ms(started_at)}"
        )
    if check.ready:
        if debug_screenshot and debug_nav:
            screenshot_path = await save_debug_screenshot(
                page,
                row,
                label=label,
                attempt=attempt,
                reason=f"{check.reason}_ok",
                debug_dir=debug_dir,
            )
            print(f"[Reviews] [Nav] label={label} debug_screenshot={screenshot_path}")
        return NavigationResult("ok")
    if check.reason == "captcha":
        return NavigationResult("captcha", f"captcha_detected | final_url={page.url}")

    if debug_nav:
        print(
            f"[Reviews] [Nav] label={label} reload=scheduled reason={check.reason} "
            f"timeout_ms={nav_reload_timeout_ms}"
        )
    try:
        await page.wait_for_timeout(NAV_RELOAD_PAUSE_MS)
        response = await page.reload(wait_until="domcontentloaded", timeout=nav_reload_timeout_ms)
        if debug_nav:
            status = response.status if response else "none"
            print(
                f"[Reviews] [Nav] label={label} reload_domcontentloaded=ok "
                f"status={status} current_url={page.url}"
            )
        await wait_for_networkidle_soft(page, debug_nav=debug_nav, label=label)
        await wait_for_body(page, timeout_ms=NAV_BODY_TIMEOUT_MS, debug_nav=debug_nav, label=label)
    except Exception as exc:
        screenshot_path = await save_debug_screenshot(
            page,
            row,
            label=label,
            attempt=attempt,
            reason=type(exc).__name__,
            debug_dir=debug_dir,
        )
        title = await safe_page_title(page)
        return NavigationResult(
            "error",
            nav_error_text(
                reason="reload_error",
                page_url=str(page.url or ""),
                title=title,
                elapsed=elapsed_ms(started_at),
                screenshot_path=screenshot_path,
                detail=short_exception(exc),
            ),
        )

    check = await diagnose_loaded_page(page, row, label=label)
    title = await safe_page_title(page)
    if debug_nav:
        print(
            f"[Reviews] [Nav] label={label} reload_check={check.reason} "
            f"signal={check.signal[:180]} current_url={page.url} "
            f"title={title[:120]} elapsed_ms={elapsed_ms(started_at)}"
        )
    if check.ready:
        return NavigationResult("ok")
    if check.reason == "captcha":
        return NavigationResult("captcha", f"captcha_detected | final_url={page.url}")

    screenshot_path = await save_debug_screenshot(
        page,
        row,
        label=label,
        attempt=attempt,
        reason=check.reason,
        debug_dir=debug_dir,
    )
    return NavigationResult(
        "error",
        nav_error_text(
            reason=check.reason,
            page_url=str(page.url or ""),
            title=title,
            elapsed=elapsed_ms(started_at),
            screenshot_path=screenshot_path,
            detail=check.signal,
        ),
    )

async def navigate_to_reviews(
    page: Page,
    row: dict,
    *,
    nav_mode: str,
    debug_nav: bool,
    proxy_label: str,
    proxy_pool: str,
    attempt: int,
    nav_timeout_ms: int,
    nav_reload_timeout_ms: int,
    debug_dir: Path,
    debug_screenshot: bool,
    after_base: Callable[[Page], Awaitable[None]] | None = None,
) -> NavigationResult:
    reviews_url = row["reviews_url"]
    if nav_mode == "direct":
        return await goto_url_soft(
            page,
            row,
            reviews_url,
            label="reviews",
            debug_nav=debug_nav,
            proxy_label=proxy_label,
            proxy_pool=proxy_pool,
            attempt=attempt,
            nav_timeout_ms=nav_timeout_ms,
            nav_reload_timeout_ms=nav_reload_timeout_ms,
            debug_dir=debug_dir,
            debug_screenshot=debug_screenshot,
        )

    base_url = base_url_from_reviews_url(reviews_url)
    base_result = await goto_url_soft(
        page,
        row,
        base_url,
        label="base",
        debug_nav=debug_nav,
        proxy_label=proxy_label,
        proxy_pool=proxy_pool,
        attempt=attempt,
        nav_timeout_ms=nav_timeout_ms,
        nav_reload_timeout_ms=nav_reload_timeout_ms,
        debug_dir=debug_dir,
        debug_screenshot=debug_screenshot,
    )
    if base_result.status != "ok":
        return base_result
    if after_base is not None:
        await after_base(page)
    return await goto_url_soft(
        page,
        row,
        reviews_url,
        label="reviews",
        debug_nav=debug_nav,
        proxy_label=proxy_label,
        proxy_pool=proxy_pool,
        attempt=attempt,
        nav_timeout_ms=nav_timeout_ms,
        nav_reload_timeout_ms=nav_reload_timeout_ms,
        debug_dir=debug_dir,
        debug_screenshot=debug_screenshot,
    )
