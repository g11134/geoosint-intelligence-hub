"""Backward-compatible exports for organization search extraction."""

from yandex_scraper.features.organizations_search.extraction import (
    build_full_address,
    extract_businesses_from_json,
)

__all__ = ["build_full_address", "extract_businesses_from_json"]
