# Data Contracts

Этот документ фиксирует форматы данных, которые нельзя менять без отдельного согласования.

## Queue CSV

Файл: `data/state/parsing_queue.csv`.

Кодировка: `utf-8-sig`.

Разделитель: `;`.

Колонки:

- `url`: полный URL Яндекс Карт для одной ячейки и одного поискового запроса.
- `query`: поисковый запрос.
- `bbox`: границы ячейки в формате `lon_min,lat_min~lon_max,lat_max`.
- `status`: состояние задачи.

Допустимые статусы:

- `pending`: задача еще не обработана.
- `done`: задача успешно обработана, даже если организаций не найдено.
- `error`: задача завершилась сетевой или технической ошибкой после retry.
- `captcha`: задача уперлась в капчу после retry.

Парсер берет в работу только строки со статусом `pending`.

## Raw JSONL

Файл: `data/raw/raw_data.jsonl`.

Формат: одна JSON-запись организации на одну строку.

Основные поля:

- `title`
- `shortTitle`
- `fullAddress`
- `categories_0_name`
- `phones_0_number`
- `coordinates_0`
- `coordinates_1`
- `permalink`
- `ratingData_ratingCount`
- `ratingData_ratingValue`
- `source_query`
- `source_bbox`

Файл дописывается в append-режиме. Не сортируй и не форматируй его как обычный JSON-массив.

## Result CSV

Файл: `data/output/result.csv`.

Кодировка: `utf-8-sig`.

Разделитель: `;`.

Порядок колонок задается `FINAL_COLUMNS` в `yandex_scraper/config.py`.

Текущий порядок:

- `title`
- `shortTitle`
- `fullAddress`
- `categories_0_name`
- `phones_0_number`
- `coordinates_0`
- `coordinates_1`
- `permalink`
- `ratingData_ratingCount`
- `ratingData_ratingValue`
- `source_query`
- `source_bbox`

## Seen IDs DB

Файлы:

- `data/state/seen_ids.db`
- `data/state/seen_ids.db-shm`
- `data/state/seen_ids.db-wal`

Таблица: `seen_ids`.

Колонка: `id TEXT PRIMARY KEY`.

DB используется для дедупликации между перезапусками. Если переносишь state, переноси все три файла вместе.

## Grid GeoJSON

Файл: `data/output/grid_visualization.geojson`.

Назначение: визуализация ячеек и статусов очереди.

Каждая feature содержит геометрию bbox и свойства:

- `status`
- `query`
- `bbox`
- `center_lon`
- `center_lat`
- `width_m`
- `height_m`

Этот файл можно пересоздавать из очереди.

## Water Cache

Файл: `data/cache/water_mask_cache.geojson`.

Назначение: кеш водных объектов для фильтрации сетки.

Файл можно пересоздать, но это может потребовать сетевого запроса к OSM через `osmnx`.
