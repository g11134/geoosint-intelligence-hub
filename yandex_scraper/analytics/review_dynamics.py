from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from yandex_scraper.config import (
    RAW_DIR,
    REVIEW_DYNAMICS_OUTPUT_DIR,
    REVIEW_DYNAMICS_PERIODS,
    REVIEW_DYNAMICS_START_DATE,
    REVIEWS_ANALYTICS_SOURCE_FILE,
)
from yandex_scraper.features.reviews.date_filter import parse_review_date


CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"

DEFAULT_RAW_REVIEWS_SOURCE = RAW_DIR / "reviews.jsonl"

ORG_ID_RE = re.compile(r"/org/(?:[^/?#]+/)?(\d+)(?:[/?#]|$)")
INTEGER_FLOAT_RE = re.compile(r"^(\d+)[,.]0+$")
BROKEN_EXCEL_ID_RE = re.compile(r"^\d+[,.]\d+e[+-]?\d+$", re.IGNORECASE)

DAILY_COLUMNS = [
    "organization_key",
    "organization_title",
    "address",
    "date",
    "reviews_count",
    "avg_rating",
    "positive_reviews_count",
    "neutral_reviews_count",
    "negative_reviews_count",
]

WEEKLY_COLUMNS = [
    "organization_key",
    "organization_title",
    "address",
    "week_start",
    "week_end",
    "reviews_count",
    "avg_rating",
    "positive_reviews_count",
    "neutral_reviews_count",
    "negative_reviews_count",
]

MONTHLY_COLUMNS = [
    "organization_key",
    "organization_title",
    "address",
    "month",
    "reviews_count",
    "avg_rating",
    "positive_reviews_count",
    "neutral_reviews_count",
    "negative_reviews_count",
]

SUMMARY_COLUMNS = [
    "organization_key",
    "organization_title",
    "address",
    "total_reviews_since_start",
    "first_review_date",
    "last_review_date",
    "days_with_reviews",
    "reviews_last_7_days",
    "reviews_last_30_days",
    "reviews_last_90_days",
    "reviews_previous_7_days",
    "reviews_previous_30_days",
    "reviews_previous_90_days",
    "growth_7d_abs",
    "growth_30d_abs",
    "growth_90d_abs",
    "growth_7d_pct",
    "growth_30d_pct",
    "growth_90d_pct",
    "avg_review_rating",
    "positive_reviews_count",
    "neutral_reviews_count",
    "negative_reviews_count",
    "negative_share",
    "avg_rating_last_30_days",
    "avg_rating_previous_30_days",
    "rating_change_30d",
    "negative_reviews_last_30_days",
    "negative_share_last_30_days",
    "dynamics_status",
]

STATUS_SORT_ORDER = {
    "active_growth": 0,
    "risk_growth": 1,
    "new_activity": 2,
    "stable": 3,
    "decline": 4,
    "no_recent_reviews": 5,
}


@dataclass(frozen=True)
class ReviewDynamicsResult:
    source_path: Path | None
    output_dir: Path
    output_files: dict[str, Path]
    rows_read: int
    reviews_processed: int
    organizations_processed: int
    start_date: date
    as_of: date
    first_review_date: date | None
    last_review_date: date | None
    missing_date_count: int
    invalid_rating_count: int
    duplicate_reviews_removed: int
    warnings: tuple[str, ...]


