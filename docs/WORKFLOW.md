# Workflow

Документ описывает штатный pipeline проекта после перехода на clean-layout.

## 1. Настройка окружения

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m patchright install chromium
```

Если окружение уже создано, достаточно активировать `.venv`.

## 2. Настройка конфигурации

Основной файл настроек: `yandex_scraper/config.py`.

Учётные данные прокси не хранятся в `config.py` и не должны попадать в Git.
Перед запуском задайте их в окружении процесса:

```powershell
$env:YANDEX_PROXY_PRIMARY_SERVER = "<proxy-server>"
$env:YANDEX_PROXY_PRIMARY_USERNAME = "<proxy-username>"
$env:YANDEX_PROXY_PRIMARY_PASSWORD = "<proxy-password>"

$env:YANDEX_PROXY_FALLBACK_SERVER = "<proxy-server>"
$env:YANDEX_PROXY_FALLBACK_USERNAME = "<proxy-username>"
$env:YANDEX_PROXY_FALLBACK_PASSWORD = "<proxy-password>"
```

Если задана только часть переменных одного пула, запуск завершится ошибкой.
Файл `.env.example` является шаблоном и не загружается приложением автоматически.

Перед запуском обычно проверяются:

- `SEARCH_QUERIES`
- `WORKERS_COUNT`
- `MAX_REQUESTS_PER_MINUTE`
- переменные окружения `YANDEX_PROXY_PRIMARY_*`
- переменные окружения `YANDEX_PROXY_FALLBACK_*`
- `CELL_SIZE_METERS`
- `FILTER_WATER`

Не меняй `FINAL_COLUMNS`, если не согласовано изменение формата downstream-экспорта.

## 3. Подготовка полигона

Вход: `data/input/spb_polygon.geojson`.

Выход: `data/input/spb_polygon.json`.

Команда:

```powershell
python scripts/prepare_polygon.py
```

Этот шаг нужен перед генерацией сетки, если подготовленный `spb_polygon.json` отсутствует.

## 4. Генерация очереди

Вход: `data/input/spb_polygon.json`.

Выход: `data/state/parsing_queue.csv`.

Дополнительный кеш: `data/cache/water_mask_cache.geojson`.

Команда:

```powershell
python scripts/generate_grid.py
```

Очередь создается со схемой `url;query;bbox;status`, где новый статус равен `pending`.

## 5. Импорт GeoJSON в очередь

Если нужно построить очередь из готовой fixed-grid разметки:

```powershell
python scripts/import_geojson_to_queue.py
```

По умолчанию вход берется из `data/output/grid_visualization.geojson`, выход пишется в `data/state/parsing_queue.csv`.

Можно явно указать вход, выход и запрос:

```powershell
python scripts/import_geojson_to_queue.py --input path\to\grid.geojson --output data\state\parsing_queue.csv --query "кофейня"
```

## 6. Запуск парсера

Команда:

```powershell
python scripts/run_scraper.py
```

Парсер читает `data/state/parsing_queue.csv`, берет только строки со статусом `pending`, пишет новые организации в `data/raw/raw_data.jsonl`, отмечает ID в `data/state/seen_ids.db` и обновляет статусы очереди.

После завершения парсер автоматически создает CSV в `data/output/result.csv`.

## 7. Экспорт в Excel

Команда:

```powershell
python scripts/export_excel.py
```

Скрипт читает `data/raw/raw_data.jsonl`, применяет текущий `FINAL_COLUMNS`, дедуплицирует по `title + fullAddress` и пишет:

- `data/output/raw_data.xlsx`
- `data/output/result.csv`

## 7.1. Build Organizations DB for API/Flutter

Input: `data/output/result.csv`.

Output: `data/output/organizations.db`.

Command:

```powershell
python scripts/build_organizations_db.py
```

This step creates the read-only SQLite database used by `scripts/run_api.py`.
It does not change queue/state, `seen_ids.db`, raw JSONL, CSV or Excel exports.

## 7.2. Second Pass: Organization Details and Reviews

Command:

```powershell
python scripts/run_reviews_parser.py
```

The second pass reads organizations from enriched output when available, otherwise from
`data/output/result.csv`. For each organization it opens the organization card, writes
`data/raw/organization_details.jsonl` with card/passport fields, writes
`data/raw/organization_services.jsonl` with `Товары и услуги` name/price pairs, then moves to
reviews and appends reviews to `data/raw/reviews.jsonl`.

Useful output overrides:

```powershell
python scripts/run_reviews_parser.py --details-output data\raw\organization_details.jsonl --services-output data\raw\organization_services.jsonl
```

To keep the old reviews-only behavior:

```powershell
python scripts/run_reviews_parser.py --skip-organization-details
```

### 7.2.1. Mass Reviews Collection

The production reviews run uses the normal organizations source, default state file
`data/state/reviews_queue.csv`, and append-only raw output `data/raw/reviews.jsonl`.

Recommended controlled start:

```powershell
python scripts/run_reviews_parser.py `
  --limit 10 `
  --sort newest `
  --date-from 2026-01-01 `
  --traffic-profile interactive-then-lean `
  --proxy-pool primary `
  --proxy-attempts 3 `
  --headful `
  --wait-on-captcha `
  --debug-nav `
  --debug-screenshot
