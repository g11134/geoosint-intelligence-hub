from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from yandex_scraper.api.models import Organization, OrganizationCard
from yandex_scraper.config import CSV_FILE, FINAL_COLUMNS, ORGANIZATIONS_DB_FILE


CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ";"
SCHEMA_VERSION = "2"

ORGANIZATION_COLUMNS = list(FINAL_COLUMNS)
DB_COLUMNS = [
    "id",
    *ORGANIZATION_COLUMNS,
    "lon",
    "lat",
    "has_valid_coordinates",
    "raw_json",
]

CARD_COLUMNS = [
    "id",
    "yandex_id",
    "permalink",
    "org_url",
    "title",
    "fullAddress",
    "category",
    "categories_json",
    "phones_0_number",
    "website_url",
    "coordinates_0",
    "coordinates_1",
    "lon",
    "lat",
    "has_valid_coordinates",
    "rating_value",
    "rating_value_raw",
    "rating_count",
    "rating_count_raw",
    "review_count",
    "captured_at",
    "source_query",
    "source_bbox",
    "cell_url",
    "search_result_index",
    "open_status_text",
    "awards_text",
    "business_verified_owner",
    "services_json",
    "services_text",
    "payment_methods_json",
    "payment_methods_text",
    "medical_specialists_json",
    "medical_specialists_text",
    "uni_medic_specializations_json",
    "uni_medic_specializations_text",
    "pediatric_specialists_json",
    "pediatric_specialists_text",
    "accessibility_json",
    "accessibility_text",
    "promotion_types_json",
    "promotion_types_text",
    "cashback_percent",
    "snippet_price_text",
    "snippet_offer_text",
    "has_for_children",
    "has_good_place",
    "has_vtb_offer",
    "has_free_examination",
    "has_installments",
    "has_guarantee",
    "has_wifi",
    "has_ramp",
    "has_disabled_parking",
    "raw_features_json",
    "raw_categories_json",
    "dom_visibleText",
    "raw_json",
]

FEATURE_COLUMNS = [
    "org_id",
    "feature_source",
    "feature_id",
    "feature_name",
    "feature_type",
    "value_id",
    "value_name",
    "value_text",
    "value_bool",
    "important",
    "value_index",
]

CATEGORY_COLUMNS = [
    "org_id",
    "category_index",
    "category_id",
    "category_name",
    "category_class",
    "category_seoname",
    "category_plural_name",
]

ENRICHED_MARKER_COLUMNS = {
    "raw_features_json",
    "raw_categories_json",
    "dom_visibleText",
    "offer_text",
}

COMMERCIAL_CATEGORY_NAME = "Коммерческие организации"
COMMERCIAL_CATEGORY_ID = "commercial_organizations"
COMMERCIAL_SUBCATEGORIES = (
    "детская стоматология",
    "диагностический центр",
    "зуботехническая лаборатория",
    "косметология",
    "медицинская лаборатория",
    "стоматологическая клиника",
    "стоматологические материалы и оборудование",
)
NON_COMMERCIAL_PRIMARY_CATEGORY_MARKERS = (
    "стоматологическая поликлиника",
    "поликлиника",
    "больница",
    "госпиталь",
)
NON_COMMERCIAL_TITLE_MARKERS = (
    "гбуз",
    "городская больница",
    "больница",
    "госпиталь",
)


