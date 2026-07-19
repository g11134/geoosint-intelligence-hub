"""
Convert grid GeoJSON into parsing_queue.csv for 2_yandex_scraper.py.

Output CSV schema (semicolon-separated):
    url;query;bbox;status

Modes:
    - manual override: --input ... --query "..."
    - fixed-grid from config: python import_geojson_to_queue.py
    - feature queries from GeoJSON: --input ... --feature-queries
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from shapely.geometry import shape

from yandex_scraper.config import FIXED_GRID_FILE, QUEUE_FILE, SEARCH_QUERIES, sanitize_url


def parse_bbox_string(raw: str) -> tuple[float, float, float, float] | None:
    text = (raw or "").strip()
    if not text:
        return None

    if "~" in text:
        left, right = text.split("~", 1)
        left_parts = [p.strip() for p in left.split(",")]
        right_parts = [p.strip() for p in right.split(",")]
        if len(left_parts) == 2 and len(right_parts) == 2:
            try:
                lon_min = float(left_parts[0])
                lat_min = float(left_parts[1])
                lon_max = float(right_parts[0])
                lat_max = float(right_parts[1])
                return normalize_bbox(lon_min, lat_min, lon_max, lat_max)
            except ValueError:
                return None

    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 4:
        try:
            lon_min, lat_min, lon_max, lat_max = map(float, parts)
            return normalize_bbox(lon_min, lat_min, lon_max, lat_max)
        except ValueError:
            return None
    return None


def normalize_bbox(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float
) -> tuple[float, float, float, float]:
    return (
        min(lon_min, lon_max),
        min(lat_min, lat_max),
        max(lon_min, lon_max),
        max(lat_min, lat_max),
    )


def format_bbox(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float
) -> str:
    return f"{lon_min:.6f},{lat_min:.6f}~{lon_max:.6f},{lat_max:.6f}"


def build_yandex_url(
    query: str, lon_min: float, lat_min: float, lon_max: float, lat_max: float
) -> str:
    lon_center = (lon_min + lon_max) / 2
    lat_center = (lat_min + lat_max) / 2
    query_encoded = quote(query, safe="", encoding="utf-8")
    bbox_str = format_bbox(lon_min, lat_min, lon_max, lat_max)
    url = (
        f"https://yandex.ru/maps/2/saint-petersburg/search/{query_encoded}/?"
        f"ll={lon_center:.6f},{lat_center:.6f}"
        f"&z=15"
        f"&type=biz"
        f"&bbox={bbox_str}"
    )
    return sanitize_url(url)


def iter_features(data: dict) -> Iterable[dict]:
    geo_type = data.get("type")
    if geo_type == "FeatureCollection":
        for feature in data.get("features", []):
            if isinstance(feature, dict):
                yield feature
        return
    if geo_type == "Feature":
        yield data
        return
    if isinstance(data, dict) and "coordinates" in data and "type" in data:
        yield {"type": "Feature", "geometry": data, "properties": {}}


def feature_bbox(feature: dict) -> tuple[float, float, float, float] | None:
    props = feature.get("properties") or {}
    bbox_from_props = parse_bbox_string(str(props.get("bbox", "")).strip())
    if bbox_from_props is not None:
        return bbox_from_props

    geometry = feature.get("geometry")
    if not geometry:
        return None
    try:
        geom = shape(geometry)
    except Exception:
        return None

    if geom.is_empty:
        return None
    minx, miny, maxx, maxy = geom.bounds
    return normalize_bbox(minx, miny, maxx, maxy)


def resolve_queries(
    feature: dict,
    override_queries: list[str],
    use_config_queries: bool,
) -> list[str]:
    if override_queries:
        return override_queries
    if use_config_queries:
        return list(SEARCH_QUERIES)
    props = feature.get("properties") or {}
    query = str(props.get("query", "")).strip()
    if query:
        return [query]
    return list(SEARCH_QUERIES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert grid GeoJSON into parsing_queue.csv"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=FIXED_GRID_FILE,
        help=f"Input GeoJSON path (FeatureCollection/Feature/Geometry). Default: {FIXED_GRID_FILE}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=QUEUE_FILE,
        help=f"Output CSV queue path. Default: {QUEUE_FILE}",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Override query (can be repeated). If omitted, uses feature query unless --config-queries is set.",
    )
    parser.add_argument(
        "--config-queries",
        action="store_true",
        help="Ignore properties.query in GeoJSON and use SEARCH_QUERIES from config.py.",
    )
    parser.add_argument(
        "--feature-queries",
        action="store_true",
        help="Force using properties.query from GeoJSON when --query is not set.",
    )
    parser.add_argument(
        "--status",
        default="pending",
        help="Status for created rows. Default: pending",
    )
    return parser.parse_args()


def should_use_config_queries(args: argparse.Namespace) -> bool:
    if args.query:
        return False
    if args.config_queries:
        return True
    if args.feature_queries:
        return False
    return args.input.resolve() == FIXED_GRID_FILE.resolve()


def main() -> None:
    args = parse_args()
    use_config_queries = should_use_config_queries(args)
    if not args.input.exists():
        print(f"[ERROR] Input GeoJSON not found: {args.input}")
        raise SystemExit(1)

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict] = []
    skipped = 0
    features_count = 0
    dedupe: set[tuple[str, str]] = set()

    for feature in iter_features(data):
        features_count += 1
        bbox_tuple = feature_bbox(feature)
        if bbox_tuple is None:
            skipped += 1
            continue

        lon_min, lat_min, lon_max, lat_max = bbox_tuple
        bbox_str = format_bbox(lon_min, lat_min, lon_max, lat_max)
        queries = resolve_queries(feature, args.query, use_config_queries)
        for query in queries:
            query_text = str(query).strip()
            if not query_text:
                continue
            key = (bbox_str, query_text)
            if key in dedupe:
                continue
            dedupe.add(key)
            rows.append(
                {
                    "url": build_yandex_url(
                        query_text, lon_min, lat_min, lon_max, lat_max
                    ),
                    "query": query_text,
                    "bbox": bbox_str,
                    "status": args.status,
                }
            )

    fieldnames = ["url", "query", "bbox", "status"]
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Input: {args.input}")
    print(f"[OK] Output: {args.output}")
    if args.query:
        print(f"[OK] Query source: CLI override ({len(args.query)})")
    elif use_config_queries:
        print(f"[OK] Query source: config SEARCH_QUERIES ({len(SEARCH_QUERIES)})")
    else:
        print("[OK] Query source: feature query fallback")
    print(f"[OK] Features read: {features_count}")
    print(f"[OK] Queue rows written: {len(rows)}")
    print(f"[OK] Features skipped (no bbox): {skipped}")


if __name__ == "__main__":
    main()
