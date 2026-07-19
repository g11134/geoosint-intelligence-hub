from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from yandex_scraper.api.organization_store import DB_COLUMNS, build_organizations_db
from yandex_scraper.config import FINAL_COLUMNS
from yandex_scraper.exporters import csv_exporter
from yandex_scraper.exporters import organization_services_csv_exporter
from yandex_scraper.exporters import reviews_csv_exporter
from yandex_scraper.features.organizations_search.extraction import extract_businesses_from_json


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
UTF8_BOM = b"\xef\xbb\xbf"
NO_REVIEWS_TEXT = "\u043e\u0442\u0437\u044b\u0432\u043e\u0432 \u043d\u0435\u0442"
NO_RATING_TEXT = "\u0440\u0435\u0439\u0442\u0438\u043d\u0433\u0430 \u043d\u0435\u0442"


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def read_semicolon_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return list(reader.fieldnames or []), list(reader)


class RegressionContractsTest(unittest.TestCase):
    def test_extraction_normalizes_legacy_fields_and_deduplicates(self) -> None:
        payload = load_jsonl(FIXTURES_DIR / "extraction_payload.jsonl")[0]

        records = extract_businesses_from_json(payload)

        expected_keys = [
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
        self.assertEqual([record["permalink"] for record in records], ["10001", "10002"])
        self.assertEqual(list(records[0].keys()), expected_keys)
        self.assertEqual(records[0]["title"], "Alpha Clinic")
        self.assertEqual(records[0]["fullAddress"], "Saint Petersburg, Nevsky Prospect, 1")
        self.assertEqual(records[0]["coordinates_0"], "30.3158")
        self.assertEqual(records[0]["coordinates_1"], "59.9391")
        self.assertEqual(records[0]["categories_0_name"], "Dental clinic")
        self.assertEqual(records[0]["phones_0_number"], "+7 812 000-00-01")
        self.assertEqual(records[0]["ratingData_ratingCount"], "7")
        self.assertEqual(records[0]["ratingData_ratingValue"], "4.8")

    def test_main_csv_exporter_preserves_final_columns_and_dedup_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            raw_path = tmp_path / "raw_data.jsonl"
            output_path = tmp_path / "result.csv"
            raw_path.write_text(
                (FIXTURES_DIR / "raw_organizations.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            old_jsonl_file = csv_exporter.JSONL_FILE
            old_csv_file = csv_exporter.CSV_FILE
            try:
                csv_exporter.JSONL_FILE = raw_path
                csv_exporter.CSV_FILE = output_path
                with redirect_stdout(io.StringIO()):
                    csv_exporter.convert_to_csv()
            finally:
                csv_exporter.JSONL_FILE = old_jsonl_file
                csv_exporter.CSV_FILE = old_csv_file

            self.assertTrue(output_path.read_bytes().startswith(UTF8_BOM))
            fieldnames, rows = read_semicolon_csv(output_path)
            self.assertEqual(fieldnames, FINAL_COLUMNS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                [row["fullAddress"] for row in rows],
                ["Admiralteyskaya Embankment 1", "Nevsky Prospect 1"],
            )
            alpha = next(row for row in rows if row["title"] == "Alpha Clinic")
            beta = next(row for row in rows if row["title"] == "Beta Clinic")
            self.assertEqual(alpha["permalink"], "10001")
            self.assertEqual(beta["ratingData_ratingCount"], NO_REVIEWS_TEXT)
            self.assertEqual(beta["ratingData_ratingValue"], NO_RATING_TEXT)

    def test_build_organizations_db_preserves_legacy_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "organizations.db"

            stats = build_organizations_db(FIXTURES_DIR / "organizations.csv", db_path)

            self.assertEqual(stats["source_rows"], 2)
            self.assertEqual(stats["unique_rows"], 2)
            self.assertEqual(stats["valid_coordinate_rows"], 1)
            self.assertEqual(stats["missing_columns"], [])
            self.assertEqual(stats["source_kind"], "legacy")

            with closing(sqlite3.connect(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                columns = [row["name"] for row in conn.execute("PRAGMA table_info(organizations)")]
                self.assertEqual(columns, DB_COLUMNS)

                rows = conn.execute(
                    "SELECT id, title, lon, lat, has_valid_coordinates, raw_json "
                    "FROM organizations ORDER BY title"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["id"], "10001")
                self.assertEqual(rows[0]["has_valid_coordinates"], 1)
                self.assertAlmostEqual(rows[0]["lon"], 30.3158)
                self.assertAlmostEqual(rows[0]["lat"], 59.9391)
                self.assertEqual(json.loads(rows[0]["raw_json"])["source_query"], "dentistry")
                self.assertEqual(rows[1]["id"], "10002")
                self.assertEqual(rows[1]["has_valid_coordinates"], 0)
                self.assertIsNone(rows[1]["lon"])
                self.assertIsNone(rows[1]["lat"])

                metadata = dict(conn.execute("SELECT key, value FROM metadata"))
                self.assertEqual(metadata["schema_version"], "2")
                self.assertEqual(metadata["source_kind"], "legacy")
                self.assertEqual(metadata["source_rows"], "2")
                self.assertEqual(metadata["valid_coordinate_rows"], "1")

    def test_reviews_exporter_joins_organizations_and_radius_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "reviews.csv"

            summary = reviews_csv_exporter.export_reviews_csv(
                reviews_source=FIXTURES_DIR / "reviews.jsonl",
                organizations_source=FIXTURES_DIR / "organizations.csv",
                output_path=output_path,
                center_org_id="10001",
                radius_m=1,
            )

            self.assertEqual(summary.reviews_read, 2)
            self.assertEqual(summary.reviews_exported, 2)
            self.assertEqual(summary.organizations_read, 2)
            self.assertEqual(summary.reviews_without_organization_match, 1)
            self.assertEqual(summary.reviews_without_coordinates, 1)
            self.assertEqual(summary.within_radius_count, 1)
            self.assertTrue(output_path.read_bytes().startswith(UTF8_BOM))

            fieldnames, rows = read_semicolon_csv(output_path)
            self.assertEqual(fieldnames, reviews_csv_exporter.OUTPUT_COLUMNS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["organization_id"], "10001")
            self.assertEqual(rows[0]["organization_full_address"], "Nevsky Prospect 1")
            self.assertEqual(rows[0]["organization_category"], "Dental clinic")
            self.assertEqual(rows[0]["date"], "01.02.2026")
            self.assertEqual(rows[0]["parsed_date"], "2026-02-01")
            self.assertEqual(rows[0]["center_org_id"], "10001")
            self.assertEqual(rows[0]["distance_to_center_m"], "0.0")
            self.assertEqual(rows[0]["within_radius"], "true")
            self.assertEqual(rows[1]["organization_id"], "99999")
            self.assertEqual(rows[1]["organization_full_address"], "")
            self.assertEqual(rows[1]["within_radius"], "false")

    def test_services_exporter_flattens_services_and_keeps_empty_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "organization_services.csv"

            summary = organization_services_csv_exporter.export_organization_services_csv(
                source_path=FIXTURES_DIR / "organization_services.jsonl",
                output_path=output_path,
            )

            self.assertEqual(summary.records_read, 2)
            self.assertEqual(summary.invalid_lines, 1)
            self.assertEqual(summary.rows_exported, 3)
            self.assertEqual(summary.records_without_services, 1)
            self.assertTrue(output_path.read_bytes().startswith(UTF8_BOM))

            fieldnames, rows = read_semicolon_csv(output_path)
            self.assertEqual(fieldnames, organization_services_csv_exporter.OUTPUT_COLUMNS)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["organization_id"], "10001")
            self.assertEqual(rows[0]["service_index"], "1")
            self.assertEqual(rows[0]["service_category"], "Dentistry")
            self.assertEqual(rows[0]["service_name"], "Cleaning")
            self.assertEqual(rows[0]["service_price"], "1000 RUB")
            self.assertEqual(rows[1]["service_index"], "2")
            self.assertEqual(rows[1]["service_name"], "Whitening")
            self.assertEqual(rows[2]["organization_id"], "10002")
            self.assertEqual(rows[2]["service_index"], "1")
            self.assertEqual(rows[2]["service_name"], "")


if __name__ == "__main__":
    unittest.main()
