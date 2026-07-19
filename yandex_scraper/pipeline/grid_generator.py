import csv
import json
import math
import random
import time
from pathlib import Path
from urllib.parse import quote
from typing import Optional, Tuple
import logging

import pyproj
from shapely.geometry import shape, box, Point
from shapely.ops import transform, unary_union
import functools
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

from yandex_scraper.config import (
    POLYGON_FILE, QUEUE_FILE,
    CELL_SIZE_METERS, SEARCH_QUERIES,
    sanitize_url,
    FILTER_WATER,
    WATER_MIN_LAND_SHARE,
    WATER_CACHE_FILE,
    GRID_LOG_FILE,
)

GRID_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(GRID_LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def download_water_mask(city_polygon_wgs84) -> Optional[shape]:
    if WATER_CACHE_FILE.exists():
        logger.info("Загружаем водную маску из кэша")
        with open(WATER_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return shape(data["geometry"])
    
    try:
        import osmnx as ox
        logger.info("Загружаем водные объекты из OSM")
        
        water_tags = {
            "natural":  ["water", "bay", "wetland"],
            "waterway": ["river", "canal", "stream"],
            "landuse":  ["basin", "reservoir"],
        }
        
        water_gdf = ox.features_from_polygon(city_polygon_wgs84, tags=water_tags)
        
        if water_gdf.empty:
            logger.warning("OSM не вернул водных объектов")
            return None
            
        water_geoms = water_gdf[
            water_gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
        ].geometry.tolist()
        
        if not water_geoms:
            logger.warning("Нет полигональных водных объектов")
            return None
            
        water_union = unary_union(water_geoms)
        water_in_city = water_union.intersection(city_polygon_wgs84)
        
        cache_data = {
            "type": "Feature",
            "geometry": water_in_city.__geo_interface__,
            "properties": {"source": "OSM via osmnx", "cached_at": time.time()}
        }
        
        with open(WATER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Водная маска сохранена в кэш: {WATER_CACHE_FILE}")
        return water_in_city
        
    except ImportError:
        logger.warning("osmnx не установлен, фильтрация воды пропущена")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки воды: {e}")
        return None


def get_optimal_utm(city_bounds: Tuple[float, float, float, float]) -> pyproj.CRS:
    minx, miny, maxx, maxy = city_bounds
    center_lon = (minx + maxx) / 2
    center_lat = (miny + maxy) / 2

    zone = int((center_lon + 180) // 6) + 1

    if center_lat >= 0:
        epsg = f"EPSG:326{zone:02d}"
    else:
        epsg = f"EPSG:327{zone:02d}"

    logger.info(f"Авто UTM зона: {epsg}")
    return pyproj.CRS(epsg)


def build_yandex_url(query: str, lon_center: float, lat_center: float,
                     lon_min: float, lat_min: float,
                     lon_max: float, lat_max: float) -> str:
    query_encoded = quote(query, safe="", encoding="utf-8")
    bbox_str = f"{lon_min:.6f},{lat_min:.6f}~{lon_max:.6f},{lat_max:.6f}"
    bbox_encoded = quote(bbox_str, safe="~,")
    url = (
        f"https://yandex.ru/maps/2/saint-petersburg/search/{query_encoded}/?"
        f"ll={lon_center:.6f},{lat_center:.6f}"
        f"&z=15"
        f"&type=biz"
        f"&bbox={bbox_encoded}"
    )
    return url


def build_wgs84_grid_edges(
    cols: int,
    rows: int,
    x_min: float,
    y_min: float,
    cell_size: float,
    to_wgs,
) -> tuple[list[float], list[float]]:
    """
    РЎС‚СЂРѕРёС‚ СЃРѕРіР»Р°СЃРѕРІР°РЅРЅС‹Рµ РіСЂР°РЅРёС†С‹ СЃРµС‚РєРё РІ WGS84.

    Р“СЂР°РЅРёС†С‹ lon/lat СЂР°СЃСЃС‡РёС‚С‹РІР°СЋС‚СЃСЏ РѕРґРёРЅ СЂР°Р· Рё РїРµСЂРµРёСЃРїРѕР»СЊР·СѓСЋС‚СЃСЏ
    РІСЃРµРјРё СЃРѕСЃРµРґРЅРёРјРё СЏС‡РµР№РєР°РјРё, С‡С‚РѕР±С‹ РёСЃРєР»СЋС‡РёС‚СЊ РІР·Р°РёРјРЅС‹Рµ РїРµСЂРµРєСЂС‹С‚РёСЏ bbox.
    """
    x_ref = x_min + (cols * cell_size) / 2.0
    y_ref = y_min + (rows * cell_size) / 2.0

    lon_edges = []
    for col_i in range(cols + 1):
        edge_x = x_min + col_i * cell_size
        lon_edge, _ = to_wgs(edge_x, y_ref)
        lon_edges.append(lon_edge)

    lat_edges = []
    for row_i in range(rows + 1):
        edge_y = y_min + row_i * cell_size
        _, lat_edge = to_wgs(x_ref, edge_y)
        lat_edges.append(lat_edge)

    return lon_edges, lat_edges


def should_skip_by_water_filter(cell_utm, city_utm, water_mask_utm) -> bool:
    if water_mask_utm is None:
        return False

    # РЎРѕС…СЂР°РЅСЏРµРј РїСЂРµР¶РЅРёР№ РѕСЃРЅРѕРІРЅРѕР№ С‚СЂРёРіРіРµСЂ: С†РµРЅС‚СЂ СЏС‡РµР№РєРё РІ РІРѕРґРµ.
    if not water_mask_utm.contains(cell_utm.centroid):
        return False

    # РЎРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ СЃ РїСЂРµР¶РЅРёРј РїРѕРІРµРґРµРЅРёРµРј (РµСЃР»Рё РїРѕСЂРѕРі = 0).
    if WATER_MIN_LAND_SHARE <= 0:
        return True

    city_part = city_utm.intersection(cell_utm)
    city_area = city_part.area
    if city_area <= 0:
        return True

    land_area = city_part.difference(water_mask_utm).area
    land_share = land_area / city_area
    return land_share < WATER_MIN_LAND_SHARE


def generate_cell_batch(row_i_range: range, col_range: range, 
                       x_min: float, y_min: float, city_utm, water_mask_utm,
                       cell_size: float, queries: list,
                       lon_edges: list[float], lat_edges: list[float]) -> list[dict]:
    rows_output = []
    for row_i in row_i_range:
        for col_i in col_range:
            cell_x_min = x_min + col_i * cell_size
            cell_x_max = cell_x_min + cell_size
            cell_y_min = y_min + row_i * cell_size
            cell_y_max = cell_y_min + cell_size
            
            cell_utm = box(cell_x_min, cell_y_min, cell_x_max, cell_y_max)
            
            if not city_utm.intersects(cell_utm):
                continue
                
            if should_skip_by_water_filter(cell_utm, city_utm, water_mask_utm):
                continue
            
            lon_a = lon_edges[col_i]
            lon_b = lon_edges[col_i + 1]
            lat_a = lat_edges[row_i]
            lat_b = lat_edges[row_i + 1]

            lon_min_wgs = min(lon_a, lon_b)
            lon_max_wgs = max(lon_a, lon_b)
            lat_min_wgs = min(lat_a, lat_b)
            lat_max_wgs = max(lat_a, lat_b)
            lon_center = (lon_min_wgs + lon_max_wgs) / 2
            lat_center = (lat_min_wgs + lat_max_wgs) / 2
            
            bbox_str = f"{lon_min_wgs:.6f},{lat_min_wgs:.6f}~{lon_max_wgs:.6f},{lat_max_wgs:.6f}"
            
            for query in queries:
                url = build_yandex_url(query, lon_center, lat_center,
                                     lon_min_wgs, lat_min_wgs, lon_max_wgs, lat_max_wgs)
                url_clean = sanitize_url(url)
                rows_output.append({
                    "url":    url_clean,
                    "query":  query,
                    "bbox":   bbox_str,
                    "status": "pending",
                })
    return rows_output


def main():
    logger.info("=" * 60)
    logger.info("ГЕНЕРАТОР СЕТКИ v3 (параллельный + кэш)")
    logger.info("=" * 60)
    
    if not POLYGON_FILE.exists():
        logger.error(f"Файл полигона не найден: {POLYGON_FILE}")
        return
    
    with open(POLYGON_FILE, encoding="utf-8") as f:
        geojson_data = json.load(f)
    
    if geojson_data.get("type") == "FeatureCollection":
        geometry = geojson_data["features"][0]["geometry"]
    elif geojson_data.get("type") == "Feature":
        geometry = geojson_data["geometry"]
    else:
        geometry = geojson_data
    
    city_polygon_wgs84 = shape(geometry)
    logger.info(f"Полигон загружен: {POLYGON_FILE}")
    
    water_mask_wgs84 = None
    if FILTER_WATER:
        water_mask_wgs84 = download_water_mask(city_polygon_wgs84)
        if water_mask_wgs84 is not None:
            logger.info("Water filter tuning: WATER_MIN_LAND_SHARE=%.2f", WATER_MIN_LAND_SHARE)
            logger.info("Фильтрация воды включена")
        else:
            logger.warning("Фильтрация воды отключена (ошибка)")
    else:
        logger.info("FILTER_WATER=False - фильтрация отключена")
    
    wgs84 = pyproj.CRS("EPSG:4326")
    city_bounds = city_polygon_wgs84.bounds
    utm_crs = get_optimal_utm(city_bounds)
    logger.info(f"Авто UTM зона: {utm_crs}")
    
    to_utm = pyproj.Transformer.from_crs(wgs84, utm_crs, always_xy=True).transform
    to_wgs = pyproj.Transformer.from_crs(utm_crs, wgs84, always_xy=True).transform
    
    city_utm = transform(to_utm, city_polygon_wgs84)
    water_mask_utm = transform(to_utm, water_mask_wgs84) if water_mask_wgs84 else None
    
    x_min, y_min, x_max, y_max = city_utm.bounds
    cols = math.ceil((x_max - x_min) / CELL_SIZE_METERS)
    rows = math.ceil((y_max - y_min) / CELL_SIZE_METERS)
    total_cells = cols * rows
    lon_edges, lat_edges = build_wgs84_grid_edges(
        cols=cols,
        rows=rows,
        x_min=x_min,
        y_min=y_min,
        cell_size=CELL_SIZE_METERS,
        to_wgs=to_wgs,
    )
    
    logger.info(f"Размер ячейки: {CELL_SIZE_METERS}m")
    logger.info(f"Bbox UTM: X:{x_min:.0f}->{x_max:.0f} Y:{y_min:.0f}->{y_max:.0f}")
    logger.info(f"Ячеек: {cols}x{rows}={total_cells:,}")
    
    n_workers = min(mp.cpu_count(), 8)
    batch_size = rows // n_workers
    row_ranges = []
    
    for i in range(n_workers):
        start = i * batch_size
        end = start + batch_size if i < n_workers - 1 else rows
        row_ranges.append(range(start, end))
    
    all_rows = []
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(generate_cell_batch, row_range, range(cols),
                          x_min, y_min, city_utm, water_mask_utm,
                          CELL_SIZE_METERS, SEARCH_QUERIES, lon_edges, lat_edges)
            for row_range in row_ranges
        ]
        
        for future in futures:
            batch = future.result()
            all_rows.extend(batch)
    
    random.shuffle(all_rows)
    
    fieldnames = ["url", "query", "bbox", "status"]
    with open(QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(all_rows)
    
    logger.info(f"Очередь сохранена: {QUEUE_FILE}")
    logger.info(f"URL сгенерировано: {len(all_rows):,}")
    logger.info("Готово!")


if __name__ == "__main__":
    main()

