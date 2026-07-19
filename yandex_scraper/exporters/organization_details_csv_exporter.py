from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yandex_scraper import config as scraper_config
from yandex_scraper.config import OUTPUT_DIR


ORGANIZATION_DETAILS_JSONL_FILE = getattr(
    scraper_config,
    "ORGANIZATION_DETAILS_JSONL_FILE",
    OUTPUT_DIR.parent / "raw" / "organization_details.jsonl",
)
DEFAULT_OUTPUT_FILE = OUTPUT_DIR / "organization_details.csv"

OUTPUT_COLUMNS = [
    "schema_version",
    "captured_at",
    "capture_status",
    "error",
    "organization_id",
    "organization_title",
    "organization_url",
    "reviews_url",
    "page_url",
    "title",
    "category",
    "full_address",
    "phone",
    "website_url",
    "rating_value",
    "rating_count",
    "review_count",
    "open_status_text",
    "has_online_booking_button",
    "online_booking_text",
    "working_hours_notice",
    "working_hours_today",
    "working_hours_text",
    "working_hours_schedule",
    "working_hours_schedule_text",
    "working_hours_schedule_reveal_clicked",
    "contacts_text",
    "features_text",
    "card_visible_text",
]


@dataclass(frozen=True)
class ExportSummary:
    source_path: Path
    output_path: Path
    records_read: int
    rows_exported: int
    invalid_lines: int


def export_organization_details_csv(
    *,
    source_path: Path | str = ORGANIZATION_DETAILS_JSONL_FILE,
    output_path: Path | str = DEFAULT_OUTPUT_FILE,
) -> ExportSummary:
    source = Path(source_path)
    output = Path(output_path)

    if not source.exists():
        raise FileNotFoundError(f"organization details source does not exist: {source}")

    records, invalid_lines = load_jsonl_records(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(build_details_row(record))

    return ExportSummary(
        source_path=source,
        output_path=output,
        records_read=len(records),
        rows_exported=len(records),
        invalid_lines=invalid_lines,
    )


def print_export_summary(summary: ExportSummary) -> None:
    print(f"[OK] Exported organization details CSV: {summary.output_path}")
    print(f"source: {summary.source_path}")
    print(f"records read: {summary.records_read}")
    print(f"rows exported: {summary.rows_exported}")
    if summary.invalid_lines:
        print(f"invalid JSONL lines skipped: {summary.invalid_lines}")


def load_jsonl_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    invalid_lines = 0

    if not path.exists() or path.stat().st_size == 0:
        return records, invalid_lines

    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                invalid_lines += 1

    return records, invalid_lines


def build_details_row(record: dict[str, Any]) -> dict[str, str]:
    row = {column: _cell_text(record.get(column)) for column in OUTPUT_COLUMNS}
    schedule = record.get("working_hours_schedule")
    row["working_hours_schedule"] = _json_text(schedule)
    row["working_hours_schedule_text"] = _schedule_text(schedule)
    return row


def _schedule_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""

    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        day = _cell_text(item.get("day"))
        date = _cell_text(item.get("date"))
        hours = _cell_text(item.get("hours"))
        label = " ".join(part for part in (day, date) if part)
        if label and hours:
            parts.append(f"{label}: {hours}")
        elif label:
            parts.append(label)
        elif hours:
            parts.append(hours)
    return " | ".join(parts)


def _json_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return _json_text(value)
    return str(value).strip()
