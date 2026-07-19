from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yandex_scraper.config import (
    GEMINI_MODEL,
    GEMINI_TIMEOUT_SEC,
    LMSTUDIO_BASE_URL,
    LMSTUDIO_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OPENROUTER_MODEL,
    REVIEW_AI_CACHE_DIR,
    REVIEW_AI_MAX_REVIEW_TEXT_CHARS,
    REVIEW_AI_MAX_REVIEWS,
    REVIEW_AI_PROVIDER,
    REVIEW_AI_TIMEOUT_SEC,
)


PROMPT_VERSION = "review_ai_v4"
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"
LEGACY_ANALYSIS_KEYS = ("summary", "strengths", "weaknesses", "themes", "risks", "recommendations", "limitations")
STRUCTURED_ANALYSIS_KEYS = (
    "organization",
    "rating_summary",
    "staff_mentions",
    "important_problems",
    "frequent_complaints",
    "frequent_praise",
    "review_health",
    "critical_reviews",
    "rare_recent_critical_reviews",
    "owner_actions",
)
SUPPORTED_PROVIDERS = {"gemini", "openrouter", "ollama", "lmstudio"}


@dataclass(frozen=True)
class ReviewAIProviderConfig:
    name: str
    model: str
    timeout_sec: int
    base_url: str = ""


class ReviewAIError(RuntimeError):
    """Base error for review AI analysis failures."""


class ReviewAIConfigurationError(ReviewAIError):
    """Raised when the selected AI provider is not configured."""


class ReviewAIProviderError(ReviewAIError):
    """Raised when an AI provider returns a transport or provider error."""


class ReviewAIResponseError(ReviewAIError):
    """Raised when an AI provider returns malformed or incomplete analysis JSON."""


def review_ai_provider_config() -> ReviewAIProviderConfig:
    provider = REVIEW_AI_PROVIDER.strip().casefold()
    if provider not in SUPPORTED_PROVIDERS:
        raise ReviewAIConfigurationError(
            "Unsupported review AI provider. Use one of: gemini, openrouter, ollama, lmstudio"
        )

    if provider == "openrouter":
        return ReviewAIProviderConfig(name=provider, model=OPENROUTER_MODEL, timeout_sec=REVIEW_AI_TIMEOUT_SEC)
    if provider == "ollama":
        return ReviewAIProviderConfig(
            name=provider,
            model=OLLAMA_MODEL,
            timeout_sec=REVIEW_AI_TIMEOUT_SEC,
            base_url=OLLAMA_BASE_URL,
        )
    if provider == "lmstudio":
        return ReviewAIProviderConfig(
            name=provider,
            model=LMSTUDIO_MODEL,
            timeout_sec=REVIEW_AI_TIMEOUT_SEC,
            base_url=LMSTUDIO_BASE_URL,
        )
    return ReviewAIProviderConfig(name="gemini", model=GEMINI_MODEL, timeout_sec=GEMINI_TIMEOUT_SEC)


def gemini_model_name() -> str:
    """Backward-compatible helper for callers that still import the old name."""
    return GEMINI_MODEL


def build_cache_key(
    *,
    org_id: str,
    source_snapshot: dict[str, Any],
    model: str,
    reviews_hash: str,
    provider: str = "gemini",
) -> str:
    value = {
        "orgId": str(org_id),
        "sourcePath": source_snapshot.get("path"),
        "sourceSizeBytes": source_snapshot.get("sizeBytes"),
        "sourceModifiedAt": source_snapshot.get("modifiedAt"),
        "provider": provider,
        "model": model,
        "promptVersion": PROMPT_VERSION,
        "reviewsHash": reviews_hash,
    }
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cached_response(org_id: str, cache_key: str) -> dict[str, Any] | None:
    path = cache_path_for_org(org_id)
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
    if not str(response.get("analysisText") or "").strip():
        response["analysisText"] = format_review_analysis_text(response)
    return response