```

Mass run after the controlled start is confirmed:

```powershell
python scripts/run_reviews_parser.py `
  --sort newest `
  --date-from 2026-01-01 `
  --traffic-profile interactive-then-lean `
  --proxy-pool primary `
  --proxy-attempts 3
```

Resume is done by running the same command again. The parser reads
`data/state/reviews_queue.csv`, processes only rows with `status=pending`, and flushes
state after each organization.

Convert collected reviews to CSV:

```powershell
python scripts/export_reviews_csv.py `
  --reviews-source data\raw\reviews.jsonl `
  --output data\output\reviews.csv
```

Operational notes:

- `--sort newest` keeps the newest-review flow.
- `--date-from 2026-01-01` stores reviews dated 2026-01-01 or newer and stops scrolling
  after older reviews are reached.
- `--traffic-profile interactive-then-lean` keeps the UI interactive until newest sort is
  confirmed, then switches to lean traffic on scrolling.
- Do not delete `data/state/reviews_queue.csv` during a run unless you intentionally want to
  rebuild the reviews queue.
- `data/raw/reviews.jsonl` is append-only. If you intentionally restart collection from
  scratch, archive or remove both the raw reviews output and the reviews queue together.

## 8. Визуализация очереди

Команда:

```powershell
python scripts/export_grid_to_geojson.py
```

Скрипт читает `data/state/parsing_queue.csv` и пишет `data/output/grid_visualization.geojson` со стилями по статусам.

## 9. Рекомендуемый порядок полного запуска

```powershell
python scripts/prepare_polygon.py
python scripts/generate_grid.py
python scripts/run_scraper.py
python scripts/export_excel.py
python scripts/build_organizations_db.py
python scripts/run_reviews_parser.py --sort newest --date-from 2026-01-01 --traffic-profile interactive-then-lean --proxy-pool primary --proxy-attempts 3
python scripts/export_reviews_csv.py --reviews-source data\raw\reviews.jsonl --output data\output\reviews.csv
python scripts/export_grid_to_geojson.py
```

Если очередь уже есть и нужно продолжить работу, запускай только:

```powershell
python scripts/run_scraper.py
```

## 10. Ежемесячные срезы по категориям

Если нужно регулярно собирать одну и ту же категорию по месяцам, не смешивая `state`, `raw` и `output`, используй snapshot-команду:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня"
```

Она создает отдельную рабочую папку `data/runs/2026-05/кофейня/` и запускает тот же pipeline внутри нее.

Повтор той же команды продолжает существующий срез, если очередь уже была создана.

Сравнение двух готовых срезов:

```powershell
python scripts/compare_snapshots.py --old data/runs/2026-04/кофейня/output/result.csv --new data/runs/2026-05/кофейня/output/result.csv --output data/analytics/compare/2026-04_to_2026-05/кофейня
```

Подробности смотри в `docs/RECURRING_SNAPSHOTS.md`.
