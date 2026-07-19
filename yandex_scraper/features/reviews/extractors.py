import json
import re

from patchright.async_api import Page

from yandex_scraper.features.reviews.records import first_text, review_key


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

TEXT_SORT_DEFAULT = "\u041f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e"
TEXT_SORT_NEWEST = "\u041f\u043e \u043d\u043e\u0432\u0438\u0437\u043d\u0435"
TEXT_SORT_NEWEST_VARIANTS = (
    TEXT_SORT_NEWEST,
    "\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043d\u043e\u0432\u044b\u0435",
    "\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u0432\u0435\u0436\u0438\u0435",
    "\u041d\u043e\u0432\u044b\u0435 \u0441\u043d\u0430\u0447\u0430\u043b\u0430",
    "\u0421\u0432\u0435\u0436\u0438\u0435 \u0441\u043d\u0430\u0447\u0430\u043b\u0430",
)
TEXT_SORT_TRIGGER_VARIANTS = (
    TEXT_SORT_DEFAULT,
    TEXT_SORT_NEWEST,
    "\u0421\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u043a\u0430",
)
TEXT_MORE_VARIANTS = {
    "\u0435\u0449\u0435",
    "\u0435\u0449\u0451",
}
TEXT_NOISE_LINES = {
    "\u043f\u043e\u0434\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f",
    "\u0435\u0449\u0435",
    "\u0435\u0449\u0451",
    "\u043e\u0442\u0432\u0435\u0442\u0438\u0442\u044c",
    "\u043d\u0440\u0430\u0432\u0438\u0442\u044c\u0441\u044f",
    "\u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
}
REVIEW_DATE_KEYS = (
    "date",
    "createdAt",
    "updatedAt",
    "time",
    "timestamp",
    "created",
    "updated",
    "createdTime",
    "updatedTime",
    "datePublished",
    "publishedAt",
    "publishTime",
)
REVIEW_REPLY_KEYS = (
    "reply",
    "answer",
    "answers",
    "response",
    "responses",
    "businessReply",
    "businessReplies",
    "companyAnswer",
    "officialAnswer",
    "ownerResponse",
    "ownerAnswer",
    "organizationReply",
)


def find_text_in_obj(obj, keys: tuple[str, ...]) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def find_reply_in_obj(obj: dict) -> tuple[str, str]:
    if not isinstance(obj, dict):
        return "", ""

    def find_reply_text(value, depth=0) -> str:
        if depth > 5 or value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            for item in value:
                text = find_reply_text(item, depth + 1)
                if text:
                    return text
            return ""
        if not isinstance(value, dict):
            return ""
        text = find_text_in_obj(value, ("text", "comment", "body", "description", "message"))
        if text:
            return text
        for child in value.values():
            text = find_reply_text(child, depth + 1)
            if text:
                return text
        return ""

    def find_reply_date(value, depth=0) -> str:
        if depth > 5 or value is None:
            return ""
        if isinstance(value, list):
            for item in value:
                date_text = find_reply_date(item, depth + 1)
                if date_text:
                    return date_text
            return ""
        if not isinstance(value, dict):
            return ""
        date_text = first_text(*(value.get(key) for key in REVIEW_DATE_KEYS))
        if date_text:
            return date_text
        for child in value.values():
            date_text = find_reply_date(child, depth + 1)
            if date_text:
                return date_text
        return ""

    for key, value in obj.items():
        key_lower = str(key).lower()
        if any(reply_key.lower() == key_lower or reply_key.lower() in key_lower for reply_key in REVIEW_REPLY_KEYS):
            reply_text = find_reply_text(value)
            reply_date = find_reply_date(value)
            if reply_text or reply_date:
                return reply_text, reply_date

    return "", ""


def extract_reviews_from_json(data) -> list[dict]:
    found = []
    seen = set()

    def walk(obj, depth=0):
        if depth > 14 or obj is None:
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
            return
        if not isinstance(obj, dict):
            return

        text = find_text_in_obj(obj, ("text", "comment", "body", "description"))
        author_obj = obj.get("author") or obj.get("user") or obj.get("userData") or {}
        author = ""
        if isinstance(author_obj, dict):
            author = find_text_in_obj(author_obj, ("name", "displayName", "title"))
        author = author or find_text_in_obj(obj, ("authorName", "userName", "name"))
        rating = first_text(obj.get("rating"), obj.get("ratingValue"), obj.get("stars"))
        date_value = first_text(*(obj.get(key) for key in REVIEW_DATE_KEYS))
        reply_text, reply_date = find_reply_in_obj(obj)
        review_id = first_text(obj.get("id"), obj.get("reviewId"), obj.get("uid"))

        if text and (author or rating or review_id):
            record = {
                "review_id": review_id,
                "author_name": author,
                "rating": rating,
                "date": date_value,
                "text": text,
                "likes": first_text(obj.get("likes"), obj.get("likesCount"), obj.get("usefulCount")),
                "has_organization_reply": bool(reply_text or reply_date),
                "organization_reply_text": reply_text,
                "organization_reply_date": reply_date,
            }
            key = review_key(record)
            if key not in seen:
                seen.add(key)
                found.append(record)

        for value in obj.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    walk(data)
    return found


