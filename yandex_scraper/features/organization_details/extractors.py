from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patchright.async_api import Page

from yandex_scraper import config as scraper_config


DETAILS_SCHEMA_VERSION = "3"
SERVICES_SCHEMA_VERSION = "1"
ORG_ID_RE = re.compile(r"/org/(?:[^/]+/)?(\d+)")
ORGANIZATION_DETAILS_MISSING_TEXT = getattr(
    scraper_config,
    "ORGANIZATION_DETAILS_MISSING_TEXT",
    "данные отсутствуют",
)
ORGANIZATION_DETAILS_MAX_ITEMS = getattr(scraper_config, "ORGANIZATION_DETAILS_MAX_ITEMS", 300)
ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS = getattr(
    scraper_config,
    "ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS",
    40000,
)


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


def base_url_from_reviews_url(reviews_url: str) -> str:
    url = normalize_yandex_url(reviews_url).split("?", 1)[0].rstrip("/")
    if url.endswith("/reviews"):
        return url[: -len("/reviews")] + "/"
    return url + "/"


def make_org_url(row: dict) -> str:
    org_url = normalize_yandex_url(row.get("org_url") or "")
    if org_url:
        return org_url.split("?", 1)[0]

    org_id = extract_org_id(row.get("org_id"), row.get("yandex_id"), row.get("permalink"))
    reviews_url = normalize_yandex_url(row.get("reviews_url") or "")
    if org_id and "/maps/org/" in reviews_url:
        return base_url_from_reviews_url(reviews_url)
    if org_id:
        return f"https://yandex.ru/maps/org/{org_id}/"
    return ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def missing_if_empty(value: Any, *, missing_text: str = ORGANIZATION_DETAILS_MISSING_TEXT) -> str:
    if value is None:
        return missing_text
    text = str(value).strip()
    return text if text else missing_text


def _source_identity(row: dict) -> tuple[str, str, str, str]:
    org_url = first_text(row.get("org_url"), make_org_url(row))
    reviews_url = first_text(row.get("reviews_url"))
    org_id = first_text(row.get("org_id"), extract_org_id(org_url), extract_org_id(reviews_url))
    title = first_text(row.get("title"))
    return org_id, title, org_url, reviews_url


def _placeholder_services(missing_text: str) -> list[dict[str, str]]:
    return [{"name": missing_text, "price": missing_text}]


def _placeholder_working_hours_schedule(missing_text: str) -> list[dict[str, str]]:
    return [{"day": missing_text, "date": missing_text, "hours": missing_text}]


def _normalize_working_hours_schedule(items: Any, *, missing_text: str) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return _placeholder_working_hours_schedule(missing_text)

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        day = missing_if_empty(item.get("day"), missing_text=missing_text)
        date = missing_if_empty(item.get("date"), missing_text=missing_text)
        hours = missing_if_empty(item.get("hours"), missing_text=missing_text)
        if day == missing_text and date == missing_text and hours == missing_text:
            continue
        key = (day.casefold(), date.casefold(), hours.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"day": day, "date": date, "hours": hours})

    return normalized or _placeholder_working_hours_schedule(missing_text)