class OrganizationRepository:
    """Read-only repository over the organizations SQLite read-model."""

    def __init__(self, db_path: Path = ORGANIZATIONS_DB_FILE) -> None:
        self.db_path = Path(db_path)

    def source_snapshot(self) -> dict:
        if not self.db_path.exists():
            return {
                "path": str(self.db_path),
                "exists": False,
                "sizeBytes": 0,
                "modifiedAt": None,
                "metadata": {},
            }

        stat = self.db_path.stat()
        snapshot = {
            "path": str(self.db_path),
            "exists": True,
            "sizeBytes": stat.st_size,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "metadata": {},
        }
        try:
            with self._connect() as conn:
                snapshot["metadata"] = read_metadata(conn)
        except sqlite3.Error as exc:
            snapshot["readable"] = False
            snapshot["error"] = str(exc)
        return snapshot

    def list(self) -> list[Organization]:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Organizations DB not found: {self.db_path}")

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        title,
                        shortTitle,
                        fullAddress,
                        categories_0_name,
                        phones_0_number,
                        coordinates_0,
                        coordinates_1,
                        permalink,
                        ratingData_ratingCount,
                        ratingData_ratingValue,
                        source_query,
                        source_bbox,
                        lon,
                        lat,
                        raw_json
                    FROM organizations
                    WHERE has_valid_coordinates = 1
                    ORDER BY fullAddress, title, id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(f"Cannot read organizations DB: {exc}") from exc
        return [_row_to_organization(row) for row in rows]

    def list_cards(self) -> list[OrganizationCard]:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Organizations DB not found: {self.db_path}")

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM organization_cards
                    WHERE has_valid_coordinates = 1
                    ORDER BY fullAddress, title, id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(f"Cannot read organization cards DB: {exc}") from exc
        return [_row_to_organization_card(row) for row in rows]

    def get_card(self, org_id: str) -> OrganizationCard | None:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Organizations DB not found: {self.db_path}")

        org_id = _clean(org_id)
        if not org_id:
            return None

        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM organization_cards
                    WHERE (id = ? OR yandex_id = ? OR permalink = ?)
                      AND has_valid_coordinates = 1
                    ORDER BY fullAddress, title, id
                    LIMIT 1
                    """,
                    (org_id, org_id, org_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError(f"Cannot read organization cards DB: {exc}") from exc
        if row is None:
            return None
        return _row_to_organization_card(row)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn


def build_organizations_db(
    source_path: Path = CSV_FILE,
    db_path: Path = ORGANIZATIONS_DB_FILE,
) -> dict:
    """Rebuild the organizations read-model from the exported CSV."""

    source_path = Path(source_path)
    db_path = Path(db_path)

    if not source_path.exists():
        raise FileNotFoundError(f"CSV export not found: {source_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(f".{db_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    stats = {
        "source_path": str(source_path),
        "db_path": str(db_path),
        "source_rows": 0,
        "valid_coordinate_rows": 0,
        "missing_columns": [],
        "unique_rows": 0,
        "source_kind": "legacy",
        "enriched_card_rows": 0,
        "feature_rows": 0,
        "category_rows": 0,
    }

    conn = sqlite3.connect(tmp_path)
    try:
        conn.row_factory = sqlite3.Row
        create_schema(conn)
        stats.update(load_csv_into_db(conn, source_path))
        write_metadata(conn, source_path=source_path, stats=stats)
        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
    except Exception:
        conn.rollback()
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    else:
        conn.close()
        tmp_path.replace(db_path)

    stats["db_size_bytes"] = db_path.stat().st_size
    return stats


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE organizations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            shortTitle TEXT NOT NULL DEFAULT '',
            fullAddress TEXT NOT NULL DEFAULT '',
            categories_0_name TEXT NOT NULL DEFAULT '',
            phones_0_number TEXT NOT NULL DEFAULT '',
            coordinates_0 TEXT NOT NULL DEFAULT '',
            coordinates_1 TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL DEFAULT '',
            ratingData_ratingCount TEXT NOT NULL DEFAULT '',
            ratingData_ratingValue TEXT NOT NULL DEFAULT '',
            source_query TEXT NOT NULL DEFAULT '',
            source_bbox TEXT NOT NULL DEFAULT '',
            lon REAL,
            lat REAL,
            has_valid_coordinates INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE organization_cards (
            id TEXT PRIMARY KEY,
            yandex_id TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL DEFAULT '',
            org_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            fullAddress TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            categories_json TEXT NOT NULL DEFAULT '[]',
            phones_0_number TEXT NOT NULL DEFAULT '',
            website_url TEXT NOT NULL DEFAULT '',
            coordinates_0 TEXT NOT NULL DEFAULT '',
            coordinates_1 TEXT NOT NULL DEFAULT '',
            lon REAL,
            lat REAL,
            has_valid_coordinates INTEGER NOT NULL DEFAULT 0,
            rating_value REAL,
            rating_value_raw TEXT NOT NULL DEFAULT '',
            rating_count INTEGER,
            rating_count_raw TEXT NOT NULL DEFAULT '',
            review_count INTEGER,
            captured_at TEXT NOT NULL DEFAULT '',
            source_query TEXT NOT NULL DEFAULT '',
            source_bbox TEXT NOT NULL DEFAULT '',
            cell_url TEXT NOT NULL DEFAULT '',
            search_result_index TEXT NOT NULL DEFAULT '',
            open_status_text TEXT NOT NULL DEFAULT '',
            awards_text TEXT NOT NULL DEFAULT '',
            business_verified_owner INTEGER NOT NULL DEFAULT 0,
            services_json TEXT NOT NULL DEFAULT '[]',
            services_text TEXT NOT NULL DEFAULT '',
            payment_methods_json TEXT NOT NULL DEFAULT '[]',
            payment_methods_text TEXT NOT NULL DEFAULT '',
            medical_specialists_json TEXT NOT NULL DEFAULT '[]',
            medical_specialists_text TEXT NOT NULL DEFAULT '',
            uni_medic_specializations_json TEXT NOT NULL DEFAULT '[]',
            uni_medic_specializations_text TEXT NOT NULL DEFAULT '',
            pediatric_specialists_json TEXT NOT NULL DEFAULT '[]',
            pediatric_specialists_text TEXT NOT NULL DEFAULT '',
            accessibility_json TEXT NOT NULL DEFAULT '[]',
            accessibility_text TEXT NOT NULL DEFAULT '',
            promotion_types_json TEXT NOT NULL DEFAULT '[]',
            promotion_types_text TEXT NOT NULL DEFAULT '',
            cashback_percent TEXT NOT NULL DEFAULT '',
            snippet_price_text TEXT NOT NULL DEFAULT '',
            snippet_offer_text TEXT NOT NULL DEFAULT '',
            has_for_children INTEGER NOT NULL DEFAULT 0,
            has_good_place INTEGER NOT NULL DEFAULT 0,
            has_vtb_offer INTEGER NOT NULL DEFAULT 0,
            has_free_examination INTEGER NOT NULL DEFAULT 0,
            has_installments INTEGER NOT NULL DEFAULT 0,
            has_guarantee INTEGER NOT NULL DEFAULT 0,
            has_wifi INTEGER NOT NULL DEFAULT 0,
            has_ramp INTEGER NOT NULL DEFAULT 0,
            has_disabled_parking INTEGER NOT NULL DEFAULT 0,
            raw_features_json TEXT NOT NULL DEFAULT '',
            raw_categories_json TEXT NOT NULL DEFAULT '',
            dom_visibleText TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE organization_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id TEXT NOT NULL,
            feature_source TEXT NOT NULL DEFAULT '',
            feature_id TEXT NOT NULL DEFAULT '',
            feature_name TEXT NOT NULL DEFAULT '',
            feature_type TEXT NOT NULL DEFAULT '',
            value_id TEXT NOT NULL DEFAULT '',
            value_name TEXT NOT NULL DEFAULT '',
            value_text TEXT NOT NULL DEFAULT '',
            value_bool INTEGER,
            important INTEGER,
            value_index INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE organization_categories (
            org_id TEXT NOT NULL,
            category_index INTEGER NOT NULL DEFAULT 0,
            category_id TEXT NOT NULL DEFAULT '',
            category_name TEXT NOT NULL DEFAULT '',
            category_class TEXT NOT NULL DEFAULT '',
            category_seoname TEXT NOT NULL DEFAULT '',
            category_plural_name TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (org_id, category_index, category_id, category_name)
        );

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX idx_organizations_title ON organizations(title);
        CREATE INDEX idx_organizations_address ON organizations(fullAddress);
        CREATE INDEX idx_organizations_category ON organizations(categories_0_name);
        CREATE INDEX idx_organizations_permalink ON organizations(permalink);
        CREATE INDEX idx_organizations_query ON organizations(source_query);
        CREATE INDEX idx_organizations_coordinates ON organizations(lon, lat);
        CREATE INDEX idx_organization_cards_title ON organization_cards(title);
        CREATE INDEX idx_organization_cards_address ON organization_cards(fullAddress);
        CREATE INDEX idx_organization_cards_category ON organization_cards(category);
        CREATE INDEX idx_organization_cards_query ON organization_cards(source_query);
        CREATE INDEX idx_organization_cards_coordinates ON organization_cards(lon, lat);
        CREATE INDEX idx_organization_features_org_id ON organization_features(org_id);
        CREATE INDEX idx_organization_features_feature_id ON organization_features(feature_id);
        CREATE INDEX idx_organization_features_value_id ON organization_features(value_id);
        CREATE INDEX idx_organization_categories_org_id ON organization_categories(org_id);
        """
    )


