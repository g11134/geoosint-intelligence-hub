# Project Structure

Проект переведен на clean-layout без root-wrappers.

## Текущая структура

```text
yandex-parser-spb-version-2000/
  README.md
  requirements.txt
  .gitignore
  AGENTS.md

  scripts/
    prepare_polygon.py
    generate_grid.py
    import_geojson_to_queue.py
    run_scraper.py
    run_api.py
    build_organizations_db.py
    export_grid_to_geojson.py
    export_excel.py
    run_category_snapshot.py
    compare_snapshots.py

  docs/
    WORKFLOW.md
    DATA_CONTRACTS.md
    OPERATIONS.md
    PROJECT_STRUCTURE.md
    RECURRING_SNAPSHOTS.md

  data/
    input/
      spb_polygon.geojson
      spb_polygon.json
    state/
      parsing_queue.csv
      seen_ids.db
      seen_ids.db-shm
      seen_ids.db-wal
    raw/
      raw_data.jsonl
    output/
      result.csv
      raw_data.xlsx
      organizations.db
      grid_visualization.geojson
    cache/
      water_mask_cache.geojson
    logs/
      grid_generator.log
    tmp/
      __queue_smoke_test.csv
    runs/
      <period>/
        <slug>/
    analytics/
      compare/
    backup_before_refactor/

  tools/
    get-pip.py

  yandex_scraper/
    config.py
    runner.py
    browser.py
    constants.py
    extraction.py
    parsing.py
    queue_ops.py
    rate_limiter.py
    storage.py
    worker.py
    snapshots.py
    analytics/
      snapshot_compare.py
    api/
      server.py
      models.py
      organization_store.py
      data_loader.py
    exporters/
      csv_exporter.py
      excel_exporter.py
    pipeline/
      prepare_polygon.py
      grid_generator.py
      import_geojson_to_queue.py
      export_grid_to_geojson.py
```

## Entry points

Новые команды запуска находятся в `scripts/`.

Старые root-команды вроде `python 2_yandex_scraper.py` больше не поддерживаются.

## Core package

`yandex_scraper/runner.py` управляет основным запуском парсера.

`yandex_scraper/worker.py` управляет браузером, прокси, retry, captcha/error rotation и сохранением статуса очереди.

`yandex_scraper/parsing.py` обрабатывает одну ячейку, извлекает карточки и пишет JSONL.

`yandex_scraper/extraction.py` нормализует найденные организации в текущую схему.

`yandex_scraper/queue_ops.py` читает и сохраняет очередь.

`yandex_scraper/storage.py` ведет SQLite-дедупликацию.

`yandex_scraper/exporters/` содержит CSV/XLSX экспорт.

`yandex_scraper/pipeline/` содержит подготовительные и сервисные шаги.

`yandex_scraper/snapshots.py` управляет изолированными месячными срезами по категориям.

`yandex_scraper/analytics/snapshot_compare.py` сравнивает два экспортированных `result.csv`.

## Data zones

`data/input/` содержит входные файлы, которые нужны для построения сетки.

`data/state/` содержит файлы, необходимые для resume.

`data/raw/` содержит append-only сырье.

`data/output/` содержит пересоздаваемые экспортные файлы.

`data/cache/` содержит пересоздаваемый кеш.

`data/logs/` содержит логи.

`data/tmp/` содержит временные проверки.

`data/runs/` содержит генерируемые изолированные snapshot-срезы по периодам и категориям.

`data/analytics/` содержит генерируемые результаты сравнений и аналитические артефакты.

## Инварианты

Нельзя менять схему очереди без миграции.

Нельзя менять `FINAL_COLUMNS` без проверки экспорта.

Нельзя переносить `seen_ids.db` отдельно от WAL/SHM файлов.

Нельзя удалять `raw_data.jsonl`, если нужно сохранить уже собранное сырье.
