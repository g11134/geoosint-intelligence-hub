from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"

DEFAULT_COMPARE_FIELDS = [
    "title",
    "shortTitle",
    "fullAddress",
    "categories_0_name",
    "phones_0_number",
    "coordinates_0",
    "coordinates_1",
    "permalink",
    "ratingData_ratingCount",
    "ratingData_ratingValue",
]

DISPLAY_FIELDS = [
    "title",
    "fullAddress",
    "categories_0_name",
    "phones_0_number",
    "coordinates_0",
    "coordinates_1",
    "permalink",
    "source_query",
    "source_bbox",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two snapshot result.csv files.")
    parser.add_argument("--old", type=Path, required=True, help="Older result.csv path.")
    parser.add_argument("--new", type=Path, required=True, help="Newer result.csv path.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Field to compare. Can be repeated. Default: core organization fields.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fields = args.field or DEFAULT_COMPARE_FIELDS
    stats = compare_snapshots(args.old, args.new, args.output, fields=fields)

    print("[OK] Snapshot comparison complete")
    print(f"    output: {stats['output_dir']}")
    print(f"    old rows: {stats['old_rows']}")
    print(f"    new rows: {stats['new_rows']}")
    print(f"    added: {stats['added']}")
    print(f"    removed: {stats['removed']}")
    print(f"    changed: {stats['changed']}")


def compare_snapshots(
    old_csv: Path,
    new_csv: Path,
    output_dir: Path,
    *,
    fields: list[str] | tuple[str, ...] = tuple(DEFAULT_COMPARE_FIELDS),
) -> dict:
    old_records = load_records(old_csv)
    new_records = load_records(new_csv)

    old_by_key = index_records(old_records)
    new_by_key = index_records(new_records)

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)
    added_keys = sorted(new_keys - old_keys, key=lambda key: sort_key(new_by_key[key]))
    removed_keys = sorted(old_keys - new_keys, key=lambda key: sort_key(old_by_key[key]))
    common_keys = sorted(old_keys & new_keys, key=lambda key: sort_key(new_by_key[key]))

    added_rows = [change_row("added", key, None, new_by_key[key]) for key in added_keys]
    removed_rows = [change_row("removed", key, old_by_key[key], None) for key in removed_keys]
    changed_rows = []

    for key in common_keys:
        old_row = old_by_key[key]
        new_row = new_by_key[key]
        changed_fields = [
            field
            for field in fields
            if normalize_value(old_row.get(field)) != normalize_value(new_row.get(field))
        ]
        if changed_fields:
            changed_rows.append(changed_row(key, old_row, new_row, changed_fields, fields))

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "added.csv", added_rows, added_removed_fieldnames())
    write_csv(output_dir / "removed.csv", removed_rows, added_removed_fieldnames())
    write_csv(output_dir / "changed.csv", changed_rows, changed_fieldnames(fields))

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "old_csv": str(old_csv),
        "new_csv": str(new_csv),
        "output_dir": str(output_dir),
        "old_rows": len(old_records),
        "new_rows": len(new_records),
        "old_unique": len(old_by_key),
        "new_unique": len(new_by_key),
        "added": len(added_rows),
        "removed": len(removed_rows),
        "changed": len(changed_rows),
        "compared_fields": list(fields),
        "files": {
            "added_csv": str(output_dir / "added.csv"),
            "removed_csv": str(output_dir / "removed.csv"),
            "changed_csv": str(output_dir / "changed.csv"),
            "summary_json": str(output_dir / "summary.json"),
        },
    }
    with (output_dir / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
        file.write("\n")

    return summary


def load_records(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    with path.open("r", encoding=CSV_ENCODING, newline="") as file:
        reader = csv.DictReader(file, delimiter=CSV_DELIMITER)
        return [{str(key): clean(value) for key, value in row.items()} for row in reader]


def index_records(records: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in records:
        key = organization_key(row)
        if key and key not in indexed:
            indexed[key] = row
    return indexed


def organization_key(row: dict[str, str]) -> str:
    permalink = clean(row.get("permalink"))
    if re.fullmatch(r"\d+", permalink):
        return f"permalink:{permalink}"

    title = normalize_identity(row.get("title"))
    address = normalize_identity(row.get("fullAddress"))
    lon = normalize_coordinate(row.get("coordinates_0"))
    lat = normalize_coordinate(row.get("coordinates_1"))
    identity = "|".join([title, address, lon, lat])
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    return f"fallback:{digest}"


def change_row(
    change_type: str,
    key: str,
    old_row: dict[str, str] | None,
    new_row: dict[str, str] | None,
) -> dict[str, str]:
    source = new_row or old_row or {}
    row = {
        "change_type": change_type,
        "org_id": key,
    }
    for field in DISPLAY_FIELDS:
        row[field] = clean(source.get(field))
    return row


def changed_row(
    key: str,
    old_row: dict[str, str],
    new_row: dict[str, str],
    changed_fields: list[str],
    fields: list[str] | tuple[str, ...],
) -> dict[str, str]:
    row = {
        "change_type": "changed",
        "org_id": key,
        "changed_fields": ",".join(changed_fields),
        "title": clean(new_row.get("title")) or clean(old_row.get("title")),
        "fullAddress": clean(new_row.get("fullAddress")) or clean(old_row.get("fullAddress")),
        "source_query": clean(new_row.get("source_query")) or clean(old_row.get("source_query")),
    }
    for field in fields:
        row[f"old_{field}"] = clean(old_row.get(field))
        row[f"new_{field}"] = clean(new_row.get(field))
    return row


def added_removed_fieldnames() -> list[str]:
    return ["change_type", "org_id", *DISPLAY_FIELDS]


def changed_fieldnames(fields: list[str] | tuple[str, ...]) -> list[str]:
    names = ["change_type", "org_id", "changed_fields", "title", "fullAddress", "source_query"]
    for field in fields:
        names.append(f"old_{field}")
        names.append(f"new_{field}")
    return names


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            delimiter=CSV_DELIMITER,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def sort_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        normalize_identity(row.get("fullAddress")),
        normalize_identity(row.get("title")),
        normalize_identity(row.get("permalink")),
    )


def clean(value: object | None) -> str:
    return str(value or "").strip()


def normalize_value(value: object | None) -> str:
    return re.sub(r"\s+", " ", clean(value)).casefold()


def normalize_identity(value: object | None) -> str:
    return normalize_value(value).replace("ё", "е")


def normalize_coordinate(value: object | None) -> str:
    text = clean(value).replace(",", ".")
    if not text:
        return ""
    try:
        return f"{float(text):.7f}"
    except ValueError:
        return normalize_identity(text)


if __name__ == "__main__":
    main()