def load_csv_into_db(conn: sqlite3.Connection, source_path: Path) -> dict:
    stats = {
        "source_rows": 0,
        "valid_coordinate_rows": 0,
        "missing_columns": [],
        "unique_rows": 0,
        "source_kind": "legacy",
        "enriched_card_rows": 0,
        "feature_rows": 0,
        "category_rows": 0,
    }

    insert_sql = _insert_sql()
    insert_card_sql = _insert_card_sql()
    insert_feature_sql = _insert_feature_sql()
    insert_category_sql = _insert_category_sql()
    with Path(source_path).open("r", encoding=CSV_ENCODING, newline="") as file:
        reader = csv.DictReader(file, delimiter=CSV_DELIMITER)
        fieldnames = set(reader.fieldnames or [])
        is_enriched = _is_enriched_source(fieldnames)
        stats["source_kind"] = "enriched" if is_enriched else "legacy"
        stats["missing_columns"] = [
            column for column in ORGANIZATION_COLUMNS if not _has_legacy_column(fieldnames, column)
        ]

        for row in reader:
            stats["source_rows"] += 1
            record = csv_row_to_db_record(row)
            if record["has_valid_coordinates"]:
                stats["valid_coordinate_rows"] += 1
            conn.execute(insert_sql, [record[column] for column in DB_COLUMNS])

            if is_enriched:
                card_record, feature_rows, category_rows = enriched_csv_row_to_card_record(row, record)
                conn.execute(insert_card_sql, [card_record[column] for column in CARD_COLUMNS])
                conn.executemany(
                    insert_feature_sql,
                    ([feature[column] for column in FEATURE_COLUMNS] for feature in feature_rows),
                )
                conn.executemany(
                    insert_category_sql,
                    ([category[column] for column in CATEGORY_COLUMNS] for category in category_rows),
                )
                stats["feature_rows"] += len(feature_rows)
                stats["category_rows"] += len(category_rows)

    stats["unique_rows"] = conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
    stats["enriched_card_rows"] = conn.execute("SELECT COUNT(*) FROM organization_cards").fetchone()[0]
    return stats


