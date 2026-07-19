# Yandex Parser SPB

Система сбора, нормализации и экспорта данных организаций из Яндекс Карт по сетке Санкт-Петербурга.

Проект переведен на clean-layout без старых root-wrappers. Запуск теперь выполняется через `scripts/` или через Python-модули пакета `yandex_scraper`.

## Быстрый старт

Установить зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m patchright install chromium
```

## Flutter-интерфейс

https://geoosint.hubbadubbahub.ru/

Каталог `frontend/` содержит Flutter Web-интерфейс для карты организаций и
аналитики отзывов. Production API URL передаётся только как публичный адрес
при сборке:

```powershell
cd frontend
flutter pub get
flutter build web --release --dart-define=API_BASE_URL=https://hubbadubbahub.ru
```

Секретные ключи нельзя передавать во Flutter Web: всё, что попадает в web-сборку,
доступно пользователю браузера. Прокси, AI-ключи и рабочие данные остаются на
backend-сервере.

Подготовить полигон, если `data/input/spb_polygon.json` еще не создан:

```powershell
python scripts/prepare_polygon.py
```

Сгенерировать очередь:

```powershell
python scripts/generate_grid.py
```

Запустить парсер:

```powershell
python scripts/run_scraper.py
```

Экспортировать Excel:

```powershell
python scripts/export_excel.py
```

Build the SQLite read-model for API/Flutter:

```powershell
python scripts/build_organizations_db.py
```

Run the API:

```powershell
python scripts/run_api.py
```

Обновить GeoJSON-визуализацию статусов очереди:

```powershell
python scripts/export_grid_to_geojson.py
```

## Основные директории

`yandex_scraper/` содержит рабочий код парсера.

`scripts/` содержит новые команды запуска.

`data/input/` содержит входные геоданные.

`data/state/` содержит очередь и SQLite-состояние дедупликации.

`data/raw/` содержит сырые JSONL-результаты.

`data/output/` содержит CSV/XLSX/GeoJSON-экспорты.

`data/cache/` содержит кеш водных объектов.

`docs/` содержит подробные инструкции и контракты данных.

## Важные файлы

`yandex_scraper/config.py` является центральной конфигурацией путей, прокси, запросов, лимитов, колонок экспорта и параметров сетки.

`data/state/parsing_queue.csv` является рабочей очередью. Не удаляй ее во время незавершенного парсинга.

`data/state/seen_ids.db` является базой дедупликации. Переноси ее только вместе с `seen_ids.db-shm` и `seen_ids.db-wal`.

`data/raw/raw_data.jsonl` является append-only сырьем парсера.

## Документация

Смотри [workflow](docs/WORKFLOW.md), [data contracts](docs/DATA_CONTRACTS.md), [operations](docs/OPERATIONS.md), [project structure](docs/PROJECT_STRUCTURE.md) и [recurring snapshots](docs/RECURRING_SNAPSHOTS.md).

## Ежемесячные category snapshots

Для регулярных срезов по категориям используй:

```powershell
python scripts/run_category_snapshot.py --period 2026-05 --query "кофейня"
```

Срезы сохраняются в `data/runs/<period>/<slug>/` с отдельными `state`, `raw` и `output`.

Сравнение двух месяцев:

```powershell
python scripts/compare_snapshots.py --old data/runs/2026-04/кофейня/output/result.csv --new data/runs/2026-05/кофейня/output/result.csv --output data/analytics/compare/2026-04_to_2026-05/кофейня
```
