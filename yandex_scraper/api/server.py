from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from yandex_scraper.api.organization_store import (
    OrganizationRepository,
    card_category_counts,
    category_counts,
    commercial_card_to_api_dict,
    filter_organization_cards,
    filter_organizations,
    is_commercial_card,
    make_feature_collection,
)
from yandex_scraper.api.review_ai import (
    ReviewAIConfigurationError,
    ReviewAIProviderError,
    ReviewAIResponseError,
    build_cache_key,
    format_review_analysis_text,
    generate_review_analysis,
    load_cached_response,
    make_review_ai_response,
    prepare_reviews_for_analysis,
    review_ai_provider_config,
    save_cached_response,
)
from yandex_scraper.api.review_dynamics_store import load_review_dynamics_for_card
from yandex_scraper.api.review_radius_ai import (
    ReviewRadiusAIError,
    build_radius_analysis_context,
    build_radius_cache_key,
    load_cached_radius_response,
)
from yandex_scraper.api.review_store import (
    ReviewSourceError,
    hash_anonymized_reviews,
    load_review_dataset,
    load_review_rows,
)
from yandex_scraper.config import REVIEWS_ANALYTICS_SOURCE_FILE


repository = OrganizationRepository()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Yandex Parser Organizations API",
        version="1.0.0",
        description="Read-only API over exported organization data.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        snapshot = _public_source_snapshot()
        if not snapshot["exists"]:
            return {
                "status": "missing",
                "ready": False,
                "source": snapshot,
                "count": 0,
            }

        try:
            count = len(repository.list())
        except Exception as exc:
            return {
                "status": "error",
                "ready": False,
                "source": snapshot,
                "error": str(exc),
                "count": 0,
            }

        return {
            "status": "ok",
            "ready": True,
            "source": snapshot,
            "count": count,
        }

    @app.get("/api/meta")
    def meta() -> dict:
        records = _records_or_503()
        return {
            "source": _public_source_snapshot(),
            "count": len(records),
            "categories": category_counts(records),
            "coordinateOrder": {
                "api": "lat/lon",
                "geojson": "[lon, lat]",
                "sourceColumns": {
                    "lon": "coordinates_0",
                    "lat": "coordinates_1",
                },
            },
        }

    @app.get("/api/organizations")
    def organizations(
        q: str | None = Query(default=None, description="Search in title, address, category, phone and source query."),
        category: str | None = Query(default=None, description="Case-insensitive category substring."),
        bbox: str | None = Query(default=None, description="lon_min,lat_min,lon_max,lat_max"),
        limit: int = Query(default=5000, ge=1, le=50000),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        filtered = _filtered_records(q=q, category=category, bbox=bbox)
        page = filtered[offset : offset + limit]
        return {
            "items": [record.to_api_dict() for record in page],
            "count": len(page),
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/organizations.geojson")
    def organizations_geojson(
        q: str | None = Query(default=None, description="Search in title, address, category, phone and source query."),
        category: str | None = Query(default=None, description="Case-insensitive category substring."),
        bbox: str | None = Query(default=None, description="lon_min,lat_min,lon_max,lat_max"),
        limit: int = Query(default=5000, ge=1, le=50000),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        filtered = _filtered_records(q=q, category=category, bbox=bbox)
        page = filtered[offset : offset + limit]
        return JSONResponse(
            content=make_feature_collection(page),
            media_type="application/geo+json",
        )

    @app.get("/api/v2/meta")
    def meta_v2() -> dict:
        records = _card_records_or_503()
        commercial_records = filter_organization_cards(records)
        return {
            "source": _public_source_snapshot(),
            "count": len(commercial_records),
            "categories": card_category_counts(records),
            "contract": {
                "recordShape": "one organization card per item",
                "featureLists": [
                    "services",
                    "paymentMethods",
                    "specialists.medical",
                    "specialists.unifiedMedical",
                    "specialists.pediatric",
                    "accessibility",
                    "promotions.types",
                ],
            },
            "coordinateOrder": {
                "api": "lat/lon",
                "sourceColumns": {
                    "lon": "coordinates_0",
                    "lat": "coordinates_1",
                },
            },
        }

    @app.get("/api/v2/organizations")
    def organizations_v2(
        q: str | None = Query(default=None, description="Search in card fields and enriched features."),
        category: str | None = Query(default=None, description="Case-insensitive category substring."),
        bbox: str | None = Query(default=None, description="lon_min,lat_min,lon_max,lat_max"),
        service: str | None = Query(default=None, description="Service id/name substring."),
        payment: str | None = Query(default=None, description="Payment method id/name substring."),
        specialist: str | None = Query(default=None, description="Specialist id/name substring."),
        limit: int = Query(default=5000, ge=1, le=50000),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        filtered = _filtered_card_records(
            q=q,
            category=category,
            bbox=bbox,
            service=service,
            payment=payment,
            specialist=specialist,
        )
        page = filtered[offset : offset + limit]
        return {
            "items": [commercial_card_to_api_dict(record) for record in page],
            "count": len(page),
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v2/organizations/{org_id}")
    def organization_v2(org_id: str) -> dict:
        record = _card_or_404(org_id)
        return commercial_card_to_api_dict(record)

    @app.get("/api/v2/organizations/{org_id}/reviews/ai-analysis")
    def review_ai_analysis_cached(org_id: str) -> dict:
        return _review_ai_analysis_cached_response(org_id)

    @app.get("/api/v2/organizations/{org_id}/reviews/ai-radius-analysis")
    def review_ai_radius_analysis_cached(
        org_id: str,
        radius_m: int = Query(default=3000, ge=1, le=50000),
    ) -> dict:
        return _review_ai_radius_analysis_cached_response(org_id, radius_m=radius_m)

    @app.get("/api/v2/organizations/{org_id}/reviews/dynamics", response_model=None)
    def review_dynamics(org_id: str) -> Any:
        record = _card_or_404(org_id)
        try:
            result = load_review_dynamics_for_card(record)
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "organizationId": record.id,
                    "organizationTitle": record.title,
                    "status": "missing",
                    "message": str(exc),
                },
            )

        if result is None:
            return JSONResponse(
                status_code=404,
                content={
                    "organizationId": record.id,
                    "organizationTitle": record.title,
                    "status": "missing",
                    "message": "Review dynamics not found for organization",
                },
            )

        data, source = result
        return {
            "organizationId": record.id,
            "organizationTitle": record.title,
            "data": data,
            "source": source,
        }

    @app.post("/api/v2/organizations/{org_id}/reviews/ai-analysis")
    def review_ai_analysis_generate(
        org_id: str,
        refresh: bool = Query(default=False, description="Ignore current cache and request the AI provider again."),
    ) -> dict:
        return _review_ai_analysis_response(org_id, refresh=refresh)

    @app.post("/api/v2/reviews/analyze")
    async def review_ai_analysis_compat(
        request: Request,
        refresh: bool = Query(default=False, description="Ignore current cache and request the AI provider again."),
    ) -> dict:
        payload = await _request_json_or_empty(request)
        org_id = _review_analysis_org_id(payload, dict(request.query_params))
        if not org_id:
            raise HTTPException(
                status_code=400,
                detail="Organization id is required. Send orgId, organizationId, id, yandexId or permalink.",
            )
        return _review_ai_analysis_compat_response(org_id, refresh=refresh)

    return app


def _public_source_snapshot() -> dict:
    """Return safe dataset metadata without exposing server or workstation paths."""
    snapshot = repository.source_snapshot()
    metadata = snapshot.get("metadata")
    public_metadata = {}
    if isinstance(metadata, dict):
        allowed_keys = {
            "built_at",
            "category_rows",
            "enriched_card_rows",
            "feature_rows",
            "schema_version",
            "source_kind",
            "source_rows",
            "unique_rows",
            "valid_coordinate_rows",
        }
        public_metadata = {
            key: value for key, value in metadata.items() if key in allowed_keys
        }

    return {
        "exists": bool(snapshot.get("exists")),
        "sizeBytes": snapshot.get("sizeBytes", 0),
        "modifiedAt": snapshot.get("modifiedAt"),
        "metadata": public_metadata,
    }


def _filtered_records(
    *,
    q: str | None,
    category: str | None,
    bbox: str | None,
) -> list:
    try:
        return filter_organizations(_records_or_503(), q=q, category=category, bbox=bbox)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _filtered_card_records(
    *,
    q: str | None,
    category: str | None,
    bbox: str | None,
    service: str | None,
    payment: str | None,
    specialist: str | None,
) -> list:
    try:
        return filter_organization_cards(
            _card_records_or_503(),
            q=q,
            category=category,
            bbox=bbox,
            service=service,
            payment=payment,
            specialist=specialist,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _records_or_503() -> list:
    try:
        return repository.list()
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _card_records_or_503() -> list:
    try:
        return repository.list_cards()
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _card_or_404(org_id: str):
    try:
        record = repository.get_card(org_id)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if record is None or not is_commercial_card(record):
        raise HTTPException(status_code=404, detail="Organization not found")
    return record


def _review_dataset_or_http(org_id: str):
    try:
        dataset = load_review_dataset(org_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReviewSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not dataset.reviews:
        raise HTTPException(status_code=404, detail="Reviews not found for organization")
    if not dataset.anonymized_reviews:
        raise HTTPException(status_code=404, detail="No non-empty review texts found for organization")
    return dataset


def _review_ai_analysis_cached_response(org_id: str) -> dict | JSONResponse:
    record = _card_or_404(org_id)
    dataset = _review_dataset_or_http(record.id)
    analysis_reviews = prepare_reviews_for_analysis(dataset.anonymized_reviews)
    provider_config = _review_ai_provider_config_or_http()
    cache_key = build_cache_key(
        org_id=record.id,
        source_snapshot=dataset.source_snapshot,
        provider=provider_config.name,
        model=provider_config.model,
        reviews_hash=hash_anonymized_reviews(analysis_reviews),
    )
    cached = load_cached_response(record.id, cache_key)
    if cached is not None and str(cached.get("analysisText") or "").strip():
        return cached

    return JSONResponse(
        status_code=404,
        content={
            "organizationId": record.id,
            "organizationTitle": record.title,
            "status": "missing",
            "message": "AI-отчет еще не сформирован. Запустите предварительную генерацию отчетов.",
        },
    )


def _review_ai_radius_analysis_cached_response(org_id: str, *, radius_m: int) -> dict | JSONResponse:
    record = _card_or_404(org_id)
    provider_config = _review_ai_provider_config_or_http()
    try:
        context = build_radius_analysis_context(
            repository,
            center_org_id=record.id,
            radius_m=radius_m,
            provider_config=provider_config,
        )
        cache_key = build_radius_cache_key(
            context,
            provider=provider_config.name,
            model=provider_config.model,
        )
        cached = load_cached_radius_response(record.id, radius_m, cache_key)
    except ReviewRadiusAIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FileNotFoundError, RuntimeError, ReviewSourceError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if cached is not None and str(cached.get("analysisText") or "").strip():
        return cached

    return JSONResponse(
        status_code=404,
        content={
            "centerOrganizationId": record.id,
            "centerOrganizationTitle": record.title,
            "radiusM": radius_m,
            "status": "missing",
            "message": "AI-отчет по радиусу еще не сформирован. Запустите предварительную генерацию отчетов по радиусу.",
        },
    )


def _review_ai_analysis_response(org_id: str, *, refresh: bool) -> dict:
    record = _card_or_404(org_id)
    dataset = _review_dataset_or_http(record.id)
    analysis_reviews = prepare_reviews_for_analysis(dataset.anonymized_reviews)
    provider_config = _review_ai_provider_config_or_http()
    cache_key = build_cache_key(
        org_id=record.id,
        source_snapshot=dataset.source_snapshot,
        provider=provider_config.name,
        model=provider_config.model,
        reviews_hash=hash_anonymized_reviews(analysis_reviews),
    )

    if not refresh:
        cached = load_cached_response(record.id, cache_key)
        if cached is not None:
            return cached

    try:
        analysis = generate_review_analysis(
            organization_title=record.title,
            rating_stats=dataset.rating_stats,
            anonymized_reviews=analysis_reviews,
            provider_config=provider_config,
        )
    except ReviewAIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ReviewAIProviderError, ReviewAIResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response = make_review_ai_response(
        org_id=record.id,
        organization_title=record.title,
        provider=provider_config.name,
        model=provider_config.model,
        reviews_count=len(dataset.reviews),
        used_reviews_count=len(analysis_reviews),
        rating_stats=dataset.rating_stats,
        analysis=analysis,
        cached=False,
    )
    save_cached_response(record.id, cache_key, response)
    return response


def _review_ai_analysis_compat_response(org_id: str, *, refresh: bool) -> dict:
    response = _review_ai_analysis_response(org_id, refresh=refresh)
    compat_response = dict(response)
    analysis = response.get("analysis")
    compat_response["analysis"] = str(response.get("analysisText") or format_review_analysis_text(response)).strip()
    if isinstance(analysis, dict):
        compat_response["analysisDetails"] = analysis
    return compat_response


async def _request_json_or_empty(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _review_analysis_org_id(payload: dict[str, Any], query_params: dict[str, Any]) -> str:
    for source in (query_params, payload):
        value = _first_org_id_candidate(source)
        if value:
            return value

    for key in ("organization", "org", "item", "card", "selectedOrganization"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _first_org_id_candidate(nested)
            if value:
                return value

    title = str(payload.get("title") or "").strip()
    if title:
        try:
            matches = [
                record
                for record in repository.list_cards()
                if record.title.casefold() == title.casefold()
            ]
        except (FileNotFoundError, RuntimeError):
            matches = []
        if len(matches) == 1:
            return matches[0].id

    return _single_review_source_org_id()


def _single_review_source_org_id() -> str:
    try:
        rows = load_review_rows(REVIEWS_ANALYTICS_SOURCE_FILE)
    except (FileNotFoundError, ReviewSourceError):
        return ""
    org_ids = sorted(
        {
            str(row.get("organization_id") or "").strip()
            for row in rows
            if str(row.get("organization_id") or "").strip()
        }
    )
    if len(org_ids) == 1:
        return org_ids[0]
    return ""


def _first_org_id_candidate(value: dict[str, Any]) -> str:
    for key in ("orgId", "org_id", "organizationId", "organization_id", "id", "yandexId", "yandex_id", "permalink"):
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _review_ai_provider_config_or_http():
    try:
        return review_ai_provider_config()
    except ReviewAIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _cors_origins() -> list[str]:
    raw = os.environ.get("YANDEX_SCRAPER_API_CORS_ORIGINS", "*")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["*"]


app = create_app()
