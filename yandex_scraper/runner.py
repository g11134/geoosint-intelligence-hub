"""
╔══════════════════════════════════════════════════════════════════╗
║  ЯНДЕКС ПАРСЕР v3.9.4                                           ║
║                2_yandex_scraper.py                              ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import random
from collections import defaultdict

from yandex_scraper.config import (
    CAPTCHA_DEFERRED_PASSES,
    DB_FILE,
    ENRICHED_DATA_ENABLED,
    JSONL_FILE,
    MAX_REQUESTS_PER_MINUTE,
    PENDING_QUEUE_SPREAD_BUCKETS_PER_AXIS,
    RANDOMIZE_PENDING_QUEUE,
    SPREAD_PENDING_QUEUE,
    WORKERS_COUNT,
)
from yandex_scraper.constants import _SENTINEL
from yandex_scraper.exporters.csv_exporter import convert_to_csv
from yandex_scraper.exporters.enriched_csv_exporter import convert_enriched_to_csv
from yandex_scraper.queue_ops import load_queue
from yandex_scraper.rate_limiter import RateLimiter
from yandex_scraper.storage import SeenIdsDB
from yandex_scraper.worker import run_worker


def _parse_bbox_center(row: dict) -> tuple[float, float] | None:
    bbox = str(row.get("bbox", "")).strip()
    if "~" not in bbox:
        return None

    left, right = bbox.split("~", 1)
    left_parts = [part.strip() for part in left.split(",")]
    right_parts = [part.strip() for part in right.split(",")]
    if len(left_parts) != 2 or len(right_parts) != 2:
        return None

    try:
        lon_a, lat_a = map(float, left_parts)
        lon_b, lat_b = map(float, right_parts)
    except ValueError:
        return None

    return (lon_a + lon_b) / 2, (lat_a + lat_b) / 2


def _spread_bucket_order(
    buckets: list[tuple[int, int]],
    *,
    randomize: bool,
) -> list[tuple[int, int]]:
    def distance_sq(left: tuple[int, int], right: tuple[int, int]) -> int:
        return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2

    remaining = set(buckets)
    if randomize:
        current = random.choice(list(remaining))
    else:
        current = min(remaining, key=lambda item: (item[1], item[0]))

    order = [current]
    remaining.remove(current)

    while remaining:
        next_bucket = max(
            remaining,
            key=lambda item: (
                min(distance_sq(item, selected) for selected in order),
                distance_sq(item, current),
                item[1],
                item[0],
            ),
        )
        order.append(next_bucket)
        remaining.remove(next_bucket)
        current = next_bucket

    return order


def spread_pending_rows(
    rows: list[dict],
    *,
    buckets_per_axis: int,
    randomize: bool,
) -> tuple[list[dict], dict]:
    parsed_rows = []
    unparsed_rows = []
    for row in rows:
        center = _parse_bbox_center(row)
        if center is None:
            unparsed_rows.append(row)
            continue
        parsed_rows.append((row, center[0], center[1]))

    if len(parsed_rows) < 2:
        if randomize:
            random.shuffle(rows)
        return rows, {"parsed": len(parsed_rows), "unparsed": len(unparsed_rows), "buckets": 0}

    lon_values = [lon for _, lon, _ in parsed_rows]
    lat_values = [lat for _, _, lat in parsed_rows]
    lon_min, lon_max = min(lon_values), max(lon_values)
    lat_min, lat_max = min(lat_values), max(lat_values)
    lon_span = lon_max - lon_min
    lat_span = lat_max - lat_min

    if lon_span <= 0 or lat_span <= 0:
        if randomize:
            random.shuffle(rows)
        return rows, {"parsed": len(parsed_rows), "unparsed": len(unparsed_rows), "buckets": 0}

    buckets_per_axis = max(2, buckets_per_axis)
    buckets = defaultdict(list)
    for row, lon, lat in parsed_rows:
        bucket_x = min(
            buckets_per_axis - 1,
            int(((lon - lon_min) / lon_span) * buckets_per_axis),
        )
        bucket_y = min(
            buckets_per_axis - 1,
            int(((lat - lat_min) / lat_span) * buckets_per_axis),
        )
        buckets[(bucket_x, bucket_y)].append(row)

    for bucket_rows in buckets.values():
        if randomize:
            random.shuffle(bucket_rows)

    bucket_order = _spread_bucket_order(list(buckets.keys()), randomize=randomize)
    spread_rows = []
    while bucket_order:
        next_order = []
        for bucket in bucket_order:
            bucket_rows = buckets[bucket]
            if not bucket_rows:
                continue
            spread_rows.append(bucket_rows.pop())
            if bucket_rows:
                next_order.append(bucket)
        bucket_order = next_order

    if unparsed_rows:
        if randomize:
            random.shuffle(unparsed_rows)
        spread_rows.extend(unparsed_rows)

    return spread_rows, {
        "parsed": len(parsed_rows),
        "unparsed": len(unparsed_rows),
        "buckets": len(buckets),
    }


async def run_rows_batch(
    *,
    rows: list[dict],
    batch_label: str,
    defer_captcha: bool,
    seen_db: SeenIdsDB,
    jsonl_handle,
    jsonl_lock: asyncio.Lock,
    url_to_row: dict,
    csv_lock: asyncio.Lock,
    rate_limiter: RateLimiter,
) -> list[dict]:
    if not rows:
        return []

    print(f"[*] {batch_label}: обрабатываем {len(rows)} ячеек в {WORKERS_COUNT} потока\n")

    task_queue = asyncio.Queue()
    for row in rows:
        await task_queue.put(row)
    for _ in range(WORKERS_COUNT):
        await task_queue.put(_SENTINEL)

    progress = {"done": 0, "total": len(rows)}
    deferred_captcha_rows: list[dict] = []

    await asyncio.gather(*[
        run_worker(
            worker_id=wid,
            task_queue=task_queue,
            seen_db=seen_db,
            jsonl_handle=jsonl_handle,
            jsonl_lock=jsonl_lock,
            url_to_row=url_to_row,
            csv_lock=csv_lock,
            progress=progress,
            rate_limiter=rate_limiter,
            defer_captcha=defer_captcha,
            deferred_captcha_rows=deferred_captcha_rows,
            batch_label=batch_label,
        )
        for wid in range(1, WORKERS_COUNT + 1)
    ])

    return deferred_captcha_rows


async def main():
    print("=" * 58)
    print(f"  ЯНДЕКС ПАРСЕР v3.9.4  |  Воркеров: {WORKERS_COUNT}")
    print("=" * 58)

    seen_db = SeenIdsDB(DB_FILE)
    await seen_db.init()

    all_rows = load_queue()
    pending_rows = [r for r in all_rows if r.get("status") == "pending"]

    if not pending_rows:
        print("[!] Нет задач 'pending'. Очередь пуста.")
        await seen_db.close()
        return

    if SPREAD_PENDING_QUEUE:
        pending_rows, spread_stats = spread_pending_rows(
            pending_rows,
            buckets_per_axis=PENDING_QUEUE_SPREAD_BUCKETS_PER_AXIS,
            randomize=RANDOMIZE_PENDING_QUEUE,
        )
        print(
            "[*] Очередь pending разнесена по пространственным корзинам "
            f"({spread_stats['buckets']} buckets, "
            f"bbox parsed: {spread_stats['parsed']}, "
            f"unparsed: {spread_stats['unparsed']})"
        )
    elif RANDOMIZE_PENDING_QUEUE:
        random.shuffle(pending_rows)
        print("[*] Очередь pending перемешана случайным образом")

    url_to_row = {r["url"]: r for r in all_rows}
    jsonl_lock = asyncio.Lock()
    csv_lock = asyncio.Lock()
    rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)

    print(f"[*] RateLimiter: потолок {MAX_REQUESTS_PER_MINUTE} сессий/мин\n")
    captcha_deferred_passes = max(0, CAPTCHA_DEFERRED_PASSES)

    with open(JSONL_FILE, "a", encoding="utf-8") as jsonl_f:
        deferred_captcha_rows = await run_rows_batch(
            rows=pending_rows,
            batch_label="Основной проход",
            defer_captcha=captcha_deferred_passes > 0,
            seen_db=seen_db,
            jsonl_handle=jsonl_f,
            jsonl_lock=jsonl_lock,
            url_to_row=url_to_row,
            csv_lock=csv_lock,
            rate_limiter=rate_limiter,
        )

        for pass_index in range(1, captcha_deferred_passes + 1):
            if not deferred_captcha_rows:
                break
            captcha_rows = deferred_captcha_rows
            print(
                "\n"
                f"[*] Captcha-проход {pass_index}/{captcha_deferred_passes}: "
                f"возвращаемся к {len(captcha_rows)} отложенным ячейкам"
            )
            deferred_captcha_rows = await run_rows_batch(
                rows=captcha_rows,
                batch_label=f"Captcha-проход {pass_index}",
                defer_captcha=pass_index < captcha_deferred_passes,
                seen_db=seen_db,
                jsonl_handle=jsonl_f,
                jsonl_lock=jsonl_lock,
                url_to_row=url_to_row,
                csv_lock=csv_lock,
                rate_limiter=rate_limiter,
            )

    await seen_db.close()

    print("\n" + "=" * 58)
    print("  ПАРСИНГ ЗАВЕРШЁН")
    print(f"  Уникальных организаций: {seen_db.count()}")
    print("=" * 58)

    convert_to_csv()
    if ENRICHED_DATA_ENABLED:
        convert_enriched_to_csv()


if __name__ == "__main__":
    asyncio.run(main())