def csv_row_to_db_record(row: dict[str, str]) -> dict:
    cleaned = {column: _legacy_csv_value(row, column) for column in ORGANIZATION_COLUMNS}
    lon = _parse_float(cleaned.get("coordinates_0"))
    lat = _parse_float(cleaned.get("coordinates_1"))
    has_valid_coordinates = _valid_coordinates(lon, lat)

    record = {
        "id": _stable_id(
            cleaned.get("permalink", ""),
            cleaned.get("title", ""),
            cleaned.get("fullAddress", ""),
            lon,
            lat,
        ),
        **cleaned,
        "lon": lon if has_valid_coordinates else None,
        "lat": lat if has_valid_coordinates else None,
        "has_valid_coordinates": 1 if has_valid_coordinates else 0,
        "raw_json": json.dumps({str(key): _clean(value) for key, value in row.items()}, ensure_ascii=False),
    }
    return record


def enriched_csv_row_to_card_record(
    row: dict[str, str],
    legacy_record: dict,
) -> tuple[dict, list[dict], list[dict]]:
    features = _parse_json_array(row.get("raw_features_json"))
    categories = _parse_json_array(row.get("raw_categories_json"))
    org_id = str(legacy_record["id"])
    snippet_price_text, snippet_offer_text = _split_offer_text(row.get("offer_text", ""))

    services = _feature_items(features, "dentist_services")
    payment_methods = _feature_items(features, "payment_method")
    medical_specialists = _feature_items(features, "medical_specialists")
    uni_medic_specializations = _feature_items(features, "uni_medic_specialization")
    pediatric_specialists = _feature_items(features, "pediatric_specialists")
    accessibility = _accessibility_items(features)
    promotion_types = _feature_items(features, "promotions")
    category_items = _category_items(categories)

    card = {
        "id": org_id,
        "yandex_id": _clean(row.get("yandex_id")) or org_id,
        "permalink": _clean(row.get("permalink")) or str(legacy_record["permalink"]),
        "org_url": _clean(row.get("org_url")),
        "title": str(legacy_record["title"]),
        "fullAddress": str(legacy_record["fullAddress"]),
        "category": str(legacy_record["categories_0_name"]),
        "categories_json": _json_dumps(category_items),
        "phones_0_number": str(legacy_record["phones_0_number"]),
        "website_url": _clean(row.get("website_url")),
        "coordinates_0": str(legacy_record["coordinates_0"]),
        "coordinates_1": str(legacy_record["coordinates_1"]),
        "lon": legacy_record["lon"],
        "lat": legacy_record["lat"],
        "has_valid_coordinates": legacy_record["has_valid_coordinates"],
        "rating_value": _parse_float(row.get("rating_value") or legacy_record["ratingData_ratingValue"]),
        "rating_value_raw": _clean(row.get("rating_value")) or str(legacy_record["ratingData_ratingValue"]),
        "rating_count": _parse_int(row.get("rating_count") or legacy_record["ratingData_ratingCount"]),
        "rating_count_raw": _clean(row.get("rating_count")) or str(legacy_record["ratingData_ratingCount"]),
        "review_count": _parse_int(row.get("review_count")),
        "captured_at": _clean(row.get("captured_at")),
        "source_query": str(legacy_record["source_query"]),
        "source_bbox": str(legacy_record["source_bbox"]),
        "cell_url": _clean(row.get("cell_url")),
        "search_result_index": _clean(row.get("search_result_index")),
        "open_status_text": _clean(row.get("open_status_text")),
        "awards_text": _clean(row.get("awards_text")),
        "business_verified_owner": 1 if _parse_bool(row.get("business_verified_owner")) else 0,
        "services_json": _json_dumps(services),
        "services_text": _item_names_text(services),
        "payment_methods_json": _json_dumps(payment_methods),
        "payment_methods_text": _item_names_text(payment_methods),
        "medical_specialists_json": _json_dumps(medical_specialists),
        "medical_specialists_text": _item_names_text(medical_specialists),
        "uni_medic_specializations_json": _json_dumps(uni_medic_specializations),
        "uni_medic_specializations_text": _item_names_text(uni_medic_specializations),
        "pediatric_specialists_json": _json_dumps(pediatric_specialists),
        "pediatric_specialists_text": _item_names_text(pediatric_specialists),
        "accessibility_json": _json_dumps(accessibility),
        "accessibility_text": _item_names_text(accessibility),
        "promotion_types_json": _json_dumps(promotion_types),
        "promotion_types_text": _item_names_text(promotion_types),
        "cashback_percent": _feature_text(features, "pay_all_category_offer"),
        "snippet_price_text": snippet_price_text,
        "snippet_offer_text": snippet_offer_text,
        "has_for_children": 1 if _feature_bool(features, "for_children") else 0,
        "has_good_place": 1 if _feature_bool(features, "good_place") else 0,
        "has_vtb_offer": 1 if _feature_bool(features, "vtb_has_offer") else 0,
        "has_free_examination": 1 if _feature_bool(features, "free examination") else 0,
        "has_installments": 1 if _feature_bool(features, "dental treatment in installments") else 0,
        "has_guarantee": 1 if _feature_bool(features, "guarantee") else 0,
        "has_wifi": 1 if _feature_bool(features, "wi_fi") else 0,
        "has_ramp": 1 if _feature_bool(features, "ramp") else 0,
        "has_disabled_parking": 1 if _feature_bool(features, "parking_disabled") else 0,
        "raw_features_json": _clean(row.get("raw_features_json")),
        "raw_categories_json": _clean(row.get("raw_categories_json")),
        "dom_visibleText": _clean(row.get("dom_visibleText")),
        "raw_json": legacy_record["raw_json"],
    }
    return card, _feature_rows(org_id, features), _category_rows(org_id, categories)


