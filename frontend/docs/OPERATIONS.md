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

## Backup после рефакторинга

Перед переносом структуры создана папка `data/backup_before_refactor/`.

В ней лежат копии прежних root-скриптов и рабочих артефактов на момент начала рефакторинга.

## Что не делать во время активного парсинга

- Не редактировать `data/state/parsing_queue.csv`.
- Не удалять `data/state/seen_ids.db*`.
- Не перемещать `data/raw/raw_data.jsonl`.
- Не менять `FINAL_COLUMNS` без проверки downstream-экспорта.
- Не менять прокси-логику одновременно со структурным рефакторингом.