async def select_reviews_sort(page: Page, sort_mode: str) -> bool:
    if sort_mode != "newest":
        return False

    newest_re = re.compile("|".join(re.escape(text) for text in TEXT_SORT_NEWEST_VARIANTS), re.IGNORECASE)
    trigger_re = re.compile(
        "|".join(re.escape(text) for text in TEXT_SORT_TRIGGER_VARIANTS)
        + r"|\b(sort|newest|latest)\b",
        re.IGNORECASE,
    )
    target_closed = False

    def is_target_closed_error(exc: Exception) -> bool:
        text = str(exc)
        return (
            "TargetClosedError" in type(exc).__name__
            or "Target page, context or browser has been closed" in text
        )

    def mark_target_closed(exc: Exception) -> None:
        nonlocal target_closed
        if is_target_closed_error(exc):
            target_closed = True

    def page_is_closed() -> bool:
        try:
            return page.is_closed()
        except Exception:
            return True

    async def is_visible(locator, index: int) -> bool:
        if page_is_closed():
            return False
        try:
            return await locator.nth(index).is_visible(timeout=500)
        except Exception as exc:
            mark_target_closed(exc)
            return False

    async def click_first_visible(
        locator,
        *,
        from_end: bool = False,
        max_candidates: int = 40,
        force: bool = False,
    ) -> bool:
        if page_is_closed():
            return False
        try:
            count = await locator.count()
        except Exception as exc:
            mark_target_closed(exc)
            return False
        if from_end:
            indexes = range(count - 1, max(-1, count - max_candidates - 1), -1)
        else:
            indexes = range(0, min(count, max_candidates))
        for index in indexes:
            if target_closed or page_is_closed():
                return False
            if not await is_visible(locator, index):
                continue
            try:
                await locator.nth(index).click(timeout=5_000, force=force)
                return True
            except Exception as exc:
                mark_target_closed(exc)
                if target_closed:
                    return False
                continue
        return False

    async def current_sort_is_newest() -> bool:
        if page_is_closed():
            return False
        try:
            return bool(
                await page.evaluate(
                    """(variants) => {
                        const normalizedVariants = variants.map((value) => String(value).trim().toLowerCase());
                        const selectors = [
                            '[class*="rating-ranking-view"]:not([class*="popup-line"])',
                            '[aria-haspopup]:not([class*="popup-line"])',
                            '[aria-expanded]:not([class*="popup-line"])',
                            '[class*="ranking"]:not([class*="popup-line"])',
                            '[class*="sort"]:not([class*="popup-line"])'
                        ];
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.visibility !== 'hidden'
                                && style.display !== 'none'
                                && rect.width > 0
                                && rect.height > 0;
                        };
                        for (const selector of selectors) {
                            for (const el of document.querySelectorAll(selector)) {
                                if (!isVisible(el)) continue;
                                if (el.closest('[role="dialog"]')) continue;
                                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                                if (normalizedVariants.includes(text)) return true;
                            }
                        }
                        return false;
                    }""",
                    list(TEXT_SORT_NEWEST_VARIANTS),
                )
            )
        except Exception as exc:
            mark_target_closed(exc)
            return False

    async def sort_text_samples() -> list[str]:
        if page_is_closed():
            return []
        try:
            return await page.evaluate(
                """(markers) => {
                    const normalizedMarkers = markers.map((value) => String(value).trim().toLowerCase());
                    const selectors = 'button, [role="button"], [role="menuitem"], [role="option"], [aria-haspopup], [aria-expanded], [class*="ranking"], [class*="sort"]';
                    const values = [];
                    const isVisible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    for (const el of document.querySelectorAll(selectors)) {
                        if (!isVisible(el)) continue;
                        const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                            .trim()
                            .replace(/\\s+/g, ' ');
                        if (!text) continue;
                        const lower = text.toLowerCase();
                        if (normalizedMarkers.some((marker) => lower.includes(marker))) {
                            values.push(text.slice(0, 120));
                        }
                    }
                    return Array.from(new Set(values)).slice(0, 8);
                }""",
                ["\u043d\u043e\u0432", "\u0441\u043e\u0440\u0442", "\u0443\u043c\u043e\u043b\u0447", "sort", "new"],
            )
        except Exception as exc:
            mark_target_closed(exc)
            return []

    async def clickable_text_points(
        variants: tuple[str, ...],
        *,
        prefer_clickable_parent: bool,
        limit: int = 8,
    ) -> list[dict]:
        if page_is_closed():
            return []
        try:
            return await page.evaluate(
                """(args) => {
                    const variants = args.variants.map((value) => String(value).trim().toLowerCase());
                    const normalize = (value) => String(value || '').trim().replace(/\\s+/g, ' ').toLowerCase();
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 0
                            && rect.height > 0
                            && rect.bottom >= 0
                            && rect.top <= window.innerHeight
                            && rect.right >= 0
                            && rect.left <= window.innerWidth;
                    };
                    const textMatches = (text) => {
                        for (const variant of variants) {
                            if (text === variant) return variant;
                            if (text.includes(variant) && text.length <= Math.max(60, variant.length + 30)) {
                                return variant;
                            }
                        }
                        return '';
                    };
                    const isClickableLike = (el) => {
                        const tag = String(el.tagName || '').toLowerCase();
                        const role = String(el.getAttribute('role') || '').toLowerCase();
                        const className = String(el.getAttribute('class') || '');
                        const cursor = window.getComputedStyle(el).cursor;
                        return tag === 'button'
                            || tag === 'a'
                            || ['button', 'menuitem', 'option'].includes(role)
                            || el.hasAttribute('aria-haspopup')
                            || el.hasAttribute('aria-expanded')
                            || cursor === 'pointer'
                            || /(ranking|sort|select|dropdown|popup|menu)/i.test(className)
                            || Boolean(el.onclick);
                    };
                    const clickableTarget = (el) => {
                        if (!args.preferClickableParent) return el;
                        let current = el;
                        for (let depth = 0; current && current !== document.body && depth < 7; depth += 1) {
                            if (!isVisible(current)) {
                                current = current.parentElement;
                                continue;
                            }
                            const text = normalize(current.innerText || current.textContent || '');
                            const rect = current.getBoundingClientRect();
                            if (
                                textMatches(text)
                                && isClickableLike(current)
                                && rect.width <= 360
                                && rect.height <= 120
                            ) {
                                return current;
                            }
                            current = current.parentElement;
                        }
                        return el;
                    };
                    const selectors = [
                        'button',
                        'a',
                        '[role="button"]',
                        '[role="menuitem"]',
                        '[role="option"]',
                        '[aria-haspopup]',
                        '[aria-expanded]',
                        '[class*="rating-ranking-view"]',
                        '[class*="ranking"]',
                        '[class*="sort"]',
                        'span',
                        'div'
                    ].join(',');
                    const candidates = [];
                    for (const el of document.querySelectorAll(selectors)) {
                        if (!isVisible(el)) continue;
                        const ownText = normalize(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                        const matchedVariant = textMatches(ownText);
                        if (!matchedVariant) continue;
                        const target = clickableTarget(el);
                        if (!isVisible(target)) continue;
                        const rect = target.getBoundingClientRect();
                        if (rect.width > 420 || rect.height > 140) continue;
                        const targetText = normalize(target.innerText || target.textContent || '');
                        candidates.push({
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            left: rect.left,
                            top: rect.top,
                            width: rect.width,
                            height: rect.height,
                            text: ownText.slice(0, 80),
                            targetText: targetText.slice(0, 80),
                            tagName: String(target.tagName || '').toLowerCase(),
                            className: String(target.getAttribute('class') || '').slice(0, 120),
                            exact: ownText === matchedVariant,
                            clickable: isClickableLike(target),
                            cursor: window.getComputedStyle(target).cursor,
                            area: rect.width * rect.height
                        });
                    }
                    candidates.sort((left, right) => {
                        if (left.clickable !== right.clickable) return left.clickable ? -1 : 1;
                        if (left.exact !== right.exact) return left.exact ? -1 : 1;
                        return left.area - right.area;
                    });
                    const seen = new Set();
                    const unique = [];
                    for (const candidate of candidates) {
                        const key = [
                            Math.round(candidate.x),
                            Math.round(candidate.y),
                            candidate.tagName,
                            candidate.targetText
                        ].join('|');
                        if (seen.has(key)) continue;
                        seen.add(key);
                        unique.push(candidate);
                        if (unique.length >= args.limit) break;
                    }
                    return unique;
                }""",
                {
                    "variants": list(variants),
                    "preferClickableParent": prefer_clickable_parent,
                    "limit": max(1, limit),
                },
            )
        except Exception as exc:
            mark_target_closed(exc)
            return []

    async def click_text_point(point: dict | None, label: str, index: int = 0) -> bool:
        if not point or page_is_closed():
            return False
        try:
            await page.mouse.click(float(point["x"]), float(point["y"]))
            text = first_text(point.get("targetText"), point.get("text"))
            tag = first_text(point.get("tagName"))
            cursor = first_text(point.get("cursor"))
            class_name = first_text(point.get("className"))
            x = int(float(point.get("x") or 0))
            y = int(float(point.get("y") or 0))
            print(
                f"[Reviews] Sort fallback -> {label}[{index}]: clicked "
                f"text={text[:80]} tag={tag} cursor={cursor} xy={x},{y} class={class_name[:80]}"
            )
            return True
        except Exception as exc:
            mark_target_closed(exc)
            return False

    async def click_sort_trigger_by_dom() -> bool:
        points = await clickable_text_points(TEXT_SORT_TRIGGER_VARIANTS, prefer_clickable_parent=True, limit=10)
        for index, point in enumerate(points):
            if target_closed or page_is_closed():
                return False
            if not await click_text_point(point, "trigger", index):
                continue
            await page.wait_for_timeout(800)
            if await click_newest_option():
                return True
            if await current_sort_is_newest():
                print("[Reviews] Sort -> newest: ok")
                return True
        return False

    async def click_newest_option_by_dom() -> bool:
        points = await clickable_text_points(TEXT_SORT_NEWEST_VARIANTS, prefer_clickable_parent=True, limit=10)
        for index, point in enumerate(points):
            if target_closed or page_is_closed():
                return False
            if not await click_text_point(point, "newest option", index):
                continue
            await page.wait_for_timeout(1_500)
            if await current_sort_is_newest():
                print("[Reviews] Sort -> newest: ok")
                return True
            print("[Reviews] Sort -> newest: DOM fallback clicked, but current label was not confirmed")
        return False

    async def click_newest_option() -> bool:
        option_locators = [
            page.locator('[class*="rating-ranking-view__popup-line"]').filter(has_text=TEXT_SORT_NEWEST),
            page.locator('[class*="rating-ranking-view__popup-line"]').filter(has_text=newest_re),
        ]
        for variant in TEXT_SORT_NEWEST_VARIANTS:
            option_locators.extend(
                [
                    page.get_by_text(variant, exact=True),
                    page.locator('[class*="rating-ranking-view__popup-line"]').filter(has_text=variant),
                    page.locator("button").filter(has_text=variant),
                    page.locator('[role="menuitem"]').filter(has_text=variant),
                    page.locator('[role="option"]').filter(has_text=variant),
                    page.locator('[role="button"]').filter(has_text=variant),
                ]
            )
        option_locators.extend(
            [
                page.locator("button").filter(has_text=newest_re),
                page.locator('[role="menuitem"]').filter(has_text=newest_re),
                page.locator('[role="option"]').filter(has_text=newest_re),
                page.locator('[role="button"]').filter(has_text=newest_re),
            ]
        )

        for option in option_locators:
            if target_closed or page_is_closed():
                return False
            if await click_first_visible(option, from_end=True, force=True):
                await page.wait_for_timeout(1_500)
                if await current_sort_is_newest():
                    print("[Reviews] Sort -> newest: ok")
                    return True
                print("[Reviews] Sort -> newest: clicked, but current label was not confirmed")
                return False
        return await click_newest_option_by_dom()

    try:
        if await current_sort_is_newest():
            print("[Reviews] Sort -> newest: already selected")
            return True

        triggers = [
            (page.get_by_text(TEXT_SORT_DEFAULT, exact=True), False),
            (page.locator("button").filter(has_text=trigger_re), False),
            (page.locator('[role="button"]:not([class*="popup-line"])').filter(has_text=trigger_re), False),
            (page.locator('[aria-haspopup]:not([class*="popup-line"])').filter(has_text=trigger_re), False),
            (page.locator('[aria-expanded]:not([class*="popup-line"])').filter(has_text=trigger_re), False),
            (page.locator('[class*="ranking"]:not([class*="popup-line"])').filter(has_text=trigger_re), False),
            (page.locator('[class*="sort"]:not([class*="popup-line"])').filter(has_text=trigger_re), False),
            (page.get_by_text(TEXT_SORT_NEWEST, exact=True), False),
            (page.locator('[class*="rating-ranking-view"]:not([class*="popup-line"])'), True),
        ]
        for trigger, force_click in triggers:
            if target_closed or page_is_closed():
                break
            try:
                trigger_count = await trigger.count()
            except Exception as exc:
                mark_target_closed(exc)
                break
            if trigger_count == 0:
                continue
            if not await click_first_visible(trigger, force=force_click):
                continue
            await page.wait_for_timeout(800)
            if await click_newest_option():
                return True
            if await current_sort_is_newest():
                print("[Reviews] Sort -> newest: ok")
                return True

        if not target_closed and not page_is_closed() and await click_sort_trigger_by_dom():
            return True

        if target_closed or page_is_closed():
            print("[Reviews] [WARN] Sort selection stopped because page/context/browser was closed")
            return False

        samples = await sort_text_samples()
        sample_text = f" candidates={samples}" if samples else ""
        print(f"[Reviews] [WARN] Sort menu/newest option was not confirmed{sample_text}")
        return False
    except Exception as exc:
        print(f"[Reviews] [WARN] Could not select newest sort: {type(exc).__name__}: {str(exc)[:100]}")
        return False