def write_metadata(conn: sqlite3.Connection, *, source_path: Path, stats: dict) -> None:
    source_stat = source_path.stat()
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_path": str(source_path),
        "source_size_bytes": str(source_stat.st_size),
        "source_modified_at": datetime.fromtimestamp(source_stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "source_kind": str(stats.get("source_kind", "legacy")),
        "source_rows": str(stats["source_rows"]),
        "valid_coordinate_rows": str(stats["valid_coordinate_rows"]),
        "unique_rows": str(stats["unique_rows"]),
        "enriched_card_rows": str(stats.get("enriched_card_rows", 0)),
        "feature_rows": str(stats.get("feature_rows", 0)),
        "category_rows": str(stats.get("category_rows", 0)),
        "missing_columns": json.dumps(stats["missing_columns"], ensure_ascii=False),
    }
    conn.executemany(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        sorted(metadata.items()),
    )


def read_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def filter_organizations(
    records: Iterable[Organization],
    *,
    q: str | None = None,
    category: str | None = None,
    bbox: str | None = None,
) -> list[Organization]:
    bbox_tuple = parse_bbox(bbox) if bbox else None
    q_norm = _clean(q).casefold()
    category_norm = _clean(category).casefold()

    filtered: list[Organization] = []
    for record in records:
        if q_norm and q_norm not in _search_text(record):
            continue
        if category_norm and category_norm not in record.category.casefold():
            continue
        if bbox_tuple and not _inside_bbox(record, bbox_tuple):
            continue
        filtered.append(record)

    return filtered


def filter_organization_cards(
    records: Iterable[OrganizationCard],
    *,
    q: str | None = None,
    category: str | None = None,
    bbox: str | None = None,
    service: str | None = None,
    payment: str | None = None,
    specialist: str | None = None,
) -> list[OrganizationCard]:
    bbox_tuple = parse_bbox(bbox) if bbox else None
    q_norm = _clean(q).casefold()
    category_norm = _clean(category).casefold()
    service_norm = _clean(service).casefold()
    payment_norm = _clean(payment).casefold()
    specialist_norm = _clean(specialist).casefold()

    filtered: list[OrganizationCard] = []
    for record in records:
        if not is_commercial_card(record):
            continue
        if q_norm and q_norm not in _card_search_text(record):
            continue
        if category_norm and not _matches_commercial_category(record, category_norm):
            continue
        if service_norm and service_norm not in _items_search_text(record.services):
            continue
        if payment_norm and payment_norm not in _items_search_text(record.payment_methods):
            continue
        if specialist_norm and specialist_norm not in _card_specialists_search_text(record):
            continue
        if bbox_tuple and not _inside_card_bbox(record, bbox_tuple):
            continue
        filtered.append(record)

    return filtered


def make_feature_collection(records: Iterable[Organization]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [record.to_geojson_feature() for record in records],
    }


def category_counts(records: Iterable[Organization]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = record.category or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())))


def card_category_counts(records: Iterable[OrganizationCard]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if not is_commercial_card(record):
            continue
        counts[COMMERCIAL_CATEGORY_NAME] = counts.get(COMMERCIAL_CATEGORY_NAME, 0) + 1
        for key in commercial_subcategories(record):
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())))


def commercial_card_to_api_dict(record: OrganizationCard) -> dict:
    data = record.to_api_dict()
    data["category"] = COMMERCIAL_CATEGORY_NAME
    data["categories"] = commercial_category_items(record)
    return data


def is_commercial_card(record: OrganizationCard) -> bool:
    primary_category = record.category.casefold()
    title = record.title.casefold()
    if any(marker in primary_category for marker in NON_COMMERCIAL_PRIMARY_CATEGORY_MARKERS):
        return False
    if any(marker in title for marker in NON_COMMERCIAL_TITLE_MARKERS):
        return False
    return bool(commercial_subcategories(record))


def commercial_subcategories(record: OrganizationCard) -> list[str]:
    allowed = {category.casefold(): category for category in COMMERCIAL_SUBCATEGORIES}
    matched: list[str] = []

    for category in [record.category, *(_clean(item.get("name")) for item in record.categories)]:
        normalized = _clean(category).casefold()
        if normalized in allowed and allowed[normalized] not in matched:
            matched.append(allowed[normalized])

    return matched


