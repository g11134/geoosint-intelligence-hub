import csv
import json
from pathlib import Path

from yandex_scraper import config as scraper_config
from yandex_scraper.config import CSV_FILE
from yandex_scraper.features.reviews.records import (
    dedup_key,
    extract_org_id,
    first_text,
    make_org_url,
    make_reviews_url,
)


ENRICHED_JSONL_FILE = getattr(
    scraper_config,
    "ENRICHED_JSONL_FILE",
    scraper_config.RAW_DIR / "enriched_data.jsonl",
)
QUEUE_COLUMNS = [
    "org_id",
    "title",
    "org_url",
    "reviews_url",
    "status",
    "error",
    "review_count",
    "captured_at",
]


def load_jsonl_records(path: Path) -> list[dict]:
    records = []
    if not path.exists() or path.stat().st_size == 0:
        return records
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def load_csv_records(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def load_source_records(input_path: Path | None) -> list[dict]:
    if input_path is None:
        if ENRICHED_JSONL_FILE.exists() and ENRICHED_JSONL_FILE.stat().st_size > 0:
            input_path = ENRICHED_JSONL_FILE
        else:
            input_path = CSV_FILE

    suffix = input_path.suffix.lower()
    if suffix == ".jsonl":
        return load_jsonl_records(input_path)
    if suffix == ".csv":
        return load_csv_records(input_path)
    raise ValueError(f"Unsupported input format: {input_path}")


def build_queue_rows(source_records: list[dict]) -> list[dict]:
    rows_by_key = {}
    for source in source_records:
        reviews_url = make_reviews_url(source)
        if not reviews_url:
            continue
        org_id = extract_org_id(
            source.get("org_id"),
            source.get("yandex_id"),
            source.get("permalink"),
            source.get("org_url"),
            reviews_url,
        )
        row = {
            "org_id": org_id,
            "title": first_text(source.get("title"), source.get("shortTitle")),
            "org_url": make_org_url(source),
            "reviews_url": reviews_url,
            "status": "pending",
            "error": "",
            "review_count": "",
            "captured_at": "",
        }
        key = dedup_key(row)
        if key and key not in rows_by_key:
            rows_by_key[key] = row
    return list(rows_by_key.values())

def load_queue(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    return [{column: str(row.get(column) or "") for column in QUEUE_COLUMNS} for row in rows]


def save_queue(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_COLUMNS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in QUEUE_COLUMNS})


def merge_queue(existing: list[dict], generated: list[dict]) -> list[dict]:
    rows_by_key = {}
    for row in existing:
        rows_by_key[dedup_key(row)] = row
    for row in generated:
        key = dedup_key(row)
        if key and key not in rows_by_key:
            rows_by_key[key] = row
    return list(rows_by_key.values())
