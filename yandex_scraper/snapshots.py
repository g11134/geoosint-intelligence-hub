from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "data" / "runs"
SCHEMA_VERSION = "1"

INPUT_FILENAMES = (
    "spb_polygon.geojson",
    "spb_polygon.json",
)
CACHE_FILENAMES = (
    "water_mask_cache.geojson",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an isolated monthly category snapshot."
    )
    parser.add_argument(
        "--period",
        default=datetime.now().strftime("%Y-%m"),
        help="Snapshot period in YYYY-MM format. Default: current month.",
    )
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="Search query to collect. Can be repeated.",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="Folder slug for the snapshot. Default: derived from query.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help=f"Root folder for snapshots. Default: {DEFAULT_RUNS_ROOT}",
    )
    parser.add_argument(
        "--rebuild-queue",
        action="store_true",
        help="Regenerate parsing_queue.csv even if it already exists.",
    )
    parser.add_argument(
        "--refresh-input",
        action="store_true",
        help="Refresh copied polygon/cache input files in the run folder.",
    )
    parser.add_argument(
        "--skip-grid",
        action="store_true",
        help="Do not generate parsing_queue.csv.",
    )
    parser.add_argument(
        "--skip-scraper",
        action="store_true",
        help="Prepare the snapshot folder without running the browser scraper.",
    )
    parser.add_argument(
        "--skip-excel",
        action="store_true",
        help="Do not export XLSX/CSV after scraping.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Do not build organizations.db after scraping.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    queries = normalize_queries(args.query)
    period = normalize_period(args.period)
    slug = normalize_slug(args.slug or queries_slug(queries))
    run_dir = args.runs_root.resolve() / period / slug

    prepare_run_dir(run_dir, refresh_input=args.refresh_input)
    configure_runtime(run_dir, queries)

    write_manifest(
        run_dir=run_dir,
        period=period,
        slug=slug,
        queries=queries,
        status="running",
    )

    try:
        if not args.skip_grid:
            run_grid(rebuild_queue=args.rebuild_queue)

        if not args.skip_scraper:
            run_scraper()

        if not args.skip_excel and not args.skip_scraper:
            run_excel_export()

        if not args.skip_db and not args.skip_scraper:
            run_organizations_db_export()

    except Exception:
        write_manifest(
            run_dir=run_dir,
            period=period,
            slug=slug,
            queries=queries,
            status="failed",
        )
        raise

    final_status = "prepared" if args.skip_scraper else "completed"
    write_manifest(
        run_dir=run_dir,
        period=period,
        slug=slug,
        queries=queries,
        status=final_status,
    )
    print(f"[OK] Snapshot {final_status}: {run_dir}")


def normalize_queries(values: list[str]) -> list[str]:
    queries = [str(value).strip() for value in values if str(value).strip()]
    if not queries:
        raise SystemExit("[ERROR] At least one --query value is required.")
    return queries


def normalize_period(value: str) -> str:
    period = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise SystemExit("[ERROR] --period must use YYYY-MM format.")
    month = int(period[-2:])
    if month < 1 or month > 12:
        raise SystemExit("[ERROR] --period month must be between 01 and 12.")
    return period


def queries_slug(queries: list[str]) -> str:
    if len(queries) == 1:
        return queries[0]
    digest = hashlib.sha1(
        json.dumps(queries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    return f"multi-{len(queries)}-{digest}"


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^\w.-]+", "-", value.strip().casefold(), flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    if not slug:
        raise SystemExit("[ERROR] Snapshot slug is empty.")
    return slug


def prepare_run_dir(run_dir: Path, *, refresh_input: bool) -> None:
    for name in ("input", "state", "raw", "output", "cache", "logs", "tmp"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    copy_inputs(
        PROJECT_ROOT / "data" / "input",
        run_dir / "input",
        INPUT_FILENAMES,
        refresh=refresh_input,
        required=True,
    )
    copy_inputs(
        PROJECT_ROOT / "data" / "cache",
        run_dir / "cache",
        CACHE_FILENAMES,
        refresh=refresh_input,
        required=False,
    )


def copy_inputs(
    source_dir: Path,
    dest_dir: Path,
    filenames: tuple[str, ...],
    *,
    refresh: bool,
    required: bool,
) -> None:
    for filename in filenames:
        source = source_dir / filename
        dest = dest_dir / filename
        if not source.exists():
            if required:
                raise FileNotFoundError(f"Required input file not found: {source}")
            continue
        if dest.exists() and not refresh:
            continue
        shutil.copy2(source, dest)


def configure_runtime(run_dir: Path, queries: list[str]) -> None:
    os.environ["YANDEX_SCRAPER_DATA_DIR"] = str(run_dir)
    os.environ["YANDEX_SCRAPER_SEARCH_QUERIES"] = json.dumps(
        queries,
        ensure_ascii=False,
    )


def run_grid(*, rebuild_queue: bool) -> None:
    from yandex_scraper.config import QUEUE_FILE

    if QUEUE_FILE.exists() and not rebuild_queue:
        print(f"[SKIP] Queue already exists: {QUEUE_FILE}")
        return

    from yandex_scraper.pipeline.grid_generator import main as generate_grid

    generate_grid()


def run_scraper() -> None:
    from yandex_scraper.runner import main as run_parser

    asyncio.run(run_parser())


def run_excel_export() -> None:
    from yandex_scraper.exporters.excel_exporter import main as export_excel

    export_excel()


def run_organizations_db_export() -> None:
    from yandex_scraper.api.organization_store import build_organizations_db
    from yandex_scraper.config import CSV_FILE, ORGANIZATIONS_DB_FILE

    if not CSV_FILE.exists():
        print(f"[SKIP] CSV export not found, organizations DB was not built: {CSV_FILE}")
        return
    stats = build_organizations_db(CSV_FILE, ORGANIZATIONS_DB_FILE)
    print(f"[OK] Organizations DB built: {stats['db_path']}")


def write_manifest(
    *,
    run_dir: Path,
    period: str,
    slug: str,
    queries: list[str],
    status: str,
) -> None:
    manifest_path = run_dir / "manifest.json"
    existing = read_manifest(manifest_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created_at = existing.get("created_at") or now

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": f"{period}__{slug}",
        "period": period,
        "slug": slug,
        "queries": queries,
        "status": status,
        "created_at": created_at,
        "updated_at": now,
        "run_dir": str(run_dir),
        "environment": {
            "YANDEX_SCRAPER_DATA_DIR": str(run_dir),
            "YANDEX_SCRAPER_SEARCH_QUERIES": json.dumps(
                queries,
                ensure_ascii=False,
            ),
        },
        "artifacts": snapshot_artifacts(run_dir),
    }

    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def read_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            parsed = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def snapshot_artifacts(run_dir: Path) -> dict:
    paths = {
        "source_polygon_geojson": run_dir / "input" / "spb_polygon.geojson",
        "prepared_polygon_json": run_dir / "input" / "spb_polygon.json",
        "queue_csv": run_dir / "state" / "parsing_queue.csv",
        "seen_ids_db": run_dir / "state" / "seen_ids.db",
        "raw_jsonl": run_dir / "raw" / "raw_data.jsonl",
        "result_csv": run_dir / "output" / "result.csv",
        "excel_xlsx": run_dir / "output" / "raw_data.xlsx",
        "organizations_db": run_dir / "output" / "organizations.db",
        "grid_geojson": run_dir / "output" / "grid_visualization.geojson",
        "water_cache_geojson": run_dir / "cache" / "water_mask_cache.geojson",
        "grid_log": run_dir / "logs" / "grid_generator.log",
    }
    return {key: file_snapshot(path) for key, path in paths.items()}


def file_snapshot(path: Path) -> dict:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "modified_at": None,
        }
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime,
            timezone.utc,
        ).isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    main()
