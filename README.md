# Sociodemographic Correlates of Medical Cannabis Certification for Anxiety Disorders Across Pennsylvania Counties, 2019–2024

Analysis code for the manuscript of the same title.

Bhuiyan A, Ahmed S, D'Mello T, Tusing LD, Piper BJ, Wengert ER.

Correspondence: Eric R. Wengert (erwengert@geisinger.edu)

---

## What this repository contains

A single script, `build_anxiety_dataset.py`, that reproduces the county-level
analysis reported in the manuscript: the annual medical cannabis certification
rate for anxiety disorders in each of Pennsylvania's 67 counties, and the
Pearson, Spearman, and ordinary least-squares associations between that rate and
17 sociodemographic measures drawn from the American Community Survey (ACS).

The script writes one Excel workbook:

| Sheet | Contents |
| --- | --- |
| `Stats_Summary` | Pearson *r*, Spearman *ρ*, their two-sided *p* values, and OLS slope, intercept, standard error, and *R*² for each measure in each year (17 measures × 6 years = 102 rows). **This sheet is the source of Table 1.** |
| `Combined_Long` | The underlying county-level observations, one row per measure × year × county (17 × 6 × 67 = 6,834 rows). |
| 17 measure sheets | One wide sheet per measure for the primary year (2024) — the form used to plot Figures 2–5. |

The 17 measures, their domain groupings, and their ACS variable codes correspond
exactly to Table 1 and Supplementary Table S2 of the manuscript.

**Scope.** This script covers Figures 2–5 and Table 1. Figure 1 — statewide
certification counts, condition shares, the county choropleth, and the
comorbidity panels — was produced separately from the same certification record
and is not generated here. All figures were drawn in GraphPad Prism from the
tabular output; the script produces no graphics.

---

## Data availability

The analysis draws on three inputs. Two are public:

- **ACS 5-year estimates** — downloaded automatically by the script from the
  Census Data API (`https://api.census.gov/data/{year}/acs/acs5`). A free API
  key is required: https://api.census.gov/data/key_signup.html
- **HUD USPS ZIP–County crosswalk** —
  https://www.huduser.gov/apps/public/uspsutilities/ (select ZIP–COUNTY,
  Pennsylvania, 4th quarter of each study year). Save as
  `PA_HUD_ZIP_COUNTY_2019.csv` … `PA_HUD_ZIP_COUNTY_2024.csv`.

The third is not:

- **The individual-level certification record** was provided by the Pennsylvania
  Department of Health under a data use agreement and **cannot be redistributed
  by the authors**. It is available to other investigators by application to the
  Department. The script reads only two fields from it — the five-digit ZIP code
  and the ten qualifying-condition fields (`SMC1`–`SMC10`) — and no direct or
  indirect patient identifier is read, stored, or written at any point.

Because that record is restricted, **the script cannot be run end to end by
readers outside the data use agreement.** It is published so that every analytic
decision behind the reported numbers is inspectable, and it ships with a
self-test (below) that verifies the calculations without any restricted data.

---

## Requirements

Python 3.9 or newer, and five packages:

```
pandas   >= 1.3
numpy    >= 1.20
scipy    >= 1.7
requests >= 2.25
openpyxl >= 3.0
```

Install them with:

```bash
pip install "pandas>=1.3" "numpy>=1.20" "scipy>=1.7" "requests>=2.25" "openpyxl>=3.0"
```

The analysis is fully deterministic — no random number generation is involved —
so re-running the script on the same inputs reproduces the workbook exactly.

---

## Verifying the calculations without the restricted data

The script includes a self-test that checks every computation against small
synthetic inputs with known answers. It requires no data files and no API key:

```bash
python build_anxiety_dataset.py --selftest
```

