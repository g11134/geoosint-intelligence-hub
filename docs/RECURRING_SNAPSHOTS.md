# Recurring Snapshots

Документ описывает безопасный слой регулярных месячных срезов по категориям.

## Идея

Обычный pipeline парсера не меняется. Для каждого периода и поискового запроса создается отдельная рабочая папка:

```text
data/runs/<period>/<slug>/
  input/
  state/
  raw/
  output/
  cache/
  logs/
  tmp/
  manifest.json
```

Например:

```text
data/runs/2026-05/кофейня/
data/runs/2026-05/пекарня/
data/runs/2026-05/салон-красоты/
data/runs/2026-05/бар/
```

Внутри каждого среза сохраняются свои:

- `state/parsing_queue.csv`
- `state/seen_ids.db`
- `raw/raw_data.jsonl`
- `output/result.csv`
- `output/raw_data.xlsx`
- `output/organizations.db`
- `manifest.json`

Это важно: повторный месячный сбор той же категории не конфликтует с предыдущим `seen_ids.db` и не смешивает сырье разных месяцев.

## Запуск одного среза

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня"
```

Скрипт:

1. создает `data/runs/2026-05/кофейня/`;
2. копирует входной полигон из `data/input/`;
3. копирует кеш воды из `data/cache/`, если он есть;
4. выставляет runtime-пути через переменные окружения;
5. генерирует очередь;
6. запускает текущий парсер;
7. создает CSV/XLSX и `organizations.db`;
8. обновляет `manifest.json`.

## Запуск нескольких категорий

```powershell
foreach ($q in @("кофейня", "пекарня", "салон красоты", "бар")) {
    python scripts/run_category_snapshot.py --period 2026-05 --query $q
}
```

Для запроса `салон красоты` slug будет `салон-красоты`.

## Resume

Если срез прервался, повтори ту же команду:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня"
```

Если `state/parsing_queue.csv` уже есть, скрипт не пересоздает очередь и парсер продолжает только `pending` строки. Это сохраняет текущую resume-семантику.

Чтобы сознательно пересоздать очередь с нуля:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня" --rebuild-queue
```

Используй это только если понимаешь, что статусы старой очереди будут перезаписаны.

## Dry preparation

Подготовить папку и `manifest.json` без браузерного парсинга:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня" --skip-grid --skip-scraper --skip-excel --skip-db
```

## Сравнение двух месяцев

```powershell
python scripts/compare_snapshots.py `
  --old data/runs/2026-04/кофейня/output/result.csv `
  --new data/runs/2026-05/кофейня/output/result.csv `
  --output data/analytics/compare/2026-04_to_2026-05/кофейня
```

Результат:

```text
data/analytics/compare/2026-04_to_2026-05/кофейня/
  added.csv
  removed.csv
  changed.csv
  summary.json
```

`added.csv` показывает новые организации, `removed.csv` - исчезнувшие, `changed.csv` - организации, у которых поменялись сравниваемые поля.

По умолчанию сравниваются:

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

Можно явно указать поля:

```powershell
python scripts/compare_snapshots.py `
  --old data/runs/2026-04/кофейня/output/result.csv `
  --new data/runs/2026-05/кофейня/output/result.csv `
  --output data/analytics/compare/ratings/кофейня `
  --field ratingData_ratingCount `
  --field ratingData_ratingValue
```

## Контракты

Snapshot-слой не меняет текущие контракты:

- очередь остается `url;query;bbox;status`;
- raw JSONL остается append-only;
- `FINAL_COLUMNS` не меняется;
- proxy/retry/captcha логика не меняется;
- старые команды из `scripts/` продолжают использовать `data/state`, `data/raw`, `data/output`.

Новые команды только временно переопределяют `YANDEX_SCRAPER_DATA_DIR` и `YANDEX_SCRAPER_SEARCH_QUERIES` для конкретного процесса.