def save_cached_response(org_id: str, cache_key: str, response: dict[str, Any]) -> None:
    path = cache_path_for_org(org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(response)
    payload["cacheKey"] = cache_key
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def cache_path_for_org(org_id: str, cache_dir: Path = REVIEW_AI_CACHE_DIR) -> Path:
    safe_org_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(org_id or "").strip()).strip("._-")
    return Path(cache_dir) / f"{safe_org_id or 'unknown'}.json"


def make_review_ai_response(
    *,
    org_id: str,
    organization_title: str,
    model: str,
    reviews_count: int,
    used_reviews_count: int,
    rating_stats: dict[str, Any],
    analysis: dict[str, Any],
    cached: bool,
    provider: str = "gemini",
) -> dict[str, Any]:
    normalized_analysis = normalize_analysis(
        analysis,
        organization_title=organization_title,
        used_reviews_count=used_reviews_count,
        rating_stats=rating_stats,
    )
    response = {
        "organizationId": str(org_id),
        "organizationTitle": organization_title,
        "status": "ready",
        "cached": cached,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": {
            "name": provider,
            "model": model,
        },
        "source": {
            "reviewsCount": reviews_count,
            "usedReviewsCount": used_reviews_count,
        },
        "ratingStats": rating_stats,
        "analysis": normalized_analysis,
    }
    response["analysisText"] = format_review_analysis_text(response)
    return response


def format_review_analysis_text(response: dict[str, Any]) -> str:
    analysis = response.get("analysis")
    if isinstance(analysis, str):
        return analysis.strip()
    if not isinstance(analysis, dict):
        return ""

    if any(
        key in analysis
        for key in ("rating_summary", "staff_mentions", "important_problems", "review_health", "owner_actions")
    ):
        return _format_structured_review_analysis_text(analysis, response)

    parts: list[str] = []
    summary = str(analysis.get("summary") or "").strip()
    if summary:
        parts.append(summary)

    rating_stats = response.get("ratingStats")
    if isinstance(rating_stats, dict):
        average = rating_stats.get("average")
        rated_count = rating_stats.get("ratedCount")
        if average is not None and rated_count is not None:
            parts.append(f"Средняя оценка: {average} по {rated_count} отзывам.")

    section_labels = (
        ("strengths", "Сильные стороны"),
        ("weaknesses", "Слабые стороны"),
        ("themes", "Частые темы"),
        ("risks", "Риски"),
        ("recommendations", "Рекомендации"),
        ("limitations", "Ограничения анализа"),
    )
    for key, label in section_labels:
        items = _analysis_text_items(analysis.get(key))
        if items:
            parts.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))

    return "\n\n".join(parts).strip()


