# Operations

Практические инструкции для безопасной работы с проектом.

## Возобновление парсинга

Если парсинг прервался, не очищай `data/state/parsing_queue.csv` и `data/state/seen_ids.db`.

Запусти:

```powershell
python scripts/run_scraper.py
```

Парсер продолжит только строки со статусом `pending`.

## Проверка статусов очереди

```powershell
Import-Csv data\state\parsing_queue.csv -Delimiter ';' | Group-Object status | Select-Object Name,Count
```

## Проверка количества сырья

```powershell
Get-Content data\raw\raw_data.jsonl | Measure-Object -Line
```

## Повторная обработка error/captcha

Перед массовой правкой сделай копию `data/state/parsing_queue.csv`.

Можно вручную заменить отдельные статусы `error` или `captcha` на `pending`, если хочешь повторить только проблемные ячейки.

Не меняй `url`, `query` и `bbox`, если цель не состоит в пересоздании очереди.

## Полный сброс очереди

Полный сброс означает потерю текущего resume-состояния очереди.

Безопасная последовательность:

```powershell
Copy-Item data\state\parsing_queue.csv data\state\parsing_queue.before_reset.csv
python scripts/generate_grid.py
```

После этого вся очередь будет пересоздана со статусом `pending`.

## Полный сброс дедупликации

Удаление `seen_ids.db*` приведет к повторной записи уже найденных организаций.

Перед сбросом сделай backup всех файлов:

```powershell
Copy-Item data\state\seen_ids.db* data\backup_before_refactor\
```

Сбрасывать DB стоит только если нужно полностью пересобрать результат с нуля.

## Экспорт без нового парсинга

CSV/XLSX можно пересоздать из текущего JSONL:

```powershell
python scripts/export_excel.py
```

GeoJSON статусов можно пересоздать из текущей очереди:

```powershell
python scripts/export_grid_to_geojson.py
```

## Регулярный месячный срез

Для нового изолированного среза категории:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня"
```

Если запуск прервался, повтори ту же команду. Очередь внутри `data/runs/<period>/<slug>/state/` будет использована повторно, и парсер продолжит `pending`.

Не редактируй snapshot-очередь во время активного запуска. Правила для `parsing_queue.csv` и `seen_ids.db` внутри snapshot-папки такие же, как для основной `data/state/`.

Для сравнения месяцев:

```powershell
python scripts/compare_snapshots.py --old data/runs/2026-04/кофейня/output/result.csv --new data/runs/2026-05/кофейня/output/result.csv --output data/analytics/compare/2026-04_to_2026-05/кофейня
```

## Backup после рефакторинга

Перед переносом структуры создана папка `data/backup_before_refactor/`.

В ней лежат копии прежних root-скриптов и рабочих артефактов на момент начала рефакторинга.

## Что не делать во время активного парсинга

- Не редактировать `data/state/parsing_queue.csv`.
- Не удалять `data/state/seen_ids.db*`.
- Не перемещать `data/raw/raw_data.jsonl`.
- Не менять `FINAL_COLUMNS` без проверки downstream-экспорта.
- Не менять прокси-логику одновременно со структурным рефакторингом.