def commercial_category_items(record: OrganizationCard) -> list[dict[str, str]]:
    items = [
        {
            "id": COMMERCIAL_CATEGORY_ID,
            "name": COMMERCIAL_CATEGORY_NAME,
            "class": "",
            "seoname": "commercial-organizations",
            "pluralName": COMMERCIAL_CATEGORY_NAME,
        }
    ]
    for name in commercial_subcategories(record):
        items.append(
            {
                "id": _commercial_subcategory_id(name),
                "name": name,
                "class": "",
                "seoname": _commercial_subcategory_id(name),
                "pluralName": name,
            }
        )
    return items


def _matches_commercial_category(record: OrganizationCard, category_norm: str) -> bool:
    if category_norm in COMMERCIAL_CATEGORY_NAME.casefold():
        return True
    return any(category_norm in category.casefold() for category in commercial_subcategories(record))


def _commercial_subcategory_id(name: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", name.casefold()).strip("_")


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    normalized = value.replace("~", ",")
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) != 4:
        raise ValueError("bbox must contain lon_min,lat_min,lon_max,lat_max")

    try:
        lon1, lat1, lon2, lat2 = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("bbox contains a non-numeric value") from exc

    lon_min, lon_max = sorted((lon1, lon2))
    lat_min, lat_max = sorted((lat1, lat2))
    return lon_min, lat_min, lon_max, lat_max


def _insert_sql() -> str:
    columns = ", ".join(DB_COLUMNS)
    placeholders = ", ".join(["?"] * len(DB_COLUMNS))
    return f"INSERT OR REPLACE INTO organizations ({columns}) VALUES ({placeholders})"


def _insert_card_sql() -> str:
    columns = ", ".join(CARD_COLUMNS)
    placeholders = ", ".join(["?"] * len(CARD_COLUMNS))
    return f"INSERT OR REPLACE INTO organization_cards ({columns}) VALUES ({placeholders})"


def _insert_feature_sql() -> str:
    columns = ", ".join(FEATURE_COLUMNS)
    placeholders = ", ".join(["?"] * len(FEATURE_COLUMNS))
    return f"INSERT INTO organization_features ({columns}) VALUES ({placeholders})"


def _insert_category_sql() -> str:
    columns = ", ".join(CATEGORY_COLUMNS)
    placeholders = ", ".join(["?"] * len(CATEGORY_COLUMNS))
    return f"INSERT OR REPLACE INTO organization_categories ({columns}) VALUES ({placeholders})"


def _row_to_organization(row: sqlite3.Row) -> Organization:
    raw = _load_raw_json(row["raw_json"])
    return Organization(
        id=str(row["id"] or ""),
        title=str(row["title"] or ""),
        short_title=str(row["shortTitle"] or ""),
        full_address=str(row["fullAddress"] or ""),
        category=str(row["categories_0_name"] or ""),
        phone=str(row["phones_0_number"] or ""),
        lon=float(row["lon"]),
        lat=float(row["lat"]),
        permalink=str(row["permalink"] or ""),
        rating_count=_parse_int(row["ratingData_ratingCount"]),
        rating_count_raw=str(row["ratingData_ratingCount"] or ""),
        rating_value=_parse_float(row["ratingData_ratingValue"]),
        rating_value_raw=str(row["ratingData_ratingValue"] or ""),
        source_query=str(row["source_query"] or ""),
        source_bbox=str(row["source_bbox"] or ""),
        raw=raw,
    )


def _row_to_organization_card(row: sqlite3.Row) -> OrganizationCard:
    raw = _load_raw_json(row["raw_json"])
    return OrganizationCard(
        id=str(row["id"] or ""),
        yandex_id=str(row["yandex_id"] or ""),
        permalink=str(row["permalink"] or ""),
        org_url=str(row["org_url"] or ""),
        title=str(row["title"] or ""),
        full_address=str(row["fullAddress"] or ""),
        category=str(row["category"] or ""),
        categories=_load_json_list(row["categories_json"]),
        phone=str(row["phones_0_number"] or ""),
        website_url=str(row["website_url"] or ""),
        lon=float(row["lon"]),
        lat=float(row["lat"]),
        rating_value=_parse_float(row["rating_value"]),
        rating_value_raw=str(row["rating_value_raw"] or ""),
        rating_count=_parse_int(row["rating_count"]),
        rating_count_raw=str(row["rating_count_raw"] or ""),
        review_count=_parse_int(row["review_count"]),
        open_status_text=str(row["open_status_text"] or ""),
        awards_text=str(row["awards_text"] or ""),
        business_verified_owner=bool(row["business_verified_owner"]),
        services=_load_json_list(row["services_json"]),
        payment_methods=_load_json_list(row["payment_methods_json"]),
        medical_specialists=_load_json_list(row["medical_specialists_json"]),
        uni_medic_specializations=_load_json_list(row["uni_medic_specializations_json"]),
        pediatric_specialists=_load_json_list(row["pediatric_specialists_json"]),
        accessibility=_load_json_list(row["accessibility_json"]),
        promotion_types=_load_json_list(row["promotion_types_json"]),
        cashback_percent=str(row["cashback_percent"] or ""),
        snippet_price_text=str(row["snippet_price_text"] or ""),
        snippet_offer_text=str(row["snippet_offer_text"] or ""),
        has_for_children=bool(row["has_for_children"]),
        has_good_place=bool(row["has_good_place"]),
        has_vtb_offer=bool(row["has_vtb_offer"]),
        has_free_examination=bool(row["has_free_examination"]),
        has_installments=bool(row["has_installments"]),
        has_guarantee=bool(row["has_guarantee"]),
        has_wifi=bool(row["has_wifi"]),
        has_ramp=bool(row["has_ramp"]),
        has_disabled_parking=bool(row["has_disabled_parking"]),
        source_query=str(row["source_query"] or ""),
        source_bbox=str(row["source_bbox"] or ""),
        raw=raw,
    )


