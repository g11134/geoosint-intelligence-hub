from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from yandex_scraper.api.review_ai import (
    ReviewAIConfigurationError,
    ReviewAIProviderConfig,
    ReviewAIProviderError,
    ReviewAIResponseError,
    build_cache_key,
    cache_path_for_org,
    generate_review_analysis,
    load_cached_response,
    make_review_ai_response,
    prepare_reviews_for_analysis,
    save_cached_response,
)
from yandex_scraper.api.review_store import (
    ReviewSourceError,
    anonymize_reviews,
    hash_anonymized_reviews,
    load_review_rows,
    rating_statistics,
    review_source_snapshot,
)
from yandex_scraper.config import (
    LMSTUDIO_BASE_URL,
    LMSTUDIO_MODEL,
    REVIEW_AI_MAX_REVIEW_TEXT_CHARS,
    REVIEW_AI_MAX_REVIEWS,
    REVIEW_AI_TIMEOUT_SEC,
    REVIEWS_ANALYTICS_SOURCE_FILE,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Precompute local LM Studio review AI reports from the reviews analytics CSV."
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=REVIEWS_ANALYTICS_SOURCE_FILE,
        help="Reviews CSV path. Defaults to YANDEX_SCRAPER_REVIEWS_ANALYTICS_SOURCE_FILE or data/output/reviews.csv.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of organizations to process after --org-id filtering.",
    )
    parser.add_argument(
        "--org-id",
        action="append",
        default=[],
        help="Process only this organization id. Can be passed more than once.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Regenerate reports even when a fresh cache file already exists.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=float,
        default=0.0,
        help="Pause between generated LM Studio requests.",
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=REVIEW_AI_MAX_REVIEWS,
        help="Maximum number of reviews sent to LM Studio per organization.",
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

    source_path = Path(args.source_file)
    try:
        rows = load_review_rows(source_path)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ReviewSourceError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    reviews_by_org = _group_reviews_by_org(rows)
    org_ids = _selected_org_ids(list(reviews_by_org), args.org_id, args.limit)
    if not org_ids:
        print("[ERROR] No matching organization_id values found in the reviews CSV.", file=sys.stderr)
        raise SystemExit(1)

    provider_config = ReviewAIProviderConfig(
        name="lmstudio",
        model=str(args.model or LMSTUDIO_MODEL).strip() or "local-model",
        timeout_sec=REVIEW_AI_TIMEOUT_SEC,
        base_url=LMSTUDIO_BASE_URL,
    )
    source_snapshot = review_source_snapshot(source_path)

    generated = 0
    skipped = 0
    upgraded = 0
    empty = 0
    failed = 0

    print(f"[INFO] source: {source_path}")
    print(f"[INFO] organizations selected: {len(org_ids)}")
    print(f"[INFO] provider: {provider_config.name}, model: {provider_config.model}")

    for index, org_id in enumerate(org_ids, start=1):
        reviews = reviews_by_org.get(org_id, [])
        organization_title = _organization_title(reviews, org_id)
        anonymized_reviews = anonymize_reviews(reviews)
        analysis_reviews = prepare_reviews_for_analysis(
            anonymized_reviews,
            max_reviews=args.max_reviews,
            max_text_chars=REVIEW_AI_MAX_REVIEW_TEXT_CHARS,
        )
        if not analysis_reviews:
            empty += 1
            print(f"[SKIP] {index}/{len(org_ids)} org={org_id}: no non-empty review texts")
            continue

        cache_key = build_cache_key(
            org_id=org_id,
            source_snapshot=source_snapshot,
            provider=provider_config.name,
            model=provider_config.model,
            reviews_hash=hash_anonymized_reviews(analysis_reviews),
        )

        cached = None if args.refresh else load_cached_response(org_id, cache_key)
        if cached is not None:
            if _cache_file_has_analysis_text(org_id, cache_key):
                skipped += 1
                print(f"[SKIP] {index}/{len(org_ids)} org={org_id}: fresh cache")
                continue

            payload = dict(cached)
            payload["cached"] = False
            save_cached_response(org_id, cache_key, payload)
            upgraded += 1
            print(f"[OK] {index}/{len(org_ids)} org={org_id}: upgraded cache with analysisText")
            continue

        try:
            analysis = generate_review_analysis(
                organization_title=organization_title,
                rating_stats=rating_statistics(reviews),
                anonymized_reviews=analysis_reviews,
                provider_config=provider_config,
            )
        except (ReviewAIConfigurationError, ReviewAIProviderError, ReviewAIResponseError) as exc:
            failed += 1
            print(f"[ERROR] {index}/{len(org_ids)} org={org_id}: {exc}", file=sys.stderr)
            continue

        response = make_review_ai_response(
            org_id=org_id,
            organization_title=organization_title,
            provider=provider_config.name,
            model=provider_config.model,
            reviews_count=len(reviews),
            used_reviews_count=len(analysis_reviews),
            rating_stats=rating_statistics(reviews),
            analysis=analysis,
            cached=False,
        )
        save_cached_response(org_id, cache_key, response)
        generated += 1
        print(f"[OK] {index}/{len(org_ids)} org={org_id}: {cache_path_for_org(org_id)}")

        if args.sleep_sec > 0 and index < len(org_ids):
            time.sleep(args.sleep_sec)

    print(
        "[SUMMARY] "
        f"generated={generated}, skipped={skipped}, upgraded={upgraded}, empty={empty}, failed={failed}"
    )
    if failed:
        raise SystemExit(1)


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.max_reviews < 1:
        parser.error("--max-reviews must be >= 1")
    if args.sleep_sec < 0:
        parser.error("--sleep-sec must be >= 0")


def _group_reviews_by_org(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        org_id = str(row.get("organization_id") or "").strip()
        if not org_id:
            continue
        grouped.setdefault(org_id, []).append(row)
    return grouped


def _selected_org_ids(all_org_ids: list[str], requested_org_ids: list[str], limit: int | None) -> list[str]:
    selected = all_org_ids
    if requested_org_ids:
        requested = [org_id.strip() for org_id in requested_org_ids if org_id.strip()]
        available = set(all_org_ids)
        selected = [org_id for org_id in requested if org_id in available]
        missing = [org_id for org_id in requested if org_id not in available]
        for org_id in missing:
            print(f"[WARN] org={org_id}: not found in reviews CSV", file=sys.stderr)

    if limit is not None:
        selected = selected[:limit]
    return selected


def _organization_title(reviews: list[dict[str, str]], org_id: str) -> str:
    for row in reviews:
        title = str(row.get("organization_title") or "").strip()
        if title:
            return title
    return org_id


def _cache_file_has_analysis_text(org_id: str, cache_key: str) -> bool:
    path = cache_path_for_org(org_id)
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload: Any = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("cacheKey") != cache_key:
        return False
    return bool(str(payload.get("analysisText") or "").strip())


if __name__ == "__main__":
    main()
