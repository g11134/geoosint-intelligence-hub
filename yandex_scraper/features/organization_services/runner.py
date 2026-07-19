from __future__ import annotations

import argparse
import asyncio
import csv
import random
from pathlib import Path

from patchright.async_api import async_playwright

from yandex_scraper import config as scraper_config
from yandex_scraper.browser import check_captcha, setup_request_blocking
from yandex_scraper.config import (
    BLOCK_SERVICE_WORKERS,
    BROWSER_HEADERS,
    DATA_DIR,
    get_random_proxy_fallback,
    get_random_proxy_primary,
)
from yandex_scraper.features.organization_details import (
    append_organization_services_record,
    build_organization_services_error_record,
    collect_organization_services_from_page,
)
from yandex_scraper.features.reviews.navigation import (
    DEFAULT_NAV_RELOAD_TIMEOUT_MS,
    DEFAULT_NAV_TIMEOUT_MS,
    goto_url_soft,
    short_exception,
)
from yandex_scraper.features.reviews.queue import build_queue_rows, load_source_records
from yandex_scraper.features.reviews.records import base_url_from_reviews_url, dedup_key, first_text, utc_now


SERVICES_QUEUE_FILE = DATA_DIR / "state" / "services_queue.csv"
SERVICES_DEBUG_DIR = DATA_DIR / "tmp" / "services_debug"
ORGANIZATION_DETAILS_MISSING_TEXT = getattr(
    scraper_config,
    "ORGANIZATION_DETAILS_MISSING_TEXT",
    "данные отсутствуют",
)
ORGANIZATION_DETAILS_MAX_ITEMS = getattr(scraper_config, "ORGANIZATION_DETAILS_MAX_ITEMS", 300)
ORGANIZATION_SERVICES_JSONL_FILE = getattr(
    scraper_config,
    "ORGANIZATION_SERVICES_JSONL_FILE",
    DATA_DIR / "raw" / "organization_services.jsonl",
)
SERVICES_QUEUE_COLUMNS = [
    "org_id",
    "title",
    "org_url",
    "reviews_url",
    "status",
    "error",
    "services_count",
    "captured_at",
]
SERVICES_LEAN_BLOCKED_RESOURCE_TYPES = {
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
SERVICES_INTERACTIVE_BLOCKED_RESOURCE_TYPES = {
    "image",
    "media",
    "texttrack",
    "eventsource",
    "websocket",
}


def load_services_queue(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    return [{column: str(row.get(column) or "") for column in SERVICES_QUEUE_COLUMNS} for row in rows]


def save_services_queue(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SERVICES_QUEUE_COLUMNS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SERVICES_QUEUE_COLUMNS})


def build_services_queue_rows(source_records: list[dict]) -> list[dict]:
    rows = []
    for row in build_queue_rows(source_records):
        rows.append(
            {
                "org_id": row.get("org_id", ""),
                "title": row.get("title", ""),
                "org_url": row.get("org_url", ""),
                "reviews_url": row.get("reviews_url", ""),
                "status": "pending",
                "error": "",
                "services_count": "",
                "captured_at": "",
            }
        )
    return rows


def merge_services_queue(existing: list[dict], generated: list[dict]) -> list[dict]:
    rows_by_key = {}
    for row in existing:
        key = dedup_key(row)
        if key:
            rows_by_key[key] = row
    for row in generated:
        key = dedup_key(row)
        if key and key not in rows_by_key:
            rows_by_key[key] = row
    return list(rows_by_key.values())


def get_proxy_picker(pool: str):
    if pool == "fallback":
        return get_random_proxy_fallback
    if pool == "primary":
        return get_random_proxy_primary
    return None


def services_blocked_resource_types(traffic_profile: str) -> set[str] | None:
    if traffic_profile == "lean":
        return SERVICES_LEAN_BLOCKED_RESOURCE_TYPES
    if traffic_profile in {"interactive", "interactive-then-lean"}:
        return SERVICES_INTERACTIVE_BLOCKED_RESOURCE_TYPES
    return None


def base_url_for_row(row: dict) -> str:
    org_url = first_text(row.get("org_url"))
    if org_url:
        return org_url.split("?", 1)[0].rstrip("/") + "/"
    return base_url_from_reviews_url(first_text(row.get("reviews_url")))


async def collect_services_for_row(
    context,
    row: dict,
    *,
    max_items: int,
    wait_on_captcha: bool,
    debug_nav: bool,
    proxy_label: str,
    proxy_pool: str,
    attempt: int,
    nav_timeout_ms: int,
    nav_reload_timeout_ms: int,
    debug_dir: Path,
    debug_screenshot: bool,
) -> tuple[str, dict, str]:
    page = await context.new_page()

    def error_record(error: str, *, page_url: str = "") -> dict:
        return build_organization_services_error_record(
            row,
            error,
            page_url=page_url,
            missing_text=ORGANIZATION_DETAILS_MISSING_TEXT,
        )

    try:
        target_url = base_url_for_row(row)
        if not target_url:
            error = "missing_organization_url"
            return "error", error_record(error), error

        nav_result = await goto_url_soft(
            page,
            row,
            target_url,
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
        if nav_result.status == "captcha":
            if not wait_on_captcha:
                return "captcha", error_record(nav_result.error, page_url=str(page.url or "")), nav_result.error
            print("[Services] Captcha detected. Solve it in the browser, then press Enter here.")
            await asyncio.to_thread(input)
            await page.wait_for_timeout(1_000)
            if await check_captcha(page):
                return "captcha", error_record("captcha_not_solved", page_url=str(page.url or "")), "captcha_not_solved"
            nav_result = await goto_url_soft(
                page,
                row,
                target_url,
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

        if nav_result.status != "ok":
            error = nav_result.error or nav_result.status
            return nav_result.status, error_record(error, page_url=str(page.url or "")), error

        if await check_captcha(page):
            if not wait_on_captcha:
                return "captcha", error_record("captcha_detected", page_url=str(page.url or "")), "captcha_detected"
            print("[Services] Captcha detected. Solve it in the browser, then press Enter here.")
            await asyncio.to_thread(input)
            await page.wait_for_timeout(1_000)
            if await check_captcha(page):
                return "captcha", error_record("captcha_not_solved", page_url=str(page.url or "")), "captcha_not_solved"

        record = await collect_organization_services_from_page(
            page,
            row,
            missing_text=ORGANIZATION_DETAILS_MISSING_TEXT,
            max_items=max_items,
        )
        return "done", record, ""
    except Exception as exc:
        error = short_exception(exc)
        page_url = ""
        try:
            page_url = str(page.url or "")
        except Exception:
            pass
        return "error", error_record(error, page_url=page_url), error
    finally:
        try:
            if not page.is_closed():
                await page.close()
        except Exception as exc:
            print(f"[Services] [WARN] Could not close page cleanly: {short_exception(exc, 120)}")


async def run_services_parser(args: argparse.Namespace) -> None:
    source_records = load_source_records(args.input)
    generated_rows = build_services_queue_rows(source_records)
    existing_rows = load_services_queue(args.state)
    queue_rows = merge_services_queue(existing_rows, generated_rows)
    save_services_queue(args.state, queue_rows)

    pending_rows = [row for row in queue_rows if row.get("status") == "pending"]
    if args.limit is not None:
        pending_rows = pending_rows[: max(0, args.limit)]

    print(f"[Services] Source organizations: {len(source_records)}")
    print(f"[Services] Queue: {len(queue_rows)} | Pending in run: {len(pending_rows)}")
    print(f"[Services] Output: {args.output}")
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
        if args.traffic_profile != "off":
            await setup_request_blocking(
                context,
                strict_xhr_fetch_filter=False,
                blocked_resource_types=services_blocked_resource_types(args.traffic_profile),
                block_map_tiles=True,
            )
        return browser, context, proxy_label

    async def close_runtime(browser, context) -> None:
        async def safe_close(obj, label: str) -> None:
            if obj is None:
                return
            try:
                await asyncio.wait_for(obj.close(), timeout=10)
            except Exception as exc:
                print(f"[Services] [WARN] close {label} ignored: {short_exception(exc, 120)}")

        await safe_close(context, "context")
        await safe_close(browser, "browser")

    async with async_playwright() as pw:
        browser, context, proxy_label = await open_runtime(pw, 1)
        try:
            for index, row in enumerate(pending_rows, start=1):
                max_attempts = 1 if args.no_proxy else max(1, args.proxy_attempts)
                status = "error"
                record = None
                error = ""

                for attempt in range(1, max_attempts + 1):
                    print(
                        f"[Services] [{index}/{len(pending_rows)}] "
                        f"{row.get('title') or row.get('org_id')} | "
                        f"attempt={attempt}/{max_attempts} | "
                        f"pool={args.proxy_pool} | proxy={proxy_label}"
                    )
                    status, record, error = await collect_services_for_row(
                        context,
                        row,
                        max_items=max(1, args.max_items),
                        wait_on_captcha=args.wait_on_captcha,
                        debug_nav=args.debug_nav,
                        proxy_label=proxy_label,
                        proxy_pool=args.proxy_pool,
                        attempt=attempt,
                        nav_timeout_ms=args.nav_timeout_ms,
                        nav_reload_timeout_ms=args.nav_reload_timeout_ms,
                        debug_dir=args.debug_dir,
                        debug_screenshot=args.debug_screenshot,
                    )
                    if status == "done":
                        break
                    if attempt >= max_attempts:
                        break
                    print(f"[Services] [Proxy] {status}: {error[:160]} | rotating proxy")
                    await close_runtime(browser, context)
                    browser, context, proxy_label = await open_runtime(pw, attempt + 1)

                if record is None:
                    record = build_organization_services_error_record(
                        row,
                        error or status,
                        missing_text=ORGANIZATION_DETAILS_MISSING_TEXT,
                    )
                append_organization_services_record(args.output, record)

                row["status"] = status
                row["error"] = error
                row["services_count"] = str(record.get("services_count") or "0")
                row["captured_at"] = utc_now()
                save_services_queue(args.state, queue_rows)

                print(f"[Services] Status -> {status} | services={row['services_count']}")
                if index < len(pending_rows):
                    await asyncio.sleep(random.uniform(args.pause_min, args.pause_max))
        finally:
            await close_runtime(browser, context)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone parser for Yandex Maps products/services.")
    parser.add_argument("--input", type=Path, default=None, help="Source .jsonl or .csv with organizations.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ORGANIZATION_SERVICES_JSONL_FILE,
        help="Append-only organization products/services JSONL.",
    )
    parser.add_argument("--state", type=Path, default=SERVICES_QUEUE_FILE, help="Services queue CSV.")
    parser.add_argument("--limit", type=int, default=None, help="Limit pending organizations for this run.")
    parser.add_argument(
        "--max-items",
        type=int,
        default=ORGANIZATION_DETAILS_MAX_ITEMS,
        help="Max products/services items to keep per organization.",
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
        default=SERVICES_DEBUG_DIR,
        help="Directory for navigation diagnostic screenshots.",
    )
    parser.add_argument("--pause-min", type=float, default=8.0, help="Min pause between organizations.")
    parser.add_argument("--pause-max", type=float, default=18.0, help="Max pause between organizations.")
    parser.add_argument(
        "--traffic-profile",
        choices=("interactive", "interactive-then-lean", "lean", "off"),
        default="interactive",
        help="Traffic/resource blocking profile. interactive keeps UI resources needed by Yandex Maps controls.",
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
    asyncio.run(run_services_parser(args))


if __name__ == "__main__":
    main()