def _legacy_csv_value(row: dict[str, str], column: str) -> str:
    for candidate in _legacy_column_candidates(column):
        value = _clean(row.get(candidate))
        if value:
            return value
    return ""


def _legacy_column_candidates(column: str) -> tuple[str, ...]:
    aliases = {
        "shortTitle": ("shortTitle", "short_title", "title"),
        "ratingData_ratingCount": ("ratingData_ratingCount", "rating_count"),
        "ratingData_ratingValue": ("ratingData_ratingValue", "rating_value"),
    }
    return aliases.get(column, (column,))


def _has_legacy_column(fieldnames: set[str], column: str) -> bool:
    return any(candidate in fieldnames for candidate in _legacy_column_candidates(column))


def _is_enriched_source(fieldnames: set[str]) -> bool:
    return bool(ENRICHED_MARKER_COLUMNS.intersection(fieldnames))


def _parse_json_array(value: object | None) -> list:
    text = _clean(value)
    if not text or text in {"nan", "None", "null"}:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _feature_rows(org_id: str, features: list) -> list[dict]:
    rows: list[dict] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        feature_id = _clean(feature.get("id"))
        feature_name = _clean(feature.get("name"))
        feature_type = _clean(feature.get("type"))
        important = _bool_int(feature.get("important"))
        value = feature.get("value")
        if isinstance(value, list):
            if not value:
                rows.append(
                    _feature_row(
                        org_id,
                        feature_id,
                        feature_name,
                        feature_type,
                        important,
                        value_index=0,
                    )
                )
            for value_index, item in enumerate(value):
                if isinstance(item, dict):
                    rows.append(
                        _feature_row(
                            org_id,
                            feature_id,
                            feature_name,
                            feature_type,
                            important,
                            value_id=_clean(item.get("id")),
                            value_name=_clean(item.get("name")),
                            value_text=_json_dumps(item),
                            value_index=value_index,
                        )
                    )
                else:
                    rows.append(
                        _feature_row(
                            org_id,
                            feature_id,
                            feature_name,
                            feature_type,
                            important,
                            value_text=_clean(item),
                            value_index=value_index,
                        )
                    )
        elif isinstance(value, bool):
            rows.append(
                _feature_row(
                    org_id,
                    feature_id,
                    feature_name,
                    feature_type,
                    important,
                    value_name=feature_name if value else "",
                    value_bool=_bool_int(value),
                    value_index=0,
                )
            )
        else:
            rows.append(
                _feature_row(
                    org_id,
                    feature_id,
                    feature_name,
                    feature_type,
                    important,
                    value_text=_clean(value),
                    value_index=0,
                )
            )
    return rows


def _feature_row(
    org_id: str,
    feature_id: str,
    feature_name: str,
    feature_type: str,
    important: int | None,
    *,
    value_id: str = "",
    value_name: str = "",
    value_text: str = "",
    value_bool: int | None = None,
    value_index: int = 0,
) -> dict:
    return {
        "org_id": org_id,
        "feature_source": "raw_features_json",
        "feature_id": feature_id,
        "feature_name": feature_name,
        "feature_type": feature_type,
        "value_id": value_id,
        "value_name": value_name,
        "value_text": value_text,
        "value_bool": value_bool,
        "important": important,
        "value_index": value_index,
    }


def _category_rows(org_id: str, categories: list) -> list[dict]:
    rows = []
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            continue
        rows.append(
            {
                "org_id": org_id,
                "category_index": index,
                "category_id": _clean(category.get("id")),
                "category_name": _clean(category.get("name")),
                "category_class": _clean(category.get("class")),
                "category_seoname": _clean(category.get("seoname")),
                "category_plural_name": _clean(category.get("pluralName")),
            }
        )
    return rows


