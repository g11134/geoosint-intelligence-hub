from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.exporters.organization_services_csv_exporter import (
    DEFAULT_OUTPUT_FILE,
    ORGANIZATION_SERVICES_JSONL_FILE,
    export_organization_services_csv,
    print_export_summary,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export organization_services.jsonl to flat CSV.")
    parser.add_argument(
        "--source",
        type=Path,
        default=ORGANIZATION_SERVICES_JSONL_FILE,
        help="Source organization services JSONL path. Defaults to data/raw/organization_services.jsonl.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Output CSV path. Defaults to data/output/organization_services.csv.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        summary = export_organization_services_csv(
            source_path=args.source,
            output_path=args.output,
        )
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print_export_summary(summary)


if __name__ == "__main__":
    main()
