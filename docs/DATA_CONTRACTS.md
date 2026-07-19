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

## Field Audit JSONL

File: `data/raw/field_audit.jsonl`.

Purpose: diagnostic append-only artifact for inspecting data visible during a cell parse.

It is written only when `YANDEX_SCRAPER_FIELD_AUDIT_ENABLED=1` is set.

Each line is one diagnostic JSON record from either:

- `xhr_business`: normalized preview plus selected raw XHR/source JSON fields and top-level raw keys.
- `dom_snippet`: visible DOM text, extracted snippet fields, links and image URLs from search result cards.

This file is not used by CSV/XLSX export, Organizations DB, queue state or deduplication. It does not change `FINAL_COLUMNS`.

## Enriched JSONL

File: `data/raw/enriched_data.jsonl`.

Purpose: extended organization records built from XHR data plus visible DOM card data.

It is written only when `YANDEX_SCRAPER_ENRICHED_DATA_ENABLED=1` is set.

The legacy `data/raw/raw_data.jsonl`, `FINAL_COLUMNS`, queue state and deduplication DB are not changed by this mode.

Main fields:

- collection metadata: `captured_at`, `source_query`, `source_bbox`, `cell_url`, `search_result_index`
- Yandex identity: `yandex_id`, `permalink`, `org_url`
- organization core: `title`, `fullAddress`, `categories_0_name`
- raw structured JSON fields: `raw_categories_json`, `raw_phones_json`, `raw_ratingData_json`, `raw_features_json`, `raw_urls_json`, `raw_photos_json`
- contacts and coordinates: `phones_0_number`, `coordinates_0`, `coordinates_1`
- rating fields: `rating_count`, `rating_value`, `review_count`
- website/photos/ownership: `website_url`, `photos_count`, `first_photo_url`, `business_verified_owner`
- DOM visible fields: `dom_category`, `dom_visibleText`, `open_status_text`, `awards_text`, `offer_text`, `gallery_url`, `reviews_url`, `dom_image_url`

## Enriched Result CSV

File: `data/output/enriched_result.csv`.

Encoding: `utf-8-sig`.

Delimiter: `;`.

Column order is defined by `ENRICHED_COLUMNS` in `yandex_scraper/config.py`.

Can be rebuilt from enriched JSONL:

```powershell
python scripts/export_enriched_csv.py
```

## Raw Reviews JSONL

Default file: `data/raw/reviews.jsonl`.

Purpose: append-only raw review records collected by `scripts/run_reviews_parser.py`.
The file is not a JSON array; each line is one JSON object.

Mass collection command:

```powershell
python scripts/run_reviews_parser.py `
  --sort newest `
  --date-from 2026-01-01 `
  --traffic-profile interactive-then-lean `
  --proxy-pool primary `
  --proxy-attempts 3
