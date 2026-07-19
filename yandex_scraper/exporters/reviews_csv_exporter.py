from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yandex_scraper.config import (
    CSV_FILE,
    DATA_DIR,
    ENRICHED_CSV_FILE,
    ENRICHED_JSONL_FILE,
    OUTPUT_DIR,
)
from yandex_scraper.features.reviews.date_filter import parse_review_date


DEFAULT_REVIEWS_SOURCE = DATA_DIR / "raw" / "reviews.jsonl"
DEFAULT_OUTPUT_FILE = OUTPUT_DIR / "reviews.csv"
ORGANIZATION_SOURCE_FALLBACKS = (
    ENRICHED_CSV_FILE,
    ENRICHED_JSONL_FILE,
    CSV_FILE,
)

REVIEW_COLUMNS = [
    "captured_at",
    "organization_id",
    "organization_title",
    "organization_url",
    "reviews_url",
    "review_id",
    "author_name",
    "rating",
    "date",
    "parsed_date",
    "text",
    "likes",
    "has_organization_reply",
    "organization_reply_text",
    "organization_reply_date",
    "source",
]

ORGANIZATION_COLUMNS = [
    "organization_lon",
    "organization_lat",
    "organization_full_address",
    "organization_category",
    "organization_rating_count",
    "organization_rating_value",
    "organization_review_count",
]

RADIUS_COLUMNS = [
    "center_org_id",
    "center_title",
    "center_lon",
    "center_lat",
    "radius_m",
    "distance_to_center_m",
    "within_radius",
]

OUTPUT_COLUMNS = REVIEW_COLUMNS + ORGANIZATION_COLUMNS + RADIUS_COLUMNS

ORG_ID_RE = re.compile(r"/org/(?:[^/?#]+/)?(\d+)(?:[/?#]|$)")
CAPTURED_YEAR_RE = re.compile(r"^(\d{4})")
BROKEN_EXCEL_ID_RE = re.compile(r"^\d+[,.]\d+e[+-]?\d+$", re.IGNORECASE)
INTEGER_FLOAT_RE = re.compile(r"^(\d+)[,.]0+$")
EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class OrganizationRecord:
    org_id: str
    ids: tuple[str, ...]
    title: str
    lon_text: str
    lat_text: str
    lon: float | None
    lat: float | None
    full_address: str
    category: str
    rating_count: str
    rating_value: str
    review_count: str


@dataclass(frozen=True)
class CenterPoint:
    org_id: str
    title: str
    lon: float
    lat: float


@dataclass(frozen=True)
class ExportSummary:
    reviews_source: Path
    organizations_source: Path | None
    output_path: Path
    reviews_read: int
    reviews_exported: int
    organizations_read: int
    reviews_without_organization_match: int
    reviews_without_coordinates: int
    within_radius_count: int


def export_reviews_csv(
    *,
    reviews_source: Path | str = DEFAULT_REVIEWS_SOURCE,
    organizations_source: Path | str | None = None,
    output_path: Path | str = DEFAULT_OUTPUT_FILE,
    center_org_id: str | None = None,
    center_lon: float | str | None = None,
    center_lat: float | str | None = None,
    radius_m: float | str | None = None,
    only_within_radius: bool = False,
) -> ExportSummary:
    reviews_path = Path(reviews_source)
    output = Path(output_path)
    org_source = resolve_organizations_source(organizations_source)
    radius = _parse_optional_positive_radius(radius_m)

    if not reviews_path.exists():
        raise FileNotFoundError(f"reviews source does not exist: {reviews_path}")
    if only_within_radius and radius is None:
        raise ValueError("--only-within-radius requires --radius-m")
    if (center_lon is None) != (center_lat is None):
        raise ValueError("pass both --center-lon and --center-lat, or neither")

    organizations: list[OrganizationRecord] = []
    organizations_read = 0
    if org_source:
        organizations, organizations_read = load_organization_records(org_source)
    organization_index = build_organization_index(organizations)
    center = resolve_center(
        organization_index,
        center_org_id=center_org_id,
        center_lon=center_lon,
        center_lat=center_lat,
    )
    if only_within_radius and center is None:
        raise ValueError("--only-within-radius requires --center-org-id or --center-lon/--center-lat")

    reviews = load_records(reviews_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    reviews_without_organization_match = 0
    reviews_without_coordinates = 0
    reviews_exported = 0
    within_radius_count = 0

    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, delimiter=";", extrasaction="ignore")
        writer.writeheader()

        for review in reviews:
            review_ids = normalized_review_ids(review)
            organization = first_matching_organization(organization_index, review_ids)
            normalized_review_id = review_ids[0] if review_ids else _cell_text(review.get("organization_id"))

            if organization is None:
                reviews_without_organization_match += 1
            if organization is None or organization.lon is None or organization.lat is None:
                reviews_without_coordinates += 1

            row = build_output_row(
                review,
                organization,
                normalized_organization_id=normalized_review_id,
                center=center,
                radius_m=radius,
            )
            if row["within_radius"] == "true":
                within_radius_count += 1
            if only_within_radius and row["within_radius"] != "true":
                continue

            writer.writerow(row)
            reviews_exported += 1

    return ExportSummary(
        reviews_source=reviews_path,
        organizations_source=org_source,
        output_path=output,
        reviews_read=len(reviews),
        reviews_exported=reviews_exported,
        organizations_read=organizations_read,
        reviews_without_organization_match=reviews_without_organization_match,
        reviews_without_coordinates=reviews_without_coordinates,
        within_radius_count=within_radius_count,
    )