def _format_structured_review_analysis_text(analysis: dict[str, Any], response: dict[str, Any]) -> str:
    parts: list[str] = []
    organization = analysis.get("organization")
    if isinstance(organization, dict):
        title = str(organization.get("title") or "").strip()
        total = organization.get("total_reviews_analyzed")
        if title:
            parts.append(f"Организация: {title}")
        if total not in (None, ""):
            parts.append(f"Проанализировано отзывов: {total}")
    elif response.get("organizationTitle"):
        parts.append(f"Организация: {response.get('organizationTitle')}")

    rating_summary = analysis.get("rating_summary")
    if isinstance(rating_summary, dict):
        conclusion = str(rating_summary.get("short_conclusion") or "").strip()
        if conclusion:
            parts.append(f"Оценки:\n{conclusion}")

    staff_mentions = analysis.get("staff_mentions")
    if isinstance(staff_mentions, dict):
        staff_parts = []
        for key, label in (
            ("positive", "Положительно"),
            ("negative", "Отрицательно"),
            ("neutral", "Нейтрально"),
        ):
            items = _analysis_text_items(staff_mentions.get(key))
            if items:
                staff_parts.append(f"{label}: " + "; ".join(items))
        summary = str(staff_mentions.get("summary") or "").strip()
        if summary:
            staff_parts.append(summary)
        if staff_parts:
            parts.append("Персонал:\n" + "\n".join(staff_parts))

    important_problems = analysis.get("important_problems")
    if isinstance(important_problems, dict):
        problem_parts = []
        for key, label in (
            ("critical", "Критические"),
            ("moderate", "Средние"),
            ("minor", "Небольшие"),
        ):
            items = _analysis_text_items(important_problems.get(key))
            if items:
                problem_parts.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))
        summary = str(important_problems.get("summary") or "").strip()
        if summary:
            problem_parts.append(summary)
        if problem_parts:
            parts.append("Проблемы:\n" + "\n\n".join(problem_parts))

    for key, label in (
        ("frequent_complaints", "Частые жалобы"),
        ("frequent_praise", "Часто хвалят"),
    ):
        items = _analysis_text_items(analysis.get(key))
        if items:
            parts.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))

    review_health = analysis.get("review_health")
    if isinstance(review_health, dict):
        health_parts = []
        status = str(review_health.get("status") or "").strip()
        score = review_health.get("score_from_1_to_10")
        conclusion = str(review_health.get("short_conclusion") or "").strip()
        if status:
            health_parts.append(f"Статус: {status}")
        if score not in (None, ""):
            health_parts.append(f"Оценка здоровья отзывов: {score}/10")
        if conclusion:
            health_parts.append(conclusion)
        risks = _analysis_text_items(review_health.get("risks"))
        if risks:
            health_parts.append("Риски:\n" + "\n".join(f"- {item}" for item in risks))
        if health_parts:
            parts.append("Здоровье отзывов:\n" + "\n".join(health_parts))

    critical_reviews = _analysis_text_items(analysis.get("critical_reviews"))
    if critical_reviews:
        parts.append("Критические отзывы:\n" + "\n\n".join(f"- {item}" for item in critical_reviews))

    owner_actions = analysis.get("owner_actions")
    if isinstance(owner_actions, dict):
        action_parts = []
        for key, label in (
            ("urgent", "Срочно"),
            ("next", "Далее"),
            ("strategic", "Стратегически"),
        ):
            items = _analysis_text_items(owner_actions.get(key))
            if items:
                action_parts.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))
        if action_parts:
            parts.append("Действия владельца:\n" + "\n\n".join(action_parts))

    return "\n\n".join(parts).strip()


def _analysis_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    return [_stringify_item(item) for item in raw_items if _stringify_item(item)]


def prepare_reviews_for_analysis(
    anonymized_reviews: list[dict[str, Any]],
    *,
    max_reviews: int = REVIEW_AI_MAX_REVIEWS,
    max_text_chars: int = REVIEW_AI_MAX_REVIEW_TEXT_CHARS,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for review in anonymized_reviews[:max_reviews]:
        text = str(review.get("text") or "").strip()
        if not text:
            continue

        item = dict(review)
        if len(text) > max_text_chars:
            item["text"] = f"{text[:max_text_chars].rstrip()}..."
        else:
            item["text"] = text
        prepared.append(item)
    return prepared


def generate_review_analysis(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
    provider_config: ReviewAIProviderConfig | None = None,
) -> dict[str, Any]:
    config = provider_config or review_ai_provider_config()
    if config.name == "openrouter":
        return generate_openrouter_review_analysis(
            organization_title=organization_title,
            rating_stats=rating_stats,
            anonymized_reviews=anonymized_reviews,
            model=config.model,
            timeout_sec=config.timeout_sec,
        )
    if config.name == "ollama":
        return generate_ollama_review_analysis(
            organization_title=organization_title,
            rating_stats=rating_stats,
            anonymized_reviews=anonymized_reviews,
            model=config.model,
            base_url=config.base_url,
            timeout_sec=config.timeout_sec,
        )
    if config.name == "lmstudio":
        return generate_lmstudio_review_analysis(
            organization_title=organization_title,
            rating_stats=rating_stats,
            anonymized_reviews=anonymized_reviews,
            model=config.model,
            base_url=config.base_url,
            timeout_sec=config.timeout_sec,
        )
    return generate_gemini_review_analysis(
        organization_title=organization_title,
        rating_stats=rating_stats,
        anonymized_reviews=anonymized_reviews,
        model=config.model,
        timeout_sec=config.timeout_sec,
    )


def generate_gemini_review_analysis(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
    model: str = GEMINI_MODEL,
    timeout_sec: int = GEMINI_TIMEOUT_SEC,
) -> dict[str, Any]:
    api_key = _gemini_api_key()
    if not api_key:
        raise ReviewAIConfigurationError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured")
    if not anonymized_reviews:
        raise ReviewAIResponseError("No anonymized reviews available for analysis")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": _analysis_prompt(
                            organization_title=organization_title,
                            rating_stats=rating_stats,
                            anonymized_reviews=anonymized_reviews,
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": _analysis_response_schema(),
        },
    }

    response_json = _post_json(
        _gemini_generate_content_url(model, api_key),
        payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout_sec=timeout_sec,
        provider_label="Gemini",
    )
    analysis_text = _extract_gemini_candidate_text(response_json)
    return _parse_analysis_json(analysis_text, "Gemini")