```

Main fields:

- `captured_at`
- `organization_id`
- `organization_title`
- `organization_url`
- `reviews_url`
- `review_id`
- `author_name`
- `rating`
- `date`
- `parsed_date`
- `text`
- `likes`
- `has_organization_reply`
- `organization_reply_text`
- `organization_reply_date`
- `source`

Collection contract for the production newest flow:

- `--sort newest` is the canonical mode for collecting recent reviews.
- DOM order defines the order of newest reviews.
- XHR-only reviews are not stored as new output rows in newest mode.
- XHR data may only enrich DOM reviews that were already observed or matched.
- `--date-from 2026-01-01` keeps reviews with date >= 2026-01-01 and stops newest
  scrolling after older reviews are reached.
- `source` is normally `dom`; when XHR enriches an existing DOM review, it can become
  `dom+xhr`.
- Missing or unparseable review dates are treated as an error in the production newest/date
  filtered flow, because date filtering cannot be verified safely.

The CSV artifact is rebuilt from this JSONL by `scripts/export_reviews_csv.py`.

## Reviews Analytics CSV

Default file: `data/output/reviews.csv`.

The API can read another file when `YANDEX_SCRAPER_REVIEWS_ANALYTICS_SOURCE_FILE` is set.

Encoding: `utf-8-sig`.

Delimiter: `;`.

Required columns for AI analytics:

- `organization_id`: used to select reviews for `/api/v2/organizations/{id}/reviews/ai-analysis`.
- `rating`: local deterministic rating statistics.
- `text`: anonymized review text sent to the selected AI provider.

Optional columns currently present in the sample file:

- `captured_at`
- `organization_title`
- `organization_url`
- `reviews_url`
- `review_id`
- `author_name`
- `date`
- `parsed_date`
- `likes`
- `source`

The AI request intentionally excludes `author_name`, `review_id`, `organization_url`, `reviews_url`
and every other identifier-like field. Only `rating`, optional review `date`, and `text` are sent
to the AI provider. The batch precompute script always targets the configured local LM Studio
server.

## Organization Details JSONL

Default file: `data/raw/organization_details.jsonl`.

Purpose: append-only records collected during the second pass before reviews. One organization
page is opened, the visible organization card/passport is captured, and then the same page
proceeds to products/services and reviews.

It is written by `scripts/run_reviews_parser.py` unless `--skip-organization-details` is passed
or `YANDEX_SCRAPER_ORGANIZATION_DETAILS_ENABLED=0` is set.

Main fields:

- metadata: `schema_version`, `captured_at`, `capture_status`, `error`
- identity/source: `organization_id`, `organization_title`, `organization_url`, `reviews_url`, `page_url`
- card fields: `title`, `category`, `full_address`, `phone`, `website_url`, `rating_value`,
  `rating_count`, `review_count`
- booking/work hours fields: `has_online_booking_button`, `online_booking_text`,
  `working_hours_notice`, `working_hours_today`,
  `working_hours_text`, `working_hours_schedule`, `working_hours_schedule_reveal_clicked`
- visible card text: `contacts_text`, `features_text`, `card_visible_text`

`working_hours_schedule` is a JSON array of objects with:

- `day`
- `date`
- `hours`

Products/services are intentionally not duplicated in this file. Missing scalar values are stored
as `данные отсутствуют`; if no working-hours schedule is found, `working_hours_schedule`
contains one placeholder item with `day`, `date`, and `hours` set to `данные отсутствуют`.

## Organization Services JSONL

Default file: `data/raw/organization_services.jsonl`.

Purpose: append-only products/services records collected during the same second-pass page session,
after the organization card has been captured and before reviews are collected.

The output path can be changed with `--services-output` or
`YANDEX_SCRAPER_ORGANIZATION_SERVICES_JSONL_FILE`.

Main fields:

- metadata: `schema_version`, `captured_at`, `capture_status`, `error`
- identity/source: `organization_id`, `organization_title`, `organization_url`, `reviews_url`, `page_url`
- services fields: `services_count`, `services_text`, `services`, `services_reveal_clicked`

`services` is a JSON array of objects with only:

- `name`
- `price`

Photo URLs for products/services are intentionally not stored. Missing scalar values are stored as
`данные отсутствуют`; if no products/services are found, the array contains one placeholder item
with `name` and `price` both set to `данные отсутствуют`.

## Review AI Cache

Default directory: `data/analytics/review_ai/`.

The API can use another directory when `YANDEX_SCRAPER_REVIEW_AI_CACHE_DIR` is set.

Each cache file is one JSON object named `{org_id}.json`. It stores both the structured `analysis`
object and the UI-ready `analysisText` string. A cache entry is fresh only when its
internal cache key matches:

- organization id
- review CSV path, size and modified time
- AI provider
- provider model
- prompt version
- SHA-256 hash of the anonymized `rating` + optional `date` + `text` payload

`GET /api/v2/organizations/{id}/reviews/ai-analysis` reads only this fresh cache and does not call
an AI provider. `POST /api/v2/organizations/{id}/reviews/ai-analysis` remains available for manual
refresh/debug generation.

The cache is not parser state. It does not affect queue files, `seen_ids.db`, raw JSONL, CSV/XLSX
exports or `organizations.db`.

## Review Radius AI Cache

Default directory: `data/analytics/review_ai_radius/`.

The API can use another directory when `YANDEX_SCRAPER_REVIEW_AI_RADIUS_CACHE_DIR` is set.

Each cache file is one JSON object named `{center_org_id}_{radius_m}.json`. It stores a
human-readable aggregate `analysisText` for the selected center organization and radius.

A radius cache entry is fresh only when its internal cache key matches:

- center organization id and coordinates from `organizations.db`
- selected radius in meters
- AI provider and model
- radius prompt version
- organizations DB source snapshot
- hashes and metadata of the ready individual review AI reports used in the aggregate
- list of nearby organizations that were missing individual reports

`GET /api/v2/organizations/{id}/reviews/ai-radius-analysis?radius_m=3000` reads only this fresh
cache and does not call an AI provider. Radius reports are generated by
`scripts/precompute_review_ai_radius_reports.py` after individual organization reports already
exist in `data/analytics/review_ai/`.

The radius cache is not parser state. It does not affect queue files, `seen_ids.db`, raw JSONL,
CSV/XLSX exports or `organizations.db`.

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

## Organizations DB

File: `data/output/organizations.db`.

Purpose: read-only SQLite database for API and Flutter clients. It is rebuilt from `data/output/result.csv` by:

```powershell
python scripts/build_organizations_db.py
```

This database is not parser state and must not be confused with `data/state/seen_ids.db`.

Table: `organizations`.

Contract columns copied from `FINAL_COLUMNS`:

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

Additional API/read-model columns:

- `id`: stable API identifier.
- `lon`: parsed longitude from `coordinates_0`.
- `lat`: parsed latitude from `coordinates_1`.
- `has_valid_coordinates`: `1` when `lon/lat` can be returned to map clients.
- `raw_json`: cleaned source CSV row as JSON for traceability.

Table: `metadata`.

Stores source CSV path, source size, source modified time, build time, schema version and row counts.

The API exposes only rows where `has_valid_coordinates = 1`, preserving the previous map/API behavior.

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

## Snapshot Run

Папка: `data/runs/<period>/<slug>/`.

Назначение: изолированный срез одной категории или набора запросов за один период.

Внутри snapshot-папки сохраняется та же структура, что и в `data/`:

- `input/`
- `state/`
- `raw/`
- `output/`
- `cache/`
- `logs/`
- `tmp/`

Контракты `parsing_queue.csv`, `raw_data.jsonl`, `result.csv`, `seen_ids.db` и `organizations.db` внутри snapshot-папки такие же, как в основном pipeline.

Файл: `manifest.json`.

Основные поля:

- `schema_version`
- `run_id`
- `period`
- `slug`
- `queries`
- `status`
- `created_at`
- `updated_at`
- `run_dir`
- `environment`
- `artifacts`

`manifest.json` служит для трассировки среза и не заменяет рабочие state-файлы.

## Snapshot Compare Output

Папка: `data/analytics/compare/...`.

Файлы:

- `added.csv`: организации, которые есть в новом срезе и отсутствуют в старом.
- `removed.csv`: организации, которые были в старом срезе и отсутствуют в новом.
- `changed.csv`: организации, у которых изменились сравниваемые поля.
- `summary.json`: статистика сравнения и пути к артефактам.

CSV-файлы используют `utf-8-sig` и разделитель `;`.
