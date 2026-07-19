import csv

from yandex_scraper.config import QUEUE_FILE
from yandex_scraper.constants import QUEUE_SAVE_BATCH_SIZE, QUEUE_SAVE_MAX_DELAY_SEC

def load_queue() -> list[dict]:
    if not QUEUE_FILE.exists():
        print(f"[ОШИБКА] Файл очереди не найден: {QUEUE_FILE}")
        exit(1)
    rows = []
    with open(QUEUE_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            rows.append(row)
    pending = sum(1 for r in rows if r.get("status") == "pending")
    done    = sum(1 for r in rows if r.get("status") == "done")
    captcha = sum(1 for r in rows if r.get("status") == "captcha")
    print(f"[Queue] Всего: {len(rows)} | Pending: {pending} | Done: {done} | Captcha: {captcha}")
    return rows

def save_queue_status(all_rows: list[dict]) -> None:
    if not all_rows:
        return
    fieldnames = list(all_rows[0].keys())
    with open(QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(all_rows)


def should_flush_queue_status(
    dirty_updates: int,
    completed: int,
    total: int,
    seconds_since_last_save: float,
) -> bool:
    if dirty_updates <= 0:
        return False
    return (
        dirty_updates >= QUEUE_SAVE_BATCH_SIZE
        or seconds_since_last_save >= QUEUE_SAVE_MAX_DELAY_SEC
        or completed >= total
    )


async def mark_progress_done(progress: dict) -> tuple[int, int]:
    async with progress["lock"]:
        progress["done"] += 1
        return progress["done"], progress["total"]