def run_review_dynamics_analysis(
    *,
    reviews_source: Path | str | None = None,
    output_dir: Path | str = REVIEW_DYNAMICS_OUTPUT_DIR,
    start_date: str | date = REVIEW_DYNAMICS_START_DATE,
    as_of: str | date | None = None,
    limit: int | None = None,
    organization_limit: int | None = None,
) -> ReviewDynamicsResult:
    """Build review dynamics CSV artifacts from the existing reviews export."""
    parsed_start_date = _parse_cli_date(start_date, "--start-date")
    parsed_as_of = _parse_cli_date(as_of, "--as-of") if as_of is not None else date.today()
    if parsed_as_of < parsed_start_date:
        raise ValueError("--as-of must be greater than or equal to --start-date")

    output_path = Path(output_dir)
    warnings: list[str] = []
    source_path = resolve_reviews_source(reviews_source, warnings)

    raw_reviews = load_review_records(source_path, warnings) if source_path is not None else pd.DataFrame()
    rows_read = len(raw_reviews)
    if limit is not None:
        raw_reviews = raw_reviews.head(max(0, limit))

    normalized, diagnostics = normalize_review_records(
        raw_reviews,
        start_date=parsed_start_date,
        as_of=parsed_as_of,
    )
    warnings.extend(diagnostics["warnings"])

    if organization_limit is not None and not normalized.empty:
        allowed_keys = normalized["organization_key"].drop_duplicates().head(max(0, organization_limit))
        normalized = normalized[normalized["organization_key"].isin(set(allowed_keys))].copy()

    daily = build_daily_dynamics(normalized)
    weekly = build_weekly_dynamics(normalized)
    monthly = build_monthly_dynamics(normalized)
    summary = build_summary(normalized, periods=REVIEW_DYNAMICS_PERIODS, as_of=parsed_as_of)

    output_files = write_outputs(
        output_path,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
        summary=summary,
    )

    first_date = None
    last_date = None
    if not normalized.empty:
        first_date = normalized["review_date"].min().date()
        last_date = normalized["review_date"].max().date()

    return ReviewDynamicsResult(
        source_path=source_path,
        output_dir=output_path,
        output_files=output_files,
        rows_read=rows_read,
        reviews_processed=len(normalized),
        organizations_processed=normalized["organization_key"].nunique() if not normalized.empty else 0,
        start_date=parsed_start_date,
        as_of=parsed_as_of,
        first_review_date=first_date,
        last_review_date=last_date,
        missing_date_count=int(diagnostics["missing_date_count"]),
        invalid_rating_count=int(diagnostics["invalid_rating_count"]),
        duplicate_reviews_removed=int(diagnostics["duplicate_reviews_removed"]),
        warnings=tuple(warnings),
    )


def resolve_reviews_source(source: Path | str | None, warnings: list[str]) -> Path | None:
    """Resolve the reviews source, preferring the exported CSV and falling back to raw JSONL."""
    if source is not None:
        path = Path(source)
        if not path.exists() or path.stat().st_size == 0:
            warnings.append(f"reviews source is missing or empty: {path}")
            return None
        return path

    candidates = (Path(REVIEWS_ANALYTICS_SOURCE_FILE), DEFAULT_RAW_REVIEWS_SOURCE)
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path

    warnings.append(
        "reviews source was not found; checked "
        f"{REVIEWS_ANALYTICS_SOURCE_FILE} and {DEFAULT_RAW_REVIEWS_SOURCE}"
    )
    return None


