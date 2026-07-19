from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yandex_scraper.api.models import OrganizationCard
from yandex_scraper.api.organization_store import OrganizationRepository, is_commercial_card
from yandex_scraper.api.review_ai import (
    ReviewAIProviderConfig,
    ReviewAIResponseError,
    _extract_openai_chat_content,
    _post_json,
    build_cache_key,
    load_cached_response,
    prepare_reviews_for_analysis,
)
from yandex_scraper.api.review_store import (
    ReviewSourceError,
    hash_anonymized_reviews,
    load_review_dataset,
)
from yandex_scraper.config import REVIEW_AI_RADIUS_CACHE_DIR


PROMPT_VERSION = "review_ai_radius_v1"
EARTH_RADIUS_M = 6_371_000.0
DEFAULT_MAX_REPORTS = 50
DEFAULT_MAX_REPORT_CHARS = 4000


@dataclass(frozen=True)
class RadiusReportContext:
    center: dict[str, Any]
    radius_m: int
    center_report: dict[str, Any] | None
    reports: list[dict[str, Any]]
    missing_reports: list[dict[str, Any]]
    repository_snapshot: dict[str, Any]


class ReviewRadiusAIError(RuntimeError):
    """Raised when a radius AI report cannot be prepared or generated."""


def radius_cache_path_for_org(
    center_org_id: str,
    radius_m: int,
    cache_dir: Path = REVIEW_AI_RADIUS_CACHE_DIR,
) -> Path:
    safe_org_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(center_org_id or "").strip()).strip("._-")
    safe_radius = max(1, int(radius_m))
    return Path(cache_dir) / f"{safe_org_id or 'unknown'}_{safe_radius}.json"


def build_radius_analysis_context(
    repository: OrganizationRepository,
    *,
    center_org_id: str,
    radius_m: int,
    provider_config: ReviewAIProviderConfig,
    max_reports: int = DEFAULT_MAX_REPORTS,
) -> RadiusReportContext:
    center = repository.get_card(center_org_id)
    if center is None or not is_commercial_card(center):
        raise ReviewRadiusAIError(f"Center organization not found: {center_org_id}")
    if radius_m <= 0:
        raise ReviewRadiusAIError("--radius-m must be greater than 0")
    if max_reports <= 0:
        raise ReviewRadiusAIError("--max-reports must be greater than 0")

    cards = [card for card in repository.list_cards() if is_commercial_card(card)]
    neighbors: list[tuple[float, OrganizationCard]] = []
    for card in cards:
        if card.id == center.id:
            continue
        distance_m = haversine_meters(center.lon, center.lat, card.lon, card.lat)
        if distance_m <= radius_m:
            neighbors.append((distance_m, card))

    neighbors.sort(key=lambda item: (item[0], item[1].title.casefold(), item[1].id))
    selected_neighbors = neighbors[:max_reports]

    reports: list[dict[str, Any]] = []
    missing_reports: list[dict[str, Any]] = []
    for distance_m, card in selected_neighbors:
        report = load_fresh_organization_report(card, provider_config)
        if report is None:
            missing_reports.append(
                {
                    "organizationId": card.id,
                    "organizationTitle": card.title,
                    "distanceM": round(distance_m, 1),
                }
            )
            continue
        reports.append(_report_item(card, report, distance_m))

    center_cached_report = load_fresh_organization_report(center, provider_config)
    center_report = _report_item(center, center_cached_report, 0.0) if center_cached_report is not None else None

    return RadiusReportContext(
        center={
            "organizationId": center.id,
            "organizationTitle": center.title,
            "lat": center.lat,
            "lon": center.lon,
            "ratingValue": center.rating_value,
            "reviewCount": center.review_count,
        },
        radius_m=int(radius_m),
        center_report=center_report,
        reports=reports,
        missing_reports=missing_reports,
        repository_snapshot=repository.source_snapshot(),
    )


def load_fresh_organization_report(
    card: OrganizationCard,
    provider_config: ReviewAIProviderConfig,
) -> dict[str, Any] | None:
    try:
        dataset = load_review_dataset(card.id)
    except (FileNotFoundError, ReviewSourceError):
        return None

    analysis_reviews = prepare_reviews_for_analysis(dataset.anonymized_reviews)
    if not analysis_reviews:
        return None

    cache_key = build_cache_key(
        org_id=card.id,
        source_snapshot=dataset.source_snapshot,
        provider=provider_config.name,
        model=provider_config.model,
        reviews_hash=hash_anonymized_reviews(analysis_reviews),
    )
    return load_cached_response(card.id, cache_key)


def build_radius_cache_key(
    context: RadiusReportContext,
    *,
    provider: str,
    model: str,
) -> str:
    signature = {
        "center": context.center,
        "radiusM": context.radius_m,
        "provider": provider,
        "model": model,
        "promptVersion": PROMPT_VERSION,
        "repositorySnapshot": {
            "path": context.repository_snapshot.get("path"),
            "sizeBytes": context.repository_snapshot.get("sizeBytes"),
            "modifiedAt": context.repository_snapshot.get("modifiedAt"),
            "metadata": context.repository_snapshot.get("metadata"),
        },
        "centerReport": _report_signature(context.center_report),
        "reports": [_report_signature(report) for report in context.reports],
        "missingReports": context.missing_reports,
    }
    payload = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cached_radius_response(center_org_id: str, radius_m: int, cache_key: str) -> dict[str, Any] | None:
    path = radius_cache_path_for_org(center_org_id, radius_m)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict) or cached.get("cacheKey") != cache_key:
        return None
    if cached.get("status") != "ready":
        return None
    response = dict(cached)
    response["cached"] = True
    response.pop("cacheKey", None)
    return response


