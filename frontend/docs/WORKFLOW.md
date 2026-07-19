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

Перед запуском обычно проверяются:

- `SEARCH_QUERIES`
- `WORKERS_COUNT`
- `MAX_REQUESTS_PER_MINUTE`
- `PROXIES_PRIMARY`
- `PROXIES_FALLBACK`
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
python scripts/export_grid_to_geojson.py
```

Если очередь уже есть и нужно продолжить работу, запускай только:

```powershell
python scripts/run_scraper.py
```
