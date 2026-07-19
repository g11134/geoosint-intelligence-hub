from yandex_scraper.constants import _NUMERIC_RE, _UUID_RE


def build_full_address(addr) -> str:
    if not addr:
        return ""
    if isinstance(addr, str):
        if "санкт-петербург" in addr.lower():
            return addr
        return f"Санкт-Петербург, {addr}"
    if not isinstance(addr, dict):
        return ""
    parts = []
    locality = (
        addr.get("locality")
        or addr.get("city")
        or addr.get("town")
        or addr.get("municipality")
        or ""
    )
    if locality:
        parts.append(str(locality))
    if not locality:
        district = addr.get("district") or addr.get("area") or ""
        if district:
            parts.append(str(district))
    street = addr.get("thoroughfare") or addr.get("street") or addr.get("road") or ""
    if street:
        parts.append(str(street))
    house = (
        addr.get("premise")
        or addr.get("house")
        or addr.get("building")
        or addr.get("houseNumber")
        or ""
    )
    if house:
        parts.append(str(house))
    if len(parts) >= 2:
        return ", ".join(parts)
    formatted = (
        addr.get("formattedAddress")
        or addr.get("formatted_address")
        or addr.get("full")
        or addr.get("text")
        or ""
    )
    if formatted:
        formatted = str(formatted)
        if "санкт-петербург" not in formatted.lower():
            return f"Санкт-Петербург, {formatted}"
        return formatted
    return ""


def extract_businesses_from_json(data, audit_collector=None) -> list[dict]:
    results = []
    seen = set()

    def pick_first(*values):
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return ""

    def walk(obj, depth=0):
        if not obj or depth > 12 or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
            return
        name_raw = obj.get("name") or obj.get("title")
        if not isinstance(name_raw, str) or not name_raw.strip():
            for v in obj.values():
                walk(v, depth + 1)
            return
        name = name_raw.strip()
        has_geo = bool(
            obj.get("geometry") or obj.get("coordinates") or obj.get("lon") or obj.get("lat")
        )
        has_address = bool(obj.get("address"))
        has_phones = bool(obj.get("phones") or obj.get("contacts") or obj.get("phone"))
        if not (has_geo or has_address or has_phones):
            for v in obj.values():
                walk(v, depth + 1)
            return
        raw_permalink = (
            obj.get("permalink")
            or obj.get("id")
            or obj.get("businessId")
            or obj.get("orgId")
            or ""
        )
        raw_permalink_str = str(raw_permalink).strip()
        if raw_permalink_str and _UUID_RE.match(raw_permalink_str):
            for v in obj.values():
                walk(v, depth + 1)
            return
        if raw_permalink_str and not _NUMERIC_RE.match(raw_permalink_str):
            for v in obj.values():
                walk(v, depth + 1)
            return
        permalink = raw_permalink_str
        geo = obj.get("geometry") or {}
        coord = geo.get("coordinates") or obj.get("coordinates") or []
        lon = lat = ""
        if isinstance(coord, list) and len(coord) >= 2:
            try:
                lon = str(float(coord[0]))
                lat = str(float(coord[1]))
            except (ValueError, TypeError):
                lon, lat = "", ""
        elif isinstance(coord, dict):
            lon = str(coord.get("lon") or coord.get("lng") or "")
            lat = str(coord.get("lat") or "")
        else:
            try:
                lon = (
                    str(float(obj.get("lon") or obj.get("lng") or ""))
                    if (obj.get("lon") or obj.get("lng"))
                    else ""
                )
                lat = str(float(obj.get("lat") or "")) if obj.get("lat") else ""
            except (ValueError, TypeError):
                lon, lat = "", ""
        full_address = build_full_address(obj.get("address") or "")
        cats = obj.get("categories") or obj.get("rubrics") or obj.get("category") or []
        category = ""
        if isinstance(cats, list) and cats:
            c = cats[0]
            category = (
                str(c.get("name") or c.get("fullName") or "")
                if isinstance(c, dict)
                else str(c)
            )
        elif isinstance(cats, str):
            category = cats
        phones = obj.get("phones") or obj.get("contacts") or obj.get("phone") or []
        phone = ""
        if isinstance(phones, list) and phones:
            p = phones[0]
            if isinstance(p, dict):
                phone = str(
                    p.get("formatted")
                    or p.get("number")
                    or p.get("value")
                    or p.get("text")
                    or ""
                )
            elif isinstance(p, str):
                phone = p
        elif isinstance(phones, str):
            phone = phones
        rating_data = obj.get("ratingData") if isinstance(obj.get("ratingData"), dict) else {}
        rating_count_raw = pick_first(
            rating_data.get("ratingCount"),
            rating_data.get("votesCount"),
            obj.get("ratingData_ratingCount"),
            obj.get("ratingCount"),
            obj.get("reviewsCount"),
        )
        rating_value_raw = pick_first(
            rating_data.get("ratingValue"),
            obj.get("ratingData_ratingValue"),
            obj.get("ratingValue"),
            obj.get("rating"),
        )
        rating_count = str(rating_count_raw).strip() if rating_count_raw is not None else ""
        rating_value = str(rating_value_raw).strip() if rating_value_raw is not None else ""
        if not rating_count:
            rating_count = "отзывов нет"
        if not rating_value:
            rating_value = "рейтинга нет"
        dedup_key = permalink or f"{name}_{full_address}"
        if dedup_key in seen:
            return
        seen.add(dedup_key)
        record = {
            "title": name,
            "shortTitle": name,
            "fullAddress": full_address,
            "categories_0_name": category,
            "phones_0_number": phone,
            "coordinates_0": lon,
            "coordinates_1": lat,
            "permalink": permalink,
            "ratingData_ratingCount": rating_count,
            "ratingData_ratingValue": rating_value,
        }
        if audit_collector is not None:
            try:
                audit_collector(obj, record)
            except Exception:
                pass
        results.append(record)

    walk(data)
    return results