async def extract_reviews_from_dom(page: Page, limit: int) -> list[dict]:
    js_months = "|".join(RU_MONTH_NAMES)
    js_noise = json.dumps(sorted(TEXT_NOISE_LINES), ensure_ascii=False)
    js_relative_dates = [
        "\u0441\u0435\u0433\u043e\u0434\u043d\u044f",
        "\u0432\u0447\u0435\u0440\u0430",
    ]
    return await page.evaluate(
        """(args) => {
            const limit = args.limit;
            const monthPattern = args.monthPattern;
            const noiseLines = new Set(args.noiseLines);
            const relativeDatePattern = args.relativeDateWords.join('|');
            const seen = new Set();
            const selectors = [
                '[class*="business-review-view"]',
                '[class*="business-reviews-card-view"]',
                '[class*="review-snippet"]'
            ];
            const reviewDatePattern = [
                relativeDatePattern,
                '\\\\d{1,2}\\\\s+(?:' + monthPattern + ')(?:\\\\s+\\\\d{4})?',
                '\\\\d{1,2}[.\\\\-/]\\\\d{1,2}(?:[.\\\\-/]\\\\d{2,4})?',
                '\\\\d{4}-\\\\d{2}-\\\\d{2}'
            ].join('|');
            const dateRe = new RegExp('(' + reviewDatePattern + ')', 'i');
            const dateLineRe = new RegExp('^\\\\s*(' + reviewDatePattern + ')(?:\\\\s*$|[\\\\s,.;])', 'i');
            const monthOnlyRe = new RegExp('^(' + monthPattern + ')$', 'i');
            const markerRe = /(ответ\\s+(владельца|организации|представителя)|официальный\\s+ответ|комментарий\\s+владельца|owner.?s\\s+reply|response\\s+from\\s+owner|official\\s+response)/i;
            const nodes = [];
            for (const selector of selectors) {
                document.querySelectorAll(selector).forEach((node) => {
                    const text = (node.innerText || node.textContent || '').trim();
                    if (!text || text.length < 40 || seen.has(text)) return;
                    if (!dateRe.test(text)) return;
                    seen.add(text);
                    nodes.push(node);
                });
                if (nodes.length) break;
            }

            const getText = (root, selList) => {
                for (const sel of selList) {
                    try {
                        const el = root.querySelector(sel);
                        const text = el && (el.innerText || el.textContent || '').trim();
                        if (text) return text;
                    } catch (e) {}
                }
                return '';
            };
            const meaningfulAuthorChars = (value) => String(value || '')
                .replace(/[^0-9A-Za-zА-Яа-яЁё]+/g, '');
            const authorMetaRe = /(знаток|отзыв|оцен|фото|местн|эксперт|level|reviews?|ratings?|photos?)/i;
            const bestAuthorLine = (value, allowShort = false) => {
                const lines = cleanText(value)
                    .split('\\n')
                    .map((line) => line.trim())
                    .filter(Boolean);
                let shortFallback = '';
                for (const line of lines) {
                    const lower = line.toLowerCase();
                    if (noiseLines.has(lower)) continue;
                    if (authorMetaRe.test(line)) continue;
                    const meaningful = meaningfulAuthorChars(line);
                    if (Array.from(meaningful).length <= 2) {
                        if (!shortFallback) shortFallback = line;
                        continue;
                    }
                    return line;
                }
                return allowShort ? shortFallback : '';
            };
            const authorFrom = (root) => {
                const preferredSelectors = [
                    'a[href*="/user/"] [class*="name"]',
                    '[class*="business-review-view__author-name"]',
                    '[class*="business-review-view__user-name"]',
                    '[class*="review"] [class*="author"] [class*="name"]',
                    '[class*="user-name"]',
                    'a[href*="/user/"]'
                ];
                for (const sel of preferredSelectors) {
                    try {
                        for (const el of Array.from(root.querySelectorAll(sel))) {
                            const candidate = bestAuthorLine(el.innerText || el.textContent || '');
                            if (candidate) return candidate;
                        }
                    } catch (e) {}
                }

                let fallback = '';
                const containerSelectors = [
                    '[class*="business-review-view__author"]',
                    '[class*="business-review-view__user"]',
                    '[class*="author"]'
                ];
                for (const sel of containerSelectors) {
                    try {
                        for (const el of Array.from(root.querySelectorAll(sel))) {
                            const raw = el.innerText || el.textContent || '';
                            const candidate = bestAuthorLine(raw);
                            if (candidate) return candidate;
                            if (!fallback) fallback = bestAuthorLine(raw, true);
                        }
                    } catch (e) {}
                }
                return fallback;
            };
            const ratingFrom = (root) => {
                const candidates = Array.from(root.querySelectorAll('[aria-label],[title]')).slice(0, 40);
                for (const el of candidates) {
                    const text = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;
                    const match = text.match(/([1-5](?:[,.]\\d)?)/);
                    if (match && /(оцен|звезд|rating|star)/i.test(text)) return match[1].replace(',', '.');
                }
                return '';
            };
            const reviewIdFrom = (root) => {
                let node = root;
                while (node && node !== document.body) {
                    for (const attr of ['data-review-id', 'data-reviewid']) {
                        const value = node.getAttribute && node.getAttribute(attr);
                        if (value && String(value).trim()) return String(value).trim();
                    }
                    node = node.parentElement;
                }
                return '';
            };
            const cleanText = (value) => String(value || '')
                .split('\\n')
                .map((line) => line.trim())
                .filter(Boolean)
                .filter((line) => !noiseLines.has(line.toLowerCase()))
                .join('\\n')
                .trim();
            const ownReviewLines = (visible) => {
                const lines = cleanText(visible).split('\\n').filter(Boolean);
                const markerIndex = lines.findIndex((line) => markerRe.test(line));
                return markerIndex >= 0 ? lines.slice(0, markerIndex) : lines;
            };
            const isReplyElement = (root, el) => {
                let node = el;
                while (node && node !== root) {
                    const classText = String(node.getAttribute && node.getAttribute('class') || '');
                    if (/(reply|answer|response)/i.test(classText)) return true;
                    const text = (node.innerText || node.textContent || '').trim().split('\\n').slice(0, 2).join(' ');
                    if (markerRe.test(text)) return true;
                    node = node.parentElement;
                }
                return false;
            };
            const reviewDateFrom = (root, visible) => {
                const explicitCandidates = Array.from(root.querySelectorAll(
                    'time, [datetime], [class*="date"], [class*="Date"]'
                ));
                for (const el of explicitCandidates) {
                    if (isReplyElement(root, el)) continue;
                    const raw = [
                        el.getAttribute && el.getAttribute('datetime'),
                        el.getAttribute && el.getAttribute('content'),
                        el.getAttribute && el.getAttribute('title'),
                        el.innerText || el.textContent || ''
                    ].filter(Boolean).join('\\n');
                    const match = raw.match(dateRe);
                    if (match) return match[1].trim();
                }

                for (const line of ownReviewLines(visible)) {
                    const match = line.match(dateLineRe);
                    if (match) return match[1].trim();
                }
                return '';
            };
            const reviewTextFrom = (root, visible) => {
                const direct = getText(root, [
                    '[class*="business-review-view__body"]',
                    '[class*="business-review-view__text"]',
                    '[class*="review-snippet-view__body"]',
                    '[class*="review-snippet-view__text"]',
                    '[class*="review"] [class*="text"]',
                    '[class*="comment"]',
                ]);
                if (direct && direct.length > 15) return cleanText(direct);

                const lines = ownReviewLines(visible);
                const candidates = lines.filter((line) => {
                    const lower = line.toLowerCase();
                    if (noiseLines.has(lower)) return false;
                    if (monthOnlyRe.test(line)) return false;
                    if (dateLineRe.test(line)) return false;
                    if (/^[1-5]$/.test(line)) return false;
                    return line.length > 20;
                });
                return candidates.join('\\n').trim();
            };
            const replyFrom = (root, visible) => {
                const direct = getText(root, [
                    '[class*="reply"]',
                    '[class*="Reply"]',
                    '[class*="answer"]',
                    '[class*="Answer"]',
                    '[class*="response"]',
                    '[class*="Response"]',
                    '[class*="owner"]',
                    '[class*="Owner"]',
                ]);
                const replyDateRe = dateLineRe;
                const cleanReplyLines = (value) => cleanText(value)
                    .split('\\n')
                    .map((line) => line.trim())
                    .filter(Boolean)
                    .filter((line) => !/^ответить$/i.test(line));
                const directLines = cleanReplyLines(direct);
                if (directLines.length && markerRe.test(directLines.join('\\n'))) {
                    const markerIndex = directLines.findIndex((line) => markerRe.test(line));
                    const replyLines = directLines.slice(markerIndex + 1);
                    let replyDate = '';
                    if (replyLines.length && replyDateRe.test(replyLines[0])) {
                        replyDate = replyLines.shift();
                    }
                    return {text: replyLines.join('\\n').trim(), date: replyDate};
                }
                const lines = cleanReplyLines(visible);
                const markerIndex = lines.findIndex((line) => markerRe.test(line));
                if (markerIndex < 0) return {text: '', date: ''};
                const replyLines = lines.slice(markerIndex + 1);
                let replyDate = '';
                if (replyLines.length && replyDateRe.test(replyLines[0])) {
                    replyDate = replyLines.shift();
                }
                const stopIndex = replyLines.findIndex((line, index) => {
                    if (index < 1) return false;
                    if (replyDateRe.test(line)) return true;
                    return /подписаться|нравится|комментировать/i.test(line);
                });
                const textLines = stopIndex >= 0 ? replyLines.slice(0, stopIndex) : replyLines;
                return {text: textLines.join('\\n').trim(), date: replyDate};
            };
            return nodes.slice(0, limit).map((node) => {
                const visible = (node.innerText || node.textContent || '').trim();
                const text = reviewTextFrom(node, visible);
                const reply = replyFrom(node, visible);
                const reviewDate = reviewDateFrom(node, visible);
                return {
                    review_id: reviewIdFrom(node),
                    author_name: authorFrom(node),
                    rating: ratingFrom(node),
                    date: reviewDate,
                    text: text,
                    likes: getText(node, ['[class*="useful"]', '[class*="like"]']),
                    has_organization_reply: Boolean(reply.text || reply.date),
                    organization_reply_text: reply.text,
                    organization_reply_date: reply.date,
                };
            }).filter((item) => item.text && item.text.length > 20 && item.text.toLowerCase() !== 'подписаться');
        }""",
        {
            "limit": limit,
            "monthPattern": js_months,
            "noiseLines": json.loads(js_noise),
            "relativeDateWords": js_relative_dates,
        },
    )


