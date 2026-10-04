# AI-інвесткомітет для біотеху

За 7 днів зібрати систему, яка на вхід бере **Disease/Indication + Mechanism/target**
(опційно — modality, development stage, preclinical results, biomarkers, route of
administration), а на вихід дає **Investment Underwriting Report** з вердиктом
(**Invest / Conditional / Do Not Invest**) і чітким розмежуванням **evidence**
(факт із джерела) та **AI inference** (судження моделі) по кожному висновку.

Технічне завдання — `plan/Moving Beyond the Molecule — How to Underwrite Biotech.docx`
(офіційний бриф хакатону). 7-денний план виконання команди, похідний від нього —
`plan/План AI-інвесткомітет для біотеху (7 днів, команда 5).docx`.

## Required Output — обов'язкові секції звіту

Звіт генерується форматуванням `Claim`-об'єктів у ці секції, точно за списком
"Required Output" з ТЗ (`report.py`, `schema.py: SECTION_TITLES`):

1. **Recommendation** — Invest / Conditional / Do Not Invest
2. **Scientific thesis**
3. **Human translation thesis**
4. **Clinical development plan**
5. **Competitive landscape**
6. **Commercial opportunity**
7. **Capital required to reach the next major milestone**
8. **Key risks and failure modes**
9. **Critical unknowns**
10. **5–10 highest-value diligence questions**
11. **Sources supporting major claims** — збирається кодом із evidence-claims, а не генерується агентом

## Команда, ролі та секції звіту

| Роль | Відповідає за | Джерела | Секції звіту |
|---|---|---|---|
| Тімлід / Голова комітету | Фінальний синтез, вердикт, ризики, risk-adjusted return | — (агрегує) | Recommendation, Capital Required, Key Risks, Critical Unknowns, Diligence Questions |
| Науковий аналітик | Біологічна валідація мішені, human translation | PubMed, Open Targets | Scientific Thesis, Human Translation Thesis |
| Клінічний аналітик | Стадія розробки, дизайн випробувань, регуляторний шлях | ClinicalTrials.gov, AACT | Clinical Development Plan |
| Комерційний аналітик | Конкуренти, unmet need, ринок (переважно inference) | похідні з AACT/Open Targets | Competitive Landscape, Commercial Opportunity |
| Інженер пайплайну | Код, інтеграції, evidence/inference-формат, стабільність демо | — (vibe-coding усього пайплайну) | Працездатна система |

Ролі без ротації. Ланцюжок: biology → human translation → clinical development →
regulations → competition → commercial value → investment return — висновок
одного виміру є вхідними даними наступного (так влаштований `pipeline.py`).

> Задача "Disease and Unmet Need" (патієнтна популяція, стандарт лікування,
> unmet need) з ТЗ не має окремої ролі серед п'яти фіксованих — покрита
> Комерційним аналітиком у секції Commercial Opportunity, оскільки тематично
> найближча і так само спирається на похідні дані, а не пряме джерело.

## Архітектура

```
collect_sources() -> scientific_agent() -> clinical_agent() -> commercial_agent() -> chair_agent() -> render_report()
```

3 агенти (scientific, clinical, commercial) працюють послідовно по ланцюжку,
голова комітету (`chair_agent`) додає нові claims (вердикт, ризики, капітал,
unknowns, diligence questions) і ніколи не перетворює `inference` на `evidence`
чи не змінює `section` вже отриманих claims.

Стек навмисно мінімальний: Python, прямі виклики Claude API (`anthropic` SDK,
без LangChain/CrewAI/LangGraph), Streamlit для UI, JSON-файли на диску замість БД.

```
.
├── app.py                      # Streamlit UI з інтерактивним drill-down
├── main.py                     # CLI для тестування без UI
├── pipeline.py                 # orchestration + JSON-трейс кожного запуску
├── schema.py                   # Claim: evidence/inference-схема + секції звіту
├── llm.py                      # виклик Claude API + трекінг токенів/вартості
├── report.py                   # рендер claims -> Markdown за секціями ТЗ
├── sources/
│   ├── cache.py                # JSON-кеш відповідей API
│   ├── fetch_pubmed.py
│   ├── fetch_opentargets.py
│   ├── fetch_clinicaltrials.py
│   └── fetch_aact.py           # жива Postgres БД, fallback — flat-файли з data/aact/
├── agents/
│   ├── scientific_agent.py
│   ├── clinical_agent.py
│   ├── commercial_agent.py
│   └── chair_agent.py
├── cache/                      # кешовані відповіді джерел (git-ignored)
├── data/aact/                  # сюди покласти studies.txt/conditions.txt з AACT
└── data/reports/               # JSON-трейси запусків (git-ignored)
```

## Evidence vs inference

Кожне твердження в системі — об'єкт `Claim` (`schema.py`), а не рядок тексту.
Кожен claim також прив'язаний до `section` зі списку Required Output:

