from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.api.organization_store import OrganizationRepository, is_commercial_card
from yandex_scraper.api.review_ai import (
    ReviewAIProviderConfig,
    ReviewAIProviderError,
    ReviewAIResponseError,
)
from yandex_scraper.api.review_radius_ai import (
    ReviewRadiusAIError,
    build_radius_analysis_context,
    build_radius_cache_key,
    generate_lmstudio_radius_analysis,
    load_cached_radius_response,
    make_radius_ai_response,
    radius_cache_path_for_org,
    save_cached_radius_response,
)
from yandex_scraper.config import LMSTUDIO_BASE_URL, LMSTUDIO_MODEL, REVIEW_AI_TIMEOUT_SEC


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Precompute local LM Studio aggregate review AI reports for organizations inside a radius."
    )
    parser.add_argument(
        "--center-org-id",
        "--org-id",
        action="append",
        default=[],
        help="Center organization id. Can be passed more than once. Defaults to all commercial organizations.",
    )
    parser.add_argument(
        "--radius-m",
        type=int,
        default=3000,
        help="Radius in meters for selecting nearby organizations.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of center organizations to process after --center-org-id filtering.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Regenerate reports even when a fresh radius cache file already exists.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=float,
        default=0.0,
        help="Pause between generated LM Studio requests.",
    )
    parser.add_argument(
        "--max-reports",
        type=int,
        default=50,
        help="Maximum number of ready organization reports to send to LM Studio per radius report.",
    )
    parser.add_argument(
        "--max-report-chars",
        type=int,
        default=4000,
        help="Maximum number of characters from each ready organization report sent to LM Studio.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="LM Studio model override. Defaults to YANDEX_SCRAPER_LMSTUDIO_MODEL.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    _validate_args(parser, args)

    repository = OrganizationRepository()
    try:
        center_ids = _selected_center_ids(repository, args.center_org_id, args.limit)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not center_ids:
        print("[ERROR] No matching commercial organizations found.", file=sys.stderr)
        raise SystemExit(1)

    provider_config = ReviewAIProviderConfig(
        name="lmstudio",
        model=str(args.model or LMSTUDIO_MODEL).strip() or "local-model",
        timeout_sec=REVIEW_AI_TIMEOUT_SEC,
        base_url=LMSTUDIO_BASE_URL,
    )

    generated = 0
    skipped = 0
    empty = 0
    failed = 0

    print(f"[INFO] centers selected: {len(center_ids)}")
    print(f"[INFO] radius: {args.radius_m} m")
    print(f"[INFO] provider: {provider_config.name}, model: {provider_config.model}")

    for index, center_org_id in enumerate(center_ids, start=1):
        try:
            context = build_radius_analysis_context(
                repository,
                center_org_id=center_org_id,
                radius_m=args.radius_m,
                provider_config=provider_config,
                max_reports=args.max_reports,
            )
            cache_key = build_radius_cache_key(
                context,
                provider=provider_config.name,
                model=provider_config.model,
            )
        except ReviewRadiusAIError as exc:
            failed += 1
            print(f"[ERROR] {index}/{len(center_ids)} center={center_org_id}: {exc}", file=sys.stderr)
            continue
        except (FileNotFoundError, RuntimeError) as exc:
            failed += 1
            print(f"[ERROR] {index}/{len(center_ids)} center={center_org_id}: {exc}", file=sys.stderr)
            continue

        cached = None if args.refresh else load_cached_radius_response(center_org_id, args.radius_m, cache_key)
        if cached is not None and str(cached.get("analysisText") or "").strip():
            skipped += 1
            print(f"[SKIP] {index}/{len(center_ids)} center={center_org_id}: fresh cache")
            continue

        if not context.reports:
            empty += 1
            print(
                f"[SKIP] {index}/{len(center_ids)} center={center_org_id}: "
                "no ready organization reports inside radius"
            )
            continue

        try:
            analysis_text = generate_lmstudio_radius_analysis(
                context=context,
                provider_config=provider_config,
                max_report_chars=args.max_report_chars,
            )
        except (ReviewRadiusAIError, ReviewAIProviderError, ReviewAIResponseError) as exc:
            failed += 1
            print(f"[ERROR] {index}/{len(center_ids)} center={center_org_id}: {exc}", file=sys.stderr)
            continue

        response = make_radius_ai_response(
            context=context,
            provider=provider_config.name,
            model=provider_config.model,
            analysis_text=analysis_text,
            cached=False,
        )
        save_cached_radius_response(center_org_id, args.radius_m, cache_key, response)
        generated += 1
        print(f"[OK] {index}/{len(center_ids)} center={center_org_id}: {radius_cache_path_for_org(center_org_id, args.radius_m)}")

        if args.sleep_sec > 0 and index < len(center_ids):
            time.sleep(args.sleep_sec)

    print(f"[SUMMARY] generated={generated}, skipped={skipped}, empty={empty}, failed={failed}")
    if failed:
        raise SystemExit(1)


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.radius_m < 1:
        parser.error("--radius-m must be >= 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.sleep_sec < 0:
        parser.error("--sleep-sec must be >= 0")
    if args.max_reports < 1:
        parser.error("--max-reports must be >= 1")
    if args.max_report_chars < 200:
        parser.error("--max-report-chars must be >= 200")


def _selected_center_ids(
    repository: OrganizationRepository,
    requested_center_ids: list[str],
    limit: int | None,
) -> list[str]:
    cards = [card for card in repository.list_cards() if is_commercial_card(card)]
    all_ids = [card.id for card in cards]

    selected = all_ids
    if requested_center_ids:
        requested = [org_id.strip() for org_id in requested_center_ids if org_id.strip()]
        available = set(all_ids)
        selected = [org_id for org_id in requested if org_id in available]
        missing = [org_id for org_id in requested if org_id not in available]
        for org_id in missing:
            print(f"[WARN] center={org_id}: not found in commercial organizations", file=sys.stderr)

    if limit is not None:
        selected = selected[:limit]
    return selected


if __name__ == "__main__":
    main()
