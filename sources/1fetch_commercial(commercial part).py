import csv
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
CSV_PATH = os.path.join(PROJECT_ROOT, "data", "market_data.csv")


def get_epidemiology_from_csv(indication_query: str) -> dict:
  if not os.path.exists(CSV_PATH):
    print(f"Помилка: Файл {CSV_PATH} не знайдено!")
    return None

  with open(CSV_PATH, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
      if (
          row["Indication"].lower() in indication_query.lower()
          or indication_query.lower() in row["Indication"].lower()
      ):
        return row
  return None