def print_export_summary(summary: ExportSummary) -> None:
    org_source = str(summary.organizations_source) if summary.organizations_source else "<not found>"
    print(f"[OK] Exported reviews CSV: {summary.output_path}")
    print(f"reviews source: {summary.reviews_source}")
    print(f"organizations source: {org_source}")
    print(f"reviews read: {summary.reviews_read}")
    print(f"reviews exported: {summary.reviews_exported}")
    print(f"organizations read: {summary.organizations_read}")
    print(f"reviews without organization match: {summary.reviews_without_organization_match}")
    print(f"reviews without coordinates: {summary.reviews_without_coordinates}")
    print(f"within radius count: {summary.within_radius_count}")


def resolve_organizations_source(source: Path | str | None) -> Path | None:
    if source is not None:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"organizations source does not exist: {path}")
        return path

    for path in ORGANIZATION_SOURCE_FALLBACKS:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def load_organization_records(source: Path) -> tuple[list[OrganizationRecord], int]:
    rows = load_records(source)
    records = []
    for row in rows:
        ids = normalized_organization_ids(row)
        if not ids:
            continue
        records.append(organization_from_row(row, ids))
    return records, len(rows)


def load_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return load_jsonl_records(path)
    if suffix == ".csv":
        return load_csv_records(path)
    raise ValueError(f"unsupported input format: {path}")


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists() or path.stat().st_size == 0:
        return records

    with path.open("r", encoding="utf-8-sig") as handle:
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


def load_csv_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        rows = []
        for row in reader:
            rows.append({str(key): value for key, value in row.items() if key is not None})
    return rows


def organization_from_row(row: dict[str, Any], ids: list[str]) -> OrganizationRecord:
    lon_text = first_text(
        row,
        "coordinates_0",
        "organization_lon",
        "lon",
        "longitude",
    )
    lat_text = first_text(
        row,
        "coordinates_1",
        "organization_lat",
        "lat",
        "latitude",
    )
    return OrganizationRecord(
        org_id=ids[0],
        ids=tuple(ids),
        title=first_text(row, "title", "shortTitle", "organization_title"),
        lon_text=lon_text,
        lat_text=lat_text,
        lon=parse_coordinate(lon_text, is_lon=True),
        lat=parse_coordinate(lat_text, is_lon=False),
        full_address=first_text(row, "fullAddress", "organization_full_address"),
        category=first_text(row, "categories_0_name", "dom_category", "organization_category"),
        rating_count=first_text(row, "rating_count", "ratingData_ratingCount", "organization_rating_count"),
        rating_value=first_text(row, "rating_value", "ratingData_ratingValue", "organization_rating_value"),
        review_count=first_text(row, "review_count", "organization_review_count"),
    )


def build_organization_index(organizations: list[OrganizationRecord]) -> dict[str, OrganizationRecord]:
    index: dict[str, OrganizationRecord] = {}
    for organization in organizations:
        for org_id in organization.ids:
            if org_id and org_id not in index:
                index[org_id] = organization
    return index


def first_matching_organization(
    organization_index: dict[str, OrganizationRecord],
    candidate_ids: list[str],
) -> OrganizationRecord | None:
    for org_id in candidate_ids:
        organization = organization_index.get(org_id)
        if organization is not None:
            return organization
    return None


def resolve_center(
    organization_index: dict[str, OrganizationRecord],
    *,
    center_org_id: str | None,
    center_lon: float | str | None,
    center_lat: float | str | None,
) -> CenterPoint | None:
    raw_center_org_id = _cell_text(center_org_id)
    normalized_center_org_id = normalize_direct_id(raw_center_org_id)
    if raw_center_org_id and not normalized_center_org_id:
        raise ValueError(f"center org id is invalid or cannot be normalized: {center_org_id}")
    if normalized_center_org_id:
        organization = organization_index.get(normalized_center_org_id)
        if organization is None:
            raise ValueError(f"center org id was not found in organizations source: {center_org_id}")
        if organization.lon is None or organization.lat is None:
            raise ValueError(f"center org id has no coordinates: {center_org_id}")
        return CenterPoint(
            org_id=organization.org_id,
            title=organization.title,
            lon=organization.lon,
            lat=organization.lat,
        )

    if center_lon is None and center_lat is None:
        return None

    lon = parse_coordinate(center_lon, is_lon=True)
    lat = parse_coordinate(center_lat, is_lon=False)
    if lon is None or lat is None:
        raise ValueError("--center-lon and --center-lat must be valid coordinates")
    return CenterPoint(org_id="", title="", lon=lon, lat=lat)


