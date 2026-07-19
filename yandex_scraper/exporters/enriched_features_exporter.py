from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from yandex_scraper.config import ENRICHED_CSV_FILE


LONG_COLUMNS = [
    "org_id",
    "yandex_id",
    "permalink",
    "org_url",
    "title",
    "fullAddress",
    "source_query",
    "source_bbox",
    "captured_at",
    "rating_value",
    "review_count",
    "feature_source",
    "feature_index",
    "value_index",
    "feature_id",
    "feature_name",
    "feature_type",
    "value_id",
    "value_name",
    "value_text",
    "value_bool",
    "value_class",
    "value_seoname",
    "value_plural_name",
    "important",
]

IDENTITY_COLUMNS = [
    "org_id",
    "yandex_id",
    "permalink",
    "org_url",
    "title",
    "fullAddress",
    "source_query",
    "source_bbox",
]

GROUP_OUTPUTS = {
    "services": ("features_services_wide.csv", "services_names"),
    "payment_methods": ("features_payment_methods_wide.csv", "payment_methods_names"),
    "specialists": ("features_specialists_wide.csv", "specialists_names"),
    "accessibility": ("features_accessibility_wide.csv", "accessibility_names"),
    "promotions": ("features_promotions_wide.csv", "promotions_names"),
    "categories": ("features_categories_wide.csv", "categories_names"),
}

SERVICE_FEATURE_IDS = {"dentist_services"}
PAYMENT_FEATURE_IDS = {
    "payment_method",
    "payment_by_credit_card",
    "dental treatment in installments",
}
SPECIALIST_FEATURE_IDS = {
    "medical_specialists",
    "uni_medic_specialization",
    "pediatric_specialists",
}
ACCESSIBILITY_FEATURE_IDS = {
    "wheelchair_access",
    "wheelchair_accessible_vocabulary",
    "parking_disabled",
    "ramp",
}
PROMOTION_FEATURE_IDS = {
    "promotions",
    "pay_all_category_offer",
    "good_place",
    "vtb_has_offer",
    "free examination",
    "dental treatment in installments",
}


