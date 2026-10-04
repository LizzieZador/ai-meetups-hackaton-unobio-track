# Bug-test report — 2026-10-04

Повний прогін усього коду репозиторію: компіляція, імпорти, юніт-тести схеми/рендеру
звіту, і живі виклики всіх 5 джерел даних. LLM-агенти (scientific/clinical/commercial/
chair) **не протестовані наживо** — `ANTHROPIC_API_KEY` у `.env` досі заглушка
(`sk-ant-...`), немає робочого ключа з кредитами.

## Знайдені й виправлені баги

| # | Файл | Баг | Виправлення |
|---|---|---|---|
| 1 | `sources/fetch_opentargets.py` | GraphQL-поле `knownDrugs` на типі `Target` більше не існує в актуальній схемі Open Targets API (`HTTP 400: Cannot query field 'knownDrugs' on type 'Target'`) — запит завжди падав, функція мовчки поверталась `None` | Замінено на актуальне поле `drugAndClinicalCandidates { rows { maxClinicalStage drug { name } } }`, перевірено наживо (повертає реальні препарати: SOTORASIB, ADAGRASIB, SALIRASIB для KRAS) |
| 2 | `sources/fetch_opentargets.py` | `_fetch()` і `_resolve_id()` перевіряли лише HTTP-статус (`raise_for_status()`), не тіло відповіді — GraphQL-помилка з кодом 200 (можлива поведінка цього API) пройшла б непоміченою і впала б з `KeyError` при читанні `data` | Додано явну перевірку `if body.get("errors"): return None` в обох функціях |
| 3 | `agents/test_local_run.py` | `get_competitors_from_aact(indication)` викликався з 1 аргументом, а функція в `fetch_aact.py` вимагає 2 (`condition, intervention`) — `TypeError`, скрипт не запускався взагалі | Виправлено виклик на `get_competitors_from_aact(indication, target)` |
| 4 | `agents/test_local_run.py` | Код шукав ключ `"claims"` у результаті `get_competitors_from_aact()`, якого там ніколи не було (ця функція повертає сирий `{source, raw_snippet, active_trials_count, ...}`, не список claims) — AACT-дані завжди мовчки губились | Перетворено результат на evidence-claim за схемою `schema.py` (`claim`, `type`, `section`, `source`, `date_accessed`) |
| 5 | `agents/test_local_run.py` | Inference-твердження про конкурентний тиск містило захардкожене "Понад 1000 активних випробувань" — число, не пов'язане з реальними даними | Замінено на справжнє значення `aact_base["active_trials_count"]` |
| 6 | `agents/test_local_run.py` | Не викликав `load_dotenv()` — при самостійному запуску (`python -m agents.test_local_run`) змінні AACT_DB_* з `.env` не підвантажувались, жива БД мовчки недоступна, flat-файлів теж нема → скрипт "успішно" завершувався, але без жодного AACT-claim | Додано `load_dotenv()` на початку файлу (той самий патерн, що й у `pipeline.py`) |
| 7 | `sources/fetch_commercial.py` | Claims у `run_commercial_analysis()` не мали обов'язкового поля `"section"` зі `schema.py` — якби їх колись передали в `parse_claims()`, вони б відкидались як невалідні | Додано `"section": "commercial_opportunity"` |
| 8 | `main.py` | Помилка пайплайна (напр. невалідний API-ключ) не ловилась — CLI показував сирий Python traceback замість зрозумілого повідомлення | Обгорнуто виклик `run_pipeline_markdown()` в `try/except`, друкує `"Pipeline failed: ..."` і виходить з кодом 1 |
| 9 | `sources/__init__.py` | Імпортував `get_competitors_from_aact`, якої не було визначено у `fetch_aact.py` — `ImportError` при будь-якому `import sources`, усі тести падали ще до старту (виправлено в попередній сесії, під час написання `fetch_aact.py` — тут лише підтверджено, що фіксація тримається) | — (вже виправлено раніше) |

## Прогін тестів (що саме перевірено)

```
py_compile  : усі .py файли репозиторію                               -> OK
import test : усі 17 модулів (schema/llm/report/pipeline/main/
              sources.*/agents.*)                                     -> OK

schema.py   : parse_claims() на 6 claims (2 валідних, 4 невалідних
              причини: без source / без confidence / невідома
              section / без section взагалі)                          -> PASS (2/6 keep)
report.py   : render_report() генерує усі 10 обов'язкових секцій
              + "Sources" + cost footer                                -> PASS

fetch_pubmed        : KRAS G12C + NSCLC, 3 результати (живий API)      -> OK
fetch_opentargets   : KRAS + NSCLC, 1 результат з реальними
                       препаратами-конкурентами (після фіксу #1-2)     -> OK
fetch_clinicaltrials: NSCLC + pembrolizumab, 3 результати               -> OK
fetch_aact           : NSCLC + pembrolizumab, жива БД, 157 трайлів     -> OK
fetch_commercial      : NSCLC, дані з market_data.csv                  -> OK

pipeline.collect_sources(): усі 5 джерел одночасно, жодне не впало     -> OK

agents/test_local_run.py (rule-based commercial-звіт, без LLM):
  3 claims (epidemiology evidence + AACT evidence + AACT inference),
  усі з коректним "section", financial_metrics порахований              -> OK
  (тестова проба збережена: commercial_agent_output.json —
   видалена після перевірки, щоб не засмічувати репо; за потреби
   перегенерується одним запуском python -m agents.test_local_run)

pipeline.run_pipeline() (повний LLM-пайплайн):
  НЕ протестовано успішно — падає з AuthenticationError (401,
  "API key is invalid") на першому ж виклику scientific_agent,
  бо ANTHROPIC_API_KEY — заглушка. Це очікувано, не новий баг.
  main.py тепер ловить цю помилку чисто (виправлення #8).
```

## Що лишається неперевіреним

Увесь LLM-ланцюжок (`scientific_agent → clinical_agent → commercial_agent →
chair_agent`, тобто чи модель справді повертає валідний JSON-масив з правильними
`section`, чи витримує схему evidence/inference) — **жодного разу не виконаний
проти реального Claude API**. Це найбільший залишковий ризик перед демо.
Потрібен робочий `ANTHROPIC_API_KEY` з кредитами, щоб закрити цю прогалину.