async def scroll_reviews_container(page: Page, *, delta: int = 1400) -> dict:
    try:
        return await page.evaluate(
            """async (args) => {
                const delta = args.delta;
                const reviewSelectors = [
                    '[class*="business-review-view"]',
                    '[class*="business-reviews-card-view"]',
                    '[class*="review-snippet"]'
                ];
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0
                        && rect.bottom >= 0
                        && rect.top <= window.innerHeight
                        && rect.right >= 0
                        && rect.left <= window.innerWidth;
                };
                const isScrollable = (el) => {
                    if (!el) return false;
                    return el.scrollHeight > el.clientHeight + 20;
                };
                const overflowScore = (el) => {
                    const overflowY = window.getComputedStyle(el).overflowY;
                    return /(auto|scroll|overlay)/i.test(overflowY) ? 25 : 0;
                };
                const candidates = [];
                const seen = new Set();
                const addCandidate = (el, reason) => {
                    if (!el || seen.has(el) || !isScrollable(el)) return;
                    seen.add(el);
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 250 || rect.height < 160) return;
                    const containsReview = reviewSelectors.some((selector) => Boolean(el.querySelector(selector)));
                    const inLeftPane = rect.left < window.innerWidth * 0.72 && rect.right <= window.innerWidth * 0.82;
                    const score = (
                        (containsReview ? 120 : 0)
                        + (inLeftPane ? 60 : 0)
                        + overflowScore(el)
                        + Math.min(80, Math.max(0, el.scrollHeight - el.clientHeight) / 80)
                        + Math.min(30, rect.height / 30)
                    );
                    candidates.push({
                        el,
                        reason,
                        score,
                        left: rect.left,
                        top: rect.top,
                        width: rect.width,
                        height: rect.height,
                        before: el.scrollTop,
                        maxScroll: el.scrollHeight - el.clientHeight,
                        className: String(el.getAttribute('class') || '').slice(0, 140),
                        tagName: String(el.tagName || '').toLowerCase()
                    });
                };

                const reviewRoots = [];
                for (const selector of reviewSelectors) {
                    document.querySelectorAll(selector).forEach((node) => reviewRoots.push(node));
                    if (reviewRoots.length) break;
                }
                for (const root of reviewRoots.slice(0, 8)) {
                    let node = root;
                    for (let depth = 0; node && node !== document.body && depth < 12; depth += 1) {
                        addCandidate(node, 'review_ancestor');
                        node = node.parentElement;
                    }
                }
                if (!candidates.length) {
                    document.querySelectorAll('div, section, main, aside').forEach((node) => {
                        if (!isVisible(node)) return;
                        addCandidate(node, 'visible_scrollable');
                    });
                }
                if (!candidates.length) {
                    return {moved: false, reason: 'no_scrollable_container', reviewNodes: reviewRoots.length};
                }

                candidates.sort((left, right) => right.score - left.score);
                const candidate = candidates[0];
                const el = candidate.el;
                const rect = el.getBoundingClientRect();
                const before = el.scrollTop;
                const target = Math.min(candidate.maxScroll, before + delta);
                el.scrollTop = target;
                el.dispatchEvent(new Event('scroll', {bubbles: true}));
                el.dispatchEvent(new WheelEvent('wheel', {deltaY: delta, bubbles: true, cancelable: true}));
                await sleep(250);
                const after = el.scrollTop;
                return {
                    moved: Math.abs(after - before) > 2,
                    reason: candidate.reason,
                    before,
                    after,
                    maxScroll: candidate.maxScroll,
                    reviewNodes: reviewRoots.length,
                    x: rect.left + Math.min(rect.width - 20, Math.max(20, rect.width / 2)),
                    y: rect.top + Math.min(rect.height - 20, Math.max(20, rect.height * 0.72)),
                    tagName: candidate.tagName,
                    className: candidate.className,
                    candidates: candidates.length
                };
            }""",
            {"delta": delta},
        )
    except Exception as exc:
        print(f"[Reviews] [WARN] Could not scroll reviews container: {type(exc).__name__}: {str(exc)[:100]}")
        return {"moved": False, "reason": f"{type(exc).__name__}: {str(exc)[:100]}"}