def _normalize_services(items: Any, *, missing_text: str) -> tuple[list[dict[str, str]], str, str]:
    if not isinstance(items, list):
        return _placeholder_services(missing_text), "0", missing_text

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        category = first_text(item.get("category"))
        name = missing_if_empty(item.get("name"), missing_text=missing_text)
        description = first_text(item.get("description"))
        price = first_text(item.get("price"))
        if not price:
            price = missing_text
        if name == missing_text and price == missing_text:
            continue
        key = (category.casefold(), name.casefold(), description.casefold(), price.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized_item = {"name": name, "price": price}
        if category:
            normalized_item["category"] = category
        if description:
            normalized_item["description"] = description
        normalized.append(normalized_item)

    if not normalized:
        return _placeholder_services(missing_text), "0", missing_text

    text = " | ".join(
        " - ".join(
            part
            for part in (
                item.get("category", ""),
                item["name"],
                item["price"],
            )
            if part
        )
        for item in normalized
    )
    return normalized, str(len(normalized)), text


def _trim_visible_text(value: Any, *, limit: int, missing_text: str) -> str:
    text = str(value or "").strip()
    if not text:
        return missing_text
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _base_record_fields(row: dict, *, missing_text: str) -> dict:
    org_id, title, org_url, reviews_url = _source_identity(row)
    return {
        "organization_id": missing_if_empty(org_id, missing_text=missing_text),
        "organization_title": missing_if_empty(title, missing_text=missing_text),
        "organization_url": missing_if_empty(org_url, missing_text=missing_text),
        "reviews_url": missing_if_empty(reviews_url, missing_text=missing_text),
    }


def build_organization_details_error_record(
    row: dict,
    error: str,
    *,
    page_url: str = "",
    missing_text: str = ORGANIZATION_DETAILS_MISSING_TEXT,
) -> dict:
    return {
        "schema_version": DETAILS_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "capture_status": "error",
        "error": missing_if_empty(error, missing_text=missing_text),
        **_base_record_fields(row, missing_text=missing_text),
        "page_url": missing_if_empty(page_url, missing_text=missing_text),
        "title": missing_text,
        "category": missing_text,
        "full_address": missing_text,
        "phone": missing_text,
        "website_url": missing_text,
        "rating_value": missing_text,
        "rating_count": missing_text,
        "review_count": missing_text,
        "open_status_text": missing_text,
        "has_online_booking_button": "false",
        "online_booking_text": missing_text,
        "working_hours_notice": missing_text,
        "working_hours_today": missing_text,
        "working_hours_text": missing_text,
        "working_hours_schedule": _placeholder_working_hours_schedule(missing_text),
        "working_hours_schedule_reveal_clicked": "false",
        "contacts_text": missing_text,
        "features_text": missing_text,
        "card_visible_text": missing_text,
    }


def build_organization_services_error_record(
    row: dict,
    error: str,
    *,
    page_url: str = "",
    missing_text: str = ORGANIZATION_DETAILS_MISSING_TEXT,
) -> dict:
    return {
        "schema_version": SERVICES_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "capture_status": "error",
        "error": missing_if_empty(error, missing_text=missing_text),
        **_base_record_fields(row, missing_text=missing_text),
        "page_url": missing_if_empty(page_url, missing_text=missing_text),
        "services_count": "0",
        "services_text": missing_text,
        "services": _placeholder_services(missing_text),
        "services_reveal_clicked": "false",
    }


async def reveal_working_hours(page: Page) -> bool:
    try:
        clicked = await page.evaluate(
            """async () => {
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const dayRe = /^(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье|Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:,\\s*.+)?$/i;
                const graphRe = /(^|\\s)График(?:\\s+работы)?(?:\\s|$)/i;
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const visibleElements = () => Array.from(
                    document.querySelectorAll('a, button, [role="button"], [tabindex], span, div')
                ).filter((el) => isVisible(el));
                const scheduleAlreadyVisible = () => Array.from(document.querySelectorAll('body *')).some((el) => {
                    if (!isVisible(el)) return false;
                    const text = clean(el.innerText || el.textContent);
                    return dayRe.test(text);
                });
                if (scheduleAlreadyVisible()) return false;

                const timeHeader = visibleElements().find((el) => {
                    const text = clean(el.innerText || el.textContent);
                    const rect = el.getBoundingClientRect();
                    return /^Время\\s+работы$/i.test(text) && rect.width <= 260 && rect.height <= 80;
                });
                if (timeHeader) {
                    timeHeader.scrollIntoView({block: 'center', inline: 'nearest'});
                    await sleep(250);
                }

                const clickableTarget = (el) => {
                    let current = el;
                    for (let depth = 0; current && current !== document.body && depth < 7; depth += 1) {
                        const rect = current.getBoundingClientRect();
                        const style = window.getComputedStyle(current);
                        const text = clean(current.innerText || current.textContent);
                        const clickable = current.matches('button, a, [role="button"], [tabindex]')
                            || style.cursor === 'pointer'
                            || Boolean(current.onclick);
                        if (
                            graphRe.test(text)
                            && rect.width > 0
                            && rect.height > 0
                            && rect.width <= 360
                            && rect.height <= 140
                            && (clickable || depth > 0)
                        ) {
                            return current;
                        }
                        current = current.parentElement;
                    }
                    return el;
                };

                const headerTop = timeHeader ? timeHeader.getBoundingClientRect().top : 0;
                let candidates = visibleElements().filter((el) => {
                    const text = clean(el.innerText || el.textContent);
                    const rect = el.getBoundingClientRect();
                    return graphRe.test(text)
                        && text.length <= 90
                        && rect.width <= 360
                        && rect.height <= 140;
                }).sort((left, right) => {
                    const leftRect = left.getBoundingClientRect();
                    const rightRect = right.getBoundingClientRect();
                    const leftDistance = timeHeader ? Math.abs(leftRect.top - headerTop) : leftRect.top;
                    const rightDistance = timeHeader ? Math.abs(rightRect.top - headerTop) : rightRect.top;
                    if (leftDistance !== rightDistance) return leftDistance - rightDistance;
                    return clean(left.innerText || left.textContent).length
                        - clean(right.innerText || right.textContent).length;
                });
                if (!candidates.length && timeHeader) {
                    window.scrollBy(0, 220);
                    await sleep(250);
                    candidates = visibleElements().filter((el) => {
                        const text = clean(el.innerText || el.textContent);
                        const rect = el.getBoundingClientRect();
                        return graphRe.test(text)
                            && text.length <= 90
                            && rect.width <= 360
                            && rect.height <= 140;
                    });
                }
                const target = candidates.length ? clickableTarget(candidates[0]) : null;
                if (!target) return false;
                target.scrollIntoView({block: 'center', inline: 'nearest'});
                await sleep(120);
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
                    target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                }
                target.click();
                await sleep(1_200);
                return true;
            }"""
        )
    except Exception:
        return False

    if clicked:
        try:
            await page.wait_for_timeout(1_500)
        except Exception:
            pass
    return bool(clicked)


async def reveal_products_services(page: Page) -> bool:
    try:
        clicked = await page.evaluate(
            """async () => {
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                if (String(location.pathname || '').includes('/prices')) {
                    return true;
                }
                const candidates = Array.from(
                    document.querySelectorAll('a, button, [role="button"], [tabindex], [href*="/prices"]')
                ).filter((el) => isVisible(el));
                const priceLink = candidates.find((el) => {
                    const href = String(el.href || el.getAttribute('href') || '').toLowerCase();
                    return href.includes('/prices');
                });
                const preferred = candidates.find((el) => {
                    const text = clean(el.innerText || el.textContent).toLowerCase();
                    return text.includes('посмотреть все товары и услуги')
                        || text.includes('все товары и услуги')
                        || text.includes('прайс')
                        || text.includes('цены');
                });
                const tab = candidates.find((el) => {
                    const text = clean(el.innerText || el.textContent).toLowerCase();
                    return text === 'товары и услуги';
                });
                const target = priceLink || preferred || tab;
                if (!target) return false;
                target.scrollIntoView({block: 'center', inline: 'nearest'});
                await sleep(120);
                target.click();
                return true;
            }"""
        )
    except Exception:
        return False

    if clicked:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=6_000)
        except Exception:
            pass
        try:
            await page.wait_for_timeout(2_000)
        except Exception:
            pass
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body ? (document.body.innerText || document.body.textContent || '') : '';
                    return /(?:₽|руб\\.?|рублей?)/i.test(text)
                        || /поиск\\s+по\\s+названию|показать\\s+все\\s+категории|товары\\s+и\\s+услуги|прайс|цены/i.test(text);
                }""",
                timeout=8_000,
            )
        except Exception:
            pass
    return bool(clicked)


async def collect_products_services_from_page(
    page: Page,
    *,
    max_items: int,
    services_reveal_clicked: bool,
) -> dict:
    await reset_products_services_scroll(page)

    merged_services: dict[tuple[str, str, str, str], dict] = {}
    last_raw: dict = {}
    stable_steps = 0

    for step in range(14):
        if step:
            try:
                await page.wait_for_timeout(450)
            except Exception:
                pass

        raw_services = await page.evaluate(
            SERVICES_EVALUATE_JS,
            {
                "maxItems": max_items,
                "servicesRevealClicked": services_reveal_clicked,
            },
        )
        if not isinstance(raw_services, dict):
            raw_services = {}
        last_raw = raw_services

        before = len(merged_services)
        for service in raw_services.get("services") or []:
            if not isinstance(service, dict):
                continue
            key = (
                first_text(service.get("category")).casefold(),
                first_text(service.get("name")).casefold(),
                first_text(service.get("description")).casefold(),
                first_text(service.get("price")).casefold(),
            )
            if key[1] and key not in merged_services:
                merged_services[key] = service

        if len(merged_services) >= max_items:
            break

        moved = await scroll_products_services_panel(page)
        if len(merged_services) == before:
            stable_steps += 1
        else:
            stable_steps = 0
        if not moved or stable_steps >= 4:
            break

    last_raw["services"] = list(merged_services.values())[:max_items]
    return last_raw


async def reset_products_services_scroll(page: Page) -> None:
    try:
        await page.evaluate(
            """() => {
                const scrollables = Array.from(document.querySelectorAll('div, section, main, aside'))
                    .filter((el) => el.scrollHeight > el.clientHeight + 40);
                for (const el of scrollables) {
                    const rect = el.getBoundingClientRect();
                    if (rect.left < window.innerWidth * 0.75 && rect.width > 260 && rect.height > 180) {
                        el.scrollTop = 0;
                    }
                }
                if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
                if (document.documentElement) document.documentElement.scrollTop = 0;
                if (document.body) document.body.scrollTop = 0;
                window.dispatchEvent(new Event('scroll'));
            }"""
        )
    except Exception:
        pass


async def scroll_products_services_panel(page: Page) -> bool:
    try:
        result = await page.evaluate(
            """async () => {
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const scoreElement = (el) => {
                    if (!isVisible(el) || el.scrollHeight <= el.clientHeight + 40) return -1;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 260 || rect.height < 180 || rect.left > window.innerWidth * 0.78) return -1;
                    const text = clean(el.innerText || el.textContent);
                    let score = Math.min(80, (el.scrollHeight - el.clientHeight) / 20);
                    if (/товары\\s+и\\s+услуги|поиск\\s+по\\s+названию|показать\\s+все\\s+категории/i.test(text)) score += 120;
                    if (/(?:₽|руб\\.?|рублей?)/i.test(text)) score += 80;
                    if (rect.left < window.innerWidth * 0.58) score += 30;
                    return score;
                };
                const candidates = Array.from(document.querySelectorAll('div, section, main, aside'))
                    .map((el) => ({el, score: scoreElement(el)}))
                    .filter((item) => item.score >= 0)
                    .sort((left, right) => right.score - left.score);
                const target = candidates.length ? candidates[0].el : document.scrollingElement;
                if (!target) return false;
                const before = target.scrollTop;
                const maxScroll = Math.max(0, target.scrollHeight - target.clientHeight);
                target.scrollTop = Math.min(maxScroll, before + Math.max(520, Math.floor(target.clientHeight * 0.75)));
                target.dispatchEvent(new Event('scroll', {bubbles: true}));
                window.dispatchEvent(new Event('scroll'));
                await sleep(350);
                return Math.abs(target.scrollTop - before) > 2;
            }"""
        )
        return bool(result)
    except Exception:
        return False


DETAILS_EVALUATE_JS = """(args) => {
    const visibleTextMaxChars = args.visibleTextMaxChars;
    const cleanLine = (value) => String(value || '')
        .replace(/\\u00a0/g, ' ')
        .replace(/[ \\t]+/g, ' ')
        .trim();
    const textLines = (value) => String(value || '')
        .split('\\n')
        .map(cleanLine)
        .filter(Boolean);
    const bodyText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    const lines = textLines(bodyText);
    const dayRe = /^(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье|Пн|Вт|Ср|Чт|Пт|Сб|Вс)(?:,\\s*(.+))?$/i;
    const hoursRe = /^(?:\\d{1,2}[:.]\\d{2}\\s*[–—-]\\s*\\d{1,2}[:.]\\d{2}|\\d{1,2}:\\d{2}\\s*(?:AM|PM)\\s*[–—-]\\s*\\d{1,2}:\\d{2}\\s*(?:AM|PM)|24\\s*часа|круглосуточно|выходной|закрыто)$/i;

    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const firstText = (selectors) => {
        for (const selector of selectors) {
            try {
                const el = document.querySelector(selector);
                const text = el && cleanLine(el.innerText || el.textContent);
                if (text) return text;
            } catch (e) {}
        }
        return '';
    };
    const firstMatchingLine = (patterns) => {
        for (const line of lines) {
            if (patterns.some((pattern) => pattern.test(line))) return line;
        }
        return '';
    };
    const firstHref = (predicate) => {
        const links = Array.from(document.querySelectorAll('a[href]'));
        for (const link of links) {
            const href = String(link.href || link.getAttribute('href') || '').trim();
            const text = cleanLine(link.innerText || link.textContent);
            if (href && predicate(href, text)) return href;
        }
        return '';
    };
    const isBlockedWebsiteHost = (value) => {
        const lower = String(value || '').toLowerCase();
        return lower === 'ya.ru'
            || lower.endsWith('.ya.ru')
            || lower.includes('yandex.')
            || lower.includes('yastatic.')
            || lower.includes('vk.com')
            || lower.includes('t.me')
            || lower.includes('telegram.')
            || lower.includes('wa.me')
            || lower.includes('whatsapp.')
            || lower.includes('viber.')
            || lower.includes('youtube.')
            || lower.includes('youtu.be')
            || lower.includes('instagram.')
            || lower.includes('facebook.')
            || lower.includes('ok.ru');
    };
    const domainFromText = (value) => {
        let text = cleanLine(value)
            .replace(/^https?:\\/\\//i, '')
            .replace(/^www\\./i, '')
            .replace(/[\\s.,;:!?]+$/g, '')
            .replace(/^@+/, '');
        if (!text || text.length > 80 || text.includes(' ')) return '';
        if (!/^[a-zа-яё0-9-]+(?:\\.[a-zа-яё0-9-]+)+(?:\\/[^\\s]*)?$/i.test(text)) return '';
        const host = text.split('/')[0].toLowerCase();
        if (isBlockedWebsiteHost(host)) return '';
        return text;
    };
    const hostFromHref = (href) => {
        try {
            return new URL(href, location.href).hostname.toLowerCase().replace(/^www\\./, '');
        } catch (e) {
            return '';
        }
    };
    const normalizeWebsiteUrl = (value) => {
        const text = cleanLine(value);
        if (!text) return '';
        if (/^https?:\\/\\//i.test(text)) return text;
        return `https://${text}`;
    };
    const websiteUrlFromContacts = () => {
        const links = Array.from(document.querySelectorAll('a[href]'))
            .filter((link) => isVisible(link));
        const candidates = [];
        for (const link of links) {
            const href = String(link.href || link.getAttribute('href') || '').trim();
            const text = cleanLine(link.innerText || link.textContent);
            const textDomain = domainFromText(text);
            if (textDomain) {
                candidates.push({score: 100, url: normalizeWebsiteUrl(textDomain)});
                continue;
            }

            const host = hostFromHref(href);
            if (!host || isBlockedWebsiteHost(host)) continue;
            if (!/^https?:\\/\\//i.test(href)) continue;
            if (/^tel:|^mailto:/i.test(href)) continue;
            const label = text.toLowerCase();
            if (/показать\\s+телефон|маршрут|исправить|фото|отзывы|филиалы/.test(label)) continue;
            candidates.push({score: 30, url: href});
        }
        candidates.sort((left, right) => right.score - left.score);
        return candidates.length ? candidates[0].url : '';
    };
    const sectionText = (startPatterns, endPatterns) => {
        let start = -1;
        for (let index = 0; index < lines.length; index += 1) {
            if (startPatterns.some((pattern) => pattern.test(lines[index]))) {
                start = index;
                break;
            }
        }
        if (start < 0) return '';
        const section = [];
        for (let index = start + 1; index < lines.length; index += 1) {
            const line = lines[index];
            if (endPatterns.some((pattern) => pattern.test(line))) break;
            section.push(line);
        }
        return section.join('\\n').trim();
    };
    const firstSectionLine = (section) => {
        const sectionLines = textLines(section);
        return sectionLines.length ? sectionLines[0] : '';
    };
    const firstLineMatch = (pattern) => {
        for (const line of lines) {
            const match = line.match(pattern);
            if (match) return cleanLine(match[1]);
        }
        return '';
    };
    const lineAfter = (labelPattern, valuePattern) => {
        for (let index = 0; index < lines.length - 1; index += 1) {
            if (!labelPattern.test(lines[index])) continue;
            const value = cleanLine(lines[index + 1]);
            if (!valuePattern || valuePattern.test(value)) return value;
        }
        return '';
    };
    const ratingValueFromLines = () => {
        for (let index = 0; index < lines.length - 1; index += 1) {
            if (!/^Рейтинг$/i.test(lines[index])) continue;
            const next = cleanLine(lines[index + 1]);
            if (/^[0-5](?:[,.]\\d)?$/.test(next)) return next;
        }
        return firstLineMatch(/^([0-5](?:[,.]\\d)?)$/);
    };
    const ratingCountFromLines = () => {
        return firstLineMatch(/^(\\d[\\d\\s]*)\\s*(?:оценок|оценки|оценка)$/i);
    };
    const reviewCountFromLines = () => {
        for (const line of lines) {
            const inlineMatch = line.match(/^(\\d[\\d\\s]*)\\s*отзыв/i);
            if (inlineMatch) return cleanLine(inlineMatch[1]);
        }
        const afterReviews = lineAfter(/^Отзывы$/i, /^\\d[\\d\\s]*$/);
        if (afterReviews) return afterReviews;
        return '';
    };
    const normalizeHours = (value) => cleanLine(value).replace(/\\s*[–—-]\\s*/g, '–');
    const workingScheduleFromLines = () => {
        const schedule = [];
        for (let index = 0; index < lines.length - 1; index += 1) {
            const dayMatch = lines[index].match(dayRe);
            if (!dayMatch) continue;
            let hours = '';
            for (let offset = 1; offset <= 3 && index + offset < lines.length; offset += 1) {
                const candidate = cleanLine(lines[index + offset]);
                if (hoursRe.test(candidate)) {
                    hours = normalizeHours(candidate);
                    break;
                }
            }
            if (!hours) continue;
            const day = cleanLine(dayMatch[1]);
            const date = cleanLine(dayMatch[2] || '');
            const key = `${day}|${date}|${hours}`;
            if (schedule.some((item) => `${item.day}|${item.date}|${item.hours}` === key)) continue;
            schedule.push({day, date, hours});
        }
        return schedule;
    };
    const onlineBookingText = () => {
        const candidates = Array.from(
            document.querySelectorAll('a, button, [role="button"], [tabindex]')
        ).filter((el) => isVisible(el));
        for (const el of candidates) {
            const text = cleanLine(el.innerText || el.textContent);
            if (!text || text.length > 80) continue;
            if (/записаться|онлайн.?запись|запись онлайн/i.test(text)) return text;
        }
        for (const line of lines) {
            if (line.length <= 80 && /записаться|онлайн.?запись|запись онлайн/i.test(line)) {
                return line;
            }
        }
        return '';
    };

    const featuresSection = sectionText(
        [/^Особенности$/i],
        [/^Подробнее об организации/i, /^Активируйте/i, /^Панорама$/i, /^Рекламировать/i, /^Награды$/i, /^Рейтинг$/i]
    );
    const workingHoursSection = sectionText(
        [/^Время работы$/i],
        [/^Исправить/i, /^Как добраться$/i, /^Особенности$/i, /^Контакты$/i, /^Адрес$/i]
    );
    const schedule = workingScheduleFromLines();
    const bookingText = onlineBookingText();
    const websiteUrl = websiteUrlFromContacts();
    const phone = firstHref((href) => href.toLowerCase().startsWith('tel:')).replace(/^tel:/i, '');
    const notice = firstMatchingLine([
        /график работы мог поменяться/i,
        /график может отличаться/i,
        /время работы может отличаться/i
    ]);
    const visibleText = bodyText.length > visibleTextMaxChars
        ? bodyText.slice(0, visibleTextMaxChars) + '...<truncated>'
        : bodyText;

    return {
        page_url: String(location.href || ''),
        title: firstText([
            'h1',
            '[class*="business-card-title"]',
            '[class*="orgpage-header"]',
            '[class*="card-title"]',
            '[class*="title__text"]'
        ]),
        category: firstSectionLine(featuresSection) || firstText([
            '[class*="business-card-title-view__categories"]',
            '[class*="business-card-view__categories"]'
        ]),
        full_address: firstText([
            '[class*="business-contacts-view__address"]',
            '[class*="business-card-view__address"]',
            '[class*="address"]'
        ]),
        phone: phone || firstMatchingLine([/^\\+?\\d[\\d\\s()+-]{6,}$/]),
        website_url: websiteUrl,
        rating_value: ratingValueFromLines(),
        rating_count: ratingCountFromLines(),
        review_count: reviewCountFromLines(),
        open_status_text: firstMatchingLine([/^Открыто/i, /^Закрыто/i, /^Круглосуточно/i]),
        has_online_booking_button: Boolean(bookingText),
        online_booking_text: bookingText,
        working_hours_notice: notice,
        working_hours_today: schedule.length ? schedule[0].hours : '',
        working_hours_text: workingHoursSection,
        working_hours_schedule: schedule,
        contacts_text: sectionText(
            [/^Контакты$/i],
            [/^Время работы$/i, /^Как добраться$/i, /^Особенности$/i]
        ),
        features_text: featuresSection,
        card_visible_text: visibleText,
    };
}"""


SERVICES_EVALUATE_JS = """(args) => {
    const maxItems = args.maxItems;
    const servicesRevealClicked = Boolean(args.servicesRevealClicked);
    const cleanLine = (value) => String(value || '')
        .replace(/\\u00a0/g, ' ')
        .replace(/[ \\t]+/g, ' ')
        .trim();
    const textLines = (value) => String(value || '')
        .split('\\n')
        .map(cleanLine)
        .filter(Boolean);
    const bodyText = document.body ? (document.body.innerText || document.body.textContent || '') : '';
    const organizationTitle = cleanLine(document.querySelector('h1') ? document.querySelector('h1').innerText : '');
    const priceRe = /(?:^|\\s)(?:от\\s*)?\\d[\\d\\s.,]*(?:₽|руб\\.?|рублей?)(?:\\s|$)/i;
    const priceValueRe = /(?:от\\s*)?\\d[\\d\\s.,]*(?:₽|руб\\.?|рублей?)/i;
    const inlinePriceRe = /^(.*?)(?:\\s+)((?:от\\s*)?\\d[\\d\\s.,]*(?:₽|руб\\.?|рублей?))\\s*$/i;
    const products = [];
    const productSeen = new Set();
    const productNoise = [
        /^витрина$/i,
        /^товары и услуги$/i,
        /^посмотреть все товары и услуги$/i,
        /^показать все категории/i,
        /^поиск по названию$/i,
        /^есть противопоказания/i,
        /^посоветуйтесь с врачом/i,
        /^адрес$/i,
        /^контакты$/i,
        /^время работы$/i,
        /^особенности$/i,
        /^маршрут$/i,
        /^маршруты$/i,
        /^поиск$/i,
        /^обзор$/i,
        /^новости$/i,
        /^фото$/i,
        /^отзывы$/i,
        /^филиалы$/i,
        /^рейтинг$/i,
        /^оцените это место$/i,
        /^показать телефон$/i,
        /^добавить фото/i,
        /^сохранить$/i,
    ];
    const stopRe = /^(адрес|контакты|время работы|как добраться|особенности|панорама|награды|рейтинг|отзывы|похожие места рядом|вы владелец этой организации\\??|сообщить об ошибке|справка|сервисы|маршруты|пробки)$/i;
    const isNoise = (line) => productNoise.some((pattern) => pattern.test(line));
    const isPrice = (line) => priceRe.test(line);
    const priceFromLine = (line) => {
        const match = cleanLine(line).match(priceValueRe);
        return match ? cleanLine(match[0]) : '';
    };
    const isQuantity = (line) => /^\\d+(?:[,.]\\d+)?\\s*(?:шт\\.?|усл\\.?|ед\\.?|мин\\.?|час\\.?|мес\\.?)$/i.test(line);
    const isLikelyDescription = (line) => {
        if (line.length > 180) return true;
        if (line.length > 120 && /[.!?]|\\s[—-]\\s/.test(line)) return true;
        return false;
    };
    const isGoodName = (line) => {
        if (!line || isNoise(line) || isPrice(line) || isQuantity(line)) return false;
        if (organizationTitle && line.toLowerCase() === organizationTitle.toLowerCase()) return false;
        if (stopRe.test(line)) return false;
        if (/такси|маршрут|парковк|метро|остановк/i.test(line)) return false;
        if (/^[\\d\\s.,]+$/.test(line)) return false;
        if (/^\\+?\\d[\\d\\s()+-]{6,}$/.test(line)) return false;
        if (/https?:\\/\\//i.test(line) || /\\.[a-zа-я]{2,}(?:\\/|$)/i.test(line)) return false;
        if (!/[A-Za-zА-Яа-яЁё]/.test(line)) return false;
        if (isLikelyDescription(line)) return false;
        return line.length >= 3 && line.length <= 160;
    };
    const isDescriptionLine = (line) => {
        if (!line || isNoise(line) || isPrice(line) || stopRe.test(line)) return false;
        if (/^\\d+$/.test(line) || /^\\+?\\d[\\d\\s()+-]{6,}$/.test(line)) return false;
        if (/https?:\\/\\//i.test(line) || /\\.[a-zа-я]{2,}(?:\\/|$)/i.test(line)) return false;
        return line.length >= 3 && line.length <= 260;
    };
    const addProduct = (nameRaw, priceRaw, categoryRaw = '', descriptionRaw = '') => {
        if (products.length >= maxItems) return;
        const name = cleanLine(nameRaw);
        const price = cleanLine(priceRaw);
        const category = cleanLine(categoryRaw);
        const description = cleanLine(descriptionRaw);
        if (!isGoodName(name)) return;
        const key = `${category.toLowerCase()}|${name.toLowerCase()}|${description.toLowerCase()}|${price.toLowerCase()}`;
        if (productSeen.has(key)) return;
        productSeen.add(key);
        products.push({category, name, description, price});
    };
    const finalProducts = () => {
        const coreKey = (product) => `${product.name.toLowerCase()}|${product.price.toLowerCase()}`;
        const fullKey = (product) => `${product.category.toLowerCase()}|${coreKey(product)}`;
        const categorizedKeys = new Set(
            products
                .filter((product) => product.category)
                .map(coreKey)
        );
        const bestByKey = new Map();
        for (const product of products) {
            if (!product.category && categorizedKeys.has(coreKey(product))) {
                continue;
            }
            const key = fullKey(product);
            const existing = bestByKey.get(key);
            if (!existing || product.description.length > existing.description.length) {
                bestByKey.set(key, product);
            }
        }
        return Array.from(bestByKey.values()).slice(0, maxItems);
    };
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const bestPanel = () => {
        const scoreElement = (el) => {
            if (!isVisible(el)) return -1;
            const rect = el.getBoundingClientRect();
            if (rect.width < 260 || rect.height < 180 || rect.left > window.innerWidth * 0.82) return -1;
            const text = cleanLine(el.innerText || el.textContent);
            let score = Math.min(80, text.length / 400);
            if (/товары\\s+и\\s+услуги/i.test(text)) score += 100;
            if (/поиск\\s+по\\s+названию/i.test(text)) score += 160;
            if (/показать\\s+все\\s+категории/i.test(text)) score += 80;
            if (priceRe.test(text)) score += 180;
            if (el.scrollHeight > el.clientHeight + 40) score += 50;
            if (rect.left < window.innerWidth * 0.62) score += 30;
            return score;
        };
        const candidates = Array.from(document.querySelectorAll('div, section, main, aside, body'))
            .map((el) => ({el, score: scoreElement(el)}))
            .filter((item) => item.score >= 0)
            .sort((left, right) => right.score - left.score);
        return candidates.length ? candidates[0].el : document.body;
    };
    const panel = bestPanel();
    let lines = textLines(panel ? (panel.innerText || panel.textContent || '') : bodyText);
    if (lines.length < 8 || (!lines.some(isPrice) && priceRe.test(bodyText))) {
        lines = textLines(bodyText);
    }
    const headerIndex = lines.findIndex((line) => /^витрина$/i.test(line) || /^товары и услуги$/i.test(line));
    const searchIndex = lines.findIndex((line) => /^поиск по названию$/i.test(line));
    const allCategoriesIndex = lines.findIndex((line) => /^показать все категории/i.test(line));
    const currentPath = String(location.pathname || '').toLowerCase();
    const shouldScanPrices = servicesRevealClicked
        || currentPath.includes('/prices')
        || headerIndex >= 0
        || searchIndex >= 0
        || lines.some((line) => /^прайс|^цены$/i.test(line))
        || lines.some(isPrice);
    const scanStart = allCategoriesIndex >= 0
        ? allCategoriesIndex + 1
        : searchIndex >= 0
            ? searchIndex + 1
            : headerIndex >= 0
                ? headerIndex + 1
                : 0;
    const nextPriceIndex = (fromIndex, maxDistance = 12) => {
        for (let offset = 1; offset <= maxDistance && fromIndex + offset < lines.length; offset += 1) {
            if (isPrice(lines[fromIndex + offset])) return fromIndex + offset;
            if (stopRe.test(lines[fromIndex + offset])) return -1;
        }
        return -1;
    };
    const findNameBefore = (priceIndex, minIndex) => {
        for (let offset = 1; offset <= 8 && priceIndex - offset >= minIndex; offset += 1) {
            const candidate = lines[priceIndex - offset];
            if (isGoodName(candidate)) return candidate;
        }
        return '';
    };
    const findNameBeforeInBlock = (blockLines, priceIndex) => {
        const candidates = blockLines.slice(0, priceIndex).filter(isGoodName);
        return candidates.length ? candidates[0] : '';
    };
    const descriptionAfter = (index) => {
        const description = [];
        for (let offset = 1; offset <= 3 && index + offset < lines.length; offset += 1) {
            const candidate = lines[index + offset];
            if (isPrice(candidate) || stopRe.test(candidate)) break;
            if (!isDescriptionLine(candidate)) continue;
            const followingPrice = nextPriceIndex(index + offset, 3);
            if (followingPrice > index + offset) {
                const followingLine = lines[followingPrice] || '';
                const followingInline = followingLine.match(inlinePriceRe);
                const followingInlineName = followingInline ? cleanLine(followingInline[1]) : '';
                if (
                    followingPrice !== index + offset + 1
                    || !followingInlineName
                    || !isGoodName(followingInlineName)
                ) {
                    break;
                }
            }
            description.push(candidate);
        }
        return description.join(' ');
    };
    const servicesFromElements = () => {
        const priceElements = Array.from(document.querySelectorAll('body *'))
            .filter((el) => isVisible(el))
            .filter((el) => {
                const text = cleanLine(el.innerText || el.textContent);
                return text && text.length <= 260 && isPrice(text);
            });
        for (const priceEl of priceElements) {
            const productRoot = priceEl.closest(
                '.related-product-view, .related-item-photo-view, .business-full-items-grouped-view__item'
            );
            if (
                !productRoot
                && priceEl.querySelector(
                    '.related-product-view, .related-item-photo-view, .business-full-items-grouped-view__item'
                )
            ) {
                continue;
            }
            let node = productRoot || priceEl;
            let best = productRoot || priceEl;
            for (let depth = 0; node && node !== document.body && depth < 8; depth += 1) {
                const rect = node.getBoundingClientRect();
                const text = cleanLine(node.innerText || node.textContent);
                if (
                    rect.width >= 260
                    && rect.width <= 900
                    && rect.height >= 18
                    && rect.height <= 260
                    && text.length >= 6
                    && text.length <= 700
                    && isPrice(text)
                ) {
                    best = node;
                }
                node = node.parentElement;
            }
            const blockLines = textLines(best.innerText || best.textContent);
            if (!productRoot && blockLines.filter(isPrice).length > 1) {
                continue;
            }
            const title = cleanLine(
                (best.querySelector('.related-item-photo-view__title, [class*="product-view__title"], [class*="item-photo-view__title"]') || {}).innerText
            );
            const rootDescription = cleanLine(
                (best.querySelector('.related-item-photo-view__description, [class*="product-view__description"], [class*="item-photo-view__description"]') || {}).innerText
            );
            for (let index = 0; index < blockLines.length; index += 1) {
                const line = blockLines[index];
                if (!isPrice(line)) continue;
                const inlinePriceMatch = line.match(inlinePriceRe);
                const inlineName = inlinePriceMatch ? cleanLine(inlinePriceMatch[1]) : '';
                const price = inlinePriceMatch ? cleanLine(inlinePriceMatch[2]) : priceFromLine(line);
                const name = inlineName && isGoodName(inlineName)
                    ? inlineName
                    : isGoodName(title)
                        ? title
                        : findNameBeforeInBlock(blockLines, index);
                if (name && price) {
                    const description = rootDescription && rootDescription !== name
                        ? rootDescription
                        : blockLines.slice(index + 1).filter(isDescriptionLine).slice(0, 2).join(' ');
                    addProduct(name, price, '', description);
                }
            }
        }
    };
    let active = shouldScanPrices && (headerIndex < 0 || scanStart > headerIndex);
    let currentName = '';
    let currentCategory = '';
    let descriptionCooldown = 0;
    servicesFromElements();
    for (let index = scanStart; index < lines.length; index += 1) {
        const line = lines[index];
        if (/^витрина$/i.test(line) || /^товары и услуги$/i.test(line)) {
            active = true;
            currentName = '';
            continue;
        }
        if (!active) continue;
        if (stopRe.test(line)) {
            break;
        }
        if (isNoise(line)) continue;
        if (!isPrice(line) && descriptionCooldown > 0) {
            descriptionCooldown -= 1;
            continue;
        }
        if (isGoodName(line)) {
            const nextPrice = nextPriceIndex(index, 10);
            const nextLine = lines[index + 1] || '';
            const nextLineInline = nextLine.match(inlinePriceRe);
            if (nextPrice > index && nextLineInline && isGoodName(cleanLine(nextLineInline[1]))) {
                currentCategory = line;
                currentName = '';
                continue;
            }
            currentName = line;
        }
        if (isPrice(line)) {
            const inlinePriceMatch = line.match(inlinePriceRe);
            const inlineName = inlinePriceMatch ? cleanLine(inlinePriceMatch[1]) : '';
            const price = inlinePriceMatch ? cleanLine(inlinePriceMatch[2]) : priceFromLine(line);
            if (
                inlinePriceMatch
                && inlineName
                && !/^от$/i.test(inlineName)
                && isGoodName(inlineName)
            ) {
                addProduct(inlineName, price, currentCategory, descriptionAfter(index));
                descriptionCooldown = 3;
                currentName = '';
                continue;
            }
            const nearbyName = currentName || findNameBefore(index, scanStart);
            if (nearbyName) {
                addProduct(nearbyName, price, currentCategory, descriptionAfter(index));
                descriptionCooldown = 3;
                currentName = '';
            } else if (products.length) {
                const previous = products[products.length - 1];
                if (!/₽/.test(previous.price) && /₽/.test(line)) {
                    previous.price = line;
                }
            }
            continue;
        }
    }
    if (!products.length) {
        let showcaseActive = false;
        for (let index = headerIndex >= 0 ? headerIndex + 1 : 0; index < lines.length; index += 1) {
            const line = lines[index];
            if (/^витрина$/i.test(line) || /^товары и услуги$/i.test(line)) {
                showcaseActive = true;
                continue;
            }
            if (!showcaseActive) continue;
            if (stopRe.test(line)) break;
            if (isGoodName(line)) {
                addProduct(line, '', '', '');
            }
        }
    }
    return {
        page_url: String(location.href || ''),
        panel_text_preview: lines.slice(0, 160).join('\\n').slice(0, 12000),
        services: finalProducts(),
    };
}"""


async def collect_organization_services_from_page(
    page: Page,
    row: dict,
    *,
    missing_text: str = ORGANIZATION_DETAILS_MISSING_TEXT,
    max_items: int = ORGANIZATION_DETAILS_MAX_ITEMS,
) -> dict:
    services_reveal_clicked = await reveal_products_services(page)
    raw_services = await collect_products_services_from_page(
        page,
        max_items=max_items,
        services_reveal_clicked=services_reveal_clicked,
    )
    if not isinstance(raw_services, dict):
        raw_services = {}

    org_id, source_title, org_url, reviews_url = _source_identity(row)
    services, services_count, services_text = _normalize_services(
        raw_services.get("services"),
        missing_text=missing_text,
    )
    return {
        "schema_version": SERVICES_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "capture_status": "done",
        "error": missing_text,
        "organization_id": missing_if_empty(org_id, missing_text=missing_text),
        "organization_title": missing_if_empty(source_title, missing_text=missing_text),
        "organization_url": missing_if_empty(org_url, missing_text=missing_text),
        "reviews_url": missing_if_empty(reviews_url, missing_text=missing_text),
        "page_url": missing_if_empty(raw_services.get("page_url"), missing_text=missing_text),
        "services_count": services_count,
        "services_text": missing_if_empty(services_text, missing_text=missing_text),
        "services": services,
        "services_reveal_clicked": "true" if services_reveal_clicked else "false",
    }


async def collect_organization_details_from_page(
    page: Page,
    row: dict,
    *,
    missing_text: str = ORGANIZATION_DETAILS_MISSING_TEXT,
    max_items: int = ORGANIZATION_DETAILS_MAX_ITEMS,
    visible_text_max_chars: int = ORGANIZATION_DETAILS_VISIBLE_TEXT_MAX_CHARS,
) -> tuple[dict, dict]:
    schedule_reveal_clicked = await reveal_working_hours(page)
    raw_details = await page.evaluate(
        DETAILS_EVALUATE_JS,
        {"visibleTextMaxChars": visible_text_max_chars},
    )
    if not isinstance(raw_details, dict):
        raw_details = {}

    services_reveal_clicked = await reveal_products_services(page)
    raw_services = await collect_products_services_from_page(
        page,
        max_items=max_items,
        services_reveal_clicked=services_reveal_clicked,
    )
    if not isinstance(raw_services, dict):
        raw_services = {}

    org_id, source_title, org_url, reviews_url = _source_identity(row)
    services, services_count, services_text = _normalize_services(
        raw_services.get("services"),
        missing_text=missing_text,
    )
    working_hours_schedule = _normalize_working_hours_schedule(
        raw_details.get("working_hours_schedule"),
        missing_text=missing_text,
    )
    schedule_today = ""
    if working_hours_schedule and working_hours_schedule[0].get("hours") != missing_text:
        schedule_today = working_hours_schedule[0].get("hours", "")

    details_record = {
        "schema_version": DETAILS_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "capture_status": "done",
        "error": missing_text,
        "organization_id": missing_if_empty(org_id, missing_text=missing_text),
        "organization_title": missing_if_empty(source_title, missing_text=missing_text),
        "organization_url": missing_if_empty(org_url, missing_text=missing_text),
        "reviews_url": missing_if_empty(reviews_url, missing_text=missing_text),
        "page_url": missing_if_empty(raw_details.get("page_url"), missing_text=missing_text),
        "title": missing_if_empty(raw_details.get("title"), missing_text=missing_text),
        "category": missing_if_empty(raw_details.get("category"), missing_text=missing_text),
        "full_address": missing_if_empty(raw_details.get("full_address"), missing_text=missing_text),
        "phone": missing_if_empty(raw_details.get("phone"), missing_text=missing_text),
        "website_url": missing_if_empty(raw_details.get("website_url"), missing_text=missing_text),
        "rating_value": missing_if_empty(raw_details.get("rating_value"), missing_text=missing_text),
        "rating_count": missing_if_empty(raw_details.get("rating_count"), missing_text=missing_text),
        "review_count": missing_if_empty(raw_details.get("review_count"), missing_text=missing_text),
        "open_status_text": missing_if_empty(raw_details.get("open_status_text"), missing_text=missing_text),
        "has_online_booking_button": "true" if raw_details.get("has_online_booking_button") else "false",
        "online_booking_text": missing_if_empty(raw_details.get("online_booking_text"), missing_text=missing_text),
        "working_hours_notice": missing_if_empty(raw_details.get("working_hours_notice"), missing_text=missing_text),
        "working_hours_today": missing_if_empty(
            first_text(raw_details.get("working_hours_today"), schedule_today),
            missing_text=missing_text,
        ),
        "working_hours_text": missing_if_empty(raw_details.get("working_hours_text"), missing_text=missing_text),
        "working_hours_schedule": working_hours_schedule,
        "working_hours_schedule_reveal_clicked": "true" if schedule_reveal_clicked else "false",
        "contacts_text": missing_if_empty(raw_details.get("contacts_text"), missing_text=missing_text),
        "features_text": missing_if_empty(raw_details.get("features_text"), missing_text=missing_text),
        "card_visible_text": _trim_visible_text(
            raw_details.get("card_visible_text"),
            limit=visible_text_max_chars,
            missing_text=missing_text,
        ),
    }
    if details_record["organization_title"] == missing_text and details_record["title"] != missing_text:
        details_record["organization_title"] = details_record["title"]

    services_record = {
        "schema_version": SERVICES_SCHEMA_VERSION,
        "captured_at": utc_now(),
        "capture_status": "done",
        "error": missing_text,
        "organization_id": details_record["organization_id"],
        "organization_title": details_record["organization_title"],
        "organization_url": details_record["organization_url"],
        "reviews_url": details_record["reviews_url"],
        "page_url": missing_if_empty(raw_services.get("page_url"), missing_text=missing_text),
        "services_count": services_count,
        "services_text": missing_if_empty(services_text, missing_text=missing_text),
        "services": services,
        "services_reveal_clicked": "true" if services_reveal_clicked else "false",
    }
    return details_record, services_record


def append_jsonl_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def append_organization_details_record(path: Path, record: dict) -> None:
    append_jsonl_record(path, record)


def append_organization_services_record(path: Path, record: dict) -> None:
    append_jsonl_record(path, record)