def export_enriched_features(
    source_path: Path | str = ENRICHED_CSV_FILE,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    source = Path(source_path)
    destination = Path(output_dir) if output_dir is not None else source.parent

    if not source.exists() or source.stat().st_size == 0:
        print(f"[!] enriched_result.csv not found or empty: {source}")
        return {
            "source_path": str(source),
            "output_dir": str(destination),
            "source_rows": 0,
            "long_rows": 0,
            "outputs": {},
            "invalid_json": {},
        }

    rows = _read_csv(source)
    destination.mkdir(parents=True, exist_ok=True)

    long_rows: list[dict[str, Any]] = []
    org_rows: dict[str, dict[str, str]] = {}
    wide_groups = _new_wide_groups()
    invalid_json: dict[str, int] = defaultdict(int)

    for row_index, row in enumerate(rows, start=1):
        org = _org_identity(row)
        org_id = org["org_id"]
        org_rows.setdefault(org_id, org)

        features = _parse_json_array(
            row.get("raw_features_json", ""),
            "raw_features_json",
            invalid_json,
        )
        categories = _parse_json_array(
            row.get("raw_categories_json", ""),
            "raw_categories_json",
            invalid_json,
        )

        for feature_index, feature in enumerate(features):
            if not isinstance(feature, dict):
                continue
            long_rows.extend(_feature_to_long_rows(org, feature, feature_index))
            for group_name in _groups_for_feature(feature):
                _add_feature_to_wide(wide_groups[group_name], org_id, feature)

        for category_index, category in enumerate(categories):
            if not isinstance(category, dict):
                continue
            long_rows.append(_category_to_long_row(org, category, category_index))
            _add_category_to_wide(wide_groups["categories"], org_id, category)

        offer_text = _clean_text(row.get("offer_text", ""))
        if offer_text:
            long_rows.append(
                _text_field_to_long_row(
                    org,
                    feature_source="offer_text",
                    feature_id="offer_text",
                    feature_name="DOM offer text",
                    value_text=offer_text,
                )
            )
            _add_text_to_wide(
                wide_groups["promotions"],
                org_id,
                column="offer_text",
                value=offer_text,
                summary=offer_text,
            )

        dom_visible_text = _clean_text(row.get("dom_visibleText", ""))
        if dom_visible_text:
            long_rows.append(
                _text_field_to_long_row(
                    org,
                    feature_source="dom_visibleText",
                    feature_id="dom_visibleText",
                    feature_name="DOM visible text",
                    value_text=dom_visible_text,
                )
            )

    outputs: dict[str, int] = {}
    long_path = destination / "features_long.csv"
    _write_csv(long_path, LONG_COLUMNS, long_rows)
    outputs[str(long_path)] = len(long_rows)

    for group_name, (filename, summary_column) in GROUP_OUTPUTS.items():
        path = destination / filename
        group_rows = _wide_group_rows(
            org_rows=org_rows,
            group=wide_groups[group_name],
            summary_column=summary_column,
        )
        columns = _wide_columns(wide_groups[group_name], summary_column)
        _write_csv(path, columns, group_rows)
        outputs[str(path)] = len(group_rows)

    print(f"[OK] Source rows: {len(rows)}")
    print(f"[OK] Long feature rows: {len(long_rows)}")
    for path, count in outputs.items():
        print(f"[OK] {path} | rows: {count}")
    if invalid_json:
        print(f"[!] Invalid JSON cells: {dict(invalid_json)}")

    return {
        "source_path": str(source),
        "output_dir": str(destination),
        "source_rows": len(rows),
        "long_rows": len(long_rows),
        "outputs": outputs,
        "invalid_json": dict(invalid_json),
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        return [dict(row) for row in reader]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _org_identity(row: dict[str, str]) -> dict[str, str]:
    yandex_id = _clean_text(row.get("yandex_id", ""))
    permalink = _clean_text(row.get("permalink", ""))
    org_url = _clean_text(row.get("org_url", ""))
    title = _clean_text(row.get("title", ""))
    address = _clean_text(row.get("fullAddress", ""))
    org_id = yandex_id or permalink or org_url or f"{title}|{address}"
    return {
        "org_id": org_id,
        "yandex_id": yandex_id,
        "permalink": permalink,
        "org_url": org_url,
        "title": title,
        "fullAddress": address,
        "source_query": _clean_text(row.get("source_query", "")),
        "source_bbox": _clean_text(row.get("source_bbox", "")),
        "captured_at": _clean_text(row.get("captured_at", "")),
        "rating_value": _clean_text(row.get("rating_value", "")),
        "review_count": _clean_text(row.get("review_count", "")),
    }


def _parse_json_array(
    raw_value: str,
    column_name: str,
    invalid_json: dict[str, int],
) -> list[Any]:
    raw_value = _clean_text(raw_value)
    if not raw_value or raw_value in {"nan", "None", "null"}:
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        invalid_json[column_name] += 1
        return []
    if isinstance(parsed, list):
        return parsed
    invalid_json[column_name] += 1
    return []


def _feature_to_long_rows(
    org: dict[str, str],
    feature: dict[str, Any],
    feature_index: int,
) -> list[dict[str, Any]]:
    base = _long_base(
        org,
        feature_source="raw_features_json",
        feature_index=feature_index,
        feature_id=_clean_text(feature.get("id", "")),
        feature_name=_clean_text(feature.get("name", "")),
        feature_type=_clean_text(feature.get("type", "")),
        important=_bool_to_text(feature.get("important", "")),
    )
    value = feature.get("value")

    if isinstance(value, list):
        if not value:
            return [{**base, "value_index": 0}]
        rows = []
        for value_index, item in enumerate(value):
            if isinstance(item, dict):
                rows.append(
                    {
                        **base,
                        "value_index": value_index,
                        "value_id": _clean_text(item.get("id", "")),
                        "value_name": _clean_text(item.get("name", "")),
                        "value_text": _json_text(item),
                    }
                )
            else:
                rows.append(
                    {
                        **base,
                        "value_index": value_index,
                        "value_text": _scalar_to_text(item),
                    }
                )
        return rows

    row = {**base, "value_index": 0}
    if isinstance(value, bool):
        row["value_bool"] = _bool_to_text(value)
        if value and not row["value_name"]:
            row["value_name"] = row["feature_name"]
    else:
        row["value_text"] = _scalar_to_text(value)
    return [row]


def _category_to_long_row(
    org: dict[str, str],
    category: dict[str, Any],
    category_index: int,
) -> dict[str, Any]:
    return {
        **_long_base(
            org,
            feature_source="raw_categories_json",
            feature_index=category_index,
            feature_id="category",
            feature_name="category",
            feature_type="category",
            important="",
        ),
        "value_index": 0,
        "value_id": _clean_text(category.get("id", "")),
        "value_name": _clean_text(category.get("name", "")),
        "value_class": _clean_text(category.get("class", "")),
        "value_seoname": _clean_text(category.get("seoname", "")),
        "value_plural_name": _clean_text(category.get("pluralName", "")),
    }


def _text_field_to_long_row(
    org: dict[str, str],
    feature_source: str,
    feature_id: str,
    feature_name: str,
    value_text: str,
) -> dict[str, Any]:
    return {
        **_long_base(
            org,
            feature_source=feature_source,
            feature_index=0,
            feature_id=feature_id,
            feature_name=feature_name,
            feature_type="text",
            important="",
        ),
        "value_index": 0,
        "value_text": value_text,
    }


def _long_base(
    org: dict[str, str],
    feature_source: str,
    feature_index: int,
    feature_id: str,
    feature_name: str,
    feature_type: str,
    important: str,
) -> dict[str, Any]:
    return {
        "org_id": org["org_id"],
        "yandex_id": org["yandex_id"],
        "permalink": org["permalink"],
        "org_url": org["org_url"],
        "title": org["title"],
        "fullAddress": org["fullAddress"],
        "source_query": org["source_query"],
        "source_bbox": org["source_bbox"],
        "captured_at": org["captured_at"],
        "rating_value": org["rating_value"],
        "review_count": org["review_count"],
        "feature_source": feature_source,
        "feature_index": feature_index,
        "value_index": "",
        "feature_id": feature_id,
        "feature_name": feature_name,
        "feature_type": feature_type,
        "value_id": "",
        "value_name": "",
        "value_text": "",
        "value_bool": "",
        "value_class": "",
        "value_seoname": "",
        "value_plural_name": "",
        "important": important,
    }


def _new_wide_groups() -> dict[str, dict[str, Any]]:
    return {
        group_name: {
            "values": defaultdict(dict),
            "summary": defaultdict(list),
            "column_types": {},
        }
        for group_name in GROUP_OUTPUTS
    }


def _groups_for_feature(feature: dict[str, Any]) -> list[str]:
    feature_id = _clean_text(feature.get("id", ""))
    feature_name = _clean_text(feature.get("name", "")).lower()
    groups: list[str] = []

    if feature_id in SERVICE_FEATURE_IDS:
        groups.append("services")
    if feature_id in PAYMENT_FEATURE_IDS or feature_id.startswith("payment_"):
        groups.append("payment_methods")
    if feature_id in SPECIALIST_FEATURE_IDS:
        groups.append("specialists")
    if (
        feature_id in ACCESSIBILITY_FEATURE_IDS
        or "wheelchair" in feature_id
        or "инвалид" in feature_name
    ):
        groups.append("accessibility")
    if (
        feature_id in PROMOTION_FEATURE_IDS
        or "offer" in feature_id
        or "promotion" in feature_id
    ):
        groups.append("promotions")

    return groups


def _add_feature_to_wide(group: dict[str, Any], org_id: str, feature: dict[str, Any]) -> None:
    feature_id = _clean_text(feature.get("id", ""))
    feature_name = _clean_text(feature.get("name", "")) or feature_id
    value = feature.get("value")

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                value_id = _clean_text(item.get("id", "")) or _clean_text(item.get("name", ""))
                value_name = _clean_text(item.get("name", "")) or value_id
            else:
                value_id = _scalar_to_text(item)
                value_name = value_id
            if not value_id and not value_name:
                continue
            column = _wide_column_name(feature_id, value_id or value_name)
            group["values"][org_id][column] = "1"
            group["column_types"][column] = "indicator"
            _append_unique(group["summary"][org_id], value_name)
        return

    column = _safe_column_name(feature_id)
    if isinstance(value, bool):
        group["values"][org_id][column] = "1" if value else "0"
        group["column_types"][column] = "indicator"
        if value:
            _append_unique(group["summary"][org_id], feature_name)
        return

    value_text = _scalar_to_text(value)
    if value_text:
        group["values"][org_id][column] = value_text
        group["column_types"][column] = "text"
        _append_unique(group["summary"][org_id], f"{feature_name}: {value_text}")


def _add_category_to_wide(group: dict[str, Any], org_id: str, category: dict[str, Any]) -> None:
    category_id = _clean_text(category.get("seoname", "")) or _clean_text(category.get("id", ""))
    category_name = _clean_text(category.get("name", "")) or category_id
    if not category_id and not category_name:
        return
    column = _wide_column_name("category", category_id or category_name)
    group["values"][org_id][column] = "1"
    group["column_types"][column] = "indicator"
    _append_unique(group["summary"][org_id], category_name)


def _add_text_to_wide(
    group: dict[str, Any],
    org_id: str,
    column: str,
    value: str,
    summary: str,
) -> None:
    safe_column = _safe_column_name(column)
    group["values"][org_id][safe_column] = value
    group["column_types"][safe_column] = "text"
    _append_unique(group["summary"][org_id], summary)


def _wide_group_rows(
    org_rows: dict[str, dict[str, str]],
    group: dict[str, Any],
    summary_column: str,
) -> list[dict[str, Any]]:
    columns = sorted(group["column_types"])
    rows = []

    for org_id, org in org_rows.items():
        row = {column: org.get(column, "") for column in IDENTITY_COLUMNS}
        row[summary_column] = ", ".join(group["summary"].get(org_id, []))
        values = group["values"].get(org_id, {})
        for column in columns:
            if group["column_types"][column] == "indicator":
                row[column] = values.get(column, "0")
            else:
                row[column] = values.get(column, "")
        rows.append(row)

    return rows


def _wide_columns(group: dict[str, Any], summary_column: str) -> list[str]:
    return IDENTITY_COLUMNS + [summary_column] + sorted(group["column_types"])


def _wide_column_name(feature_id: str, value_id: str) -> str:
    return f"{_safe_column_name(feature_id)}__{_safe_column_name(value_id)}"


def _safe_column_name(value: str) -> str:
    value = _clean_text(value).lower()
    value = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def _append_unique(items: list[str], value: str) -> None:
    value = _clean_text(value)
    if value and value not in items:
        items.append(value)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _scalar_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return _bool_to_text(value)
    if isinstance(value, (dict, list)):
        return _json_text(value)
    return _clean_text(value)


def _bool_to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _clean_text(value)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
