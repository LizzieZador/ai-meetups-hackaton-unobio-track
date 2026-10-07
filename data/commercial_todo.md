# Manual Commercial & Epidemiology Dataset TODO

Цей файл містить перелік ручних даних, які необхідно зібрати та внести до локального датасету (`data/market_data.csv` та/або `data/pricing_data.csv`) для еталонних тестових показань (indications).

> **Правило верифікації доказів (Evidence Integrity):**  
> Кожен рядок у CSV стає валідним `evidence`-джерелом для системи лише за наявності:
> 1. Прямого посилання на первинне авторитетне джерело (`Source_URL`).
> 2. Року публікації даних (`Year`).
> 
> Якщо посилання або рік відсутні, рядок автоматично позначається системою як **неперевірений (unverified)** і не може бути процитований як прямий факт (`evidence`), а використовується лише як орієнтир для гіпотез (`inference`) з низькою впевненістю.

---

## 1. Рекомендовані відкриті джерела для пошуку

| Категорія | Рекомендоване джерело | Що шукати | Посилання / Підказка |
| :--- | :--- | :--- | :--- |
| **Епідеміологія (Онкологія)** | **NCI SEER Program** | Prevalence, 5-year survival, Annual incidence in US | [seer.cancer.gov](https://seer.cancer.gov) — розділ Cancer Stat Facts |
| **Епідеміологія (Глобальна)** | **GLOBOCAN / IARC WHO** | Incidence & Mortality worldwide & EU | [gco.iarc.who.int](https://gco.iarc.who.int) |
| **Глобальний тягар хвороб** | **IHME Global Burden of Disease (GBD)** | Prevalence, DALYs, Incidence (US, EU, Global) | [ghdx.healthdata.org](https://ghdx.healthdata.org/gbd-results-tool) |
| **Хвороби органів і систем** | **WHO Global Health Estimates / CDC WONDER** | Кількість пацієнтів, захворюваність | [cdc.gov/wonder](https://wonder.cdc.gov/) |
| **Рідкісні захворювання** | **Orphanet / NORD** | Поширеність рідкісних мутацій (per 100k) | [orpha.net](https://www.orpha.net), [rarediseases.org](https://rarediseases.org) |
| **Цінові аналоги (US WAC)** | **SEC 10-K / Investor Reports** | Річний дохід та WAC ціна схвалених аналогів | EDGAR пошук річних звітів фармкомпаній |
| **Ціни та компенсація** | **CMS Medicare Part B / Part D Dashboard** | Середня вартість лікування на пацієнта в рік | [data.cms.gov](https://data.cms.gov) |
| **Клінічні рекомендації (SoC)** | **NCCN Guidelines / ESMO Guidelines** | Поточний стандарт лікування 1-ї та 2-ї ліній | [nccn.org](https://www.nccn.org), [esmo.org](https://www.esmo.org) |
| **Лейбли препаратів** | **FDA Drugs@FDA & Orange Book** | Затверджені показання, режим дозування | [accessdata.fda.gov/scripts/cder/daf/](https://accessdata.fda.gov/scripts/cder/daf/) |

---

## 2. Обов'язкова структура колонок для `data/market_data.csv`

Для повної інтеграції з перевіркою цитування додайте або оновіть наступні колонки:

```csv
Indication,Prevalence_US_EU,Incidence_Annual_US_EU,Avg_Annual_Treatment_Cost_USD,Primary_Epidemiology_Source,Source_URL,Year,Region,Pricing_Analogue_Drug,Pricing_Source_URL,Pricing_Year
```

Пояснення полів:
- `Indication`: Точна назва показання (наприклад, `Non-Small Cell Lung Cancer`).
- `Prevalence_US_EU`: Загальна кількість живих пацієнтів з діагнозом у США + ЄС.
- `Incidence_Annual_US_EU`: Нові випадки щороку в США + ЄС.
- `Avg_Annual_Treatment_Cost_USD`: Орієнтовна річна вартість терапії аналогами (USD).
- `Primary_Epidemiology_Source`: Назва установи чи бази (наприклад, `SEER Cancer Statistics`).
- `Source_URL`: Повне пряме посилання на сторінку/звіт/DOI.
- `Year`: Рік виходу даних (YYYY, наприклад `2023`).
- `Region`: Регіон дослідження (`US+EU`, `US`, `Global`).
- `Pricing_Analogue_Drug`: Назва препарату-аналога стандарту лікування (наприклад, `Sotorasib / Lumakras`).
- `Pricing_Source_URL`: Посилання на джерело ціни (SEC 10-K, FDA label, прайс-лист CMS).
- `Pricing_Year`: Рік ціни (YYYY).

---

## 3. Список необхідних даних за тестовими показаннями

### 1. Non-Small Cell Lung Cancer (NSCLC)
- [ ] **Поширеність / Захворюваність**: Загальна захворюваність NSCLC у США та ЄС.
  - *Підказка*: NCI SEER (Lung and Bronchus), GLOBOCAN.
- [ ] **Сегментація мутацій**: Частка пацієнтів з мутацією KRAS G12C (~13% non-squamous) або іншими (EGFR, ALK).
- [ ] **Ціновий аналог**: Річна ціна інгібітора KRAS G12C (Sotorasib або Adagrasib) — WAC ~$20,000–$22,000/місяць (~$240k–$260k/рік).
  - *Джерело*: Прес-реліз Amgen / Mirati або звіт SEC 10-K.

### 2. Alzheimer's Disease
- [ ] **Поширеність**: Кількість людей з хворобою Альцгеймера та легкими когнітивними порушеннями (MCI) в США та ЄС.
  - *Підказка*: Alzheimer's Association Facts and Figures Report (2023/2024).
- [ ] **Цільова підгрупа**: Частка пацієнтів на ранній стадії (Mild Cognitive Impairment / Early AD), придатних для моноклональних антитіл.
- [ ] **Ціновий аналог**: Річна ціна Lecanemab (Leqembi) — $26,500/рік або Donanemab (Kisunla) — ~$32,000/рік.
  - *Джерело*: Офіційні прайс-релізи Eisai / Biogen та Eli Lilly.

### 3. Multiple Sclerosis (MS)
- [ ] **Поширеність**: Загальна кількість пацієнтів (US+EU), розподіл на RRMS (рецидивуючо-ремітуючий) та PMS (прогресуючий).
  - *Підказка*: MS International Federation (Atlas of MS), National MS Society.
- [ ] **Ціновий аналог**: Річна вартість високоактивної анти-CD20 терапії (Ocrelizumab / Kesimpta) — ~$65k–$85k/рік.
  - *Джерело*: Roche / Novartis річні фінансові звіти.

### 4. Type 2 Diabetes
- [ ] **Поширеність / Захворюваність**: Кількість дорослих з T2D (US+EU).
  - *Підказка*: IDF Diabetes Atlas (10th/11th Edition), CDC National Diabetes Statistics Report.
- [ ] **Цільова підгрупа**: Пацієнти з неконтрольованим HbA1c або ожирінням, які потребують інкретинової терапії (GLP-1 / GIP).
- [ ] **Ціновий аналог**: Річна ціна GLP-1/GIP агоністів (Semaglutide / Tirzepatide) — ~$12,000–$16,000/рік (US WAC).
  - *Джерело*: Novo Nordisk / Eli Lilly прес-матеріали.

### 5. Parkinson's Disease
- [ ] **Поширеність / Захворюваність**: Пацієнти з діагностованою хворобою Паркінсона (US+EU).
  - *Підказка*: Parkinson's Foundation, Global Burden of Disease.
- [ ] **Цільова підгрупа**: Пацієнти з мутаціями LRRK2 або GBA (якщо розглядається таргетна терапія).
- [ ] **Ціновий аналог**: Річна вартість терапії пізніх стадій (Duopa, комбінації леводопи, апоморфін).

### 6. Rheumatoid Arthritis (RA)
- [ ] **Поширеність**: Кількість дорослих з РА (US+EU).
  - *Підказка*: ACR (American College of Rheumatology), EULAR.
- [ ] **Цільова підгрупа**: Пацієнти, резистентні до метотрексату / anti-TNF, кандидати на JAK-інгібітори або нові мішені.
- [ ] **Ціновий аналог**: Річна ціна JAK-інгібіторів (Upadacitinib / Rinvoq) або bDMARDs — ~$60,000–$75,000/рік.

### 7. Atopic Dermatitis
- [ ] **Поширеність**: Пацієнти з середньо-тяжким та тяжким атопічним дерматитом.
  - *Підказка*: National Eczema Association, GBD study.
- [ ] **Ціновий аналог**: Річна вартість Dupilumab (Dupixent) — ~$40,000–$45,000/рік.
  - *Джерело*: Sanofi / Regeneron SEC 10-K.

### 8. Gene Therapy (Рідкісні моногенні патології)
- [ ] **Захворюваність (Incidence per live births)**: Наприклад, SMA (1:10,000), DMD (1:3,500 хлопчиків), Hemophilia A/B.
  - *Підказка*: NORD, CDC, Orphanet.
- [ ] **Ціновий аналог**: Одноразова вартість генної терапії (Zolgensma: $2.1M, Elevidys: $3.2M, Hemgenix: $3.5M).
  - *Джерело*: Офіційні FDA press releases та звіти виробників (Novartis, Sarepta, CSL Behring).

---

## 4. Інструкція після заповнення
1. Вставте знайдені показники та посилання у відповідні рядки `data/market_data.csv`.
2. Запустіть перевірку через `python -m agents.commercial_agent` або пайплайн.
3. Система автоматично підхопить первинні URL, додасть їх до перевірених джерел (`is_evidence=True`), а комерційний агент зможе цитувати їх з верифікованими числовими цитатами `exact_snippet`.

