# AI-інвесткомітет для біотеху

Веб-застосунок: на вхід — **Mechanism/Target + Indication**, на вихід — вердикт
**Invest / Conditional / Do Not Invest** і 10 карток з даними, де кожне
твердження чітко позначене як **evidence** (факт із джерела з посиланням) або
**inference** (судження моделі з обґрунтуванням і рівнем впевненості).

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env    # ключі LLM і (опційно) доступ до AACT
python server.py        # http://127.0.0.1:5000
```

Запит вводиться одним рядком: `KRAS G12C | Non-Small Cell Lung Cancer`
(роздільники: `|`, `×`, `/`, `for`, `in`) або двома полями в Detailed search.

## Як це працює

```
collect_sources() -> scientific_agent -> clinical_agent -> commercial_agent
                  -> детермінована фінансова модель (rNPV) -> chair_agent
```

- **Агенти** (`agents/`) працюють послідовно: висновок одного виміру —
  вхід наступного. Голова комітету додає вердикт, ризики, капітал, unknowns
  і diligence questions; він ніколи не перетворює inference на evidence.
- **Evidence перевіряється кодом, а не довірою до LLM** (`agents/base.py`):
  evidence-claim приймається, лише якщо посилається на реально отримане
  джерело і його цитата дослівно є в тексті цього джерела.
- **LLM** (`llm.py`): ChatGPT → Gemini → Claude з автоматичним fallback,
  дисковий кеш відповідей (повторний запит не витрачає токени).
- **Картки** будуються детерміновано без LLM (`cards.py`, `card_tables.py`).
- **Assistant** (`assistant.py`) відповідає лише на основі завантаженого звіту
  та живих запитів до підключених баз — не з власних знань моделі. На питання
  «які препарати є / у що інвестувати при X» будує ланцюжок
  потреба → дані → ранжовані кандидати → обґрунтування → пропозиція оцінки,
  де кандидати беруться з Open Targets (стадія саме для цієї хвороби),
  openFDA / Orange Book (схвалення в США) і ClinicalTrials.gov (активні
  дослідження) — `sources/fetch_indication_drugs.py`.
- Кожен запуск пише JSON-трейс у `data/reports/` (джерела, claims, вартість).

## Джерела

| Джерело | Що дає |
|---|---|
| PubMed | наукові публікації за 8 дослідницькими напрямами |
| Open Targets | асоціація мішень–хвороба, відомі препарати, профілі (мішені, модальність, стадія) |
| ClinicalTrials.gov | дослідження, фази, статуси, препарати-інтервенції |
| AACT | агрегована база ClinicalTrials.gov (Postgres або flat-файли в `data/aact/`) |
| openFDA Drugs@FDA | схвалення, спонсори, designations (Orphan, Priority Review) |
| FDA Orange Book | патенти США з датами закінчення та регуляторні ексклюзивності |
| Lens.org | патентні заявки по світу (потрібен `LENS_API_TOKEN`) |
| SEC EDGAR | 8-K filings компаній (активність, не sentiment) |
| `data/market_data.csv` | епідеміологія та ціни (ручні, позначені як неверифіковані) |

Будь-яке недоступне джерело просто пропускається — пайплайн продовжує роботу.
Для бектестів (`run_pipeline(..., cutoff_date=...)`) знімки «на сьогодні»
(профілі препаратів, Orange Book) вимикаються, щоб не підглядати в майбутнє.

## Структура

```
.
├── server.py              # Flask: веб-UI + /api/evaluate, /api/assistant
├── pipeline.py            # оркестрація + JSON-трейс
├── assistant.py           # чат-асистент, заземлений на дані
├── cards.py               # 10 карток: які секції/джерела куди
├── card_tables.py         # таблиці/графіки карток (без LLM)
├── raw_data.py            # список конкурентів, валідні джерела
├── schema.py              # Claim: evidence/inference + секції
├── financials.py          # детермінований rNPV і капітал
├── market_model.py        # ринкова модель
├── llm.py                 # провайдери LLM, кеш, облік токенів
├── agents/                # scientific, clinical, commercial, chair
├── sources/               # фетчери всіх джерел
├── web/                   # index.html, app.js, style.css
├── tests/                 # офлайн-тести фінмоделі та ризиків
└── data/                  # market_data.csv, aact/, reports/
```

## Тести

```bash
python -m tests.test_financials
python -m unittest tests.test_structured_risks
```

Обидва офлайн: без мережі й без витрат токенів.