def generate_openrouter_review_analysis(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
    model: str = OPENROUTER_MODEL,
    timeout_sec: int = REVIEW_AI_TIMEOUT_SEC,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ReviewAIConfigurationError("OPENROUTER_API_KEY is not configured")
    if not anonymized_reviews:
        raise ReviewAIResponseError("No anonymized reviews available for analysis")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON that matches the requested schema.",
            },
            {
                "role": "user",
                "content": _analysis_prompt(
                    organization_title=organization_title,
                    rating_stats=rating_stats,
                    anonymized_reviews=anonymized_reviews,
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    response_json = _post_json(
        f"{OPENROUTER_API_BASE_URL}/chat/completions",
        payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "http://127.0.0.1",
            "X-Title": "Yandex Parser Review AI",
        },
        timeout_sec=timeout_sec,
        provider_label="OpenRouter",
    )
    analysis_text = _extract_openai_chat_content(response_json, "OpenRouter")
    return _parse_analysis_json(analysis_text, "OpenRouter")


def generate_ollama_review_analysis(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
    model: str = OLLAMA_MODEL,
    base_url: str = OLLAMA_BASE_URL,
    timeout_sec: int = REVIEW_AI_TIMEOUT_SEC,
) -> dict[str, Any]:
    if not anonymized_reviews:
        raise ReviewAIResponseError("No anonymized reviews available for analysis")

    payload = {
        "model": model,
        "prompt": _analysis_prompt(
            organization_title=organization_title,
            rating_stats=rating_stats,
            anonymized_reviews=anonymized_reviews,
        ),
        "stream": False,
        "format": _analysis_response_schema(),
        "options": {
            "temperature": 0.2,
        },
    }

    response_json = _post_json(
        f"{base_url.rstrip('/')}/api/generate",
        payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout_sec=timeout_sec,
        provider_label="Ollama",
    )
    analysis_text = str(response_json.get("response") or "").strip()
    if not analysis_text:
        raise ReviewAIResponseError("Ollama response text is empty")
    return _parse_analysis_json(analysis_text, "Ollama")


def generate_lmstudio_review_analysis(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
    model: str = LMSTUDIO_MODEL,
    base_url: str = LMSTUDIO_BASE_URL,
    timeout_sec: int = REVIEW_AI_TIMEOUT_SEC,
) -> dict[str, Any]:
    if not anonymized_reviews:
        raise ReviewAIResponseError("No anonymized reviews available for analysis")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON that matches the requested schema.",
            },
            {
                "role": "user",
                "content": _analysis_prompt(
                    organization_title=organization_title,
                    rating_stats=rating_stats,
                    anonymized_reviews=anonymized_reviews,
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "review_analysis",
                "schema": _analysis_response_schema(),
            },
        },
    }

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        response_json = _post_json(
            url,
            payload,
            headers=headers,
            timeout_sec=timeout_sec,
            provider_label="LM Studio",
        )
    except ReviewAIProviderError as exc:
        if "response_format" not in str(exc).casefold():
            raise
        fallback_payload = dict(payload)
        fallback_payload.pop("response_format", None)
        response_json = _post_json(
            url,
            fallback_payload,
            headers=headers,
            timeout_sec=timeout_sec,
            provider_label="LM Studio",
        )
    analysis_text = _extract_openai_chat_content(response_json, "LM Studio")
    return _parse_analysis_json(analysis_text, "LM Studio")


