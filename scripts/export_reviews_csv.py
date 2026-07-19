from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.exporters.reviews_csv_exporter import (
    DEFAULT_OUTPUT_FILE,
    DEFAULT_REVIEWS_SOURCE,
    export_reviews_csv,
    print_export_summary,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export reviews.jsonl to CSV and optionally calculate distance from a center clinic."
    )
    parser.add_argument(
        "--reviews-source",
        type=Path,
        default=DEFAULT_REVIEWS_SOURCE,
        help="Source reviews JSONL path. Defaults to data/raw/reviews.jsonl.",
    )
    parser.add_argument(
        "--organizations-source",
        type=Path,
        default=None,
        help="Organizations source CSV/JSONL. Defaults to enriched_result.csv, enriched_data.jsonl, then result.csv.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Output CSV path. Defaults to data/output/reviews.csv.",
    )
    parser.add_argument(
        "--center-org-id",
        default="",
        help="Organization id to use as the distance center.",
    )
    parser.add_argument(
        "--center-lon",
        type=float,
        default=None,
        help="Center longitude. Must be used together with --center-lat.",
    )
    parser.add_argument(
        "--center-lat",
        type=float,
        default=None,
        help="Center latitude. Must be used together with --center-lon.",
    )
    parser.add_argument(
        "--radius-m",
        type=float,
        default=None,
        help="Radius in meters for within_radius calculation.",
    )
    parser.add_argument(
        "--only-within-radius",
        action="store_true",
        help="Export only reviews whose organization is within --radius-m.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        summary = export_reviews_csv(
            reviews_source=args.reviews_source,
            organizations_source=args.organizations_source,
            output_path=args.output,
            center_org_id=args.center_org_id,
            center_lon=args.center_lon,
            center_lat=args.center_lat,
            radius_m=args.radius_m,
            only_within_radius=args.only_within_radius,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print_export_summary(summary)


if __name__ == "__main__":
    main()
