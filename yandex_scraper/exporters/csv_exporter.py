import pandas as pd

from yandex_scraper.config import CSV_FILE, FINAL_COLUMNS, JSONL_FILE

def convert_to_csv() -> None:
    if not JSONL_FILE.exists() or JSONL_FILE.stat().st_size == 0:
        print("[!] raw_data.jsonl пустой.")
        return
    print(f"\n[*] Конвертация: {JSONL_FILE} → {CSV_FILE}")
    df = pd.read_json(JSONL_FILE, lines=True, dtype=str).fillna("")
    print(f"[+] Строк: {len(df)}")
    missing = [c for c in FINAL_COLUMNS if c not in df.columns]
    if missing:
        print(f"[!] Отсутствующие колонки: {missing}")
    df = df.reindex(columns=FINAL_COLUMNS, fill_value="")
    for col_name, missing_text in (
        ("ratingData_ratingCount", "отзывов нет"),
        ("ratingData_ratingValue", "рейтинга нет"),
    ):
        df[col_name] = (
            df[col_name]
            .astype(str)
            .str.strip()
            .replace({"": missing_text, "nan": missing_text, "None": missing_text})
        )
    before = len(df)
    df = df.drop_duplicates(subset=["title", "fullAddress"], keep="first")
    if before != len(df):
        print(f"[+] Дедупликация: удалено {before - len(df)} дублей")
    df = df.sort_values("fullAddress").reset_index(drop=True)
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig", sep=";")
    print(f"[OK] {CSV_FILE} | Организаций: {len(df)}")