It verifies, among other things, that percentage measures are computed as
`100 × sum(numerator) / denominator`; that Census special values
(−666666666 and related codes) are recoded as missing before any arithmetic;
that the certification rate denominator is the sum of the 38 ACS B01001 cells
covering ages 18 and over; that ZIP-to-county allocation renormalizes HUD
residential ratios within each ZIP code and conserves the certification total;
that a certification listing the same condition in more than one field is
counted once; that condition-name matching is insensitive to case and
punctuation; that the correlation and OLS routines return the correct values on
an exactly linear relationship; and that the measure specification matches
Table 1 and Supplementary Table S2 (17 measures across 4 domains, 66 distinct
ACS variables drawn from 10 ACS tables, with the four education categories
partitioning `B15003_017E`–`B15003_025E` exactly once).

All checks should print `PASS`.

---

## Running the full analysis

With the certification record and HUD crosswalks in one directory:

```bash
export CENSUS_API_KEY=your_key_here      # Windows: set CENSUS_API_KEY=...
python build_anxiety_dataset.py
```

Options:

```bash
python build_anxiety_dataset.py --data-dir /path/to/data --out results.xlsx
python build_anxiety_dataset.py --years 2019-2024 --primary-year 2024
```

Expected input filenames in the data directory:

```
certifications(2019).csv ... certifications(2024).csv    # columns: Zipcode, SMC1 ... SMC10
PA_HUD_ZIP_COUNTY_2019.csv ... PA_HUD_ZIP_COUNTY_2024.csv # columns: zip, county, res_ratio
```

Runtime is roughly two minutes, almost all of it reading the certification CSVs.

---

## Method summary

**Case definition.** A certification is assigned to anxiety disorders in a given
year if any of its ten qualifying-condition fields names anxiety disorders.
Condition names are matched after reducing them to lowercase alphanumerics,
which makes the match robust to the apostrophe and punctuation variants present
in the record. All certifications appearing in a given year's record are
counted, irrespective of certification status. A certification listing the
condition in more than one field is counted once.

**Geography.** Certifications are allocated from five-digit ZIP code to county
using the year-matched HUD USPS ZIP–County crosswalk residential address ratio.
For ZIP codes crossing county lines the ratios are renormalized to sum to 1
within each ZIP code, so no certification is lost or duplicated. County totals
are rounded to the nearest whole certification; county counts therefore may not
sum exactly to the statewide total.

**Outcome.** The annual county-level certification rate, expressed per 1,000
adults aged 18 and over:

```
rate(c, y) = certifications(c, y) / adult_pop(c, y) × 1,000
```

The denominator is the county adult population from ACS table B01001, the sum
of the 38 male and female sex-by-age cells spanning ages 18 and over
(Supplementary Table S2c).

**Sociodemographic measures.** Each certification year is paired with the ACS
5-year release of the same vintage (2019 certifications with the 2015–2019
release, and so on), so every measure reflects a five-year window ending in the
certification year. Percentage measures are expressed relative to the universe
of their source ACS table. Median household income, per capita income, median
gross rent, and median age are used as reported by the ACS.

**Statistics.** Pearson product-moment and Spearman rank correlations are
computed between each measure and the corresponding certification rate, because
county-level variables frequently violate the bivariate normality assumed by
Pearson's test. Ordinary least-squares lines are fit for display. Significance
is assessed at α = 0.05, two-sided. Because the analysis is exploratory and
hypothesis-generating, no correction for multiple comparisons is applied;
consistency of a given association across the six study years is reported
instead.

---

## Disclosure

Portions of this script were drafted with the assistance of large language
models (Google Gemini 2.5 Flash; Anthropic Claude Opus 4.7, 4.8, and 5.0). All
code was manually inspected and verified by the authors before use.

## Purpose of this repository

This code is published for transparency and inspection. Because the
certification record is restricted (see **Data availability** above), the
analysis cannot be re-run in full by readers outside the data use agreement; the
repository exists so that every analytic decision behind the reported numbers
can be examined directly, and so that the calculations can be verified
independently via the self-test. Researchers interested in adapting the approach
to other jurisdictions are welcome to contact the corresponding author.
