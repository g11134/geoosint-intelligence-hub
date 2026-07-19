import argparse
import asyncio
import hashlib
import json
import random
from datetime import date
from pathlib import Path

from patchright.async_api import BrowserContext, async_playwright

from yandex_scraper.browser import RequestBlockingState, check_captcha, setup_request_blocking
from yandex_scraper.config import (
    BLOCK_SERVICE_WORKERS,
    BROWSER_HEADERS,
    DATA_DIR,
    ORGANIZATION_DETAILS_ENABLED,
    ORGANIZATION_DETAILS_JSONL_FILE,
    ORGANIZATION_DETAILS_MAX_ITEMS,
    ORGANIZATION_DETAILS_MISSING_TEXT,
    ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS,
    ORGANIZATION_SERVICES_JSONL_FILE,
    REVIEWS_DATE_FROM,
    REVIEWS_MAX_REVIEWS,
    REVIEWS_SCROLL_NO_GROWTH_LIMIT,
    REVIEWS_SCROLL_STEPS,
    get_random_proxy_fallback,
    get_random_proxy_primary,
)
from yandex_scraper.features.organization_details import (
    append_organization_details_record,
    append_organization_services_record,
    build_organization_details_error_record,
    build_organization_services_error_record,
    collect_organization_details_from_page,
)
from yandex_scraper.features.reviews.date_filter import (
    DateFilterResult,
    filter_reviews_by_date,
    is_missing_review_date_error,
    missing_review_date_error,
    parse_iso_date,
    parse_review_date,
)
from yandex_scraper.features.reviews.extractors import (
    click_reviews_load_more,
    expand_visible_reviews,
    extract_reviews_from_dom,
    extract_reviews_from_json,
    get_expected_reviews_count,
    scroll_reviews_container,
    select_reviews_sort,
)
from yandex_scraper.features.reviews.navigation import (
    DEFAULT_NAV_RELOAD_TIMEOUT_MS,
    DEFAULT_NAV_TIMEOUT_MS,
    REVIEW_WAIT_SELECTOR,
    browser_diagnostics_text,
    diagnose_loaded_page,
    navigate_to_reviews,
    nav_error_text,
    safe_page_title,
    save_debug_screenshot,
    short_exception,
)
from yandex_scraper.features.reviews.queue import (
    build_queue_rows,
    load_queue,
    load_source_records,
    merge_queue,
    save_queue,
)
from yandex_scraper.features.reviews.records import (
    compact_review_record,
    first_text,
    merge_review_records,
    merge_review_record_by_position,
    review_key,
    safe_name_part,
    store_review_record,
    reviews_look_same,
    utc_now,
)


REVIEWS_JSONL_FILE = DATA_DIR / "raw" / "reviews.jsonl"
REVIEWS_QUEUE_FILE = DATA_DIR / "state" / "reviews_queue.csv"
REVIEWS_DEBUG_DIR = DATA_DIR / "tmp" / "reviews_debug"
XHR_DEBUG_URL_MARKERS = ("review", "reviews", "ugc", "business")
REVIEWS_LEAN_BLOCKED_RESOURCE_TYPES = {
    "image",
    "media",
    "font",
    "stylesheet",
    "texttrack",
    "eventsource",
    "websocket",
    "manifest",
    "other",
}
REVIEWS_INTERACTIVE_BLOCKED_RESOURCE_TYPES = {
    "image",
    "media",
    "texttrack",
    "eventsource",
    "websocket",
}
XHR_PAGINATION_KEY_MARKERS = (
    "cursor",
    "page",
    "offset",
    "token",
    "next",
    "continuation",
    "limit",
    "skip",
    "batch",
    "more",
    "hasmore",
    "has_more",
    "pagination",
)


def summarize_json_response(data, extracted_reviews: list[dict], *, max_items: int = 40) -> dict:
    top_keys = sorted(str(key) for key in data.keys())[:60] if isinstance(data, dict) else []
    pagination_candidates: list[dict] = []
    array_summaries: list[dict] = []
    visited = 0

    def short_value(value) -> str | int | float | bool | None:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        text = str(value).replace("\n", " ").strip()
        return text[:180]

    def walk(value, path: str = "$", depth: int = 0) -> None:
        nonlocal visited
        if visited >= 900 or depth > 8:
            return
        visited += 1
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                child_path = f"{path}.{key_text}"
                if any(marker in key_lower for marker in XHR_PAGINATION_KEY_MARKERS):
                    if not isinstance(child, (dict, list)):
                        pagination_candidates.append(
                            {
                                "path": child_path,
                                "value": short_value(child),
                            }
                        )
                if isinstance(child, (dict, list)):
                    walk(child, child_path, depth + 1)
            return
        if isinstance(value, list):
            first_dict = next((item for item in value if isinstance(item, dict)), None)
            summary = {
                "path": path,
                "length": len(value),
            }
            if first_dict is not None:
                summary["first_keys"] = sorted(str(key) for key in first_dict.keys())[:30]
            elif value:
                summary["first_type"] = type(value[0]).__name__
            array_summaries.append(summary)
            for index, item in enumerate(value[:8]):
                if isinstance(item, (dict, list)):
                    walk(item, f"{path}[{index}]", depth + 1)

    walk(data)

    return {
        "json_type": type(data).__name__,
        "top_keys": top_keys,
        "review_count": len(extracted_reviews),
        "review_samples": [
            {
                "review_id": first_text(review.get("review_id")),
                "author_name": first_text(review.get("author_name"))[:80],
                "rating": first_text(review.get("rating")),
                "date": first_text(review.get("date")),
                "text_len": len(first_text(review.get("text"))),
            }
            for review in extracted_reviews[:3]
        ],
        "pagination_candidates": pagination_candidates[:max_items],
        "array_summaries": array_summaries[:max_items],
    }


