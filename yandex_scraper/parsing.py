import asyncio
import json
import random
import re
from datetime import datetime, timezone

from patchright.async_api import BrowserContext

from yandex_scraper.config import (
    CAPTCHA_MAX_RETRIES,
    ENABLE_SAFE_FALLBACK_PASS,
    ENRICHED_DATA_ENABLED,
    ENRICHED_JSONL_FILE,
    FALLBACK_MIN_RESULTS,
    FIELD_AUDIT_ENABLED,
    FIELD_AUDIT_FILE,
    FIELD_AUDIT_MAX_RECORDS_PER_CELL,
    FIELD_AUDIT_RAW_PREVIEW_MAX_CHARS,
    NAV_WAIT_UNTIL_SEQUENCE,
    SCROLL_MAX_STEPS,
    SCROLL_NO_GROWTH_LIMIT,
    SCROLL_NO_GROWTH_MIN_STEP,
    SCROLL_PAUSE_MAX,
    SCROLL_PAUSE_MIN,
    sanitize_url,
)
from yandex_scraper.browser import check_captcha
from yandex_scraper.constants import EMPTY_SCROLL_LIMIT
from yandex_scraper.extraction import extract_businesses_from_json
from yandex_scraper.storage import SeenIdsDB


async def parse_one_cell(
    worker_id: int,
    row: dict,
    seen_db: SeenIdsDB,
    jsonl_handle,
    jsonl_lock: asyncio.Lock,
    context_primary: BrowserContext,
    context_fallback: BrowserContext | None,
    proxy_server_primary: str,
    proxy_server_fallback: str | None,
) -> str:
    def normalize_or_missing(value, missing_text: str) -> str:
        if value is None:
            return missing_text
        text = str(value).strip()
        return text if text else missing_text

    def bytes_to_mb(value: int) -> float:
        return value / (1024 * 1024)

    def make_key(biz: dict) -> str:
        permalink = str(biz.get("permalink") or "").strip()
        if permalink:
            return permalink
        title = str(biz.get("title") or "").strip()
        full_addr = str(biz.get("fullAddress") or "").strip()
        return f"{title}_{full_addr}" if title else ""

    def merge_businesses(dst: dict[str, dict], src: list[dict]) -> None:
        for biz in src:
            key = make_key(biz)
            if key and key != "_":
                dst[key] = biz

    def compact_json(value) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) <= FIELD_AUDIT_RAW_PREVIEW_MAX_CHARS:
            return text
        return text[:FIELD_AUDIT_RAW_PREVIEW_MAX_CHARS] + "...<truncated>"

    def top_level_keys(value) -> list[str]:
        if not isinstance(value, dict):
            return []
        return sorted(str(key) for key in value.keys())

    def selected_raw_fields(value) -> dict:
        if not isinstance(value, dict):
            return {}
        selected = {}
        for key in (
            "id",
            "permalink",
            "businessId",
            "orgId",
            "name",
            "title",
            "shortTitle",
            "address",
            "categories",
            "rubrics",
            "phones",
            "contacts",
            "ratingData",
            "rating",
            "reviewsCount",
            "workingHours",
            "features",
            "urls",
            "links",
            "photos",
            "badges",
            "description",
        ):
            if key in value:
                selected[key] = value.get(key)
        return selected

    def json_field(value) -> str:
        if value in (None, ""):
            return ""
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    def first_text(value) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def extract_yandex_id(value: str) -> str:
        text = str(value or "")
        match = re.search(r"/org/[^/]+/(\d+)", text)
        if match:
            return match.group(1)
        if re.fullmatch(r"\d+", text.strip()):
            return text.strip()
        return ""

    def make_org_url(yandex_id: str, raw_obj: dict | None = None) -> str:
        if not yandex_id:
            return ""
        seoname = ""
        if isinstance(raw_obj, dict):
            seoname = str(raw_obj.get("seoname") or "").strip()
        if seoname:
            return f"https://yandex.ru/maps/org/{seoname}/{yandex_id}/"
        return f"https://yandex.ru/maps/org/{yandex_id}/"

    def first_website_url(raw_obj: dict) -> str:
        urls = raw_obj.get("urls") if isinstance(raw_obj, dict) else None
        if isinstance(urls, list) and urls:
            first = urls[0]
            if isinstance(first, dict):
                return first_text(first.get("url") or first.get("href") or first.get("value"))
            return first_text(first)
        if isinstance(urls, str):
            return urls.strip()
        return ""

    def photo_url_from_template(value: str) -> str:
        text = first_text(value)
        if "%s" in text:
            return text.replace("%s", "orig")
        return text

    def photo_count_and_first_url(raw_obj: dict) -> tuple[str, str]:
        photos = raw_obj.get("photos") if isinstance(raw_obj, dict) else None
        if not isinstance(photos, dict):
            return "", ""
        count = first_text(photos.get("count"))
        first_url = photo_url_from_template(photos.get("urlTemplate"))
        items = photos.get("items")
        if isinstance(items, list) and items:
            item = items[0]
            if isinstance(item, dict):
                first_url = photo_url_from_template(
                    item.get("url")
                    or item.get("urlTemplate")
                    or item.get("href")
                    or first_url
                )
        return count, first_url

    def business_verified_owner(raw_obj: dict) -> str:
        properties = raw_obj.get("businessProperties") if isinstance(raw_obj, dict) else None
        if not isinstance(properties, dict):
            return ""
        value = properties.get("has_verified_owner")
        if value is True:
            return "true"
        if value is False:
            return "false"
        return first_text(value)

    def build_enriched_from_xhr(raw_obj, normalized: dict) -> dict:
        raw = raw_obj if isinstance(raw_obj, dict) else {}
        yandex_id = first_text(
            raw.get("id")
            or raw.get("permalink")
            or raw.get("businessId")
            or raw.get("orgId")
            or normalized.get("permalink")
        )
        rating_data = raw.get("ratingData") if isinstance(raw.get("ratingData"), dict) else {}
        photos_count, first_photo_url = photo_count_and_first_url(raw)
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_query": query,
            "source_bbox": bbox,
            "cell_url": url,
            "search_result_index": first_text(raw.get("index")),
            "yandex_id": yandex_id,
            "permalink": first_text(normalized.get("permalink") or yandex_id),
            "org_url": make_org_url(yandex_id, raw),
            "title": first_text(normalized.get("title") or raw.get("title") or raw.get("name")),
            "fullAddress": first_text(normalized.get("fullAddress") or raw.get("fullAddress") or raw.get("address")),
            "categories_0_name": first_text(normalized.get("categories_0_name")),
            "raw_categories_json": json_field(raw.get("categories")),
            "phones_0_number": first_text(normalized.get("phones_0_number")),
            "raw_phones_json": json_field(raw.get("phones")),
            "coordinates_0": first_text(normalized.get("coordinates_0")),
            "coordinates_1": first_text(normalized.get("coordinates_1")),
            "rating_count": first_text(normalized.get("ratingData_ratingCount") or rating_data.get("ratingCount")),
            "rating_value": first_text(normalized.get("ratingData_ratingValue") or rating_data.get("ratingValue")),
            "review_count": first_text(rating_data.get("reviewCount")),
            "raw_ratingData_json": json_field(raw.get("ratingData")),
            "raw_features_json": json_field(raw.get("features")),
            "raw_urls_json": json_field(raw.get("urls")),
            "website_url": first_website_url(raw),
            "photos_count": photos_count,
            "first_photo_url": first_photo_url,
            "raw_photos_json": json_field(raw.get("photos")),
            "business_verified_owner": business_verified_owner(raw),
            "dom_category": "",
            "dom_visibleText": "",
            "open_status_text": "",
            "awards_text": "",
            "offer_text": "",
            "gallery_url": "",
            "reviews_url": "",
            "dom_image_url": "",
        }

    def visible_lines(text: str) -> list[str]:
        return [line.strip() for line in str(text or "").splitlines() if line.strip()]

    def open_status_from_text(text: str) -> str:
        for line in visible_lines(text):
            if (
                line.startswith("Открыто")
                or line.startswith("Закрыто")
                or line.startswith("Круглосуточно")
            ):
                return line
        return ""

    def awards_from_text(text: str) -> str:
        lines = visible_lines(text)
        awards = []
        for index, line in enumerate(lines):
            if line.startswith("Награды") and index + 1 < len(lines):
                awards.append(lines[index + 1])
            elif "Хорошее место" in line:
                awards.append(line)
        return " | ".join(dict.fromkeys(awards))

    def offer_from_text(text: str) -> str:
        lines = visible_lines(text)
        offers = [
            line
            for line in lines
            if "₽" in line or "руб" in line.lower() or line.lower().startswith("акция")
        ]
        return " | ".join(dict.fromkeys(offers))

    def find_link(links: list, marker: str) -> str:
        if not isinstance(links, list):
            return ""
        for link in links:
            if not isinstance(link, dict):
                continue
            href = str(link.get("href") or "")
            if marker in href:
                return href
        return ""

    def build_enriched_from_dom(dom: dict) -> dict:
        org_url = first_text(dom.get("orgLink"))
        yandex_id = extract_yandex_id(org_url)
        links = dom.get("links") if isinstance(dom.get("links"), list) else []
        images = dom.get("imageUrls") if isinstance(dom.get("imageUrls"), list) else []
        visible_text = first_text(dom.get("visibleText"))
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_query": query,
            "source_bbox": bbox,
            "cell_url": url,
            "search_result_index": first_text(dom.get("index")),
            "yandex_id": yandex_id,
            "permalink": yandex_id,
            "org_url": org_url,
            "title": first_text(dom.get("title")),
            "dom_category": first_text(dom.get("category")),
            "dom_visibleText": visible_text,
            "open_status_text": open_status_from_text(visible_text),
            "awards_text": awards_from_text(visible_text),
            "offer_text": offer_from_text(visible_text),
            "gallery_url": find_link(links, "/gallery/"),
            "reviews_url": find_link(links, "/reviews/"),
            "dom_image_url": first_text(images[0] if images else ""),
        }

    raw_url = row["url"]
    query = row.get("query", "")
    bbox = row.get("bbox", "")
    prefix = f"  [W{worker_id}]"
    url = sanitize_url(raw_url)
    max_retries = 3
    audit_records: list[dict] = []
    audit_seen: set[str] = set()
    enriched_records: dict[str, dict] = {}
    enriched_overwrite_fields = {
        "org_url",
        "dom_category",
        "dom_visibleText",
        "open_status_text",
        "awards_text",
        "offer_text",
        "gallery_url",
        "reviews_url",
        "dom_image_url",
    }

    def enriched_key(record: dict) -> str:
        yandex_id = first_text(record.get("yandex_id"))
        if yandex_id:
            return yandex_id
        org_url = first_text(record.get("org_url"))
        if org_url:
            return org_url
        title = first_text(record.get("title"))
        address = first_text(record.get("fullAddress"))
        return f"{title}_{address}" if title else ""

    def merge_enriched_record(record: dict) -> None:
        if not ENRICHED_DATA_ENABLED:
            return
        key = enriched_key(record)
        if not key:
            return
        existing = enriched_records.get(key)
        if existing is None:
            enriched_records[key] = dict(record)
            return
        for field, value in record.items():
            if value not in (None, ""):
                if not existing.get(field) or field in enriched_overwrite_fields:
                    existing[field] = value

    async def flush_enriched_records(status: str) -> None:
        if not ENRICHED_DATA_ENABLED or not enriched_records:
            return
        async with jsonl_lock:
            with open(ENRICHED_JSONL_FILE, "a", encoding="utf-8") as enriched_f:
                for record in enriched_records.values():
                    record["cell_status"] = status
                    enriched_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                enriched_f.flush()
        print(f"{prefix} [Enriched] Сохранено расширенных записей: {len(enriched_records)}")

    def add_audit_record(kind: str, pass_name: str, payload: dict) -> None:
        if not FIELD_AUDIT_ENABLED:
            return
        if len(audit_records) >= FIELD_AUDIT_MAX_RECORDS_PER_CELL:
            return
        fingerprint = json.dumps(
            {
                "kind": kind,
                "pass": pass_name,
                "permalink": payload.get("permalink"),
                "title": payload.get("title"),
                "index": payload.get("index"),
                "response_url": payload.get("response_url"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if fingerprint in audit_seen:
            return
        audit_seen.add(fingerprint)
        audit_records.append(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": kind,
                "pass_name": pass_name,
                "source_query": query,
                "source_bbox": bbox,
                "cell_url": url,
                **payload,
            }
        )

    async def flush_audit_records(status: str) -> None:
        if not FIELD_AUDIT_ENABLED or not audit_records:
            return
        async with jsonl_lock:
            with open(FIELD_AUDIT_FILE, "a", encoding="utf-8") as audit_f:
                for record in audit_records:
                    record["cell_status"] = status
                    audit_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                audit_f.flush()
        print(f"{prefix} [Audit] Сохранено diagnostic-записей: {len(audit_records)}")

    def collect_xhr_audit(pass_name: str, response_url: str):
        if not FIELD_AUDIT_ENABLED and not ENRICHED_DATA_ENABLED:
            return None

        def collector(raw_obj, normalized: dict) -> None:
            if ENRICHED_DATA_ENABLED:
                merge_enriched_record(build_enriched_from_xhr(raw_obj, normalized))
            if FIELD_AUDIT_ENABLED:
                add_audit_record(
                    "xhr_business",
                    pass_name,
                    {
                        "response_url": response_url,
                        "permalink": normalized.get("permalink", ""),
                        "title": normalized.get("title", ""),
                        "normalized_preview": normalized,
                        "raw_top_level_keys": top_level_keys(raw_obj),
                        "raw_selected_fields": selected_raw_fields(raw_obj),
                        "raw_preview_json": compact_json(raw_obj),
                    },
                )

        return collector

    async def run_pass(
        active_context: BrowserContext,
        pass_name: str,
        proxy_server: str,
    ) -> tuple[str, list[dict], int, int]:
        captcha_attempts = 0

        for attempt in range(1, max_retries + 1):
            xhr_businesses: dict[str, dict] = {}
            traffic_up_bytes = 0
            traffic_down_bytes = 0
            dom_max_count = 0
            dom_logged = False
            page = None

            print(f"{prefix} [{pass_name}] [Попытка {attempt}/{max_retries}] Прокси: {proxy_server}")

            def print_traffic(stage: str) -> None:
                total = traffic_up_bytes + traffic_down_bytes
                print(
                    f"{prefix} [{pass_name}] [Traffic] {stage} | "
                    f"↑{bytes_to_mb(traffic_up_bytes):.2f} MB "
                    f"↓{bytes_to_mb(traffic_down_bytes):.2f} MB "
                    f"Σ{bytes_to_mb(total):.2f} MB"
                )

            try:
                page = await active_context.new_page()

                try:
                    cdp = await active_context.new_cdp_session(page)
                    await cdp.send("Network.enable")

                    def on_request_will_be_sent(event):
                        nonlocal traffic_up_bytes
                        request = event.get("request") or {}
                        headers = request.get("headers") or {}
                        headers_size = (
                            sum(
                                len(str(k).encode("utf-8")) + len(str(v).encode("utf-8")) + 4
                                for k, v in headers.items()
                            )
                            + 2
                        )
                        post_data = request.get("postData") or ""
                        body_size = len(post_data.encode("utf-8")) if isinstance(post_data, str) else 0
                        traffic_up_bytes += headers_size + body_size

                    def on_loading_finished(event):
                        nonlocal traffic_down_bytes
                        encoded_len = event.get("encodedDataLength")
                        if isinstance(encoded_len, (int, float)):
                            traffic_down_bytes += int(encoded_len)

                    cdp.on("Network.requestWillBeSent", on_request_will_be_sent)
                    cdp.on("Network.loadingFinished", on_loading_finished)
                except Exception as e:
                    print(f"{prefix} [{pass_name}] [WARN] Traffic meter off: {type(e).__name__}: {str(e)[:80]}")

                async def on_response(response):
                    url_resp = response.url
                    is_api = (
                        "maps-api" in url_resp
                        or "api/search" in url_resp
                        or "api/business" in url_resp
                        or "sprav.yandex" in url_resp
                        or ("search" in url_resp and "json" in url_resp)
                        or ("maps" in url_resp and "results" in url_resp)
                    )
                    if not is_api:
                        return
                    if "json" not in response.headers.get("content-type", ""):
                        return
                    try:
                        data = await response.json()
                        found = extract_businesses_from_json(
                            data,
                            audit_collector=collect_xhr_audit(pass_name, url_resp),
                        )
                        for biz in found:
                            key = make_key(biz)
                            if key and key != "_":
                                xhr_businesses[key] = biz
                    except Exception as e:
                        print(f"{prefix} [{pass_name}] [WARN] on_response: {type(e).__name__}: {str(e)[:80]}")

                page.on("response", on_response)

                nav_ok = False
                for wait_until in NAV_WAIT_UNTIL_SEQUENCE:
                    try:
                        await page.goto(url, wait_until=wait_until, timeout=60_000)
                        nav_ok = True
                        print(f"{prefix} [{pass_name}] [+] Страница загружена ({wait_until})")
                        break
                    except Exception as e:
                        err_str = str(e)
                        if "Timeout" in err_str or "timeout" in err_str:
                            continue
                        print(f"{prefix} [{pass_name}] [!] Сетевая ошибка: {err_str[:100]}")
                        break

                if not nav_ok:
                    print_traffic("nav_not_ok")
                    if attempt < max_retries:
                        await asyncio.sleep(random.uniform(5, 15))
                    continue

                if await check_captcha(page):
                    print_traffic("captcha")
                    captcha_attempts += 1
                    if captcha_attempts >= CAPTCHA_MAX_RETRIES:
                        return "captcha", [], 0, 0
                    await asyncio.sleep(random.uniform(15, 30))
                    continue

                snippet_selector = None
                for sel in [
                    '[class*="search-snippet-view"]',
                    '[class*="search-snippet"]',
                    '[class*="serp-item"]',
                    '[class*="orgcard"]',
                ]:
                    try:
                        await page.wait_for_selector(sel, timeout=15_000)
                        snippet_selector = sel
                        print(f"{prefix} [{pass_name}] [+] Карточки: {sel}")
                        break
                    except Exception:
                        continue

                if not snippet_selector:
                    print_traffic("no_snippets")
                    return "done", [], len(xhr_businesses), dom_max_count

                print(
                    f"{prefix} [{pass_name}] [*] Скроллинг... | "
                    f"scroll={SCROLL_PAUSE_MIN:.2f}–{SCROLL_PAUSE_MAX:.2f}"
                )
                prev_count = 0
                no_growth_count = 0
                zero_count = 0

                for i in range(SCROLL_MAX_STEPS):
                    await page.mouse.move(200, 500)
                    await page.mouse.wheel(0, 700)
                    await asyncio.sleep(random.uniform(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX))

                    current_count = await page.evaluate(
                        f"() => document.querySelectorAll('{snippet_selector}').length"
                    )
                    if current_count > dom_max_count:
                        dom_max_count = current_count

                    if current_count == 0:
                        zero_count += 1
                        if zero_count >= EMPTY_SCROLL_LIMIT:
                            print_traffic("empty_scroll")
                            return "done", [], len(xhr_businesses), dom_max_count
                        continue

                    zero_count = 0
                    if current_count == prev_count:
                        no_growth_count += 1
                        if no_growth_count >= SCROLL_NO_GROWTH_LIMIT and i >= SCROLL_NO_GROWTH_MIN_STEP:
                            print(f"{prefix} [{pass_name}] [+] DOM: {current_count} | XHR: {len(xhr_businesses)}")
                            dom_logged = True
                            break
                    else:
                        no_growth_count = 0
                        prev_count = current_count

                if not dom_logged and dom_max_count > 0:
                    print(f"{prefix} [{pass_name}] [+] DOM: {dom_max_count} | XHR: {len(xhr_businesses)}")

                await asyncio.sleep(2.0)
                if FIELD_AUDIT_ENABLED or ENRICHED_DATA_ENABLED:
                    dom_audit = await page.evaluate(
                        """(args) => {
                            var snippets = Array.prototype.slice.call(
                                document.querySelectorAll(args.snippetSel),
                                0,
                                args.limit
                            );
                            var getText = function(root) {
                                var sels = Array.prototype.slice.call(arguments, 1);
                                for (var i = 0; i < sels.length; i++) {
                                    try {
                                        var el = root.querySelector(sels[i]);
                                        if (el && el.textContent.trim()) return el.textContent.trim();
                                    } catch (e) {}
                                }
                                return "";
                            };
                            return snippets.map(function(snippet, index) {
                                var links = Array.prototype.slice.call(snippet.querySelectorAll("a"), 0, 12)
                                    .map(function(link) {
                                        return {
                                            text: (link.textContent || "").trim(),
                                            href: link.href || ""
                                        };
                                    });
                                var imageUrls = Array.prototype.slice.call(snippet.querySelectorAll("img"), 0, 8)
                                    .map(function(img) {
                                        return img.currentSrc || img.src || "";
                                    })
                                    .filter(Boolean);
                                var title = getText(snippet, '[class*="title__text"]',
                                    '[class*="snippet__name"]', '[class*="title"]',
                                    '[class*="name"]', 'h2', 'h3');
                                var fullAddress = getText(snippet, '[class*="address__text"]',
                                    '[class*="address"]', '[class*="where"]');
                                var category = getText(snippet, '[class*="category__name"]',
                                    '[class*="rubric__text"]', '[class*="category"]',
                                    '[class*="subtitle"]');
                                var phoneLink = snippet.querySelector('a[href^="tel:"]');
                                var phone = phoneLink ? phoneLink.href.replace("tel:", "").trim()
                                    : getText(snippet, '[class*="phone"]');
                                var orgLink = "";
                                for (var i = 0; i < links.length; i++) {
                                    if (links[i].href.indexOf("/org/") !== -1) {
                                        orgLink = links[i].href;
                                        break;
                                    }
                                }
                                return {
                                    index: index,
                                    title: title,
                                    fullAddress: fullAddress,
                                    category: category,
                                    phone: phone,
                                    orgLink: orgLink,
                                    className: snippet.className || "",
                                    visibleText: (snippet.innerText || snippet.textContent || "").trim(),
                                    links: links,
                                    imageUrls: imageUrls
                                };
                            });
                        }""",
                        {
                            "snippetSel": snippet_selector,
                            "limit": FIELD_AUDIT_MAX_RECORDS_PER_CELL,
                        },
                    )
                    for item in dom_audit:
                        if ENRICHED_DATA_ENABLED:
                            merge_enriched_record(build_enriched_from_dom(item))
                        if FIELD_AUDIT_ENABLED:
                            add_audit_record(
                                "dom_snippet",
                                pass_name,
                                {
                                    "index": item.get("index"),
                                    "title": item.get("title", ""),
                                    "permalink": item.get("orgLink", ""),
                                    "dom_fields": item,
                                    "raw_preview_json": compact_json(item),
                                },
                            )

                businesses_raw = list(xhr_businesses.values())

                if len(businesses_raw) == 0:
                    js_data = await page.evaluate(
                        """() => {
                            const c = [window.__data, window.__serverState,
                                       window.__initialData, window.pageData,
                                       window.__PRELOADED_STATE__];
                            for (const x of c) if (x && typeof x==='object') return x;
                            return null;
                        }"""
                    )
                    if js_data:
                        businesses_raw = extract_businesses_from_json(
                            js_data,
                            audit_collector=collect_xhr_audit(pass_name, "window_state"),
                        )

                if len(businesses_raw) == 0:
                    scripts_content = await page.evaluate(
                        """() => {
                            const r = [];
                            document.querySelectorAll('script').forEach(s => {
                                const t = s.textContent;
                                if (t && t.length>200 &&
                                    (t.includes('"coordinates"') ||
                                     t.includes('"phones"') ||
                                     t.includes('"permalink"')))
                                    r.push(t);
                            });
                            return r;
                        }"""
                    )
                    for script_text in scripts_content:
                        try:
                            start = script_text.find("{")
                            end = script_text.rfind("}")
                            if start != -1 and end > start:
                                parsed = json.loads(script_text[start : end + 1])
                                for biz in extract_businesses_from_json(
                                    parsed,
                                    audit_collector=collect_xhr_audit(pass_name, "script_json"),
                                ):
                                    key = make_key(biz)
                                    if key and key != "_":
                                        xhr_businesses[key] = biz
                        except Exception:
                            pass
                    businesses_raw = list(xhr_businesses.values())

                if len(businesses_raw) == 0:
                    dom_results = await page.evaluate(
                        """(snippetSel) => {
                            var results=[]; var seen_ids=new Set();
                            var getText=function(root){
                                var sels=Array.prototype.slice.call(arguments,1);
                                for(var i=0;i<sels.length;i++){
                                    try{var el=root.querySelector(sels[i]);
                                        if(el&&el.textContent.trim())return el.textContent.trim();}
                                    catch(e){}
                                }
                                return "";
                            };
                            document.querySelectorAll(snippetSel).forEach(function(snippet){
                                var title=getText(snippet,'[class*="title__text"]',
                                    '[class*="snippet__name"]','[class*="title"]',
                                    '[class*="name"]','h2','h3');
                                if(!title)return;
                                var fullAddress=getText(snippet,'[class*="address__text"]',
                                    '[class*="address"]','[class*="where"]');
                                if(fullAddress&&fullAddress.toLowerCase().indexOf("санкт-петербург")===-1)
                                    fullAddress="Санкт-Петербург, "+fullAddress;
                                var category=getText(snippet,'[class*="category__name"]',
                                    '[class*="rubric__text"]','[class*="category"]',
                                    '[class*="subtitle"]');
                                var phoneLink=snippet.querySelector('a[href^="tel:"]');
                                var phone=phoneLink?phoneLink.href.replace("tel:","").trim()
                                    :getText(snippet,'[class*="phone"]');
                                var permalink="";
                                var links=snippet.querySelectorAll('a[href*="/org/"]');
                                for(var li=0;li<links.length;li++){
                                    var m=links[li].href.match(/[/]org[/][^/]+[/]([0-9]+)/);
                                    if(m){permalink=m[1];break;}
                                }
                                var dedup_key=permalink||(title+"_"+fullAddress);
                                if(seen_ids.has(dedup_key))return;
                                seen_ids.add(dedup_key);
                                results.push({title:title,shortTitle:title,
                                    fullAddress:fullAddress,categories_0_name:category,
                                    phones_0_number:phone,coordinates_0:"",coordinates_1:"",
                                    permalink:permalink,
                                    ratingData_ratingCount:"",
                                    ratingData_ratingValue:""});
                            });
                            return results;
                        }""",
                        snippet_selector,
                    )
                    for biz in dom_results:
                        key = make_key(biz)
                        if key and key != "_":
                            xhr_businesses[key] = biz
                    businesses_raw = list(xhr_businesses.values())

                print_traffic("done")
                print(f"{prefix} [{pass_name}] [Сессия] Найдено: {len(businesses_raw)}")
                return "done", businesses_raw, len(xhr_businesses), dom_max_count

            except Exception as e:
                print_traffic("error")
                print(f"{prefix} [{pass_name}] [ОШИБКА {attempt}] {type(e).__name__}: {str(e)[:120]}")
                if attempt < max_retries:
                    await asyncio.sleep(random.uniform(5, 15))

            finally:
                if page and not page.is_closed():
                    await page.close()

        return "error", [], 0, 0

    primary_status, primary_businesses, primary_xhr, primary_dom = await run_pass(
        context_primary,
        "primary",
        proxy_server_primary,
    )

    if primary_status == "captcha":
        await flush_audit_records("captcha")
        await flush_enriched_records("captcha")
        return "captcha"
    if primary_status == "error":
        await flush_audit_records("error")
        await flush_enriched_records("error")
        return "error"

    merged_businesses: dict[str, dict] = {}
    merge_businesses(merged_businesses, primary_businesses)

    need_fallback = (
        ENABLE_SAFE_FALLBACK_PASS
        and context_fallback is not None
        and (primary_xhr == 0 or len(merged_businesses) < FALLBACK_MIN_RESULTS)
    )

    if need_fallback:
        print(
            f"{prefix} [Fallback] Запуск мягкого прохода "
            f"(DOM={primary_dom}, XHR={primary_xhr}, найдено={len(merged_businesses)})"
        )
        fallback_proxy = proxy_server_fallback or proxy_server_primary
        fallback_status, fallback_businesses, _, _ = await run_pass(
            context_fallback,
            "safe",
            fallback_proxy,
        )

        if fallback_status == "done":
            before = len(merged_businesses)
            merge_businesses(merged_businesses, fallback_businesses)
            print(f"{prefix} [Fallback] Добавлено карточек: {len(merged_businesses) - before}")
        elif fallback_status == "captcha":
            print(f"{prefix} [Fallback] Капча — оставляем результаты primary")
        else:
            print(f"{prefix} [Fallback] Ошибка — оставляем результаты primary")

    saved_count = 0
    async with jsonl_lock:
        for biz in merged_businesses.values():
            title = str(biz.get("title") or "").strip()
            full_addr = str(biz.get("fullAddress") or "").strip()
            permalink_v = str(biz.get("permalink") or "").strip()
            if not title:
                continue
            oid = permalink_v or f"{title}_{full_addr}"
            if not oid or oid == "_":
                continue
            if await seen_db.is_seen(oid):
                continue
            await seen_db.mark_seen(oid)
            biz["ratingData_ratingCount"] = normalize_or_missing(
                biz.get("ratingData_ratingCount"), "отзывов нет"
            )
            biz["ratingData_ratingValue"] = normalize_or_missing(
                biz.get("ratingData_ratingValue"), "рейтинга нет"
            )
            biz["source_query"] = query
            biz["source_bbox"] = bbox
            jsonl_handle.write(json.dumps(biz, ensure_ascii=False) + "\n")
            saved_count += 1
        jsonl_handle.flush()

    print(f"{prefix} [+] Сохранено: {saved_count} | Всего в БД: {seen_db.count()}")
    await flush_audit_records("done")
    await flush_enriched_records("done")
    return "done"