```json
{"claim": "Експресія мішені підвищена при метастазах", "type": "evidence", "section": "scientific_thesis", "source": "PubMed PMID:12345678", "url": "...", "date_accessed": "2026-10-04", "exact_snippet": "..."}
{"claim": "Це підвищує ймовірність відповіді на терапію в цій підгрупі пацієнтів", "type": "inference", "section": "human_translation_thesis", "reasoning": "...", "confidence": "medium"}
```

- `type: evidence` — модель НЕ генерує це поле сама; `source` обов'язковий, інакше
  `parse_claims()` відкидає запис кодом, а не довірою до LLM.
- `type: inference` — обов'язкові `reasoning` і `confidence` (low/medium/high).
- `section`, що не входить у `SECTION_TITLES`, теж відкидається кодом — агент не
  може "вигадати" нову секцію на льоту.

## Interactive Evidence

ТЗ вимагає можливість переходу **Recommendation → Claims → Evidence → Sources**.
`app.py` реалізує це через Streamlit expanders: вердикт — одразу вгорі, далі
розгортувані секції з claims, кожен claim показує своє джерело або reasoning,
і окремий розгортуваний список усіх зібраних джерел унизу.

## Reproducibility (Required Deliverable)

- **Source attribution** — кожен evidence claim має точне джерело й URL.
- **Model/API usage** — `llm.py`, модель `claude-sonnet-5` через `anthropic` SDK.
- **Approximate cost per run** — `llm.py` рахує токени з кожної відповіді API,
  `pipeline.py` виводить orientировну вартість (ціни Anthropic: $2/$10 за 1M
  input/output токенів) унизу кожного звіту.
- **Logs / traces** — кожен запуск `run_pipeline()` пише JSON-трейс у
  `data/reports/run_<timestamp>_<pair>.json`: indication, mechanism, сирі
  джерела, усі claims, вартість — повна відтворюваність одного запуску.

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env   # вписати ANTHROPIC_API_KEY (+ опційно AACT_DB_* для живої БД)
streamlit run app.py
# або веб-UI з боковою панеллю пошуків і картками звіту (server.py + web/):
python server.py   # http://127.0.0.1:5000
# або без UI:
python main.py "non-small cell lung cancer" "KRAS G12C inhibitor"
python main.py "non-small cell lung cancer" "KRAS G12C inhibitor" "oral, Phase 1 ongoing"
```

AACT підключається двома способами (`fetch_aact.py` пробує їх по черзі):
1. **Жива БД** (пріоритетно) — заповніть `AACT_DB_HOST/PORT/NAME/USER/PASSWORD`
   у `.env` (read-only доступ до `aact-db.ctti-clinicaltrials.org`, схема `ctgov`,
   таблиці `studies`/`conditions`). Пароль лежить лише в `.env`, який у
   `.gitignore` — ніколи не комітиться.
2. **Flat-файли** (fallback) — якщо БД недоступна, завантажте `studies.txt` і
   `conditions.txt` з aact.ctti-clinicaltrials.org у `data/aact/`.

Якщо недоступне жодне з двох — `fetch_aact` повертає `None`, і пайплайн
продовжує роботу без цього джерела. Перший успішний результат кешується
(`sources/cache.py`), тож навіть короткий збій живої БД під час демо не зламає
показ — далі піде з кешу. (AACT не значиться в переліку джерел самого ТЗ, але
це той самий датасет
ClinicalTrials.gov в агрегованому вигляді, а не нове джерело.)

## Жорсткі обмеження скоупу

Без автентифікації, без бази даних, без Docker. Один happy path: Indication +
Mechanism на вході → один повний звіт на виході. Систему тестувати мінімум на
3 різнотипних парах indication/mechanism (одна — складний/рідкісний кейс) —
промпти параметризуються через Indication/Mechanism, у коді немає розгалужень
по конкретній хворобі.

## Критерії готовності до демо

- [ ] Indication + Mechanism на вході → повний звіт на виході без ручного втручання
- [ ] Усі 10 обов'язкових секцій звіту присутні, включно з Critical Unknowns і Diligence Questions
- [ ] Кожен claim позначений evidence/inference, кожен evidence має клікабельне джерело
- [ ] UI дозволяє drill-down Recommendation → Claims → Evidence → Sources
- [ ] Протестовано на 3+ різних парах, одна — складний/рідкісний кейс
- [ ] Є кешовані відповіді для пари, яку показуватимете наживо
- [ ] Записане відео-демо як запасний варіант
- [ ] Є приклад, де нові/суперечливі дані змінюють вердикт системи (вимога фінального демо з ТЗ)
- [ ] Кожен топ-ризик має явну ймовірність і вплив на rNPV
- [ ] Вердикт явно посилається на найважливіший ризик, коли його ймовірність і вплив високі
- [ ] Показано approximate cost per run і JSON-трейс запуску