async def get_expected_reviews_count(page: Page) -> int | None:
    try:
        value = await page.evaluate(
            """() => {
                const selectors = [
                    'h1', 'h2', 'h3',
                    'button',
                    '[role="button"]',
                    '[class*="tabs"]',
                    '[class*="tab"]',
                    '[class*="review"]',
                    'span',
                    'div'
                ].join(',');
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const counts = [];
                const re = /(?:^|\\D)(\\d[\\d\\s]{0,8})\\s*(?:отзыв|отзыва|отзывов)(?:\\D|$)/gi;
                for (const el of document.querySelectorAll(selectors)) {
                    if (!isVisible(el)) continue;
                    const text = String(el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    if (!text || text.length > 160) continue;
                    let match;
                    while ((match = re.exec(text)) !== null) {
                        const count = Number(String(match[1] || '').replace(/\\s+/g, ''));
                        if (Number.isFinite(count) && count > 0) counts.push(count);
                    }
                }
                return counts.length ? Math.max(...counts) : null;
            }"""
        )
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    except Exception as exc:
        print(f"[Reviews] [WARN] Could not read expected reviews count: {type(exc).__name__}: {str(exc)[:100]}")
    return None


async def click_reviews_load_more(page: Page) -> dict:
    try:
        return await page.evaluate(
            """async () => {
                const reviewSelectors = [
                    '[class*="business-review-view"]',
                    '[class*="business-reviews-card-view"]',
                    '[class*="review-snippet"]'
                ];
                const triggerRe = /(показать\\s+ещ[её]|ещ[её]\\s+отзыв|загрузить\\s+ещ[её]|показать\\s+все|show\\s+more|load\\s+more|more\\s+reviews)/i;
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0
                        && rect.bottom >= 0
                        && rect.top <= window.innerHeight
                        && rect.right >= 0
                        && rect.left <= window.innerWidth;
                };
                const isReviewRoot = (el) => {
                    return reviewSelectors.some((selector) => Boolean(el.closest(selector)));
                };
                const isClickableLike = (el) => {
                    const tag = String(el.tagName || '').toLowerCase();
                    const role = String(el.getAttribute('role') || '').toLowerCase();
                    const cursor = window.getComputedStyle(el).cursor;
                    const className = String(el.getAttribute('class') || '');
                    return tag === 'button'
                        || tag === 'a'
                        || ['button', 'menuitem', 'option'].includes(role)
                        || cursor === 'pointer'
                        || /(button|link|more|loader|pager|pagination)/i.test(className)
                        || Boolean(el.onclick);
                };
                const candidates = [];
                const selectors = [
                    'button',
                    'a',
                    '[role="button"]',
                    '[class*="button"]',
                    '[class*="more"]',
                    '[class*="loader"]',
                    '[class*="pager"]',
                    '[class*="pagination"]',
                    'span',
                    'div'
                ].join(',');
                for (const el of document.querySelectorAll(selectors)) {
                    if (!isVisible(el)) continue;
                    if (isReviewRoot(el)) continue;
                    const text = String(el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    if (!text || text.length > 100 || !triggerRe.test(text)) continue;
                    let target = el;
                    for (let node = el; node && node !== document.body; node = node.parentElement) {
                        if (!isVisible(node)) continue;
                        const nodeText = String(node.innerText || node.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        const rect = node.getBoundingClientRect();
                        if (triggerRe.test(nodeText) && isClickableLike(node) && rect.width <= 460 && rect.height <= 140) {
                            target = node;
                            break;
                        }
                    }
                    const rect = target.getBoundingClientRect();
                    candidates.push({
                        el: target,
                        text,
                        y: rect.top + rect.height / 2,
                        x: rect.left + rect.width / 2,
                        bottomDistance: Math.abs(window.innerHeight - (rect.top + rect.height / 2)),
                        clickable: isClickableLike(target),
                        tagName: String(target.tagName || '').toLowerCase(),
                        className: String(target.getAttribute('class') || '').slice(0, 120)
                    });
                }
                candidates.sort((left, right) => {
                    if (left.clickable !== right.clickable) return left.clickable ? -1 : 1;
                    return left.bottomDistance - right.bottomDistance;
                });
                const candidate = candidates[0];
                if (!candidate) {
                    return {clicked: false, reason: 'no_load_more_candidate', candidates: 0};
                }
                candidate.el.scrollIntoView({block: 'center', inline: 'nearest'});
                await sleep(120);
                candidate.el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                candidate.el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                candidate.el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                candidate.el.click();
                await sleep(900);
                return {
                    clicked: true,
                    reason: 'clicked',
                    text: candidate.text.slice(0, 100),
                    tagName: candidate.tagName,
                    className: candidate.className,
                    x: Math.round(candidate.x),
                    y: Math.round(candidate.y),
                    candidates: candidates.length
                };
            }"""
        )
    except Exception as exc:
        print(f"[Reviews] [WARN] Could not click reviews load-more: {type(exc).__name__}: {str(exc)[:100]}")
        return {"clicked": False, "reason": f"{type(exc).__name__}: {str(exc)[:100]}"}


