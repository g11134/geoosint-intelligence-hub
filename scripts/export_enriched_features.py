from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.config import ENRICHED_CSV_FILE
from yandex_scraper.exporters.enriched_features_exporter import export_enriched_features


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export analytical feature tables from enriched_result.csv."
    )
    parser.add_argument(
        "--source",
        default=str(ENRICHED_CSV_FILE),
        help="Source enriched_result.csv path.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for generated feature CSV files. Defaults to source file directory.",
    )
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir) if args.output_dir else source.parent
    export_enriched_features(source, output_dir)


if __name__ == "__main__":
    main()
