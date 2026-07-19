import asyncio
import random

from patchright.async_api import async_playwright

from yandex_scraper.config import (
    BLOCK_SERVICE_WORKERS,
    BROWSER_HEADLESS,
    BROWSER_HEADERS,
    ERROR_PROXY_ROTATIONS,
    CAPTCHA_PROXY_ROTATIONS,
    ENABLE_SAFE_FALLBACK_PASS,
    MAX_REQUESTS_PER_MINUTE,
    PAUSE_BETWEEN_CELLS,
    get_random_proxy_fallback,
    get_random_proxy_primary,
)
from yandex_scraper.browser import setup_request_blocking
from yandex_scraper.constants import _SENTINEL
from yandex_scraper.parsing import parse_one_cell
from yandex_scraper.queue_ops import save_queue_status
from yandex_scraper.rate_limiter import RateLimiter
from yandex_scraper.storage import SeenIdsDB


async def run_worker(
    worker_id: int,
    task_queue: asyncio.Queue,
    seen_db: SeenIdsDB,
    jsonl_handle,
    jsonl_lock: asyncio.Lock,
    url_to_row: dict,
    csv_lock: asyncio.Lock,
    progress: dict,
    rate_limiter: RateLimiter,
    defer_captcha: bool = False,
    deferred_captcha_rows: list[dict] | None = None,
    batch_label: str = "main",
) -> None:
    async with async_playwright() as pw:
        async def build_runtime() -> dict:
            primary_proxy = get_random_proxy_primary()
            primary_proxy_server = primary_proxy["server"]

            fallback_proxy = None
            fallback_proxy_server = None
            if ENABLE_SAFE_FALLBACK_PASS:
                fallback_proxy = get_random_proxy_fallback()
                fallback_proxy_server = fallback_proxy["server"]

            primary_browser = await pw.chromium.launch(headless=BROWSER_HEADLESS, proxy=primary_proxy)
            fallback_browser = None
            if ENABLE_SAFE_FALLBACK_PASS and fallback_proxy is not None:
                fallback_browser = await pw.chromium.launch(headless=BROWSER_HEADLESS, proxy=fallback_proxy)

            context_kwargs = {
                "extra_http_headers": BROWSER_HEADERS,
                "java_script_enabled": True,
            }
            if BLOCK_SERVICE_WORKERS:
                context_kwargs["service_workers"] = "block"

            primary_context = await primary_browser.new_context(**context_kwargs)
            await setup_request_blocking(primary_context, strict_xhr_fetch_filter=True)

            fallback_context = None
            if fallback_browser is not None:
                fallback_context = await fallback_browser.new_context(**context_kwargs)
                await setup_request_blocking(fallback_context, strict_xhr_fetch_filter=False)

            return {
                "primary_browser": primary_browser,
                "primary_context": primary_context,
                "primary_proxy_server": primary_proxy_server,
                "fallback_browser": fallback_browser,
                "fallback_context": fallback_context,
                "fallback_proxy_server": fallback_proxy_server,
            }

        async def close_runtime(runtime: dict | None) -> None:
            if not runtime:
                return
            async def safe_close(obj, label: str) -> None:
                if obj is None:
                    return
                try:
                    await obj.close()
                except Exception as e:
                    print(f"  [W{worker_id}] [WARN] close {label}: {type(e).__name__}")

            fallback_context = runtime.get("fallback_context")
            fallback_browser = runtime.get("fallback_browser")
            primary_context = runtime.get("primary_context")
            primary_browser = runtime.get("primary_browser")

            await safe_close(fallback_context, "fallback_context")
            await safe_close(fallback_browser, "fallback_browser")
            await safe_close(primary_context, "primary_context")
            await safe_close(primary_browser, "primary_browser")

        runtime = await build_runtime()

        try:
            while True:
                row = await task_queue.get()
                if row is _SENTINEL:
                    print(f"  [W{worker_id}] Стоп-сигнал — воркер завершён")
                    task_queue.task_done()
                    break

                if runtime is None:
                    runtime = await build_runtime()

                total = progress["total"]
                progress["done"] += 1
                current = progress["done"]

                print(
                    f"\n[W{worker_id}] [{batch_label}] [{current}/{total}] "
                    f"{row.get('query','')} | "
                    f"{row.get('bbox','')[:40]}..."
                )

                await rate_limiter.acquire(prefix=f"  [W{worker_id}]")

                max_captcha_proxy_attempts = max(1, CAPTCHA_PROXY_ROTATIONS + 1)
                max_error_proxy_attempts = max(1, ERROR_PROXY_ROTATIONS + 1)
                captcha_proxy_rotations_done = 0
                error_proxy_rotations_done = 0
                status = "error"
                while True:
                    status = await parse_one_cell(
                        worker_id=worker_id,
                        row=row,
                        seen_db=seen_db,
                        jsonl_handle=jsonl_handle,
                        jsonl_lock=jsonl_lock,
                        context_primary=runtime["primary_context"],
                        context_fallback=runtime["fallback_context"],
                        proxy_server_primary=runtime["primary_proxy_server"],
                        proxy_server_fallback=runtime["fallback_proxy_server"],
                    )

                    if status == "captcha":
                        if defer_captcha:
                            break
                        if captcha_proxy_rotations_done >= max_captcha_proxy_attempts - 1:
                            break
                        captcha_proxy_rotations_done += 1
                        print(
                            f"  [W{worker_id}] [CAPTCHA] "
                            f"Смена прокси и повтор ячейки "
                            f"({captcha_proxy_rotations_done}/{max_captcha_proxy_attempts - 1})"
                        )
                        await close_runtime(runtime)
                        runtime = await build_runtime()
                        continue

                    if status == "error":
                        if error_proxy_rotations_done >= max_error_proxy_attempts - 1:
                            break
                        error_proxy_rotations_done += 1
                        print(
                            f"  [W{worker_id}] [ERROR] "
                            f"Смена прокси после сетевой ошибки "
                            f"({error_proxy_rotations_done}/{max_error_proxy_attempts - 1})"
                        )
                        await close_runtime(runtime)
                        runtime = await build_runtime()
                        continue

                    break

                deferred_captcha = False
                async with csv_lock:
                    target = url_to_row.get(row["url"])
                    if status == "captcha" and defer_captcha:
                        if deferred_captcha_rows is not None:
                            deferred_captcha_rows.append(row)
                        deferred_captcha = True
                        print(
                            f"  [W{worker_id}] [CAPTCHA] "
                            f"Ячейка отложена до повторного captcha-прохода"
                        )
                    else:
                        if target:
                            target["status"] = status
                    save_queue_status(list(url_to_row.values()))

                rate_now = rate_limiter.current_rate()
                status_for_log = "captcha_deferred" if deferred_captcha else status
                print(
                    f"  [W{worker_id}] Status → {status_for_log} | "
                    f"Rate: {rate_now}/{MAX_REQUESTS_PER_MINUTE}/мин"
                )

                if status == "captcha":
                    print(
                        f"  [W{worker_id}] [CAPTCHA] "
                        f"Закрываем текущую сессию; следующая ячейка получит новый прокси"
                    )
                    await close_runtime(runtime)
                    runtime = None

                pause = random.uniform(*PAUSE_BETWEEN_CELLS)
                print(f"  [W{worker_id}] [Пауза] {pause:.1f} сек (случайная)...")
                await asyncio.sleep(pause)

                task_queue.task_done()
        finally:
            await close_runtime(runtime)