def build_output_row(
    review: dict[str, Any],
    organization: OrganizationRecord | None,
    *,
    normalized_organization_id: str,
    center: CenterPoint | None,
    radius_m: float | None,
) -> dict[str, str]:
    row = {column: _cell_text(review.get(column)) for column in REVIEW_COLUMNS}
    normalize_review_date_fields(row, review)
    if normalized_organization_id:
        row["organization_id"] = normalized_organization_id

    if organization is not None:
        row.update(
            {
                "organization_lon": organization.lon_text,
                "organization_lat": organization.lat_text,
                "organization_full_address": organization.full_address,
                "organization_category": organization.category,
                "organization_rating_count": organization.rating_count,
                "organization_rating_value": organization.rating_value,
                "organization_review_count": organization.review_count,
            }
        )
    else:
        row.update({column: "" for column in ORGANIZATION_COLUMNS})

    row.update({column: "" for column in RADIUS_COLUMNS})
    if center is None:
        return row

    row.update(
        {
            "center_org_id": center.org_id,
            "center_title": center.title,
            "center_lon": format_float(center.lon),
            "center_lat": format_float(center.lat),
        }
    )
    if radius_m is not None:
        row["radius_m"] = format_float(radius_m)

    if organization is None or organization.lon is None or organization.lat is None:
        if radius_m is not None:
            row["within_radius"] = "false"
        return row

    distance = haversine_meters(organization.lon, organization.lat, center.lon, center.lat)
    row["distance_to_center_m"] = f"{distance:.1f}"
    if radius_m is not None:
        row["within_radius"] = "true" if distance <= radius_m else "false"
    return row


def normalize_review_date_fields(row: dict[str, str], review: dict[str, Any]) -> None:
    source_date = _cell_text(review.get("date")) or _cell_text(review.get("parsed_date"))
    if not source_date:
        return

    parsed = parse_review_date(source_date, default_year=captured_year(review))
    if parsed is None:
        return

    row["date"] = parsed.strftime("%d.%m.%Y")
    row["parsed_date"] = parsed.isoformat()


def captured_year(review: dict[str, Any]) -> int | None:
    captured_at = _cell_text(review.get("captured_at"))
    match = CAPTURED_YEAR_RE.match(captured_at)
    if not match:
        return None
    year = int(match.group(1))
    if 1900 <= year <= 2100:
        return year
    return None


def normalized_organization_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("org_url", "organization_url", "reviews_url"):
        _append_unique(ids, extract_org_id_from_url(row.get(key)))
    for key in ("yandex_id", "permalink", "id", "org_id", "organization_id"):
        _append_unique(ids, normalize_direct_id(row.get(key)))
    return ids


def normalized_review_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("organization_url", "org_url", "reviews_url"):
        _append_unique(ids, extract_org_id_from_url(row.get(key)))
    for key in ("organization_id", "org_id", "yandex_id", "permalink", "id"):
        _append_unique(ids, normalize_direct_id(row.get(key)))
    return ids


def extract_org_id_from_url(value: Any) -> str:
    text = _cell_text(value)
    if not text:
        return ""
    match = ORG_ID_RE.search(text)
    if match:
        return match.group(1)
    return ""


def normalize_direct_id(value: Any) -> str:
    text = _cell_text(value)
    if not text:
        return ""

    url_id = extract_org_id_from_url(text)
    if url_id:
        return url_id

    compact = text.replace(" ", "").replace("\u00a0", "")
    if not compact:
        return ""
    if BROKEN_EXCEL_ID_RE.match(compact):
        return ""
    if compact.isdigit():
        return compact

    integer_float_match = INTEGER_FLOAT_RE.match(compact)
    if integer_float_match:
        return integer_float_match.group(1)
    return ""


def parse_coordinate(value: Any, *, is_lon: bool) -> float | None:
    text = _cell_text(value).replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    if is_lon and not -180 <= parsed <= 180:
        return None
    if not is_lon and not -90 <= parsed <= 90:
        return None
    return parsed


def haversine_meters(org_lon: float, org_lat: float, center_lon: float, center_lat: float) -> float:
    org_lon_rad = math.radians(org_lon)
    org_lat_rad = math.radians(org_lat)
    center_lon_rad = math.radians(center_lon)
    center_lat_rad = math.radians(center_lat)

    delta_lon = center_lon_rad - org_lon_rad
    delta_lat = center_lat_rad - org_lat_rad
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(org_lat_rad) * math.cos(center_lat_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(EARTH_RADIUS_M * c, 1)


def first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _cell_text(row.get(key))
        if value:
            return value
    return ""


def format_float(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _parse_optional_positive_radius(value: float | str | None) -> float | None:
    if value is None:
        return None
    try:
        radius = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--radius-m must be a positive number") from exc
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("--radius-m must be > 0")
    return radius


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
