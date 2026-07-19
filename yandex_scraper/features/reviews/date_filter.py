import re
from dataclasses import dataclass
from datetime import date, datetime

from yandex_scraper.features.reviews.records import clean_review_author, first_text


DATE_NUMERIC_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?")

RU_MONTH_NAMES = [
    "\u044f\u043d\u0432\u0430\u0440\u044f",
    "\u0444\u0435\u0432\u0440\u0430\u043b\u044f",
    "\u043c\u0430\u0440\u0442\u0430",
    "\u0430\u043f\u0440\u0435\u043b\u044f",
    "\u043c\u0430\u044f",
    "\u0438\u044e\u043d\u044f",
    "\u0438\u044e\u043b\u044f",
    "\u0430\u0432\u0433\u0443\u0441\u0442\u0430",
    "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f",
    "\u043e\u043a\u0442\u044f\u0431\u0440\u044f",
    "\u043d\u043e\u044f\u0431\u0440\u044f",
    "\u0434\u0435\u043a\u0430\u0431\u0440\u044f",
]
RU_MONTHS = {month: index for index, month in enumerate(RU_MONTH_NAMES, start=1)}
DATE_RU_RE = re.compile(
    r"(\d{1,2})\s+("
    + "|".join(re.escape(month) for month in RU_MONTH_NAMES)
    + r")(?:\s+(\d{4}))?",
    re.IGNORECASE,
)


@dataclass
class DateFilterResult:
    reviews: list[dict]
    saw_too_old: bool = False
    missing_review_date: bool = False
    missing_review_date_count: int = 0



def parse_iso_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_review_date(value: str | None, *, default_year: int | None = None) -> date | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if default_year is None:
        default_year = datetime.now().year

    iso_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso_match:
        parsed = parse_iso_date(iso_match.group(0))
        if parsed:
            return parsed

    numeric_match = DATE_NUMERIC_RE.search(text)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        year_raw = numeric_match.group(3)
        year = int(year_raw) if year_raw else default_year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    ru_match = DATE_RU_RE.search(text)
    if ru_match:
        day = int(ru_match.group(1))
        month = RU_MONTHS.get(ru_match.group(2).lower())
        year = int(ru_match.group(3)) if ru_match.group(3) else default_year
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return None

    if "\u0441\u0435\u0433\u043e\u0434\u043d\u044f" in text:
        return date.today()
    if "\u0432\u0447\u0435\u0440\u0430" in text:
        return date.fromordinal(date.today().toordinal() - 1)
    return None


def filter_reviews_by_date(
    reviews: list[dict],
    *,
    date_from: date | None,
) -> DateFilterResult:
    if date_from is None:
        return DateFilterResult(reviews=reviews)

    filtered = []
    saw_too_old = False
    missing_review_date_count = 0
    for review in reviews:
        parsed = parse_review_date(first_text(review.get("date")))
        if parsed is None:
            filtered.append(review)
            missing_review_date_count += 1
            continue
        review["parsed_date"] = parsed.isoformat()
        if parsed >= date_from:
            filtered.append(review)
        else:
            saw_too_old = True
    return DateFilterResult(
        reviews=filtered,
        saw_too_old=saw_too_old,
        missing_review_date=missing_review_date_count > 0,
        missing_review_date_count=missing_review_date_count,
    )


def missing_review_date_error(filter_result: DateFilterResult) -> str:
    samples = []
    for review in filter_result.reviews:
        if parse_review_date(first_text(review.get("date"))) is not None:
            continue
        author = clean_review_author(first_text(review.get("author_name"))) or "unknown_author"
        source = first_text(review.get("source")) or "unknown_source"
        review_id = first_text(review.get("review_id"))
        text = re.sub(r"\s+", " ", first_text(review.get("text")))[:90]
        parts = [f"source={source}", f"author={author}"]
        if review_id:
            parts.append(f"id={review_id}")
        if text:
            parts.append(f"text={text}")
        samples.append("{" + ", ".join(parts) + "}")
        if len(samples) >= 3:
            break
    sample_text = f" samples={'; '.join(samples)}" if samples else ""
    return (
        "missing_review_date: "
        f"{filter_result.missing_review_date_count} review(s) have empty or unrecognized date "
        "after DOM/XHR merge."
        f"{sample_text}"
    )


def is_missing_review_date_error(error: str) -> bool:
    return "missing_review_date" in str(error or "")