def save_cached_radius_response(
    center_org_id: str,
    radius_m: int,
    cache_key: str,
    response: dict[str, Any],
) -> None:
    path = radius_cache_path_for_org(center_org_id, radius_m)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(response)
    payload["cacheKey"] = cache_key
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def make_radius_ai_response(
    *,
    context: RadiusReportContext,
    provider: str,
    model: str,
    analysis_text: str,
    cached: bool,
) -> dict[str, Any]:
    return {
        "centerOrganizationId": str(context.center["organizationId"]),
        "centerOrganizationTitle": str(context.center["organizationTitle"]),
        "radiusM": context.radius_m,
        "status": "ready",
        "cached": cached,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": {
            "name": provider,
            "model": model,
        },
        "source": {
            "organizationsInRadius": len(context.reports) + len(context.missing_reports),
            "usedReportsCount": len(context.reports),
            "missingReportsCount": len(context.missing_reports),
            "centerReportUsed": context.center_report is not None,
            "reports": [
                {
                    "organizationId": report["organizationId"],
                    "organizationTitle": report["organizationTitle"],
                    "distanceM": report["distanceM"],
                }
                for report in context.reports
            ],
            "missingReports": context.missing_reports,
        },
        "analysis": {
            "type": "radius_review_ai",
            "promptVersion": PROMPT_VERSION,
            "reportsAnalyzed": len(context.reports),
        },
        "analysisText": analysis_text.strip(),
    }


def generate_lmstudio_radius_analysis(
    *,
    context: RadiusReportContext,
    provider_config: ReviewAIProviderConfig,
    max_report_chars: int = DEFAULT_MAX_REPORT_CHARS,
) -> str:
    if not context.reports:
        raise ReviewRadiusAIError("No ready organization reports found inside the selected radius")
    if provider_config.name != "lmstudio":
        raise ReviewRadiusAIError("Radius AI reports are generated only through local LM Studio")

    payload = {
        "model": provider_config.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты аналитик клиентского опыта и локальной конкуренции. "
                    "Верни только готовый человекочитаемый отчет на русском языке."
                ),
            },
            {
                "role": "user",
                "content": _radius_prompt(context, max_report_chars=max_report_chars),
            },
        ],
        "temperature": 0.2,
    }
    response_json = _post_json(
        f"{provider_config.base_url.rstrip('/')}/chat/completions",
        payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout_sec=provider_config.timeout_sec,
        provider_label="LM Studio",
    )
    analysis_text = _extract_openai_chat_content(response_json, "LM Studio")
    if not analysis_text.strip():
        raise ReviewAIResponseError("LM Studio returned empty radius analysis")
    return analysis_text.strip()


def haversine_meters(left_lon: float, left_lat: float, right_lon: float, right_lat: float) -> float:
    left_lat_rad = math.radians(left_lat)
    right_lat_rad = math.radians(right_lat)
    delta_lat = math.radians(right_lat - left_lat)
    delta_lon = math.radians(right_lon - left_lon)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(left_lat_rad) * math.cos(right_lat_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(EARTH_RADIUS_M * c, 1)


def _report_item(card: OrganizationCard, report: dict[str, Any], distance_m: float) -> dict[str, Any]:
    analysis_text = str(report.get("analysisText") or "").strip()
    return {
        "organizationId": card.id,
        "organizationTitle": card.title,
        "distanceM": round(distance_m, 1),
        "ratingValue": card.rating_value,
        "reviewCount": card.review_count,
        "generatedAt": report.get("generatedAt"),
        "provider": report.get("provider"),
        "source": report.get("source"),
        "analysisText": analysis_text,
        "analysisTextHash": hashlib.sha256(analysis_text.encode("utf-8")).hexdigest(),
    }


def _report_signature(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "organizationId": report.get("organizationId"),
        "distanceM": report.get("distanceM"),
        "generatedAt": report.get("generatedAt"),
        "provider": report.get("provider"),
        "source": report.get("source"),
        "analysisTextHash": report.get("analysisTextHash"),
    }


def _radius_prompt(context: RadiusReportContext, *, max_report_chars: int) -> str:
    center_title = context.center.get("organizationTitle")
    center_report_text = ""
    if context.center_report is not None:
        center_report_text = _truncate(str(context.center_report.get("analysisText") or ""), max_report_chars)

    reports = [
        {
            "organizationId": report["organizationId"],
            "organizationTitle": report["organizationTitle"],
            "distanceM": report["distanceM"],
            "ratingValue": report.get("ratingValue"),
            "reviewCount": report.get("reviewCount"),
            "analysisText": _truncate(str(report.get("analysisText") or ""), max_report_chars),
        }
        for report in context.reports
    ]
    reports_json = json.dumps(reports, ensure_ascii=False, separators=(",", ":"))
    missing_json = json.dumps(context.missing_reports, ensure_ascii=False, separators=(",", ":"))

    return (
        f"Центральная организация: {center_title} ({context.center.get('organizationId')}).\n"
        f"Радиус анализа: {context.radius_m} м.\n"
        f"Готовый отчет центральной организации, если есть:\n{center_report_text or 'Нет готового отчета.'}\n\n"
        f"Готовые отчеты организаций в радиусе JSON:\n{reports_json}\n\n"
        f"Организации без готового отчета JSON:\n{missing_json}\n\n"
        "Сделай сводный отчет для владельца центральной организации. "
        "Используй только переданные готовые отчеты, не придумывай факты, врачей, даты и события. "
        "Обязательно выдели: общий фон отзывов в радиусе, повторяющиеся жалобы, сильные стороны конкурентов, "
        "риски для центральной организации, возможности отличиться, срочные действия и стратегические действия. "
        "Если отчетов мало или есть пропуски, явно укажи ограничение анализа."
    )


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."
