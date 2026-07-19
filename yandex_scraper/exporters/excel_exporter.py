import pandas as pd
from pathlib import Path

def load_and_clean_jsonl(jsonl_path: Path) -> pd.DataFrame:
    """Загружает JSONL с дедупликацией и очисткой."""
    if not jsonl_path.exists():
        print(f"[ОШИБКА] Файл не найден: {jsonl_path}")
        return pd.DataFrame()
    
    print(f"[+] Загружаем {jsonl_path}")
    try:
        df = pd.read_json(jsonl_path, lines=True, dtype=str).fillna("")
        print(f"[+] Строк загружено: {len(df):,}")
        return df
    except Exception as e:
        print(f"[ОШИБКА] Не удалось прочитать JSONL: {e}")
        return pd.DataFrame()


def deduplicate_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """Дедупликация и сортировка как в парсере."""
    before = len(df)
    
    # Дедуп по title + fullAddress (как в 2_yandex_scraper.py)
    df = df.drop_duplicates(subset=["title", "fullAddress"], keep="first")
    
    # Сортировка по адресу
    df = df.sort_values("fullAddress").reset_index(drop=True)
    
    after = len(df)
    if before != after:
        print(f"[+] Дедупликация: {before:,} → {after:,} (-{before-after:,})")
    
    return df


def save_excel_with_autofit(df: pd.DataFrame, output_path: Path):
    """Сохраняет в XLSX с автошириной колонок."""
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Organizations')
        
        worksheet = writer.sheets['Organizations']
        
        # Автоширина колонок (max 50 символов)
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            
            for cell in column:
                try:
                    cell_length = len(str(cell.value or ''))
                    if cell_length > max_length:
                        max_length = cell_length
                except:
                    pass
            
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
        
        # Замораживаем первую строку (заголовки)
        worksheet.freeze_panes = 'A2'


def main():
    from yandex_scraper.config import CSV_FILE, EXCEL_FILE, FINAL_COLUMNS, JSONL_FILE
    
    jsonl_path = JSONL_FILE
    excel_path = EXCEL_FILE
    
    # Загрузка + очистка
    df = load_and_clean_jsonl(jsonl_path)
    if df.empty:
        print("[ОШИБКА] Нет данных для конвертации")
        return
    
    # Дедупликация и сортировка (как в парсере)
    df = deduplicate_and_sort(df)
    
    # Приводим к FINAL_COLUMNS
    missing_cols = [col for col in FINAL_COLUMNS if col not in df.columns]
    if missing_cols:
        print(f"[!] Добавляем отсутствующие колонки: {missing_cols}")
    
    df = df.reindex(columns=FINAL_COLUMNS, fill_value="")
    
    # Сохраняем XLSX
    save_excel_with_autofit(df, excel_path)
    
    # Дополнительно сохраняем CSV (совместимость)
    df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig", sep=";")
    
    print(f"\n[OK] Файлы созданы:")
    print(f"    📊 {excel_path} ({len(df):,} строк)")
    print(f"    📄 {CSV_FILE} ({len(df):,} строк)")
    print(f"\n[→] XLSX готов для Excel/Google Sheets")


if __name__ == "__main__":
    main()