def load_review_records(path: Path, warnings: list[str]) -> pd.DataFrame:
    """Load review records from CSV or JSONL without changing source artifacts."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(
                path,
                sep=CSV_DELIMITER,
                encoding=CSV_ENCODING,
                dtype=str,
                keep_default_na=False,
            )
        if suffix == ".jsonl":
            rows: list[dict[str, Any]] = []
            with path.open("r", encoding=CSV_ENCODING) as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        warnings.append(f"skipped invalid JSONL line {line_number}: {path}")
                        continue
                    if isinstance(value, dict):
                        rows.append(value)
            return pd.DataFrame(rows)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        warnings.append(f"cannot read reviews source {path}: {exc}")
        return pd.DataFrame()

    warnings.append(f"unsupported reviews source format: {path}")
    return pd.DataFrame()


def normalize_review_records(
    raw_reviews: pd.DataFrame,
    *,
    start_date: date,
    as_of: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Normalize review rows into a compact schema used by all dynamics calculations."""
    warnings: list[str] = []
    missing_date_count = 0
    old_review_count = 0
    future_review_count = 0
    invalid_rating_count = 0
    normalized_rows: list[dict[str, Any]] = []

    if raw_reviews.empty:
        return empty_normalized_reviews(), {
            "missing_date_count": 0,
            "invalid_rating_count": 0,
            "duplicate_reviews_removed": 0,
            "warnings": warnings,
        }

    for _, row in raw_reviews.iterrows():
        row_dict = {str(key): value for key, value in row.items()}
        review_date = _review_date_from_row(row_dict)
        if review_date is None:
            missing_date_count += 1
            continue
        if review_date < start_date:
            old_review_count += 1
            continue
        if review_date > as_of:
            future_review_count += 1
            continue

        rating = _parse_rating(_first_text(row_dict, "rating", "ratingValue", "stars"))
        if rating is None:
            invalid_rating_count += 1

        organization_key = build_organization_key(row_dict)
        record = {
            "organization_key": organization_key,
            "organization_title": _first_text(row_dict, "organization_title", "title", "shortTitle"),
            "address": _first_text(row_dict, "organization_full_address", "fullAddress", "address"),
            "review_id": _first_text(row_dict, "review_id", "id"),
            "author_name": _first_text(row_dict, "author_name", "authorName", "userName", "name"),
            "rating": rating,
            "review_date": review_date,
            "review_text": _first_text(row_dict, "text", "comment", "body", "description"),
            "source": _first_text(row_dict, "source"),
            "captured_at": _first_text(row_dict, "captured_at", "collected_at", "created_at"),
        }
        record["dedup_key"] = build_review_dedup_key(record)
        normalized_rows.append(record)

    normalized = pd.DataFrame(normalized_rows)
    if normalized.empty:
        normalized = empty_normalized_reviews()
        duplicate_reviews_removed = 0
    else:
        before_dedup = len(normalized)
        normalized = normalized.drop_duplicates(subset=["dedup_key"], keep="first").copy()
        duplicate_reviews_removed = before_dedup - len(normalized)
        normalized["review_date"] = pd.to_datetime(normalized["review_date"])

    if missing_date_count:
        warnings.append(f"skipped reviews without usable review date: {missing_date_count}")
    if old_review_count:
        warnings.append(f"skipped reviews before {start_date.isoformat()}: {old_review_count}")
    if future_review_count:
        warnings.append(f"skipped reviews after as-of date {as_of.isoformat()}: {future_review_count}")
    if invalid_rating_count:
        warnings.append(f"reviews without usable rating are included in counts but excluded from rating averages: {invalid_rating_count}")
    if duplicate_reviews_removed:
        warnings.append(f"duplicate reviews removed: {duplicate_reviews_removed}")

    return normalized, {
        "missing_date_count": missing_date_count,
        "invalid_rating_count": invalid_rating_count,
        "duplicate_reviews_removed": duplicate_reviews_removed,
        "warnings": warnings,
    }


def build_organization_key(row: dict[str, Any]) -> str:
    """Return a stable organization key, with a safe hash fallback."""
    for key in ("organization_id", "org_id", "yandex_id", "permalink", "id"):
        normalized = normalize_organization_id(row.get(key))
        if normalized:
            return normalized
    for key in ("organization_url", "org_url", "reviews_url", "source_url"):
        normalized = extract_org_id_from_url(row.get(key))
        if normalized:
            return normalized

    title = _normalize_identity(_first_text(row, "organization_title", "title", "shortTitle"))
    address = _normalize_identity(_first_text(row, "organization_full_address", "fullAddress", "address"))
    source_url = _normalize_identity(_first_text(row, "reviews_url", "organization_url", "org_url", "source_url"))
    payload = "|".join([title, address, source_url])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"fallback:{digest}"


def build_review_dedup_key(record: dict[str, Any]) -> str:
    """Return a review deduplication key using review_id or a deterministic content hash."""
    review_id = _clean(record.get("review_id"))
    if review_id:
        return f"review_id:{review_id}"

    review_date = record.get("review_date")
    review_date_text = review_date.isoformat() if isinstance(review_date, date) else _clean(review_date)
    payload = "|".join(
        [
            _clean(record.get("organization_key")),
            review_date_text,
            _clean(record.get("rating")),
            _clean(record.get("review_text")),
            _clean(record.get("author_name")),
        ]
    )
    return "hash:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_daily_dynamics(reviews: pd.DataFrame) -> pd.DataFrame:
    """Build per-organization daily review dynamics."""
    if reviews.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    working = _with_rating_flags(reviews)
    working["date"] = working["review_date"].dt.date.astype(str)
    return _aggregate_reviews(working, ["date"], DAILY_COLUMNS)


