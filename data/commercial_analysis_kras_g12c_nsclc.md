# Commercial diligence memo: KRAS G12C in NSCLC

**Snapshot:** 9 October 2026. This is a preliminary commercial screen for the
project's demo case, not a forecast or legal opinion. Registry statuses can lag
and should be refreshed before an investment decision.

## Executive take

This is a real, validated target market, but a new KRAS G12C inhibitor cannot
underwrite against an empty market. Sotorasib and adagrasib are established
later-line products, while several investigational programs are testing earlier
lines, combinations with checkpoint inhibitors/chemotherapy, and adjuvant or
perioperative use. A new asset needs a defined differentiation wedge: deeper or
more durable response, CNS activity, tolerability, resistance coverage,
combination fit, or a materially easier dosing profile.

The present `market_data.csv` values are not investment-grade evidence. Their
regional labels, source links, and numbers have not been reconciled; in
particular, the sotorasib FDA approval page is not a pricing source, and a US
SEER lung-cancer page cannot substantiate a combined US+EU NSCLC prevalence
number. The code now keeps these values out of evidence claims until a reviewer
adds exact source text and explicit verification fields.

## Patient pool and market-sizing inputs

An FDA advisory-committee briefing estimated approximately **28,000 annual
KRAS G12C-mutated NSCLC cases in North America and 53,000 in Europe**, based on
2020 lung-cancer incidence and estimated mutation prevalence. This is a rough
annual incidence estimate, not prevalence, not a current 2026 forecast, and
not the number eligible for a particular treatment line. North America also
does not mean US alone. Use it only as a historical top-of-funnel cross-check.
[FDA ODAC sotorasib briefing, p. 23](https://www.fda.gov/media/172698/download)

For a current model, build separate US and EU5 country estimates from incidence
and mutation prevalence, then apply successive eligibility filters:

`incident NSCLC × KRAS G12C share × stage × line of therapy × testing/diagnosis × access`

Model prevalence separately for an existing-patient pool. Do not multiply an
annual incidence input by annual treatment price and call it TAM without
adjusting for treatment duration, line eligibility, access, share, net price,
and repeat treatment.

### Pricing

The BMS manufacturer disclosure gives **Krazati WAC $22,592.24 as of 1 January
2025**. BMS explicitly says WAC is a list price, not the price paid by
patients, pharmacies, or payers. This disclosure does not establish net price
or EU pricing, and its excerpt does not identify a treatment-course duration;
I have therefore not converted it into annual treatment cost in the CSV.
[BMS WAC disclosure](https://www.bms.com/assets/bms/us/en-us/pdf/krazati_pricing.pdf)

No similarly dated primary WAC disclosure for Lumakras was verified in this
pass. The existing CSV price cells remain explicitly unverified: FDA approval
records and labels establish approval/dose context, not price. Before using
annual cost, confirm pack quantity, dosing, duration, rebates/discounts, and
distinguish US WAC from net revenue and EU country-level negotiated prices.

## Competitive landscape

### Marketed therapies

| Asset | Sponsor | Position supported by public source | Commercial implication |
|---|---|---|---|
| Sotorasib (Lumakras) | Amgen | FDA label includes KRAS G12C locally advanced/metastatic NSCLC; current label available from FDA | Existing approved option; benchmark for efficacy, safety, dose, and price research |
| Adagrasib (Krazati) | Mirati / Bristol Myers Squibb | FDA snapshot describes use after at least one prior treatment | Second marketed direct inhibitor; a new entrant must explain why physicians/payers switch |

Sources: [FDA Lumakras label](https://www.accessdata.fda.gov/drugsatfda_docs/label/2025/214665Orig1s009correctedlbl.pdf), [FDA Krazati Drug Trials Snapshot](https://www.fda.gov/drugs/drug-trials-snapshots/drug-trials-snapshots-krazati).

### Late-stage programs to diligence

The following are examples from a ClinicalTrials.gov API query for NSCLC +
KRAS G12C, retrieved 9 October 2026. Status and primary completion dates are
registry fields, not confirmed readout dates. Several studies have estimated
completion dates in the past or near term; check posted results and sponsor
communications before treating them as active catalysts.

| Program | Trial | Registry status / phase | Why it matters |
|---|---|---|---|
| Divarasib vs sotorasib or adagrasib | [NCT06497556](https://clinicaltrials.gov/study/NCT06497556) | Active, not recruiting / Phase 3 | Direct comparative trial; listed primary completion was 2026-05-19, so results/status need immediate follow-up |
| JDQ443 vs docetaxel | [NCT05132075](https://clinicaltrials.gov/study/NCT05132075) | Active, not recruiting / Phase 3 | Direct late-line competitor; listed primary completion 2026-09-03 has passed |
| D-1553 vs docetaxel | [NCT06300177](https://clinicaltrials.gov/study/NCT06300177) | Recruiting / Phase 3 | Another direct later-line entrant; completion listed as 2026-10 |
| Sotorasib + platinum doublet vs pembrolizumab + platinum doublet | [NCT05920356](https://clinicaltrials.gov/study/NCT05920356) | Recruiting / Phase 3 | Tests expansion into first-line treatment |
| Divarasib + pembrolizumab + chemotherapy | [NCT06793215](https://clinicaltrials.gov/study/NCT06793215) | Recruiting / Phase 3 | First-line combination pressure from Roche |
| Adagrasib + pembrolizumab ± chemotherapy | [NCT06875310](https://clinicaltrials.gov/study/NCT06875310) | Recruiting / Phase 3 | Could extend an approved product into first-line combinations |
| Olomorasib + pembrolizumab ± chemotherapy | [NCT06119581](https://clinicaltrials.gov/study/NCT06119581) | Recruiting / Phase 3 | Lilly first-line program |
| Calderasib (MK-1084) + pembrolizumab, PD-L1 TPS ≥50% | [NCT06345729](https://clinicaltrials.gov/study/NCT06345729) | Recruiting / Phase 3 | Merck program targets a biomarker-defined first-line segment |
| JAB-21822 + JAB-3312 vs standard therapy | [NCT06416410](https://clinicaltrials.gov/study/NCT06416410) | Recruiting / Phase 3 | Combination strategy with SHP2 inhibitor; first-line |
| JMKX001899 vs docetaxel | [NCT07242274](https://clinicaltrials.gov/study/NCT07242274) | Not yet recruiting / Phase 3 | Additional direct later-line competition |

Registry snapshot counts are not a market-share estimate: studies differ by
line, geography, design, and active enrollment; some records may be duplicated
or stale. The API search returned 124 records before program-level filtering.

## Preliminary patent leads (not FTO)

These public patent records show that the target class has significant
composition, use, dosing, and process patent activity. They are leads for
claim-level review only. Google Patents warns that its legal status and
assignee information may be inaccurate; verify prosecution, assignments,
family members, maintenance, and current claims in official national records.

| Lead | Public record | Initial relevance |
|---|---|---|
| Amgen sotorasib/KRAS G12C compound family | [US10519146B2](https://patents.google.com/patent/US10519146B2) | Composition/compound and use family associated with the marketed comparator |
| Mirati adagrasib/KRAS G12C inhibitor family | [US20190144444A1](https://patents.google.com/patent/US20190144444A1) | Compound family associated with adagrasib/MRTX849 |
| Amgen use in selected cancer patients | [US20240091230A1](https://patents.google.com/patent/US20240091230A1) | Published use/application lead mentioning sotorasib and NSCLC; verify current prosecution and claim scope |
| Mirati adagrasib synthesis/process | [US20240400558A1](https://patents.google.com/patent/US20240400558A1) | Process/intermediate claims; relevant to manufacturing review, not automatically a blocker to a different molecule |

USPTO provides free public search for US patents and published applications
through [Patent Public Search](https://www.uspto.gov/patents/search/patent-public-search).
The project’s ODP bulk downloader is a different service and currently needs
`USPTO_ODP_API_KEY`. A search hit does not establish infringement or freedom to
operate. Because the project's own candidate molecule, structure, claims,
territories, and intended filing strategy were not provided, no asset-specific
FTO conclusion is possible.

## Commercial analyst next steps

1. Lock the target product profile: treatment line, disease stage, mono vs
   combination, biomarker test, route, dosing, and target geography.
2. Rebuild the patient funnel with dated, country-specific incidence, mutation
   prevalence, stage, and line-of-therapy data; show low/base/high assumptions.
3. Obtain dated US WAC and net-price benchmarks, EU5 reimbursement/pricing
   ranges, treatment duration, and payer restrictions for sotorasib/adagrasib.
4. For every Phase 3 comparator above, review protocol, recruitment geography,
   endpoints, comparator, enrollment, results, and next verifiable catalyst.
5. Ask IP counsel to chart the candidate's structure and proposed use against
   live patent claims in US/EU/China/Japan and review licenses/encumbrances.
6. Run blinded KOL and payer interviews on switching criteria, testing,
   combination adoption, and unmet need; public sources cannot answer these.

## Data limitations and requested input

I can do the public-source analysis and maintain the data tables. For the
project-specific conclusion, provide the candidate's modality/structure
description (or a confidential non-structure summary if appropriate), stage,
target line, intended geographies, and any existing price or access assumptions.
Do not put a confidential chemical structure into a public patent search query.
