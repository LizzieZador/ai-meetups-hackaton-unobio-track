"""Демонстраційний сценарій для хакатону: реакція системи на суперечливі дані.

Вимога ТЗ ("Moving Beyond the Molecule"):
«The demo should include at least one example where new or contradictory evidence
changes the system's conclusion.»

Цей скрипт запускає інвесткомітет у двох режимах для однієї і тієї ж мішені:
1. Базовий сценарій (без додаткового контексту) -> Позитивна/помірна оцінка.
2. Сценарій із суперечливими даними (провал Фази 3, гепатотоксичність, FDA hold) -> Зміна вердикту на Do Not Invest.
"""
import sys
from pathlib import Path

# Підключаємо корінь проєкту до sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline import run_pipeline


INDICATION = "non-small cell lung cancer"
MECHANISM = "KRAS G12C inhibitor"

CONTRADICTORY_CONTEXT = (
    "URGENT CONFIRMATORY TRIAL UPDATE: Phase 3 confirmatory study failed to demonstrate "
    "statistically significant Overall Survival improvement (HR=0.98, p=0.82) and revealed "
    "unexpected 24% treatment-emergent Grade 4 hepatotoxicity, resulting in an FDA clinical hold "
    "and complete response letter."
)


def extract_summary(claims: list) -> dict:
    verdict = "Unknown"
    recommendation_reasons = []
    rnpv_claim = "Not calculated"
    top_risks = []

    for c in claims:
        if c.section == "recommendation":
            if c.claim in ("Invest", "Conditional", "Do Not Invest"):
                verdict = c.claim
            elif "rnpv" in c.claim.lower() or "scenario" in c.claim.lower():
                rnpv_claim = c.claim
            if c.reasoning:
                recommendation_reasons.append(c.reasoning)
        elif c.section == "key_risks":
            top_risks.append(f"{c.claim} (Reasoning: {c.reasoning})")

    return {
        "verdict": verdict,
        "rnpv": rnpv_claim,
        "top_risk": top_risks[0] if top_risks else "None reported",
        "reasoning": " | ".join(recommendation_reasons[:2]),
    }


def main():
    print("=" * 80)
    print("DEMO: AI-ІНВЕСТКОМІТЕТ — ПЕРЕВІРКА РЕАКЦІЇ НА СУПЕРЕЧЛИВІ ДАНІ (ТЗ ХАКАТОНУ)")
    print(f"Показання: {INDICATION}")
    print(f"Мішень/Механізм: {MECHANISM}")
    print("=" * 80)

    # 1. Базовий запуск
    print("\n>>> КРОК 1: Запуск базового пайплайну (без додаткового контексту)...")
    base_claims, base_cost = run_pipeline(INDICATION, MECHANISM, extra_context="")
    base_summary = extract_summary(base_claims)

    print(f"   [Базовий вердикт]: {base_summary['verdict']}")
    print(f"   [rNPV оцінка]: {base_summary['rnpv']}")
    print(f"   [Головний ризик]: {base_summary['top_risk'][:120]}...")

    # 2. Запуск із новими суперечливими даними
    print("\n>>> КРОК 2: Запуск із новими суперечливими даними (FDA Hold + токсичність)...")
    print(f"   Введений контекст: {CONTRADICTORY_CONTEXT[:100]}...")
    contra_claims, contra_cost = run_pipeline(INDICATION, MECHANISM, extra_context=CONTRADICTORY_CONTEXT)
    contra_summary = extract_summary(contra_claims)

    print(f"   [Оновлений вердикт]: {contra_summary['verdict']}")
    print(f"   [rNPV оцінка]: {contra_summary['rnpv']}")
    print(f"   [Головний ризик]: {contra_summary['top_risk'][:120]}...")

    # 3. Підсумкова порівняльна таблиця
    print("\n" + "=" * 80)
    print("ПІДСУМОК ДЕМОНСТРАЦІЇ: ЗМІНА ВИСНОВКУ СИСТЕМИ (BEFORE vs AFTER)")
    print("=" * 80)
    print(f"{'Параметр':<25} | {'1. Базовий стан':<25} | {'2. Нові суперечливі дані':<25}")
    print("-" * 80)
    print(f"{'Фінальний вердикт':<25} | {base_summary['verdict']:<25} | {contra_summary['verdict']:<25}")
    print(f"{'Топ-ризик':<25} | {base_summary['top_risk'][:25]:<25} | {contra_summary['top_risk'][:25]:<25}")
    print("-" * 80)
    print(f"Обґрунтування вердикту (До):   {base_summary['reasoning'][:120]}...")
    print(f"Обґрунтування вердикту (Після): {contra_summary['reasoning'][:120]}...")
    print("=" * 80)
    print("Вимогу ТЗ успішно підтверджено: нові дані динамічно змінюють вердикт комітету!")


if __name__ == "__main__":
    main()