def build_weekly_dynamics(reviews: pd.DataFrame) -> pd.DataFrame:
    """Build per-organization weekly review dynamics using Monday as week start."""
    if reviews.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    working = _with_rating_flags(reviews)
    week_start = working["review_date"] - pd.to_timedelta(working["review_date"].dt.weekday, unit="D")
    working["week_start"] = week_start.dt.date.astype(str)
    working["week_end"] = (week_start + pd.to_timedelta(6, unit="D")).dt.date.astype(str)
    return _aggregate_reviews(working, ["week_start", "week_end"], WEEKLY_COLUMNS)


def build_monthly_dynamics(reviews: pd.DataFrame) -> pd.DataFrame:
    """Build per-organization monthly review dynamics."""
    if reviews.empty:
        return pd.DataFrame(columns=MONTHLY_COLUMNS)
    working = _with_rating_flags(reviews)
    working["month"] = working["review_date"].dt.to_period("M").astype(str)
    return _aggregate_reviews(working, ["month"], MONTHLY_COLUMNS)


def build_summary(reviews: pd.DataFrame, *, periods: list[int], as_of: date) -> pd.DataFrame:
    """Build the per-organization review dynamics summary table."""
    if reviews.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    rows: list[dict[str, Any]] = []
    as_of_ts = pd.Timestamp(as_of)
    for organization_key, group in reviews.groupby("organization_key", sort=False):
        group = _with_rating_flags(group.copy())
        ratings = group["rating"].dropna()
        total_reviews = len(group)
        rated_reviews = len(ratings)
        negative_reviews_count = int(group["negative_reviews_count"].sum())

        row: dict[str, Any] = {
            "organization_key": organization_key,
            "organization_title": _first_nonempty(group["organization_title"]),
            "address": _first_nonempty(group["address"]),
            "total_reviews_since_start": total_reviews,
            "first_review_date": group["review_date"].min().date().isoformat(),
            "last_review_date": group["review_date"].max().date().isoformat(),
            "days_with_reviews": int(group["review_date"].dt.date.nunique()),
            "avg_review_rating": _average_rating(group),
            "positive_reviews_count": int(group["positive_reviews_count"].sum()),
            "neutral_reviews_count": int(group["neutral_reviews_count"].sum()),
            "negative_reviews_count": negative_reviews_count,
            "negative_share": _share(negative_reviews_count, rated_reviews),
        }

        for period in periods:
            current_mask = _window_mask(group, as_of_ts=as_of_ts, days=period)
            previous_mask = _previous_window_mask(group, as_of_ts=as_of_ts, days=period)
            current_count = int(current_mask.sum())
            previous_count = int(previous_mask.sum())
            row[f"reviews_last_{period}_days"] = current_count
            row[f"reviews_previous_{period}_days"] = previous_count
            row[f"growth_{period}d_abs"] = current_count - previous_count
            row[f"growth_{period}d_pct"] = _growth_pct(current_count, previous_count)

        last_30_mask = _window_mask(group, as_of_ts=as_of_ts, days=30)
        previous_30_mask = _previous_window_mask(group, as_of_ts=as_of_ts, days=30)
        before_last_30_mask = group["review_date"] < (as_of_ts - pd.Timedelta(days=29))
        negative_previous_30 = int(group.loc[previous_30_mask, "negative_reviews_count"].sum())
        negative_last_30 = int(group.loc[last_30_mask, "negative_reviews_count"].sum())
        rated_last_30 = int(group.loc[last_30_mask, "rating"].dropna().shape[0])
        avg_last_30 = _average_rating(group.loc[last_30_mask])
        avg_previous_30 = _average_rating(group.loc[previous_30_mask])

        row["avg_rating_last_30_days"] = avg_last_30
        row["avg_rating_previous_30_days"] = avg_previous_30
        row["rating_change_30d"] = _rating_change(avg_last_30, avg_previous_30)
        row["negative_reviews_last_30_days"] = negative_last_30
        row["negative_share_last_30_days"] = _share(negative_last_30, rated_last_30)
        row["dynamics_status"] = classify_dynamics_status(
            reviews_last_30_days=int(row["reviews_last_30_days"]),
            reviews_previous_30_days=int(row["reviews_previous_30_days"]),
            negative_reviews_last_30_days=negative_last_30,
            negative_reviews_previous_30_days=negative_previous_30,
            reviews_before_last_30_days=int(before_last_30_mask.sum()),
        )
        rows.append(row)

    summary = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    summary["_status_sort"] = summary["dynamics_status"].map(STATUS_SORT_ORDER).fillna(99).astype(int)
    summary = summary.sort_values(
        by=["_status_sort", "reviews_last_30_days", "total_reviews_since_start", "organization_title"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    return summary.drop(columns=["_status_sort"]).reset_index(drop=True)


def classify_dynamics_status(
    *,
    reviews_last_30_days: int,
    reviews_previous_30_days: int,
    negative_reviews_last_30_days: int,
    negative_reviews_previous_30_days: int,
    reviews_before_last_30_days: int,
) -> str:
    """Classify an organization's review dynamics status."""
    if reviews_last_30_days <= 0:
        return "no_recent_reviews"
    if negative_reviews_last_30_days > negative_reviews_previous_30_days:
        return "risk_growth"
    if reviews_before_last_30_days <= 0 and reviews_last_30_days > 0:
        return "new_activity"

    growth = reviews_last_30_days - reviews_previous_30_days
    stable_threshold = max(1.0, reviews_previous_30_days * 0.1)
    if abs(growth) <= stable_threshold:
        return "stable"
    if growth > 0:
        return "active_growth"
    return "decline"


def write_outputs(
    output_dir: Path,
    *,
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    monthly: pd.DataFrame,
    summary: pd.DataFrame,
) -> dict[str, Path]:
    """Write all review dynamics CSV artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "daily": output_dir / "review_dynamics_daily.csv",
        "weekly": output_dir / "review_dynamics_weekly.csv",
        "monthly": output_dir / "review_dynamics_monthly.csv",
        "summary": output_dir / "review_dynamics_summary.csv",
    }
    _write_csv(daily, files["daily"], DAILY_COLUMNS)
    _write_csv(weekly, files["weekly"], WEEKLY_COLUMNS)
    _write_csv(monthly, files["monthly"], MONTHLY_COLUMNS)
    _write_csv(summary, files["summary"], SUMMARY_COLUMNS)
    return files


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze review dynamics from existing Yandex reviews data.")
    parser.add_argument("--reviews-source", type=Path, default=None, help="Source reviews CSV or JSONL.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REVIEW_DYNAMICS_OUTPUT_DIR,
        help="Directory for review dynamics CSV files.",
    )
    parser.add_argument(
        "--start-date",
        default=REVIEW_DYNAMICS_START_DATE,
        help="Keep reviews with date >= YYYY-MM-DD.",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Anchor date for last/previous periods in YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit input reviews for diagnostics.")
    parser.add_argument(
        "--organization-limit",
        type=int,
        default=None,
        help="Limit organizations after normalization for diagnostics.",
    )
    return parser


def print_analysis_summary(result: ReviewDynamicsResult) -> None:
    source = str(result.source_path) if result.source_path is not None else "<not found>"
    period = (
        f"{result.first_review_date.isoformat()}..{result.last_review_date.isoformat()}"
        if result.first_review_date and result.last_review_date
        else "<no usable reviews>"
    )
    print("[ReviewDynamics] Analysis complete")
    print(f"reviews source: {source}")
    print(f"organizations processed: {result.organizations_processed}")
    print(f"reviews processed: {result.reviews_processed}")
    print(f"rows read: {result.rows_read}")
    print(f"analysis start date: {result.start_date.isoformat()}")
    print(f"as-of date: {result.as_of.isoformat()}")
    print(f"review date period: {period}")
    print(f"output dir: {result.output_dir}")
    for name, path in result.output_files.items():
        print(f"{name}: {path}")
    if result.warnings:
        for warning in result.warnings:
            print(f"[WARN] {warning}")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        result = run_review_dynamics_analysis(
            reviews_source=args.reviews_source,
            output_dir=args.output_dir,
            start_date=args.start_date,
            as_of=args.as_of,
            limit=args.limit,
            organization_limit=args.organization_limit,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print_analysis_summary(result)


def _aggregate_reviews(working: pd.DataFrame, period_columns: list[str], columns: list[str]) -> pd.DataFrame:
    grouped = (
        working.groupby(["organization_key", *period_columns], dropna=False)
        .agg(
            organization_title=("organization_title", _first_nonempty),
            address=("address", _first_nonempty),
            reviews_count=("review_date", "size"),
            avg_rating=("rating", "mean"),
            positive_reviews_count=("positive_reviews_count", "sum"),
            neutral_reviews_count=("neutral_reviews_count", "sum"),
            negative_reviews_count=("negative_reviews_count", "sum"),
        )
        .reset_index()
    )
    grouped["avg_rating"] = grouped["avg_rating"].round(2)
    return grouped[columns].sort_values(
        by=["organization_key", *period_columns],
        kind="mergesort",
    ).reset_index(drop=True)


def _with_rating_flags(reviews: pd.DataFrame) -> pd.DataFrame:
    working = reviews.copy()
    ratings = working["rating"]
    working["positive_reviews_count"] = ((ratings >= 4) & (ratings <= 5)).astype(int)
    working["negative_reviews_count"] = ((ratings >= 1) & (ratings <= 2)).astype(int)
    working["neutral_reviews_count"] = (ratings == 3).astype(int)
    return working


def _window_mask(group: pd.DataFrame, *, as_of_ts: pd.Timestamp, days: int) -> pd.Series:
    start = as_of_ts - pd.Timedelta(days=days - 1)
    return (group["review_date"] >= start) & (group["review_date"] <= as_of_ts)


def _previous_window_mask(group: pd.DataFrame, *, as_of_ts: pd.Timestamp, days: int) -> pd.Series:
    current_start = as_of_ts - pd.Timedelta(days=days - 1)
    previous_start = current_start - pd.Timedelta(days=days)
    previous_end = current_start - pd.Timedelta(days=1)
    return (group["review_date"] >= previous_start) & (group["review_date"] <= previous_end)


def _average_rating(group: pd.DataFrame) -> float | str:
    if group.empty:
        return ""
    ratings = group["rating"].dropna()
    if ratings.empty:
        return ""
    return round(float(ratings.mean()), 2)


def _rating_change(current: float | str, previous: float | str) -> float | str:
    if current == "" or previous == "":
        return ""
    return round(float(current) - float(previous), 2)


def _growth_pct(current: int, previous: int) -> float | str:
    if previous > 0:
        return round(((current - previous) / previous) * 100, 2)
    if current > 0:
        return ""
    return 0.0


def _share(count: int, denominator: int) -> float | str:
    if denominator <= 0:
        return ""
    return round(count / denominator, 4)


def _write_csv(frame: pd.DataFrame, path: Path, columns: list[str]) -> None:
    frame.reindex(columns=columns).to_csv(
        path,
        sep=CSV_DELIMITER,
        encoding=CSV_ENCODING,
        index=False,
        lineterminator="\n",
        na_rep="",
    )


def _review_date_from_row(row: dict[str, Any]) -> date | None:
    for value in (_first_text(row, "parsed_date"), _first_text(row, "date", "createdAt", "publishedAt")):
        parsed = parse_review_date(value)
        if parsed is not None:
            return parsed
    return None


def _parse_cli_date(value: str | date | None, label: str) -> date:
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        raise ValueError(f"{label} must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from exc


def _parse_rating(value: Any) -> float | None:
    text = _clean(value).replace(",", ".")
    if not text:
        return None
    try:
        rating = float(text)
    except ValueError:
        return None
    if not math.isfinite(rating) or rating < 1 or rating > 5:
        return None
    return rating


def normalize_organization_id(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    from_url = extract_org_id_from_url(text)
    if from_url:
        return from_url
    compact = text.replace(" ", "").replace("\u00a0", "")
    if compact.isdigit():
        return compact
    if BROKEN_EXCEL_ID_RE.match(compact):
        return ""
    integer_float_match = INTEGER_FLOAT_RE.match(compact)
    if integer_float_match:
        return integer_float_match.group(1)
    return ""


def extract_org_id_from_url(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    match = ORG_ID_RE.search(text)
    return match.group(1) if match else ""


def empty_normalized_reviews() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "organization_key",
            "organization_title",
            "address",
            "review_id",
            "author_name",
            "rating",
            "review_date",
            "review_text",
            "source",
            "captured_at",
            "dedup_key",
        ]
    )


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _first_nonempty(values: pd.Series) -> str:
    for value in values:
        text = _clean(value)
        if text:
            return text
    return ""


def _normalize_identity(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value)).casefold()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


if __name__ == "__main__":
    main()