def _feature_items(features: list, feature_id: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for feature in features:
        if not isinstance(feature, dict) or _clean(feature.get("id")) != feature_id:
            continue
        value = feature.get("value")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _append_item(items, _clean(item.get("id")), _clean(item.get("name")))
                else:
                    text = _clean(item)
                    _append_item(items, text, text)
        elif isinstance(value, bool):
            if value:
                _append_item(items, feature_id, _clean(feature.get("name")) or feature_id)
        else:
            text = _clean(value)
            if text:
                _append_item(items, feature_id, text)
    return items


def _accessibility_items(features: list) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for feature_id in ("wheelchair_access", "wheelchair_accessible_vocabulary"):
        for item in _feature_items(features, feature_id):
            _append_item(items, f"{feature_id}:{item['id']}", item["name"])
    for feature_id in ("ramp", "parking_disabled"):
        if _feature_bool(features, feature_id):
            feature = _find_feature(features, feature_id)
            _append_item(items, feature_id, _clean(feature.get("name")) or feature_id)
    return items


def _category_items(categories: list) -> list[dict[str, str]]:
    items = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        items.append(
            {
                "id": _clean(category.get("id")),
                "name": _clean(category.get("name")),
                "class": _clean(category.get("class")),
                "seoname": _clean(category.get("seoname")),
                "pluralName": _clean(category.get("pluralName")),
            }
        )
    return items


def _append_item(items: list[dict[str, str]], item_id: str, name: str) -> None:
    if not item_id and not name:
        return
    item = {"id": item_id or name, "name": name or item_id}
    if item not in items:
        items.append(item)


def _feature_bool(features: list, feature_id: str) -> bool:
    feature = _find_feature(features, feature_id)
    return _parse_bool(feature.get("value")) if feature else False


def _feature_text(features: list, feature_id: str) -> str:
    feature = _find_feature(features, feature_id)
    if not feature:
        return ""
    value = feature.get("value")
    if isinstance(value, list):
        return _item_names_text(_feature_items(features, feature_id))
    if isinstance(value, bool):
        return _clean(feature.get("name")) if value else ""
    return _clean(value)


def _find_feature(features: list, feature_id: str) -> dict:
    for feature in features:
        if isinstance(feature, dict) and _clean(feature.get("id")) == feature_id:
            return feature
    return {}


def _split_offer_text(value: object | None) -> tuple[str, str]:
    text = _clean(value)
    if not text:
        return "", ""
    parts = [part.strip() for part in text.split("|") if part.strip()]
    price = ""
    offer_parts = []
    for part in parts:
        if not price and re.search(r"\d[\d\s]*(?:[,.]\d+)?\s*\u20bd", part):
            price = part
            continue
        normalized = part.strip().casefold()
        if normalized in {"\u0430\u043a\u0446\u0438\u044f", "\u0430\u043a\u0446\u0438\u044f:"}:
            continue
        offer_parts.append(part)
    return price, " | ".join(offer_parts)


def _item_names_text(items: list[dict[str, str]]) -> str:
    names = []
    for item in items:
        name = _clean(item.get("name"))
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def _load_json_list(value: object | None) -> list[dict[str, str]]:
    text = _clean(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _load_raw_json(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): _clean(item) for key, item in parsed.items()}


def _stable_id(
    permalink: str,
    title: str,
    full_address: str,
    lon: float | None,
    lat: float | None,
) -> str:
    permalink = _clean(permalink)
    if re.fullmatch(r"\d+", permalink):
        return permalink

    lon_part = f"{lon:.8f}" if lon is not None else ""
    lat_part = f"{lat:.8f}" if lat is not None else ""
    value = f"{title}|{full_address}|{lon_part}|{lat_part}"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean(value: object | None) -> str:
    return str(value or "").strip()


def _parse_bool(value: object | None) -> bool:
    if isinstance(value, bool):
        return value
    text = _clean(value).casefold()
    return text in {"1", "true", "yes", "on", "y", "\u0434\u0430"}


def _bool_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    return 1 if _parse_bool(value) else 0


def _parse_float(value: object | None) -> float | None:
    text = _clean(value).replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: object | None) -> int | None:
    text = _clean(value)
    if not text:
        return None
    digits = re.sub(r"[^\d-]", "", text)
    if not digits or digits == "-":
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _valid_coordinates(lon: float | None, lat: float | None) -> bool:
    return lon is not None and lat is not None and -180 <= lon <= 180 and -90 <= lat <= 90


def _search_text(record: Organization) -> str:
    return " ".join(
        [
            record.title,
            record.short_title,
            record.full_address,
            record.category,
            record.phone,
            record.source_query,
        ]
    ).casefold()


def _card_search_text(record: OrganizationCard) -> str:
    return " ".join(
        [
            record.title,
            record.full_address,
            record.category,
            record.phone,
            record.website_url,
            record.source_query,
            _items_search_text(record.categories),
            _items_search_text(record.services),
            _items_search_text(record.payment_methods),
            _card_specialists_search_text(record),
            _items_search_text(record.promotion_types),
        ]
    ).casefold()


def _items_search_text(items: list[dict[str, str]]) -> str:
    return " ".join(
        part
        for item in items
        for part in (_clean(item.get("id")), _clean(item.get("name")))
        if part
    ).casefold()


def _card_specialists_search_text(record: OrganizationCard) -> str:
    return " ".join(
        [
            _items_search_text(record.medical_specialists),
            _items_search_text(record.uni_medic_specializations),
            _items_search_text(record.pediatric_specialists),
        ]
    ).casefold()


def _inside_bbox(record: Organization, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= record.lon <= lon_max and lat_min <= record.lat <= lat_max


def _inside_card_bbox(record: OrganizationCard, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= record.lon <= lon_max and lat_min <= record.lat <= lat_max
