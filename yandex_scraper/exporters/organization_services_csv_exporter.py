from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yandex_scraper import config as scraper_config
from yandex_scraper.config import OUTPUT_DIR


ORGANIZATION_SERVICES_JSONL_FILE = getattr(
    scraper_config,
    "ORGANIZATION_SERVICES_JSONL_FILE",
    OUTPUT_DIR.parent / "raw" / "organization_services.jsonl",
)
DEFAULT_OUTPUT_FILE = OUTPUT_DIR / "organization_services.csv"

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
    "service_index",
    "service_category",
    "service_name",
    "service_description",
    "service_price",
    "services_count",
    "services_text",
    "services_reveal_clicked",
]


@dataclass(frozen=True)
class ExportSummary:
    source_path: Path
    output_path: Path
    records_read: int
    rows_exported: int
    invalid_lines: int
    records_without_services: int


def export_organization_services_csv(
    *,
    source_path: Path | str = ORGANIZATION_SERVICES_JSONL_FILE,
    output_path: Path | str = DEFAULT_OUTPUT_FILE,
) -> ExportSummary:
    source = Path(source_path)
    output = Path(output_path)

    if not source.exists():
        raise FileNotFoundError(f"organization services source does not exist: {source}")

    records, invalid_lines = load_jsonl_records(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    rows_exported = 0
    records_without_services = 0
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, delimiter=";", extrasaction="ignore")
        writer.writeheader()

        for record in records:
            services = _normalized_services(record.get("services"))
            if not services:
                records_without_services += 1
                services = [{}]

            for index, service in enumerate(services, start=1):
                writer.writerow(build_service_row(record, service, index))
                rows_exported += 1

    return ExportSummary(
        source_path=source,
        output_path=output,
        records_read=len(records),
        rows_exported=rows_exported,
        invalid_lines=invalid_lines,
        records_without_services=records_without_services,
    )


def print_export_summary(summary: ExportSummary) -> None:
    print(f"[OK] Exported organization services CSV: {summary.output_path}")
    print(f"source: {summary.source_path}")
    print(f"records read: {summary.records_read}")
    print(f"rows exported: {summary.rows_exported}")
    print(f"records without services: {summary.records_without_services}")
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


def build_service_row(record: dict[str, Any], service: dict[str, Any], service_index: int) -> dict[str, str]:
    return {
        "schema_version": _cell_text(record.get("schema_version")),
        "captured_at": _cell_text(record.get("captured_at")),
        "capture_status": _cell_text(record.get("capture_status")),
        "error": _cell_text(record.get("error")),
        "organization_id": _cell_text(record.get("organization_id")),
        "organization_title": _cell_text(record.get("organization_title")),
        "organization_url": _cell_text(record.get("organization_url")),
        "reviews_url": _cell_text(record.get("reviews_url")),
        "page_url": _cell_text(record.get("page_url")),
        "service_index": str(service_index),
        "service_category": _cell_text(service.get("category")),
        "service_name": _cell_text(service.get("name")),
        "service_description": _cell_text(service.get("description")),
        "service_price": _cell_text(service.get("price")),
        "services_count": _cell_text(record.get("services_count")),
        "services_text": _cell_text(record.get("services_text")),
        "services_reveal_clicked": _cell_text(record.get("services_reveal_clicked")),
    }


def _normalized_services(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    services: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            services.append(item)
        elif item is not None:
            services.append({"name": str(item), "price": ""})
    return services


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value).strip()
