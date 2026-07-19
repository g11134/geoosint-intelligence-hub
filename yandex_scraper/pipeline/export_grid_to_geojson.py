"""
export_grid_to_geojson.py — v2 (исправлен детектор разделителя)

Исправление: разделитель жёстко задан как ";"
Причина: URL и bbox содержат запятые, из-за которых автодетектор
выбирал "," вместо ";" → колонки не читались → файл был пустым.
"""

import csv
import json
import math
from collections import defaultdict

from yandex_scraper.config import FIXED_GRID_FILE, QUEUE_FILE


# ── Настройки ──────────────────────────────────────────────────────
OUTPUT_FILE = FIXED_GRID_FILE

# ── Цвета по статусам для geojson.io ──────────────────────────────
STATUS_STYLES = {
    "pending": {
        "stroke": "#2196F3", "fill": "#2196F3",
        "fill-opacity": 0.15, "stroke-width": 1,
    },
    "done": {
        "stroke": "#4CAF50", "fill": "#4CAF50",
        "fill-opacity": 0.20, "stroke-width": 1,
    },
    "error": {
        "stroke": "#F44336", "fill": "#F44336",
        "fill-opacity": 0.35, "stroke-width": 2,
    },
    "captcha": {
        "stroke": "#FF9800", "fill": "#FF9800",
        "fill-opacity": 0.35, "stroke-width": 2,
    },
}
DEFAULT_STYLE = {
    "stroke": "#9E9E9E", "fill": "#9E9E9E",
    "fill-opacity": 0.10, "stroke-width": 1,
}


def parse_bbox(bbox_str: str):
    """
    Парсит bbox из строки в четыре числа.

    Поддерживаемый формат:
      "30.464385,59.826130~30.481525,59.835443"
       lon_min   lat_min   lon_max   lat_max

    Символ "~" — разделитель между двумя углами прямоугольника.
    Каждый угол: lon,lat через запятую.

    Возвращает (lon_min, lat_min, lon_max, lat_max) или None.
    """
    if not bbox_str or not bbox_str.strip():
        return None

    # Формат с тильдой: "30.464385,59.826130~30.481525,59.835443"
    if "~" in bbox_str:
        parts = bbox_str.strip().split("~")
        if len(parts) == 2:
            left  = parts[0].strip().split(",")
            right = parts[1].strip().split(",")
            if len(left) == 2 and len(right) == 2:
                try:
                    lon_min = float(left[0])
                    lat_min = float(left[1])
                    lon_max = float(right[0])
                    lat_max = float(right[1])
                    # Проверяем что это похоже на координаты СПб
                    if 28.0 < lon_min < 33.0 and 28.0 < lon_max < 33.0:
                        if 58.0 < lat_min < 62.0 and 58.0 < lat_max < 62.0:
                            return lon_min, lat_min, lon_max, lat_max
                except (ValueError, TypeError):
                    pass

    # Запасной вариант: четыре числа через запятую
    parts = bbox_str.strip().split(",")
    if len(parts) == 4:
        try:
            lon_min, lat_min, lon_max, lat_max = map(float, parts)
            if 28.0 < lon_min < 33.0 and 58.0 < lat_min < 62.0:
                return lon_min, lat_min, lon_max, lat_max
        except (ValueError, TypeError):
            pass

    return None


def bbox_to_polygon_coords(lon_min, lat_min, lon_max, lat_max):
    """
    Прямоугольник bbox → замкнутое кольцо координат GeoJSON Polygon.
    GeoJSON требует [longitude, latitude] и замыкание (первая = последняя).
    """
    return [[
        [lon_min, lat_min],
        [lon_max, lat_min],
        [lon_max, lat_max],
        [lon_min, lat_max],
        [lon_min, lat_min],
    ]]


def main():
    if not QUEUE_FILE.exists():
        print(f"[ОШИБКА] Файл не найден: {QUEUE_FILE}")
        return

    # ── Читаем CSV ─────────────────────────────────────────────────
    # ИСПРАВЛЕНИЕ: жёстко задаём delimiter=";"
    # Автодетектор ошибался: URL и bbox содержат запятые,
    # из-за чего в образце запятых было больше чем точек с запятой.
    # Результат: колонки не читались, features был пустым.
    all_rows = []
    with open(QUEUE_FILE, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            all_rows.append(row)

    print(f"[+] Строк прочитано: {len(all_rows)}")

    # Диагностика: покажем названия колонок и первую строку
    if all_rows:
        print(f"[+] Колонки: {list(all_rows[0].keys())}")
        print(f"[+] Первая строка:")
        for k, v in all_rows[0].items():
            print(f"      {k!r}: {str(v)[:60]!r}")

    # ── Статистика статусов ────────────────────────────────────────
    status_counts = defaultdict(int)
    for row in all_rows:
        status_counts[row.get("status", "unknown")] += 1
    print(f"\n[+] Статусы:")
    for status, count in sorted(status_counts.items()):
        print(f"    {status:10s}: {count:,}")

    # ── Дедупликация по bbox ───────────────────────────────────────
    # 4 категории × 1 ячейка = 4 одинаковых bbox → берём первый
    seen_bboxes = {}
    for row in all_rows:
        bbox_str = row.get("bbox", "").strip()
        if bbox_str and bbox_str not in seen_bboxes:
            seen_bboxes[bbox_str] = row

    unique_cells = list(seen_bboxes.values())
    print(f"\n[+] Уникальных ячеек (bbox): {len(unique_cells):,}")

    # ── Строим GeoJSON ─────────────────────────────────────────────
    features = []
    skipped  = 0

    for row in unique_cells:
        bbox_str = row.get("bbox", "")
        status   = row.get("status", "unknown")
        query    = row.get("query", "")

        coords = parse_bbox(bbox_str)
        if coords is None:
            skipped += 1
            continue

        lon_min, lat_min, lon_max, lat_max = coords

        center_lon = round((lon_min + lon_max) / 2, 6)
        center_lat = round((lat_min + lat_max) / 2, 6)

        width_m  = abs(lon_max - lon_min) * 111_000 * math.cos(math.radians(center_lat))
        height_m = abs(lat_max - lat_min) * 111_000

        style = STATUS_STYLES.get(status, DEFAULT_STYLE)

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": bbox_to_polygon_coords(lon_min, lat_min, lon_max, lat_max),
            },
            "properties": {
                "status":     status,
                "query":      query,
                "bbox":       bbox_str,
                "center_lon": center_lon,
                "center_lat": center_lat,
                "width_m":    round(width_m),
                "height_m":   round(height_m),
                **style,
            },
        })

    geojson = {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": {
            "total_cells":   len(features),
            "skipped":       skipped,
            "status_counts": dict(status_counts),
            "description":   "Сетка парсинга Яндекс Карт — загрузи на geojson.io",
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"\n[OK] Сохранено: {OUTPUT_FILE}")
    print(f"     Ячеек:      {len(features):,}")
    print(f"     Пропущено:  {skipped}")
    print(f"     Размер:     {size_kb:.1f} КБ")
    print(f"\n[→] Открой geojson.io и перетащи файл '{OUTPUT_FILE}'")
    print(f"\n[→] Легенда:")
    print(f"    🔵 Синий    — pending  (не обработано)")
    print(f"    🟢 Зелёный  — done    (готово)")
    print(f"    🔴 Красный  — error   (ошибка)")
    print(f"    🟠 Оранжевый— captcha (капча)")


if __name__ == "__main__":
    main()
