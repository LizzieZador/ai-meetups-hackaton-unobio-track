import json
import sys
from pathlib import Path

# Дозволяє запускати і як "python agents/test_local_run.py" напряму, і як
# "python -m agents.test_local_run" з кореня репо.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Без цього AACT_DB_* з .env не підвантажуються і get_competitors_from_aact()
# мовчки падає назад на (відсутні) flat-файли — скрипт "успішно" завершується,
# але без жодного AACT-claim, що виглядає як неправильно малий результат.
load_dotenv()

# Імпортуємо наші два працюючих модулі
from sources import run_commercial_analysis
from sources import get_competitors_from_aact

def generate_commercial_report(indication: str, target: str) -> dict:
    print(f"=== ЗАПУСК COMMERCIAL AGENT ДЛЯ [{indication} | {target}] ===")

    # 1. Збираємо дані про ринок та епідеміологію
    commercial_base = run_commercial_analysis(indication, target)

    # 2. Збираємо дані про конкурентів з AACT (indication + target/mechanism)
    aact_base = get_competitors_from_aact(indication, target)

    all_claims = []

    # Об'єднуємо evidence з epidemiology-джерела
    if commercial_base and "claims" in commercial_base:
        all_claims.extend(commercial_base["claims"])

    # get_competitors_from_aact() повертає сирий аналіз (count + розподіл за
    # фазами), а не вже готові claims — перетворюємо його тут в evidence claim
    # за тією ж схемою, що й решта пайплайна (schema.py: Claim).
    if aact_base:
        all_claims.append({
            "claim": aact_base["raw_snippet"],
            "type": "evidence",
            "section": "competitive_landscape",
            "source": aact_base["source"],
            "date_accessed": aact_base["date_accessed"],
        })

    # 3. Додаємо інтелектуальний аналіз (inference) на основі РЕАЛЬНОЇ
    # кількості активних випробувань, а не вигаданого числа.
    if aact_base and aact_base["active_trials_count"] > 0:
        all_claims.append({
            "claim": (
                f"Високий рівень конкурентного тиску у ніші '{indication}'. "
                f"{aact_base['active_trials_count']} активних випробувань ускладнюють "
                f"набір пацієнтів у нові клінічні дослідження."
            ),
            "type": "inference",
            "section": "competitive_landscape",
            "reasoning": "Оцінено на основі агрегованих даних AACT про кількість активних фаз.",
            "confidence": "medium",
        })

    financial_metrics = commercial_base.get("financial_metrics", {}) if commercial_base else {}

    return {
        "agent": "commercial_agent",
        "indication": indication,
        "target": target,
        "claims": all_claims,
        "financial_metrics": financial_metrics
    }

if __name__ == "__main__":
    report = generate_commercial_report("Non-Small Cell Lung Cancer", "KRAS G12C")
    
    # Зберігаємо підсумковий результат у файл для Голови Комітету (Chair Agent)
    with open("commercial_agent_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    print("\n Звіт Commercial Agent успішно згенеровано та збережено у 'commercial_agent_output.json'!")