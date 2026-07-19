from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from yandex_scraper.config import REVIEWS_ANALYTICS_SOURCE_FILE


CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"
REQUIRED_REVIEW_COLUMNS = {"organization_id", "rating", "text"}


@dataclass(frozen=True)
class ReviewDataset:
    source_path: Path
    source_snapshot: dict[str, Any]
    reviews: list[dict[str, str]]
    anonymized_reviews: list[dict[str, Any]]
    rating_stats: dict[str, Any]
    reviews_hash: str


class ReviewSourceError(RuntimeError):
    """Raised when the configured review source cannot be read safely."""


def load_review_dataset(
    org_id: str,
    source_path: Path = REVIEWS_ANALYTICS_SOURCE_FILE,
) -> ReviewDataset:
    path = Path(source_path)
    reviews = load_reviews_for_organization(org_id, path)
    anonymized_reviews = anonymize_reviews(reviews)
    return ReviewDataset(
        source_path=path,
        source_snapshot=review_source_snapshot(path),
        reviews=reviews,
        anonymized_reviews=anonymized_reviews,
        rating_stats=rating_statistics(reviews),
        reviews_hash=hash_anonymized_reviews(anonymized_reviews),
    )


def load_reviews_for_organization(org_id: str, source_path: Path) -> list[dict[str, str]]:
    org_id = _clean(org_id)
    if not org_id:
        return []

    rows = load_review_rows(source_path)
    return [row for row in rows if _clean(row.get("organization_id")) == org_id]


def load_review_rows(source_path: Path) -> list[dict[str, str]]:
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Reviews source file not found: {path}")
    if path.stat().st_size == 0:
        return []

    try:
        with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=CSV_DELIMITER)
            fieldnames = set(reader.fieldnames or [])
            missing = sorted(REQUIRED_REVIEW_COLUMNS - fieldnames)
            if missing:
                raise ReviewSourceError(f"Reviews CSV is missing required columns: {', '.join(missing)}")
            return [{str(key): _clean(value) for key, value in row.items()} for row in reader]
    except UnicodeDecodeError as exc:
        raise ReviewSourceError(f"Cannot decode reviews CSV as {CSV_ENCODING}: {source_path}") from exc
    except csv.Error as exc:
        raise ReviewSourceError(f"Cannot read reviews CSV: {source_path}: {exc}") from exc


def anonymize_reviews(reviews: list[dict[str, str]]) -> list[dict[str, Any]]:
    anonymized: list[dict[str, Any]] = []
    for row in reviews:
        text = _clean(row.get("text"))
        if not text:
            continue
        item: dict[str, Any] = {"rating": _parse_rating(row.get("rating"))}
        review_date = _clean(row.get("parsed_date")) or _clean(row.get("date"))
        if review_date:
            item["date"] = review_date
        item["text"] = text
        anonymized.append(item)
    return anonymized


def rating_statistics(reviews: list[dict[str, str]]) -> dict[str, Any]:
    distribution = {str(value): 0 for value in range(5, 0, -1)}
    ratings: list[int] = []
    for row in reviews:
        rating = _parse_rating(row.get("rating"))
        if rating is None or rating < 1 or rating > 5:
            continue
        ratings.append(rating)
        distribution[str(rating)] += 1

    average = round(sum(ratings) / len(ratings), 2) if ratings else None
    return {
        "average": average,
        "distribution": distribution,
        "ratedCount": len(ratings),
    }


def hash_anonymized_reviews(reviews: list[dict[str, Any]]) -> str:
    payload = json.dumps(reviews, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_source_snapshot(path: Path) -> dict[str, Any]:
    source_path = Path(path)
    if not source_path.exists():
        return {
            "path": str(source_path),
            "exists": False,
            "sizeBytes": 0,
            "modifiedAt": None,
        }

    stat = source_path.stat()
    return {
        "path": str(source_path),
        "exists": True,
        "sizeBytes": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def _parse_rating(value: object | None) -> int | None:
    text = _clean(value).replace(",", ".")
    if not text:
        return None
    try:
        rating = int(float(text))
    except ValueError:
        return None
    return rating if 1 <= rating <= 5 else None


def _clean(value: object | None) -> str:
    return str(value or "").strip()
