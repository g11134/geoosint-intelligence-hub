import pandas as pd

from yandex_scraper.config import ENRICHED_COLUMNS, ENRICHED_CSV_FILE, ENRICHED_JSONL_FILE


def convert_enriched_to_csv() -> None:
    if not ENRICHED_JSONL_FILE.exists() or ENRICHED_JSONL_FILE.stat().st_size == 0:
        print("[!] enriched_data.jsonl пустой.")
        return

    print(f"\n[*] Конвертация enriched: {ENRICHED_JSONL_FILE} -> {ENRICHED_CSV_FILE}")
    df = pd.read_json(ENRICHED_JSONL_FILE, lines=True, dtype=str).fillna("")
    print(f"[+] Enriched строк: {len(df)}")

    missing = [column for column in ENRICHED_COLUMNS if column not in df.columns]
    if missing:
        print(f"[!] Enriched отсутствующие колонки: {missing}")

    df = df.reindex(columns=ENRICHED_COLUMNS, fill_value="")
    dedup_subset = [column for column in ("yandex_id", "title", "fullAddress") if column in df.columns]
    if dedup_subset:
        before = len(df)
        df = df.drop_duplicates(subset=dedup_subset, keep="first")
        if before != len(df):
            print(f"[+] Enriched дедупликация: удалено {before - len(df)} дублей")

    sort_columns = [column for column in ("fullAddress", "title") if column in df.columns]
    if sort_columns:
        df = df.sort_values(sort_columns).reset_index(drop=True)

    ENRICHED_CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ENRICHED_CSV_FILE, index=False, encoding="utf-8-sig", sep=";")
    print(f"[OK] {ENRICHED_CSV_FILE} | Enriched организаций: {len(df)}")
