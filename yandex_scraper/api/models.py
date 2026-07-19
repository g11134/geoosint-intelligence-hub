from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Organization:
    """Normalized organization record exposed by the API."""

    id: str
    title: str
    short_title: str
    full_address: str
    category: str
    phone: str
    lon: float
    lat: float
    permalink: str
    rating_count: int | None
    rating_count_raw: str
    rating_value: float | None
    rating_value_raw: str
    source_query: str
    source_bbox: str
    raw: dict[str, str]

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "fullAddress": self.full_address,
            "categories": self.category,
            "phones_0_number": self.phone,
            "ratingData_ratingValue": self._rating_display(),
            "ratingData_ratingCount": self._review_count(),
            "lat": self.lat,
            "lon": self.lon,
        }

    def to_geojson_feature(self) -> dict:
        properties = self.to_api_dict()
        properties.pop("lat", None)
        properties.pop("lon", None)
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.lon, self.lat],
            },
            "properties": properties,
        }

    def _rating_display(self) -> str:
        if self.rating_value is None:
            return ""
        return f"{self.rating_value:.2f}"

    def _review_count(self) -> int:
        return self.rating_count or 0


@dataclass(frozen=True)
class OrganizationCard:
    """Flutter-oriented enriched organization card."""

    id: str
    yandex_id: str
    permalink: str
    org_url: str
    title: str
    full_address: str
    category: str
    categories: list[dict[str, str]]
    phone: str
    website_url: str
    lon: float
    lat: float
    rating_value: float | None
    rating_value_raw: str
    rating_count: int | None
    rating_count_raw: str
    review_count: int | None
    open_status_text: str
    awards_text: str
    business_verified_owner: bool
    services: list[dict[str, str]]
    payment_methods: list[dict[str, str]]
    medical_specialists: list[dict[str, str]]
    uni_medic_specializations: list[dict[str, str]]
    pediatric_specialists: list[dict[str, str]]
    accessibility: list[dict[str, str]]
    promotion_types: list[dict[str, str]]
    cashback_percent: str
    snippet_price_text: str
    snippet_offer_text: str
    has_for_children: bool
    has_good_place: bool
    has_vtb_offer: bool
    has_free_examination: bool
    has_installments: bool
    has_guarantee: bool
    has_wifi: bool
    has_ramp: bool
    has_disabled_parking: bool
    source_query: str
    source_bbox: str
    raw: dict[str, Any]

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "yandexId": self.yandex_id,
            "permalink": self.permalink,
            "orgUrl": self.org_url,
            "title": self.title,
            "fullAddress": self.full_address,
            "category": self.category,
            "categories": self.categories,
            "phone": self.phone,
            "websiteUrl": self.website_url,
            "lat": self.lat,
            "lon": self.lon,
            "rating": {
                "value": self.rating_value,
                "valueRaw": self.rating_value_raw,
                "count": self.rating_count or 0,
                "countRaw": self.rating_count_raw,
                "reviewCount": self.review_count or 0,
            },
            "status": {
                "openStatusText": self.open_status_text,
                "awardsText": self.awards_text,
                "businessVerifiedOwner": self.business_verified_owner,
            },
            "features": {
                "services": self.services,
                "paymentMethods": self.payment_methods,
                "specialists": {
                    "medical": self.medical_specialists,
                    "unifiedMedical": self.uni_medic_specializations,
                    "pediatric": self.pediatric_specialists,
                },
                "accessibility": self.accessibility,
                "promotions": {
                    "types": self.promotion_types,
                    "cashbackPercent": self.cashback_percent,
                    "snippetPriceText": self.snippet_price_text,
                    "snippetOfferText": self.snippet_offer_text,
                    "hasGoodPlace": self.has_good_place,
                    "hasVtbOffer": self.has_vtb_offer,
                    "hasFreeExamination": self.has_free_examination,
                    "hasInstallments": self.has_installments,
                },
                "flags": {
                    "forChildren": self.has_for_children,
                    "guarantee": self.has_guarantee,
                    "wiFi": self.has_wifi,
                    "ramp": self.has_ramp,
                    "disabledParking": self.has_disabled_parking,
                },
            },
            "source": {
                "query": self.source_query,
                "bbox": self.source_bbox,
            },
        }
