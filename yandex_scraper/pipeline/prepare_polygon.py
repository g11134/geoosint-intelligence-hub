"""
╔══════════════════════════════════════════════════════════════════╗
║  СКРИПТ 0: ПОДГОТОВКА ПОЛИГОНА                                  ║
║  Файл: 0_prepare_polygon.py                                      ║
║                                                                  ║
║  Что делает:                                                     ║
║  - Читает детальный полигон из spb_polygon.geojson              ║
║    (25 000+ строк, скачан с overpass-turbo.eu)                  ║
║  - Упрощает геометрию алгоритмом Ramer–Douglas–Peucker          ║
║    (убирает точки ближе ~100м друг к другу)                     ║
║  - Сохраняет облегчённый файл spb_polygon.json                  ║
║    (~300–500 строк — в 50+ раз меньше)                         ║
║                                                                  ║
║  Запускать ОДИН РАЗ перед стартом парсинга.                     ║
║                                                                  ║
║  Зависимости:                                                    ║
║    pip install shapely                                           ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from yandex_scraper.config import POLYGON_FILE, SOURCE_POLYGON_FILE


# ══════════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ══════════════════════════════════════════════════════════════════


# Входной файл — детальный полигон, скачанный с overpass-turbo.eu
INPUT_FILE = SOURCE_POLYGON_FILE

# Выходной файл — упрощённый, готовый для парсера
OUTPUT_FILE = POLYGON_FILE

# Допустимое упрощение в ГРАДУСАХ.
# 0.001° ≈ 111 метров на земле.
# Точки ближе 111м друг к другу — лишние при сетке 1500м.
# Уменьши до 0.0005 если хочешь чуть точнее (файл будет немного больше).
TOLERANCE = 0.001


# ══════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: подсчёт точек полигона
# ══════════════════════════════════════════════════════════════════


def count_coords(geom) -> int:
    """
    Считает общее количество координатных точек в геометрии.

    Работает для Polygon и MultiPolygon одинаково.
    Нужно чтобы показать насколько уменьшился полигон.
    """
    # geom_type — строка с типом геометрии: "Polygon" или "MultiPolygon"
    if geom.geom_type == "Polygon":
        # exterior.coords — внешний контур полигона (список точек)
        # interiors — список внутренних дырок (если есть)
        total = len(geom.exterior.coords)
        for interior in geom.interiors:
            total += len(interior.coords)
        return total

    elif geom.geom_type == "MultiPolygon":
        # MultiPolygon — несколько отдельных полигонов
        # (у СПб это Кронштадт, острова и основная часть)
        total = 0
        for poly in geom.geoms:
            total += len(poly.exterior.coords)
            for interior in poly.interiors:
                total += len(interior.coords)
        return total

    return 0


# ══════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: извлечение геометрии из GeoJSON
# ══════════════════════════════════════════════════════════════════


def extract_geometry(data: dict):
    """
    Извлекает геометрию из GeoJSON любого формата.

    Overpass-turbo может выгрузить три разных формата:
    1. FeatureCollection — коллекция объектов (самый частый)
    2. Feature           — один объект с геометрией
    3. Geometry          — чистая геометрия без обёртки
    """
    geo_type = data.get("type", "")

    if geo_type == "FeatureCollection":
        print(f"  Тип файла: FeatureCollection")
        print(f"  Объектов в файле: {len(data['features'])}")

        # Собираем все геометрии из всех объектов коллекции
        # и объединяем их в одну через unary_union()
        # Это нужно потому что Overpass иногда разбивает
        # один регион на несколько частей (основная часть + острова)
        geometries = []
        for i, feature in enumerate(data["features"]):
            geom = feature.get("geometry")
            if geom:
                try:
                    geometries.append(shape(geom))
                except Exception as e:
                    print(f"  [!] Объект {i}: не удалось прочитать геометрию: {e}")

        if not geometries:
            raise ValueError("В FeatureCollection нет геометрий!")

        if len(geometries) == 1:
            return geometries[0]

        # unary_union() объединяет несколько полигонов в один
        # (или MultiPolygon если они не соприкасаются)
        print(f"  Объединяем {len(geometries)} геометрий...")
        return unary_union(geometries)

    elif geo_type == "Feature":
        print(f"  Тип файла: Feature")
        return shape(data["geometry"])

    else:
        # Предполагаем чистую геометрию (Polygon / MultiPolygon)
        print(f"  Тип файла: Geometry ({geo_type})")
        return shape(data)


# ══════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ══════════════════════════════════════════════════════════════════


def main():
    print("=" * 60)
    print("  ПОДГОТОВКА ПОЛИГОНА")
    print("=" * 60)

    # ── Шаг 1: Проверяем наличие входного файла ───────────────────
    if not INPUT_FILE.exists():
        print(f"\n[ОШИБКА] Файл не найден: {INPUT_FILE}")
        print(f"  Убедись, что файл '{INPUT_FILE}' лежит в той же папке")
        print(f"  что и этот скрипт.")
        return

    print(f"\n[1/4] Читаем файл: {INPUT_FILE}")

    # ── Шаг 2: Загружаем GeoJSON ──────────────────────────────────
    # encoding="utf-8" — GeoJSON всегда в UTF-8 по стандарту
    with open(INPUT_FILE, encoding="utf-8") as f:
        data = json.load(f)

    # Извлекаем геометрию из GeoJSON
    polygon_raw = extract_geometry(data)

    # Считаем сколько точек в исходном полигоне
    coords_before = count_coords(polygon_raw)
    print(f"  Тип геометрии: {polygon_raw.geom_type}")
    print(f"  Точек в исходнике: {coords_before:,}")

    # ── Шаг 3: Упрощаем полигон ───────────────────────────────────
    print(f"\n[2/4] Упрощаем полигон (tolerance={TOLERANCE}°  ≈ {TOLERANCE * 111_000:.0f} м)...")

    # simplify() — алгоритм Ramer–Douglas–Peucker
    # tolerance        — максимальное отклонение в единицах координат (градусах)
    # preserve_topology=True — гарантирует, что результат останется
    #                          валидным полигоном (без самопересечений)
    polygon_simplified = polygon_raw.simplify(
        tolerance=TOLERANCE,
        preserve_topology=True,
    )

    coords_after = count_coords(polygon_simplified)
    reduction = (1 - coords_after / coords_before) * 100

    print(f"  Точек после упрощения: {coords_after:,}")
    print(f"  Сокращение: {reduction:.1f}%  ({coords_before:,} → {coords_after:,})")
    print(f"  Тип результата: {polygon_simplified.geom_type}")

    # ── Шаг 4: Проверяем валидность результата ────────────────────
    print(f"\n[3/4] Проверяем валидность...")

    # is_valid — Shapely проверяет полигон по правилам OGC:
    # нет самопересечений, кольца замкнуты, ориентация правильная
    if not polygon_simplified.is_valid:
        print(f"  [!] Полигон невалиден! Пробуем исправить через buffer(0)...")
        # buffer(0) — стандартный трюк Shapely для исправления
        # мелких топологических ошибок
        polygon_simplified = polygon_simplified.buffer(0)
        if polygon_simplified.is_valid:
            print(f"  [+] Исправлено успешно!")
        else:
            print(f"  [ОШИБКА] Не удалось исправить. Попробуй уменьшить TOLERANCE.")
            return
    else:
        print(f"  [+] Полигон валиден!")

    # ── Шаг 5: Сохраняем результат ────────────────────────────────
    print(f"\n[4/4] Сохраняем: {OUTPUT_FILE}")

    # __geo_interface__ — стандартное свойство Shapely:
    # возвращает геометрию в виде словаря GeoJSON
    output = {
        "type": "Feature",
        "geometry": polygon_simplified.__geo_interface__,
        "properties": {
            "name": "Санкт-Петербург",
            "source": "OpenStreetMap via Overpass Turbo",
            "tolerance": TOLERANCE,
            "coords_original": coords_before,
            "coords_simplified": coords_after,
        }
    }

    # ensure_ascii=False — сохраняем кириллицу как есть, не как \uXXXX
    # indent=2          — читаемый формат с отступами
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Считаем размер файлов для сравнения
    size_before = INPUT_FILE.stat().st_size / 1024
    size_after  = OUTPUT_FILE.stat().st_size / 1024

    print(f"\n{'=' * 60}")
    print(f"  ГОТОВО!")
    print(f"{'=' * 60}")
    print(f"  Входной файл:  {INPUT_FILE.name:<30} {size_before:>8.1f} КБ")
    print(f"  Выходной файл: {OUTPUT_FILE.name:<30} {size_after:>8.1f} КБ")
    print(f"  Сжатие файла:  в {size_before / size_after:.0f} раз меньше")
    print(f"\n  [→] Следующий шаг: запусти python 1_grid_generator.py")
    print(f"{'=' * 60}")


# ══════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()
