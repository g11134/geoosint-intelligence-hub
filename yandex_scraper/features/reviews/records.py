import re
from datetime import datetime, timezone

from yandex_scraper import config as scraper_config


ORG_ID_RE = re.compile(r"/org/(?:[^/]+/)?(\d+)")
REVIEWS_STORE_ORGANIZATION_REPLY_TEXT = getattr(
    scraper_config,
    "REVIEWS_STORE_ORGANIZATION_REPLY_TEXT",
    False,
)

REVIEW_MERGE_FIELDS = (
    "review_id",
    "author_name",
    "rating",
    "date",
    "text",
    "likes",
    "organization_reply_text",
    "organization_reply_date",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def first_text(*values) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_yandex_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return "https://yandex.ru" + text
    return text


def extract_org_id(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text.isdigit():
            return text
        match = ORG_ID_RE.search(text)
        if match:
            return match.group(1)
    return ""


def safe_name_part(value: str, default: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._-")[:80] or default


def clean_review_author(value: str | None) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    noise = {
        "\u043f\u043e\u0434\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f",
        "\u0435\u0449\u0435",
        "\u0435\u0449\u0451",
    }
    fallback = ""
    for line in lines:
        lower = line.lower()
        if lower in noise:
            continue
        if "\u0437\u043d\u0430\u0442\u043e\u043a" in lower:
            continue
        if "\u043e\u0442\u0437\u044b\u0432" in lower and re.search(r"\d", lower):
            continue
        if "\u043e\u0446\u0435\u043d" in lower and re.search(r"\d", lower):
            continue
        if "\u0444\u043e\u0442\u043e" in lower and re.search(r"\d", lower):
            continue
        if not fallback:
            fallback = line
        meaningful = re.sub(r"[^0-9A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u0451]+", "", line)
        if len(meaningful) <= 2:
            continue
        return line
    return fallback or lines[0]


def clean_match_text(value: str | None) -> str:
    text = str(value or "").lower().replace("\u2026", "")
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def clean_match_author(value: str | None) -> str:
    return clean_match_text(clean_review_author(value))


def is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "\u0434\u0430"}


def base_url_from_reviews_url(reviews_url: str) -> str:
    url = normalize_yandex_url(reviews_url).split("?", 1)[0].rstrip("/")
    if url.endswith("/reviews"):
        return url[: -len("/reviews")] + "/"
    return url + "/"


def make_reviews_url(row: dict) -> str:
    explicit = normalize_yandex_url(row.get("reviews_url") or "")
    if explicit:
        return explicit

    org_url = normalize_yandex_url(row.get("org_url") or "")
    org_id = extract_org_id(row.get("org_id"), row.get("yandex_id"), row.get("permalink"), org_url)
    if org_url and "/reviews" in org_url:
        return org_url
    if org_url and "/org/" in org_url:
        base = org_url.split("?", 1)[0].rstrip("/")
        return f"{base}/reviews/"
    if org_id:
        return f"https://yandex.ru/maps/org/{org_id}/reviews/"
    return ""


def make_org_url(row: dict) -> str:
    org_url = normalize_yandex_url(row.get("org_url") or "")
    if org_url:
        return org_url.split("?", 1)[0]

    org_id = extract_org_id(row.get("org_id"), row.get("yandex_id"), row.get("permalink"))
    reviews_url = normalize_yandex_url(row.get("reviews_url") or "")
    if org_id and "/maps/org/" in reviews_url:
        return base_url_from_reviews_url(reviews_url)
    if org_id and reviews_url:
        if "yandex.com" in reviews_url:
            return f"https://yandex.com/maps/org/{org_id}/"
        if "yandex.ru" in reviews_url:
            return f"https://yandex.ru/maps/org/{org_id}/"
    if org_id:
        return f"https://yandex.ru/maps/org/{org_id}/"
    return ""


def dedup_key(row: dict) -> str:
    org_id = first_text(row.get("org_id"), row.get("yandex_id"), row.get("permalink"))
    title = first_text(row.get("title"), row.get("shortTitle"))
    url = make_reviews_url(row)
    return org_id or url or title



def compact_review_record(review: dict, queue_row: dict, source: str) -> dict:
    org_id = first_text(queue_row.get("org_id"), extract_org_id(queue_row.get("reviews_url")))
    reply_text = first_text(review.get("organization_reply_text"), review.get("reply_text"))
    reply_date = first_text(review.get("organization_reply_date"), review.get("reply_date"))
    stored_reply_text = reply_text if REVIEWS_STORE_ORGANIZATION_REPLY_TEXT else ""
    return {
        "captured_at": utc_now(),
        "organization_id": org_id,
        "organization_title": first_text(queue_row.get("title")),
        "organization_url": first_text(queue_row.get("org_url")),
        "reviews_url": first_text(queue_row.get("reviews_url")),
        "review_id": first_text(review.get("review_id")),
        "author_name": clean_review_author(first_text(review.get("author_name"))),
        "rating": first_text(review.get("rating")),
        "date": first_text(review.get("date")),
        "text": first_text(review.get("text")),
        "likes": first_text(review.get("likes")),
        "has_organization_reply": bool(reply_text or reply_date or is_truthy(review.get("has_organization_reply"))),
        "organization_reply_text": stored_reply_text,
        "organization_reply_date": reply_date,
        "source": source,
    }


def review_key(record: dict) -> str:
    review_id = first_text(record.get("review_id"))
    if review_id:
        return review_id
    return "|".join(
        [
            first_text(record.get("organization_id")),
            first_text(record.get("author_name")),
            first_text(record.get("date")),
            first_text(record.get("text"))[:200],
        ]
    )


def reviews_look_same(left: dict, right: dict) -> bool:
    left_id = first_text(left.get("review_id"))
    right_id = first_text(right.get("review_id"))
    if left_id and right_id:
        return left_id == right_id

    left_text = clean_match_text(left.get("text"))
    right_text = clean_match_text(right.get("text"))
    if len(left_text) < 40 or len(right_text) < 40:
        return False

    left_rating = first_text(left.get("rating"))
    right_rating = first_text(right.get("rating"))
    if left_rating and right_rating and left_rating != right_rating:
        return False

    left_author = clean_match_author(left.get("author_name"))
    right_author = clean_match_author(right.get("author_name"))
    if left_author and right_author and left_author != right_author:
        return False

    left_prefix = left_text[:120]
    right_prefix = right_text[:120]
    if left_prefix == right_prefix:
        return True
    return left_prefix[:80] in right_text or right_prefix[:80] in left_text


def review_text_looks_truncated(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.endswith("...") or text.endswith("\u2026")


def should_replace_review_text(primary_text: str, secondary_text: str) -> bool:
    if not primary_text or not secondary_text:
        return False
    if len(secondary_text) <= len(primary_text):
        return False

    primary_clean = clean_match_text(primary_text)
    secondary_clean = clean_match_text(secondary_text)
    if len(secondary_clean) <= len(primary_clean):
        return False
    if len(primary_clean) < 40:
        return False

    primary_prefix = primary_clean[: min(160, len(primary_clean))]
    secondary_prefix = secondary_clean[: max(240, len(primary_prefix) + 80)]
    if primary_prefix and secondary_prefix.startswith(primary_prefix):
        return True
    if review_text_looks_truncated(primary_text) and primary_clean[:80] in secondary_clean:
        return True
    return False


def merge_review_records(primary: dict, secondary: dict) -> dict:
    for field in REVIEW_MERGE_FIELDS:
        if not first_text(primary.get(field)) and first_text(secondary.get(field)):
            primary[field] = secondary[field]

    primary_text = first_text(primary.get("text"))
    secondary_text = first_text(secondary.get("text"))
    if should_replace_review_text(primary_text, secondary_text):
        primary["text"] = secondary_text

    primary_has_reply = bool(primary.get("has_organization_reply"))
    secondary_has_reply = bool(secondary.get("has_organization_reply"))
    primary["has_organization_reply"] = primary_has_reply or secondary_has_reply

    primary_source = first_text(primary.get("source"))
    secondary_source = first_text(secondary.get("source"))
    if secondary_source and primary_source and secondary_source not in primary_source.split("+"):
        primary["source"] = f"{primary_source}+{secondary_source}"
    elif secondary_source and not primary_source:
        primary["source"] = secondary_source

    return primary


def store_review_record(
    reviews_by_key: dict[str, dict],
    record: dict,
    *,
    insert_if_new: bool = True,
) -> bool:
    key = review_key(record)
    if key and key in reviews_by_key:
        merge_review_records(reviews_by_key[key], record)
        return True

    matched_key = ""
    for existing_key, existing in reviews_by_key.items():
        if reviews_look_same(existing, record):
            matched_key = existing_key
            break

    if matched_key:
        existing = reviews_by_key[matched_key]
        merge_review_records(existing, record)
        if key and not first_text(existing.get("review_id")) and first_text(record.get("review_id")):
            reviews_by_key[key] = reviews_by_key.pop(matched_key)
        return True

    if key and insert_if_new:
        reviews_by_key[key] = record
        return True
    return False


def merge_review_record_by_position(
    reviews_by_key: dict[str, dict],
    position: int,
    record: dict,
) -> bool:
    keys = list(reviews_by_key.keys())
    if position < 0 or position >= len(keys):
        return False
    existing = reviews_by_key[keys[position]]
    record_has_reply_signal = bool(
        first_text(record.get("organization_reply_text"), record.get("organization_reply_date"))
        or is_truthy(record.get("has_organization_reply"))
    )
    if first_text(existing.get("date")) and not record_has_reply_signal:
        return False

    existing_rating = first_text(existing.get("rating"))
    record_rating = first_text(record.get("rating"))
    if existing_rating and record_rating and existing_rating != record_rating:
        return False

    merge_review_records(existing, record)
    return True
