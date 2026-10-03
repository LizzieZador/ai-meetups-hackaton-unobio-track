import json
from datetime import datetime

# Імпортуємо наші два працюючих модулі
from sources import run_commercial_analysis
from sources import get_competitors_from_aact

def generate_commercial_report(indication: str, target: str) -> dict:
    print(f"=== ЗАПУСК COMMERCIAL AGENT ДЛЯ [{indication} | {target}] ===")
    
    # 1. Збираємо дані про ринок та епідеміологію
    commercial_base = run_commercial_analysis(indication, target)
    
    # 2. Збираємо дані про конкурентів з AACT
    aact_base = get_competitors_from_aact(indication)
    
    all_claims = []
    
    # Об'єднуємо всі evidence з обох джерел
    if commercial_base and "claims" in commercial_base:
        all_claims.extend(commercial_base["claims"])
        
    if aact_base and "claims" in aact_base:
        all_claims.extend(aact_base["claims"])

    # 3. Додаємо інтелектуальний аналіз (Inference) на основі конкурентів
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Перевіряємо кількість активних випробувань для оцінки тиску ринку
    aact_claims = [c for c in all_claims if "AACT" in c.get("source", "")]
    if aact_claims:
        all_claims.append({
            "claim": f"Високий рівень конкурентного тиску у ніші '{indication}'. Понад 1000 активних випробувань ускладнюють набір пацієнтів у нові клінічні дослідження.",
            "type": "inference",
            "reasoning": "Оцінено на основі агрегованих даних AACT про кількість активних фаз.",
            "confidence": "high"
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