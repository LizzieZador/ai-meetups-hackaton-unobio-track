import csv
import json
import os
from datetime import datetime

# Отримуємо корінь проєкту (виходимо з папки sources/ на рівень вгору)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
AACT_DIR = os.path.join(PROJECT_ROOT, "data", "aact")


def get_competitors_from_aact(indication: str) -> dict:
  """Шукає кількість активних випробувань (конкурентів) у локальних файлах AACT."""
  conditions_file = os.path.join(AACT_DIR, "conditions.txt")
  studies_file = os.path.join(AACT_DIR, "studies.txt")

  if not os.path.exists(conditions_file) or not os.path.exists(studies_file):
    print(
        f"Помилка: Не знайдено файли AACT за шляхом {AACT_DIR}. Перевірте"
        " наявність conditions.txt та studies.txt"
    )
    return None

  date_str = datetime.now().strftime("%Y-%m-%d")
  target_nct_ids = set()

  with open(conditions_file, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f, delimiter="|")
    for row in reader:
      condition_name = row.get("downcase_name", "")
      if condition_name and indication.lower() in condition_name.lower():
        target_nct_ids.add(row.get("nct_id"))

  if not target_nct_ids:
    return _build_empty_response(indication, date_str)

  active_trials_count = 0
  phases_distribution = {}

  with open(studies_file, mode="r", encoding="utf-8") as f:
    reader = csv.DictReader(f, delimiter="|")
    for row in reader:
      if row.get("nct_id") in target_nct_ids:
        status = row.get("overall_status", "").strip().lower()
        is_active = any(
            k in status for k in ["recruit", "active", "enrolling"]
        ) and not any(
            k in status for k in ["completed", "terminated", "withdrawn"]
        )

        if is_active:
          active_trials_count += 1
          phase = row.get("phase", "Unknown").strip()
          phases_distribution[phase] = phases_distribution.get(phase, 0) + 1

  return {
      "source_type": "aact",
      "indication": indication,
      "raw_snippet": (
          f"Знайдено {active_trials_count} активних клінічних випробувань за"
          f" показанням '{indication}'. Розподіл за фазами:"
          f" {phases_distribution}"
      ),
      "active_trials_count": active_trials_count,
      "phases_distribution": phases_distribution,
  }


def _build_empty_response(indication: str, date_str: str) -> dict:
  return {
      "source_type": "aact",
      "indication": indication,
      "raw_snippet": (
          f"Не знайдено клінічних випробувань у базі AACT для показання"
          f" '{indication}'."
      ),
      "active_trials_count": 0,
  }