async def collect_reviews_for_row(
    context: BrowserContext,
    row: dict,
    *,
    max_reviews: int,
    scroll_steps: int,
    scroll_no_growth_limit: int,
    scroll_pause: tuple[float, float],
    sort_mode: str,
    date_from: date | None,
    wait_on_captcha: bool,
    nav_mode: str,
    debug_nav: bool,
    proxy_label: str,
    proxy_pool: str,
    attempt: int,
    nav_timeout_ms: int,
    nav_reload_timeout_ms: int,
    debug_dir: Path,
    debug_screenshot: bool,
    collect_organization_details: bool,
    organization_details_max_items: int,
    organization_details_visible_text_max_chars: int,
    traffic_profile: str,
    traffic_blocking_state: RequestBlockingState | None,
) -> tuple[str, list[dict], str, dict | None, dict | None]:
    reviews_by_key: dict[str, dict] = {}
    dom_order_records: list[dict] = []
    buffered_xhr_reviews_by_key: dict[str, dict] = {}
    pre_sort_xhr_request_ids: set[int] = set()
    accept_xhr_reviews = sort_mode != "newest"
    allow_xhr_only_reviews = sort_mode != "newest"
    console_errors: list[str] = []
    network_failures: list[str] = []
    organization_details_record: dict | None = None
    organization_services_record: dict | None = None
    page = await context.new_page()
    traffic_up_bytes = 0
    traffic_down_bytes = 0
    traffic_meter_enabled = False
    traffic_base_logged = False
    traffic_reviews_logged = False
    traffic_total_logged = False
    traffic_stage_start = (0, 0)
    xhr_debug_count = 0
    xhr_debug_max_records = 80
    xhr_debug_path = (
        debug_dir
        / f"{safe_name_part(first_text(row.get('org_id')) or 'unknown')}_attempt{attempt}_xhr_debug.jsonl"
    )

    def bytes_to_mb(value: int) -> float:
        return value / (1024 * 1024)

    def traffic_snapshot() -> tuple[int, int]:
        return traffic_up_bytes, traffic_down_bytes

    def print_traffic_delta(stage: str, start: tuple[int, int], end: tuple[int, int]) -> None:
        up_bytes = max(0, end[0] - start[0])
        down_bytes = max(0, end[1] - start[1])
        total_bytes = up_bytes + down_bytes
        print(
            "[Reviews] [Traffic] "
            f"{stage} | "
            f"up={bytes_to_mb(up_bytes):.2f} MB "
            f"down={bytes_to_mb(down_bytes):.2f} MB "
            f"total={bytes_to_mb(total_bytes):.2f} MB"
        )

    def mark_base_traffic() -> None:
        nonlocal traffic_base_logged, traffic_stage_start
        if not traffic_meter_enabled or traffic_base_logged:
            return
        snapshot = traffic_snapshot()
        print_traffic_delta("base_details", (0, 0), snapshot)
        traffic_stage_start = snapshot
        traffic_base_logged = True

    def finish_traffic() -> None:
        nonlocal traffic_reviews_logged, traffic_total_logged
        if not traffic_meter_enabled:
            return
        snapshot = traffic_snapshot()
        if traffic_base_logged and not traffic_reviews_logged:
            print_traffic_delta("reviews", traffic_stage_start, snapshot)
            traffic_reviews_logged = True
        if not traffic_total_logged:
            print_traffic_delta("total", (0, 0), snapshot)
            traffic_total_logged = True

    def result(status: str, reviews: list[dict], error: str) -> tuple[str, list[dict], str, dict | None, dict | None]:
        finish_traffic()
        return status, reviews, error, organization_details_record, organization_services_record

    def current_review_key(record: dict) -> str:
        key = review_key(record)
        if key and key in reviews_by_key:
            return key
        for existing_key, existing in reviews_by_key.items():
            if reviews_look_same(existing, record):
                return existing_key
        return key

    def remember_dom_key(key: str) -> None:
        if not key or key not in reviews_by_key:
            return
        record = reviews_by_key[key]
        if any(existing is record for existing in dom_order_records):
            return
        dom_order_records.append(record)

    def remember_dom_order(record: dict) -> None:
        remember_dom_key(current_review_key(record))

    def review_key_at_position(position: int) -> str:
        keys = list(reviews_by_key.keys())
        if position < 0 or position >= len(keys):
            return ""
        return keys[position]

    def ordered_review_records() -> list[dict]:
        if sort_mode != "newest":
            return list(reviews_by_key.values())

        ordered: list[dict] = []
        seen_ids: set[int] = set()
        current_record_ids = {id(record) for record in reviews_by_key.values()}
        for record in dom_order_records:
            record_id = id(record)
            if record_id in seen_ids or record_id not in current_record_ids:
                continue
            ordered.append(record)
            seen_ids.add(record_id)

        for record in reviews_by_key.values():
            record_id = id(record)
            if record_id in seen_ids:
                continue
            ordered.append(record)
        return ordered

    def is_newest_reviews_response_url(url_lower: str) -> bool:
        return (
            sort_mode == "newest"
            and "business/fetchreviews" in url_lower
            and "ranking=by_time" in url_lower
        )

    def buffer_xhr_review(record: dict) -> None:
        key = review_key(record)
        if key and key in buffered_xhr_reviews_by_key:
            merge_review_records(buffered_xhr_reviews_by_key[key], record)
            return

        for existing_key, existing in list(buffered_xhr_reviews_by_key.items()):
            if reviews_look_same(existing, record):
                merge_review_records(existing, record)
                if key and not first_text(existing.get("review_id")) and first_text(record.get("review_id")):
                    buffered_xhr_reviews_by_key[key] = buffered_xhr_reviews_by_key.pop(existing_key)
                return

        if key:
            buffered_xhr_reviews_by_key[key] = record

    def apply_buffered_xhr_reviews() -> int:
        if not buffered_xhr_reviews_by_key:
            return 0

        merged_keys: list[str] = []
        for key, record in list(buffered_xhr_reviews_by_key.items()):
            if store_review_record(reviews_by_key, record, insert_if_new=False):
                merged_keys.append(key)

        for key in merged_keys:
            buffered_xhr_reviews_by_key.pop(key, None)
        return len(merged_keys)

    def missing_date_result(reviews: list[dict]) -> DateFilterResult:
        missing_count = sum(
            1 for review in reviews if parse_review_date(first_text(review.get("date"))) is None
        )
        return DateFilterResult(
            reviews=reviews,
            missing_review_date=missing_count > 0,
            missing_review_date_count=missing_count,
        )

    def switch_to_lean_after_sort() -> None:
        if traffic_profile != "interactive-then-lean" or traffic_blocking_state is None:
            return
        traffic_blocking_state["blocked_resource_types"] = REVIEWS_LEAN_BLOCKED_RESOURCE_TYPES
        traffic_blocking_state["block_map_tiles"] = True
        traffic_blocking_state["strict_xhr_fetch_filter"] = False
        print("[Reviews] [Traffic] profile switched -> lean after newest sort")

    async def collect_details_after_base(base_page) -> None:
        nonlocal organization_details_record, organization_services_record
        if not collect_organization_details or organization_details_record is not None:
            return
        try:
            organization_details_record, organization_services_record = await collect_organization_details_from_page(
                base_page,
                row,
                missing_text=ORGANIZATION_DETAILS_MISSING_TEXT,
                max_items=organization_details_max_items,
                visible_text_max_chars=organization_details_visible_text_max_chars,
            )
            print(
                "[Details] Organization card collected | "
                f"services={organization_services_record.get('services_count', '0')}"
            )
        except Exception as exc:
            page_url = ""
            try:
                page_url = str(base_page.url or "")
            except Exception:
                pass
            organization_details_record = build_organization_details_error_record(
                row,
                f"{type(exc).__name__}: {str(exc)[:180]}",
                page_url=page_url,
            )
            organization_services_record = build_organization_services_error_record(
                row,
                f"{type(exc).__name__}: {str(exc)[:180]}",
                page_url=page_url,
            )
            print(f"[Details] [WARN] Could not collect organization card: {type(exc).__name__}: {str(exc)[:120]}")
        finally:
            mark_base_traffic()

    def remember_console(msg) -> None:
        if len(console_errors) >= 8:
            return
        try:
            if msg.type not in {"error", "warning"}:
                return
            console_errors.append(f"{msg.type}:{str(msg.text)[:180]}")
        except Exception:
            pass

    def remember_request_failure(request) -> None:
        if len(network_failures) >= 8:
            return
        try:
            resource_type = getattr(request, "resource_type", "")
            if callable(resource_type):
                resource_type = resource_type()
            resource_type = str(resource_type)
            if resource_type not in {"document", "xhr", "fetch"}:
                return
            url = getattr(request, "url", "")
            if callable(url):
                url = url()
            url = str(url)
            url_lower = url.lower()
            if not any(marker in url_lower for marker in ("maps", "business", "review", "ugc")):
                return
            failure = getattr(request, "failure", "")
            if callable(failure):
                failure = failure()
            network_failures.append(f"{resource_type}:{url[:120]}:{str(failure)[:80]}")
        except Exception:
            pass

    def remember_request_phase(request) -> None:
        if accept_xhr_reviews:
            return
        try:
            resource_type = getattr(request, "resource_type", "")
            if callable(resource_type):
                resource_type = resource_type()
            if str(resource_type) not in {"xhr", "fetch"}:
                return
            url = getattr(request, "url", "")
            if callable(url):
                url = url()
            url_lower = str(url).lower()
            if any(marker in url_lower for marker in XHR_DEBUG_URL_MARKERS):
                pre_sort_xhr_request_ids.add(id(request))
        except Exception:
            pass

    async def on_response(response):
        nonlocal xhr_debug_count
        url = response.url
        url_lower = url.lower()
        if not any(marker in url_lower for marker in XHR_DEBUG_URL_MARKERS):
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        try:
            data = await response.json()
        except Exception:
            return
        extracted_reviews = extract_reviews_from_json(data)
        if debug_nav and xhr_debug_count < xhr_debug_max_records:
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                summary = summarize_json_response(data, extracted_reviews)
                record = {
                    "captured_at": utc_now(),
                    "attempt": attempt,
                    "proxy_pool": proxy_pool,
                    "proxy_label": proxy_label,
                    "status": getattr(response, "status", ""),
                    "url": url,
                    "url_sha1": hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest(),
                    "content_type": content_type[:160],
                    "content_length": response.headers.get("content-length", ""),
                    "summary": summary,
                }
                with open(xhr_debug_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                xhr_debug_count += 1
                if extracted_reviews or any(marker in url_lower for marker in ("review", "ugc")):
                    print(
                        "[Reviews] [XHR] "
                        f"debug={xhr_debug_path} "
                        f"status={record['status']} "
                        f"reviews={len(extracted_reviews)} "
                        f"url={url[:180]}"
                    )
            except Exception as exc:
                if debug_nav:
                    print(f"[Reviews] [XHR] debug write failed: {type(exc).__name__}: {str(exc)[:120]}")
        request = getattr(response, "request", None)
        if callable(request):
            request = request()
        newest_reviews_response = is_newest_reviews_response_url(url_lower)
        if request is not None and id(request) in pre_sort_xhr_request_ids and not newest_reviews_response:
            return
        if not accept_xhr_reviews and not newest_reviews_response:
            return
        for review in extracted_reviews:
            record = compact_review_record(review, row, "xhr")
            if store_review_record(reviews_by_key, record, insert_if_new=allow_xhr_only_reviews):
                continue
            if not allow_xhr_only_reviews:
                buffer_xhr_review(record)

    page.on("response", on_response)
    page.on("console", remember_console)
    page.on("request", remember_request_phase)
    page.on("requestfailed", remember_request_failure)

    try:
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Network.enable")
            traffic_meter_enabled = True

            def on_request_will_be_sent(event) -> None:
                nonlocal traffic_up_bytes
                request = event.get("request") or {}
                headers = request.get("headers") or {}
                headers_size = (
                    sum(
                        len(str(key).encode("utf-8")) + len(str(value).encode("utf-8")) + 4
                        for key, value in headers.items()
                    )
                    + 2
                )
                post_data = request.get("postData") or ""
                body_size = len(post_data.encode("utf-8")) if isinstance(post_data, str) else 0
                traffic_up_bytes += headers_size + body_size

            def on_loading_finished(event) -> None:
                nonlocal traffic_down_bytes
                encoded_len = event.get("encodedDataLength")
                if isinstance(encoded_len, (int, float)):
                    traffic_down_bytes += int(encoded_len)

            cdp.on("Network.requestWillBeSent", on_request_will_be_sent)
            cdp.on("Network.loadingFinished", on_loading_finished)
        except Exception as exc:
            print(f"[Reviews] [Traffic] meter unavailable: {type(exc).__name__}: {str(exc)[:100]}")

        nav_result = await navigate_to_reviews(
            page,
            row,
            nav_mode=nav_mode,
            debug_nav=debug_nav,
            proxy_label=proxy_label,
            proxy_pool=proxy_pool,
            attempt=attempt,
            nav_timeout_ms=nav_timeout_ms,
            nav_reload_timeout_ms=nav_reload_timeout_ms,
            debug_dir=debug_dir,
            debug_screenshot=debug_screenshot,
            after_base=collect_details_after_base if collect_organization_details else None,
        )
        if nav_result.status == "captcha":
            if not wait_on_captcha:
                return result("captcha", [], nav_result.error)
            print("[Reviews] Captcha detected. Solve it in the browser, then press Enter here.")
            await asyncio.to_thread(input)
            await page.wait_for_timeout(1_000)
            if await check_captcha(page):
                return result("captcha", [], "captcha_not_solved")
            nav_result = await navigate_to_reviews(
                page,
                row,
                nav_mode=nav_mode,
                debug_nav=debug_nav,
                proxy_label=proxy_label,
                proxy_pool=proxy_pool,
                attempt=attempt,
                nav_timeout_ms=nav_timeout_ms,
                nav_reload_timeout_ms=nav_reload_timeout_ms,
                debug_dir=debug_dir,
                debug_screenshot=debug_screenshot,
                after_base=collect_details_after_base if collect_organization_details else None,
            )
            if nav_result.status == "captcha":
                return result("captcha", [], nav_result.error or "captcha_after_manual_solve")
            if nav_result.status != "ok":
                diagnostics = browser_diagnostics_text(console_errors, network_failures)
                return result("error", [], " | ".join(part for part in (nav_result.error, diagnostics) if part))
            post_captcha_check = await diagnose_loaded_page(page, row, label="reviews")
            if not post_captcha_check.ready:
                screenshot_path = await save_debug_screenshot(
                    page,
                    row,
                    label="reviews",
                    attempt=attempt,
                    reason=post_captcha_check.reason,
                    debug_dir=debug_dir,
                )
                title = await safe_page_title(page)
                return result(
                    "error",
                    [],
                    nav_error_text(
                        reason=post_captcha_check.reason,
                        page_url=str(page.url or ""),
                        title=title,
                        elapsed=0,
                        screenshot_path=screenshot_path,
                        detail=post_captcha_check.signal,
                    ),
                )
        elif nav_result.status != "ok":
            diagnostics = browser_diagnostics_text(console_errors, network_failures)
            return result("error", [], " | ".join(part for part in (nav_result.error, diagnostics) if part))

        if await check_captcha(page):
            if not wait_on_captcha:
                return result("captcha", [], "")
            print("[Reviews] Captcha detected. Solve it in the browser, then press Enter here.")
            await asyncio.to_thread(input)
            await page.wait_for_timeout(1_000)
            if await check_captcha(page):
                return result("captcha", [], "captcha_not_solved")

        sort_applied = await select_reviews_sort(page, sort_mode)
        if sort_mode == "newest" and not sort_applied:
            return result("error", [], "newest_sort_not_confirmed")
        if sort_mode == "newest":
            reviews_by_key.clear()
            dom_order_records.clear()
            accept_xhr_reviews = True
            switch_to_lean_after_sort()

        try:
            await page.wait_for_selector(REVIEW_WAIT_SELECTOR, timeout=15_000)
        except Exception as exc:
            if debug_nav:
                print(f"[Reviews] [Nav] reviews selector wait soft-failed: {short_exception(exc, 120)}")

        expected_reviews_count = await get_expected_reviews_count(page)
        if expected_reviews_count and debug_nav:
            print(f"[Reviews] Expected reviews count from page -> {expected_reviews_count}")

        stable_steps = 0
        load_more_attempts = 0
        max_load_more_attempts = 4
        previous_count = 0
        stop_reason = "scroll_steps_exhausted"
        for scroll_step in range(1, scroll_steps + 1):
            expanded_count = await expand_visible_reviews(page)
            if expanded_count:
                print(f"[Reviews] Expanded hidden reviews: {expanded_count}")

            dom_reviews = await extract_reviews_from_dom(page, max_reviews)
            for review_index, review in enumerate(dom_reviews):
                record = compact_review_record(review, row, "dom")
                if store_review_record(reviews_by_key, record, insert_if_new=False):
                    remember_dom_order(record)
                    continue
                position_key = review_key_at_position(review_index)
                if merge_review_record_by_position(reviews_by_key, review_index, record):
                    remember_dom_key(position_key)
                    continue
                store_review_record(reviews_by_key, record)
                remember_dom_order(record)

            merged_buffered_xhr = apply_buffered_xhr_reviews()
            if debug_nav and merged_buffered_xhr:
                print(
                    "[Reviews] [XHR] "
                    f"merged buffered reviews={merged_buffered_xhr} "
                    f"pending_buffer={len(buffered_xhr_reviews_by_key)}"
                )

            date_filter = filter_reviews_by_date(
                ordered_review_records(),
                date_from=date_from,
            )
            visible_reviews = date_filter.reviews
            if sort_mode == "newest" and sort_applied and date_filter.saw_too_old:
                stop_reason = "too_old"
                break
            if len(visible_reviews) >= max_reviews:
                stop_reason = "max_reviews"
                break
            if len(reviews_by_key) == previous_count:
                stable_steps += 1
                if stable_steps >= scroll_no_growth_limit:
                    if load_more_attempts < max_load_more_attempts:
                        load_more_result = await click_reviews_load_more(page)
                        print(
                            "[Reviews] [LoadMore] "
                            f"attempt={load_more_attempts + 1}/{max_load_more_attempts} "
                            f"clicked={bool(load_more_result.get('clicked'))} "
                            f"reason={load_more_result.get('reason', '')} "
                            f"text={str(load_more_result.get('text', ''))[:80]} "
                            f"class={str(load_more_result.get('className', ''))[:80]}"
                        )
                        load_more_attempts += 1
                        if load_more_result.get("clicked"):
                            stable_steps = 0
                            await asyncio.sleep(random.uniform(*scroll_pause))
                            continue
                    stop_reason = "no_growth"
                    break
            else:
                stable_steps = 0
                previous_count = len(reviews_by_key)

            scroll_result = await scroll_reviews_container(page)
            try:
                x = int(float(scroll_result.get("x") or 360))
                y = int(float(scroll_result.get("y") or 640))
                await page.mouse.move(x, y)
            except Exception:
                pass
            await page.mouse.wheel(0, 1200)
            if debug_nav and (
                scroll_step <= 5
                or stable_steps in {0, 1, scroll_no_growth_limit - 1}
                or scroll_step % 10 == 0
            ):
                print(
                    "[Reviews] [Scroll] "
                    f"step={scroll_step}/{scroll_steps} "
                    f"seen={len(reviews_by_key)} "
                    f"dom={len(dom_reviews)} "
                    f"stable={stable_steps}/{scroll_no_growth_limit} "
                    f"moved={bool(scroll_result.get('moved'))} "
                    f"{scroll_result.get('before')}->{scroll_result.get('after')} "
                    f"max={scroll_result.get('maxScroll')} "
                    f"reviews_dom={scroll_result.get('reviewNodes', '')} "
                    f"reason={scroll_result.get('reason', '')} "
                    f"class={str(scroll_result.get('className', ''))[:80]}"
                )
            await asyncio.sleep(random.uniform(*scroll_pause))

        apply_buffered_xhr_reviews()
        date_filter = filter_reviews_by_date(
            ordered_review_records(),
            date_from=date_from,
        )
        candidate_reviews = date_filter.reviews[:max_reviews]
        print(
            f"[Reviews] Stop reason -> {stop_reason} | "
            f"saved={len(candidate_reviews)} | seen={len(reviews_by_key)}"
        )
        if date_from is not None or sort_mode == "newest":
            candidate_filter = missing_date_result(candidate_reviews)
            if candidate_filter.missing_review_date:
                return result("error", [], missing_review_date_error(candidate_filter))
        if expected_reviews_count:
            target_reviews_count = min(expected_reviews_count, max_reviews)
            if stop_reason == "no_growth" and len(candidate_reviews) < target_reviews_count:
                return result(
                    "error",
                    candidate_reviews,
                    f"incomplete_reviews_expected_{target_reviews_count}_seen_{len(candidate_reviews)}",
                )
        return result("done", candidate_reviews, "")
    except Exception as exc:
        screenshot_path = await save_debug_screenshot(
            page,
            row,
            label="collect",
            attempt=attempt,
            reason=type(exc).__name__,
            debug_dir=debug_dir,
        )
        diagnostics = browser_diagnostics_text(console_errors, network_failures)
        error = nav_error_text(
            reason="collect_error",
            page_url=str(page.url or ""),
            title=await safe_page_title(page),
            elapsed=0,
            screenshot_path=screenshot_path,
            detail=short_exception(exc, 160),
        )
        return result("error", [], " | ".join(part for part in (error, diagnostics) if part))
    finally:
        try:
            page_closed = page.is_closed()
        except Exception:
            page_closed = True
        if not page_closed:
            try:
                await asyncio.wait_for(page.close(), timeout=5)
            except Exception as exc:
                print(f"[Reviews] [WARN] Could not close page cleanly: {type(exc).__name__}: {str(exc)[:120]}")


def get_proxy_picker(pool: str):
    if pool == "fallback":
        return get_random_proxy_fallback
    if pool == "primary":
        return get_random_proxy_primary
    return None


def reviews_blocked_resource_types(traffic_profile: str, *, phase: str = "initial") -> set[str] | None:
    if traffic_profile == "lean":
        return REVIEWS_LEAN_BLOCKED_RESOURCE_TYPES
    if traffic_profile == "interactive-then-lean":
        if phase == "after_sort":
            return REVIEWS_LEAN_BLOCKED_RESOURCE_TYPES
        return REVIEWS_INTERACTIVE_BLOCKED_RESOURCE_TYPES
    if traffic_profile == "interactive":
        return REVIEWS_INTERACTIVE_BLOCKED_RESOURCE_TYPES
    return None


async def run_reviews_parser(args: argparse.Namespace) -> None:
    date_from = parse_iso_date(args.date_from)
    if args.date_from and date_from is None:
        raise ValueError("--date-from must use YYYY-MM-DD format, for example 2025-08-01")

    source_records = load_source_records(args.input)
    generated_rows = build_queue_rows(source_records)
    existing_rows = load_queue(args.state)
    queue_rows = merge_queue(existing_rows, generated_rows)
    save_queue(args.state, queue_rows)

    pending_rows = [row for row in queue_rows if row.get("status") == "pending"]
    if args.limit is not None:
        pending_rows = pending_rows[: max(0, args.limit)]

    print(f"[Reviews] Source organizations: {len(source_records)}")
    print(f"[Reviews] Queue: {len(queue_rows)} | Pending in run: {len(pending_rows)}")
    collect_details = bool(args.organization_details_enabled)
    if collect_details and args.nav_mode == "direct":
        print("[Details] Skipped: --nav-mode=direct bypasses the organization card.")
        collect_details = False
    if collect_details:
        print(f"[Details] Output: {args.details_output}")
        print(f"[Details] Services output: {args.services_output}")
    if not pending_rows:
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    used_proxy_servers: set[str] = set()

    def pick_proxy(attempt_index: int = 1) -> dict | None:
        if args.no_proxy:
            return None

        if args.proxy_pool == "auto":
            picker_order = (
                (get_random_proxy_primary, get_random_proxy_fallback)
                if attempt_index % 2 == 1
                else (get_random_proxy_fallback, get_random_proxy_primary)
            )
        else:
            picker_order = (get_proxy_picker(args.proxy_pool),)

        last_proxy = None
        for picker in picker_order:
            if picker is None:
                continue
            for _ in range(25):
                proxy = picker()
                last_proxy = proxy
                server = str(proxy.get("server") or "")
                if server and server not in used_proxy_servers:
                    used_proxy_servers.add(server)
                    return proxy
        if last_proxy is not None:
            used_proxy_servers.add(str(last_proxy.get("server") or ""))
        return last_proxy

    async def open_runtime(pw, attempt_index: int = 1):
        proxy = pick_proxy(attempt_index)
        proxy_label = "disabled" if proxy is None else str(proxy.get("server", ""))
        block_service_workers = BLOCK_SERVICE_WORKERS and args.traffic_profile == "lean"
        context_kwargs = {
            "extra_http_headers": BROWSER_HEADERS,
            "java_script_enabled": True,
        }
        if block_service_workers:
            context_kwargs["service_workers"] = "block"
        if args.user_data_dir:
            args.user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await pw.chromium.launch_persistent_context(
                str(args.user_data_dir),
                headless=not args.headful,
                proxy=proxy,
                **context_kwargs,
            )
            browser = None
        else:
            browser = await pw.chromium.launch(headless=not args.headful, proxy=proxy)
            context = await browser.new_context(**context_kwargs)
        traffic_blocking_state = None
        if args.traffic_profile != "off":
            traffic_blocking_state = await setup_request_blocking(
                context,
                strict_xhr_fetch_filter=False,
                blocked_resource_types=reviews_blocked_resource_types(args.traffic_profile),
                block_map_tiles=True,
            )
        return browser, context, proxy_label, traffic_blocking_state

    async def close_runtime(browser, context) -> None:
        async def safe_close(obj, label: str) -> None:
            if obj is None:
                return
            try:
                await asyncio.wait_for(obj.close(), timeout=10)
            except Exception as exc:
                print(f"[Reviews] [WARN] close {label} ignored: {type(exc).__name__}: {str(exc)[:120]}")

        await safe_close(context, "context")
        await safe_close(browser, "browser")

    async with async_playwright() as pw:
        browser, context, proxy_label, traffic_blocking_state = await open_runtime(pw, 1)

        try:
            for index, row in enumerate(pending_rows, start=1):
                max_attempts = 1 if args.no_proxy else max(1, args.proxy_attempts)
                status = "error"
                reviews = []
                error = ""
                organization_details_record = None
                organization_services_record = None

                for attempt in range(1, max_attempts + 1):
                    print(
                        f"[Reviews] [{index}/{len(pending_rows)}] "
                        f"{row.get('title') or row.get('org_id')} | "
                        f"attempt={attempt}/{max_attempts} | "
                        f"pool={args.proxy_pool} | proxy={proxy_label}"
                    )
                    status, reviews, error, attempt_details_record, attempt_services_record = await collect_reviews_for_row(
                        context,
                        row,
                        max_reviews=args.max_reviews,
                        scroll_steps=args.scroll_steps,
                        scroll_no_growth_limit=args.scroll_no_growth_limit,
                        scroll_pause=(args.scroll_pause_min, args.scroll_pause_max),
                        sort_mode=args.sort,
                        date_from=date_from,
                        wait_on_captcha=args.wait_on_captcha,
                        nav_mode=args.nav_mode,
                        debug_nav=args.debug_nav,
                        proxy_label=proxy_label,
                        proxy_pool=args.proxy_pool,
                        attempt=attempt,
                        nav_timeout_ms=args.nav_timeout_ms,
                        nav_reload_timeout_ms=args.nav_reload_timeout_ms,
                        debug_dir=args.debug_dir,
                        debug_screenshot=args.debug_screenshot,
                        collect_organization_details=collect_details,
                        organization_details_max_items=max(1, args.details_max_items),
                        organization_details_visible_text_max_chars=max(500, args.details_visible_text_max_chars),
                        traffic_profile=args.traffic_profile,
                        traffic_blocking_state=traffic_blocking_state,
                    )
                    if attempt_details_record is not None:
                        if (
                            organization_details_record is None
                            or organization_details_record.get("capture_status") != "done"
                        ):
                            organization_details_record = attempt_details_record
                    if attempt_services_record is not None:
                        if (
                            organization_services_record is None
                            or organization_services_record.get("capture_status") != "done"
                        ):
                            organization_services_record = attempt_services_record
                    if status == "done":
                        break
                    if status == "captcha":
                        if attempt >= max_attempts:
                            print(
                                f"[Reviews] [Proxy] Captcha: {(error or 'captcha_detected')[:160]} | "
                                "proxy attempts exhausted"
                            )
                            if not args.no_proxy:
                                await close_runtime(browser, context)
                                browser, context, proxy_label, traffic_blocking_state = await open_runtime(pw, attempt + 1)
                            break
                        print(
                            f"[Reviews] [Proxy] Captcha: {(error or 'captcha_detected')[:160]} | "
                            "rotating proxy"
                        )
                        await close_runtime(browser, context)
                        browser, context, proxy_label, traffic_blocking_state = await open_runtime(pw, attempt + 1)
                        continue
                    if status != "error":
                        break
                    if is_missing_review_date_error(error):
                        print("[Reviews] [Date] missing_review_date is not proxy-related; keeping current error")
                        break
                    if attempt >= max_attempts:
                        break
                    print(f"[Reviews] [Proxy] Error: {error[:160]} | rotating proxy")
                    await close_runtime(browser, context)
                    browser, context, proxy_label, traffic_blocking_state = await open_runtime(pw, attempt + 1)

                if reviews:
                    with open(args.output, "a", encoding="utf-8") as handle:
                        for review in reviews:
                            handle.write(json.dumps(review, ensure_ascii=False) + "\n")
                        handle.flush()

                if organization_details_record is not None:
                    append_organization_details_record(args.details_output, organization_details_record)
                if organization_services_record is not None:
                    append_organization_services_record(args.services_output, organization_services_record)

                row["status"] = status
                row["error"] = error
                row["review_count"] = str(len(reviews))
                row["captured_at"] = utc_now()
                save_queue(args.state, queue_rows)

                print(f"[Reviews] Status -> {status} | reviews={len(reviews)}")
                if index < len(pending_rows):
                    await asyncio.sleep(random.uniform(args.pause_min, args.pause_max))
        finally:
            await close_runtime(browser, context)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mini parser for Yandex Maps organization reviews.")
    parser.add_argument("--input", type=Path, default=None, help="Source .jsonl or .csv with organizations.")
    parser.add_argument("--output", type=Path, default=REVIEWS_JSONL_FILE, help="Append-only reviews JSONL.")
    parser.add_argument("--state", type=Path, default=REVIEWS_QUEUE_FILE, help="Reviews queue CSV.")
    parser.add_argument(
        "--details-output",
        type=Path,
        default=ORGANIZATION_DETAILS_JSONL_FILE,
        help="Append-only organization card details JSONL.",
    )
    parser.add_argument(
        "--services-output",
        type=Path,
        default=ORGANIZATION_SERVICES_JSONL_FILE,
        help="Append-only organization products/services JSONL.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit pending organizations for this run.")
    parser.add_argument("--max-reviews", type=int, default=REVIEWS_MAX_REVIEWS, help="Max reviews per organization.")
    parser.add_argument("--scroll-steps", type=int, default=REVIEWS_SCROLL_STEPS, help="Max scroll steps per reviews page.")
    parser.add_argument(
        "--scroll-no-growth-limit",
        type=int,
        default=REVIEWS_SCROLL_NO_GROWTH_LIMIT,
        help="Stop after this many scroll steps without newly detected reviews.",
    )
    parser.add_argument(
        "--date-from",
        default=REVIEWS_DATE_FROM,
        help="Keep reviews with date >= YYYY-MM-DD. Defaults to REVIEWS_DATE_FROM from config.py.",
    )
    parser.add_argument(
        "--sort",
        choices=("default", "newest"),
        default="newest",
        help="Reviews sort mode before collection.",
    )
    parser.add_argument(
        "--nav-mode",
        choices=("base-then-reviews", "direct"),
        default="base-then-reviews",
        help="How to navigate to reviews page.",
    )
    details_group = parser.add_mutually_exclusive_group()
    details_group.add_argument(
        "--collect-organization-details",
        dest="organization_details_enabled",
        action="store_true",
        default=ORGANIZATION_DETAILS_ENABLED,
        help="Collect organization card and products/services before reviews.",
    )
    details_group.add_argument(
        "--skip-organization-details",
        dest="organization_details_enabled",
        action="store_false",
        help="Keep the old reviews-only second pass.",
    )
    parser.add_argument(
        "--details-max-items",
        type=int,
        default=ORGANIZATION_DETAILS_MAX_ITEMS,
        help="Max products/services items to keep per organization.",
    )
    parser.add_argument(
        "--details-visible-text-max-chars",
        type=int,
        default=ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS,
        help="Max visible organization card text chars stored in details JSONL.",
    )
    parser.add_argument("--debug-nav", action="store_true", help="Print detailed navigation diagnostics.")
    parser.add_argument(
        "--nav-timeout-ms",
        type=int,
        default=DEFAULT_NAV_TIMEOUT_MS,
        help="Timeout for first page navigation attempt.",
    )
    parser.add_argument(
        "--nav-reload-timeout-ms",
        type=int,
        default=DEFAULT_NAV_RELOAD_TIMEOUT_MS,
        help="Timeout for reload fallback when the organization page is not ready.",
    )
    parser.add_argument(
        "--debug-screenshot",
        action="store_true",
        help="Save additional navigation screenshots when --debug-nav is enabled. Error screenshots are always saved.",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=REVIEWS_DEBUG_DIR,
        help="Directory for navigation diagnostic screenshots.",
    )
    parser.add_argument("--scroll-pause-min", type=float, default=0.4)
    parser.add_argument("--scroll-pause-max", type=float, default=1.2)
    parser.add_argument("--pause-min", type=float, default=8.0, help="Min pause between organizations.")
    parser.add_argument("--pause-max", type=float, default=18.0, help="Max pause between organizations.")
    parser.add_argument(
        "--traffic-profile",
        choices=("interactive", "interactive-then-lean", "lean", "off"),
        default="interactive-then-lean",
        help=(
            "Traffic/resource blocking profile. interactive keeps UI resources needed by Yandex Maps "
            "sort controls while blocking heavy media/tiles; interactive-then-lean switches to lean after newest sort; "
            "lean is the old aggressive mode; off disables blocking."
        ),
    )
    parser.add_argument("--headful", action="store_true", help="Run browser with visible UI.")
    parser.add_argument("--no-proxy", action="store_true", help="Run without configured proxy.")
    parser.add_argument(
        "--proxy-attempts",
        type=int,
        default=3,
        help="How many proxy/browser attempts to try per organization when proxy is enabled.",
    )
    parser.add_argument(
        "--proxy-pool",
        choices=("primary", "fallback", "auto"),
        default="primary",
        help="Proxy pool to use when proxy is enabled.",
    )
    parser.add_argument(
        "--wait-on-captcha",
        action="store_true",
        help="In headful/manual runs, wait for Enter after captcha is solved in the browser.",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        default=None,
        help="Persistent browser profile directory for cookies/session reuse.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    asyncio.run(run_reviews_parser(args))


if __name__ == "__main__":
    main()