def normalize_analysis(
    value: Any,
    *,
    organization_title: str = "",
    used_reviews_count: int | None = None,
    rating_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewAIResponseError("Review AI analysis must be a JSON object")

    if any(key in value for key in STRUCTURED_ANALYSIS_KEYS):
        return _normalize_structured_analysis(
            value,
            organization_title=organization_title,
            used_reviews_count=used_reviews_count,
            rating_stats=rating_stats,
        )

    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewAIResponseError("Review AI analysis is missing structured fields")

    normalized: dict[str, Any] = {"summary": summary.strip()}
    for key in LEGACY_ANALYSIS_KEYS:
        if key == "summary":
            continue
        items = value.get(key)
        if items is None:
            normalized[key] = []
            continue
        if not isinstance(items, list):
            items = [items]
        normalized[key] = [_stringify_item(item) for item in items if _stringify_item(item)]
    return normalized


def _normalize_structured_analysis(
    value: dict[str, Any],
    *,
    organization_title: str,
    used_reviews_count: int | None,
    rating_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    organization = _object_value(value.get("organization"))
    rating_summary = _object_value(value.get("rating_summary"))
    staff_mentions = _object_value(value.get("staff_mentions"))
    important_problems = _object_value(value.get("important_problems"))
    review_health = _object_value(value.get("review_health"))
    owner_actions = _object_value(value.get("owner_actions"))

    rating_distribution = _object_value(rating_summary.get("rating_distribution"))
    if not rating_distribution and isinstance(rating_stats, dict):
        rating_distribution = _object_value(rating_stats.get("distribution"))

    return {
        "organization": {
            "title": _text_value(organization.get("title")) or organization_title,
            "total_reviews_analyzed": _int_value(
                organization.get("total_reviews_analyzed"),
                default=used_reviews_count or 0,
            ),
        },
        "rating_summary": {
            "rating_distribution": rating_distribution,
            "short_conclusion": _text_value(rating_summary.get("short_conclusion")),
        },
        "staff_mentions": {
            "positive": _string_list(staff_mentions.get("positive")),
            "negative": _string_list(staff_mentions.get("negative")),
            "neutral": _string_list(staff_mentions.get("neutral")),
            "summary": _text_value(staff_mentions.get("summary")),
        },
        "important_problems": {
            "critical": _string_list(important_problems.get("critical")),
            "moderate": _string_list(important_problems.get("moderate")),
            "minor": _string_list(important_problems.get("minor")),
            "summary": _text_value(important_problems.get("summary")),
        },
        "frequent_complaints": _string_list(value.get("frequent_complaints")),
        "frequent_praise": _string_list(value.get("frequent_praise")),
        "review_health": {
            "status": _text_value(review_health.get("status")),
            "score_from_1_to_10": _int_value(review_health.get("score_from_1_to_10"), default=0),
            "strengths": _string_list(review_health.get("strengths")),
            "risks": _string_list(review_health.get("risks")),
            "short_conclusion": _text_value(review_health.get("short_conclusion")),
        },
        "critical_reviews": _critical_review_list(value.get("critical_reviews"), include_rare_fields=True),
        "rare_recent_critical_reviews": _critical_review_list(
            value.get("rare_recent_critical_reviews"),
            include_rare_fields=False,
        ),
        "owner_actions": {
            "urgent": _string_list(owner_actions.get("urgent")),
            "next": _string_list(owner_actions.get("next")),
            "strategic": _string_list(owner_actions.get("strategic")),
        },
    }


def _object_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [_stringify_item(item) for item in items if _stringify_item(item)]


def _int_value(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return default
    return parsed


def _nullable_rating(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _nullable_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "y", "да"}:
        return True
    if text in {"false", "0", "no", "n", "нет"}:
        return False
    return None


def _critical_review_list(value: Any, *, include_rare_fields: bool) -> list[dict[str, Any]]:
    if value is None:
        return []

    raw_items = value if isinstance(value, list) else [value]
    reviews: list[dict[str, Any]] = []
    for item in raw_items:
        source = _object_value(item)
        if not source:
            text = _text_value(item)
            if not text:
                continue
            source = {"text": text}

        review = {
            "date": _text_value(source.get("date")),
            "rating": _nullable_rating(source.get("rating")),
            "reason": _text_value(source.get("reason")),
            "text": _text_value(source.get("text")),
        }
        if include_rare_fields:
            review["is_recent_if_date_available"] = _nullable_bool(
                source.get("is_recent_if_date_available")
            )
            review["is_rare_issue"] = _nullable_bool(source.get("is_rare_issue"))
        reviews.append(review)
    return reviews


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_sec: int,
    provider_label: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "User-Agent": "yandex-scraper-review-ai/1.0",
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise ReviewAIProviderError(f"{provider_label} API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ReviewAIProviderError(f"Cannot reach {provider_label} API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ReviewAIProviderError(f"{provider_label} API request timed out after {timeout_sec}s") from exc

    try:
        response_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewAIResponseError(f"{provider_label} API returned non-JSON response") from exc
    if not isinstance(response_json, dict):
        raise ReviewAIResponseError(f"{provider_label} API returned non-object JSON")
    return response_json


def _parse_analysis_json(value: str, provider_label: str) -> dict[str, Any]:
    try:
        analysis = json.loads(_strip_json_fences(value))
    except json.JSONDecodeError as exc:
        raise ReviewAIResponseError(f"{provider_label} analysis is not valid JSON") from exc
    if not isinstance(analysis, dict):
        raise ReviewAIResponseError(f"{provider_label} analysis is not a JSON object")
    return analysis


def _strip_json_fences(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()


def _gemini_generate_content_url(model: str, api_key: str) -> str:
    safe_model = urllib.parse.quote(str(model).strip(), safe="")
    encoded_key = urllib.parse.quote(api_key, safe="")
    return f"{GEMINI_API_BASE_URL}/models/{safe_model}:generateContent?key={encoded_key}"


def _legacy_analysis_prompt(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
) -> str:
    reviews_json = json.dumps(anonymized_reviews, ensure_ascii=False, separators=(",", ":"))
    rating_stats_json = json.dumps(rating_stats, ensure_ascii=False, separators=(",", ":"))
    return (
        "Ты аналитик клиентского опыта медицинских организаций. "
        "Проанализируй только переданные обезличенные отзывы и оценки. "
        "Не придумывай факты, даты, имена врачей или события, которых нет в отзывах. "
        "Пиши по-русски, кратко и прикладно для владельца клиники.\n\n"
        f"Организация: {organization_title}\n"
        f"Локальная статистика оценок: {rating_stats_json}\n"
        f"Отзывы JSON: {reviews_json}\n\n"
        "Верни только JSON по схеме. Все массивы должны содержать короткие строки."
    )


def _legacy_analysis_response_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "strengths": string_array,
            "weaknesses": string_array,
            "themes": string_array,
            "risks": string_array,
            "recommendations": string_array,
            "limitations": string_array,
        },
        "required": list(LEGACY_ANALYSIS_KEYS),
        "additionalProperties": False,
    }


def _analysis_prompt(
    *,
    organization_title: str,
    rating_stats: dict[str, Any],
    anonymized_reviews: list[dict[str, Any]],
) -> str:
    reviews_json = json.dumps(anonymized_reviews, ensure_ascii=False, separators=(",", ":"))
    rating_stats_json = json.dumps(rating_stats, ensure_ascii=False, separators=(",", ":"))
    response_schema_example = """
{
  "organization": {
    "title": "",
    "total_reviews_analyzed": 0
  },
  "rating_summary": {
    "rating_distribution": {},
    "short_conclusion": ""
  },
  "staff_mentions": {
    "positive": [],
    "negative": [],
    "neutral": [],
    "summary": ""
  },
  "important_problems": {
    "critical": [],
    "moderate": [],
    "minor": [],
    "summary": ""
  },
  "frequent_complaints": [],
  "frequent_praise": [],
  "review_health": {
    "status": "",
    "score_from_1_to_10": 0,
    "strengths": [],
    "risks": [],
    "short_conclusion": ""
  },
  "critical_reviews": [
    {
      "date": "",
      "rating": null,
      "reason": "",
      "is_recent_if_date_available": null,
      "is_rare_issue": null,
      "text": ""
    }
  ],
  "rare_recent_critical_reviews": [
    {
      "date": "",
      "rating": null,
      "reason": "",
      "text": ""
    }
  ],
  "owner_actions": {
    "urgent": [],
    "next": [],
    "strategic": []
  }
}
""".strip()
    return (
        "Ты аналитик клиентского опыта медицинских организаций.\n\n"
        "Проанализируй только переданные обезличенные отзывы и оценки.\n"
        "Не придумывай факты, даты, имена врачей, должности, события или причины, которых нет в отзывах.\n"
        "Если данных недостаточно — так и укажи.\n"
        "Пиши по-русски, кратко и прикладно для владельца клиники.\n\n"
        f"Организация: {organization_title}\n"
        f"Локальная статистика оценок: {rating_stats_json}\n"
        f"Отзывы JSON: {reviews_json}\n\n"
        "Задача:\n"
        "Проанализируй отзывы по следующим критериям:\n\n"
        "1. Упоминания врачей и другого персонала\n"
        "- Какие врачи, администраторы, ассистенты или другой персонал упоминаются.\n"
        "- В каком тоне их упоминают: положительно, отрицательно или нейтрально.\n"
        "- Не добавляй имена, если их нет в отзывах.\n\n"
        "2. Важные проблемы\n"
        "- Найди проблемы, которые могут влиять на репутацию, повторные визиты и доверие пациентов.\n"
        "- Отдельно выдели критические проблемы: грубость, боль, некачественное лечение, "
        "навязывание услуг, ошибки, долгое ожидание, конфликт, плохая коммуникация, "
        "проблемы с записью, ценами или оплатой.\n\n"
        "3. На что жалуются часто\n"
        "- Выяви повторяющиеся жалобы.\n"
        "- Укажи только то, что реально встречается в отзывах.\n"
        "- Если жалоба единичная, не называй ее частой.\n\n"
        "4. Что хвалят часто\n"
        "- Выяви повторяющиеся положительные темы.\n"
        "- Например: врачи, качество лечения, внимательность, чистота, сервис, запись, "
        "цены, оборудование, атмосфера.\n\n"
        "5. Общее здоровье отзывов\n"
        "- Дай краткую оценку состояния отзывов организации.\n"
        "- Учитывай рейтинг, распределение оценок, частоту жалоб, тональность, "
        "наличие критических отзывов и повторяющиеся темы.\n"
        "- Не делай медицинских выводов — только клиентский опыт и репутация.\n\n"
        "6. Критические отзывы\n"
        "- Выведи все критические отзывы.\n"
        "- Особенно выдели критические отзывы, если они редкие и появились за последнее время.\n"
        "- Если в отзывах есть дата — используй ее.\n"
        "- Если даты нет — не придумывай дату.\n"
        "- Для каждого критического отзыва укажи оценку, дату при наличии, "
        "краткую суть проблемы и сам текст отзыва.\n"
        "- Не сокращай текст критического отзыва так, чтобы терялся смысл.\n\n"
        "Верни только валидный JSON по схеме ниже.\n"
        "Не добавляй пояснения вне JSON.\n"
        "Все массивы должны содержать короткие строки, кроме массива critical_reviews, "
        "где можно возвращать объекты с текстом отзыва.\n\n"
        f"Схема ответа:\n\n{response_schema_example}"
    )


def _analysis_response_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}
    nullable_number = {"anyOf": [{"type": "number"}, {"type": "null"}]}
    nullable_bool = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
    critical_review = {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
            "rating": nullable_number,
            "reason": {"type": "string"},
            "is_recent_if_date_available": nullable_bool,
            "is_rare_issue": nullable_bool,
            "text": {"type": "string"},
        },
        "required": [
            "date",
            "rating",
            "reason",
            "is_recent_if_date_available",
            "is_rare_issue",
            "text",
        ],
        "additionalProperties": False,
    }
    rare_recent_review = {
        "type": "object",
        "properties": {
            "date": {"type": "string"},
            "rating": nullable_number,
            "reason": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["date", "rating", "reason", "text"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "organization": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "total_reviews_analyzed": {"type": "integer"},
                },
                "required": ["title", "total_reviews_analyzed"],
                "additionalProperties": False,
            },
            "rating_summary": {
                "type": "object",
                "properties": {
                    "rating_distribution": {"type": "object"},
                    "short_conclusion": {"type": "string"},
                },
                "required": ["rating_distribution", "short_conclusion"],
                "additionalProperties": False,
            },
            "staff_mentions": {
                "type": "object",
                "properties": {
                    "positive": string_array,
                    "negative": string_array,
                    "neutral": string_array,
                    "summary": {"type": "string"},
                },
                "required": ["positive", "negative", "neutral", "summary"],
                "additionalProperties": False,
            },
            "important_problems": {
                "type": "object",
                "properties": {
                    "critical": string_array,
                    "moderate": string_array,
                    "minor": string_array,
                    "summary": {"type": "string"},
                },
                "required": ["critical", "moderate", "minor", "summary"],
                "additionalProperties": False,
            },
            "frequent_complaints": string_array,
            "frequent_praise": string_array,
            "review_health": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "score_from_1_to_10": {"type": "integer"},
                    "strengths": string_array,
                    "risks": string_array,
                    "short_conclusion": {"type": "string"},
                },
                "required": ["status", "score_from_1_to_10", "strengths", "risks", "short_conclusion"],
                "additionalProperties": False,
            },
            "critical_reviews": {"type": "array", "items": critical_review},
            "rare_recent_critical_reviews": {"type": "array", "items": rare_recent_review},
            "owner_actions": {
                "type": "object",
                "properties": {
                    "urgent": string_array,
                    "next": string_array,
                    "strategic": string_array,
                },
                "required": ["urgent", "next", "strategic"],
                "additionalProperties": False,
            },
        },
        "required": list(STRUCTURED_ANALYSIS_KEYS),
        "additionalProperties": False,
    }


def _extract_gemini_candidate_text(response_json: dict[str, Any]) -> str:
    candidates = response_json.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ReviewAIResponseError("Gemini response has no candidates")

    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ReviewAIResponseError("Gemini response candidate has no text parts")

    text_parts = [part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")]
    text = "".join(text_parts).strip()
    if not text:
        raise ReviewAIResponseError("Gemini response text is empty")
    return text


def _extract_openai_chat_content(response_json: dict[str, Any], provider_label: str) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ReviewAIResponseError(f"{provider_label} response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        content = "".join(parts)
    text = str(content or "").strip()
    if not text:
        raise ReviewAIResponseError(f"{provider_label} response content is empty")
    return text


def _stringify_item(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()
