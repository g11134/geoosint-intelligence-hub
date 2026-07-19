from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.api.organization_store import build_organizations_db
from yandex_scraper.config import CSV_FILE, ORGANIZATIONS_DB_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the organizations SQLite database for the API.")
    parser.add_argument("--source", default=str(CSV_FILE), help="Source CSV export path.")
    parser.add_argument("--db", default=str(ORGANIZATIONS_DB_FILE), help="Output SQLite database path.")
    args = parser.parse_args()

    stats = build_organizations_db(Path(args.source), Path(args.db))

    print("[OK] Organizations DB built")
    print(f"    source: {stats['source_path']}")
    print(f"    db: {stats['db_path']}")
    print(f"    source kind: {stats.get('source_kind', 'legacy')}")
    print(f"    source rows: {stats['source_rows']}")
    print(f"    unique rows: {stats['unique_rows']}")
    print(f"    valid coordinate rows: {stats['valid_coordinate_rows']}")
    print(f"    enriched cards: {stats.get('enriched_card_rows', 0)}")
    print(f"    feature rows: {stats.get('feature_rows', 0)}")
    print(f"    category rows: {stats.get('category_rows', 0)}")
    if stats["missing_columns"]:
        print(f"    missing columns: {', '.join(stats['missing_columns'])}")


if __name__ == "__main__":
    main()