async def expand_visible_reviews(page: Page, *, max_clicks: int = 20) -> int:
    try:
        return await page.evaluate(
            """async (args) => {
                const maxClicks = args.maxClicks;
                const normalize = (value) => String(value || '')
                    .replace(/\\u00a0/g, ' ')
                    .trim()
                    .replace(/\\s+/g, ' ')
                    .toLowerCase()
                    .replace(/\\u0451/g, '\\u0435');
                const moreTexts = new Set(args.moreTexts.map(normalize));
                const reviewSelectors = [
                    '[class*="business-review-view"]',
                    '[class*="business-reviews-card-view"]',
                    '[class*="review-snippet"]'
                ];
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0
                        && rect.height > 0
                        && rect.bottom >= 0
                        && rect.top <= window.innerHeight;
                };
                const isMoreText = (value) => {
                    const text = normalize(value);
                    return moreTexts.has(text) || text === 'show more' || text === 'read more';
                };
                const hasMoreMarker = (el) => {
                    const values = [
                        el.innerText || '',
                        el.textContent || '',
                        el.getAttribute && el.getAttribute('aria-label'),
                        el.getAttribute && el.getAttribute('title')
                    ];
                    for (const value of values) {
                        if (isMoreText(value)) return true;
                    }
                    const lines = String(el.innerText || el.textContent || '')
                        .split('\\n')
                        .map(normalize)
                        .filter(Boolean);
                    return lines.some((line) => moreTexts.has(line));
                };
                const isClickableLike = (el) => {
                    if (!el) return false;
                    const tag = String(el.tagName || '').toLowerCase();
                    const role = String(el.getAttribute('role') || '').toLowerCase();
                    const className = String(el.getAttribute('class') || '');
                    const cursor = window.getComputedStyle(el).cursor;
                    return tag === 'button'
                        || tag === 'a'
                        || role === 'button'
                        || cursor === 'pointer'
                        || /(button|link|more|expand|spoiler|collapsed)/i.test(className)
                        || Boolean(el.onclick);
                };
                const clickTargetFor = (el, root) => {
                    let fallback = el;
                    for (let node = el; node && node !== root && node !== document.body; node = node.parentElement) {
                        if (!isVisible(node)) continue;
                        const rect = node.getBoundingClientRect();
                        if (!fallback) fallback = node;
                        if (isClickableLike(node) && rect.width <= 520 && rect.height <= 180) {
                            return node;
                        }
                    }
                    return fallback;
                };
                const collectReviewRoots = () => {
                    const roots = [];
                    const seen = new Set();
                    for (const selector of reviewSelectors) {
                        document.querySelectorAll(selector).forEach((node) => {
                            if (!isVisible(node) || seen.has(node)) return;
                            seen.add(node);
                            roots.push(node);
                        });
                        if (roots.length) break;
                    }
                    return roots;
                };
                const collectCandidates = (attempted) => {
                    const candidates = [];
                    for (const root of collectReviewRoots()) {
                        const elements = Array.from(root.querySelectorAll(
                            'button, a, [role="button"], [tabindex], span, div, p'
                        ));
                        for (const el of elements) {
                            if (!isVisible(el) || !hasMoreMarker(el)) continue;
                            const target = clickTargetFor(el, root);
                            if (!target || attempted.has(target)) continue;
                            const markerRect = el.getBoundingClientRect();
                            const targetRect = target.getBoundingClientRect();
                            if (targetRect.width > 640 || targetRect.height > 220) continue;
                            candidates.push({
                                marker: el,
                                target,
                                exact: isMoreText(el.innerText || el.textContent || ''),
                                clickable: isClickableLike(target),
                                area: targetRect.width * targetRect.height,
                                top: markerRect.top
                            });
                        }
                    }
                    candidates.sort((left, right) => {
                        if (left.exact !== right.exact) return left.exact ? -1 : 1;
                        if (left.clickable !== right.clickable) return left.clickable ? -1 : 1;
                        if (left.top !== right.top) return left.top - right.top;
                        return left.area - right.area;
                    });
                    return candidates;
                };
                const dispatchClick = (el) => {
                    const rect = el.getBoundingClientRect();
                    const x = Math.max(1, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
                    const y = Math.max(1, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
                    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        el.dispatchEvent(new MouseEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: x,
                            clientY: y
                        }));
                    }
                    if (typeof el.click === 'function') {
                        el.click();
                    }
                };

                let clicked = 0;
                const attempted = new Set();
                while (clicked < maxClicks) {
                    const candidate = collectCandidates(attempted)[0];
                    if (!candidate) break;
                    attempted.add(candidate.target);
                    try {
                        candidate.marker.scrollIntoView({block: 'center', inline: 'nearest'});
                        await sleep(80);
                        dispatchClick(candidate.target);
                        if (candidate.marker !== candidate.target) {
                            dispatchClick(candidate.marker);
                        }
                        clicked += 1;
                        await sleep(180);
                    } catch (e) {
                        attempted.add(candidate.marker);
                    }
                }
                return clicked;
            }""",
            {"maxClicks": max_clicks, "moreTexts": sorted(TEXT_MORE_VARIANTS)},
        )
    except Exception as exc:
        print(f"[Reviews] [WARN] Could not expand long reviews: {type(exc).__name__}: {str(exc)[:100]}")
        return 0

