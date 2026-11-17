# Revision analyses (Frontiers in Psychiatry, manuscript 2035644, first revision)

Analysis code added in response to reviewer comments on Bhuiyan et al., *Sociodemographic
Correlates of Medical Cannabis Certification for Anxiety Disorders Across Pennsylvania
Counties, 2019–2024*. The code for the submitted analysis is **unchanged** in the repository
root (`build_anxiety_dataset.py`); `build_anxiety_dataset_v1.py` in this folder is an identical
copy that the revision scripts import, so every new analysis uses exactly the same 17
measures and the same outcome as Table 1.

## Scripts (run in order)

| Script | What it does | Reviewer comments |
| --- | --- | --- |
| `01_build_panel.py` | Builds the county × year panel from the certification record, HUD crosswalks and ACS; accounting of statewide vs. county totals, certification status, treatment periods, creation months and an upper bound on within-year duplicate certifications; corrected ZIP-to-county allocation; household-adult denominator (ACS B09021); 2020 Census rural share; queen contiguity and state borders | R1.1, R1.2–R1.4, R1.7 |
| `02_run_analyses.py` | Reproduces Table 1; global and local Moran's I; negative binomial models with a log adult-population offset (unadjusted and rurality-adjusted) with residual Moran's I; spatial-error models; sensitivity analyses (excluding Philadelphia, excluding Forest, household denominator, corrected allocation, valid statuses only); DFBETA influence; labor force participation vs. age structure; panel GEE measure × year interactions; border-county analyses (descriptive, Mann–Whitney, two-way fixed-effects Poisson with and without county-specific trends) | R1.2–R1.5, R1.7, R1.8, R2.8, R2.9 |
| `03_make_figures.py` | Forest plot of rate ratios (2024), LISA cluster map, border-group and change maps | R1.2, R1.3, R1.7, R2.9 |
| `04_make_report.py` | Word report of all results organized by reviewer comment | — |

```bash
pip install pandas numpy scipy statsmodels openpyxl requests libpysal esda spreg shapely matplotlib python-docx
export CENSUS_API_KEY=your_key            # or save it in ../census_api_key.txt (never committed)
python 01_build_panel.py --selftest       # checks the new allocation, duplicate-bound and exposure code
python 02_run_analyses.py --selftest      # checks partial correlation, Moran's I and NB2 estimation
python 01_build_panel.py && python 02_run_analyses.py && python 03_make_figures.py && python 04_make_report.py
```

Optional: place the USDA ERS 2023 Rural–Urban Continuum Codes file (CSV or XLSX) in the
revision folder; `01_build_panel.py` detects it and adds RUCC (nonmetro = codes 4–9).

## Notes on the data

* **Year assignment.** Each annual Department of Health file contains the certifications
  *created* in that calendar year; certifications are assigned to their creation year.
* **Persons vs. certifications.** The record has no patient identifier. `01_build_panel.py`
  computes an upper bound on within-year duplication (certifications that ended before
  December 31 and could have been renewed in the same year, or that were canceled,
  created in error or invalid and could have been re-issued).
* **ZIP-to-county allocation.** The submitted code drops certifications whose ZIP code is not
  in the HUD crosswalk (7–22 per year) and, for multi-county ZIP codes whose HUD residential
  ratios are all zero, assigns the full count to each county (0–11 extra per year). The
  revision code matches ZIP+4 entries on their first five digits and splits zero-residential
  ZIPs by the total-address ratio. Because the effect on every Table 1 coefficient is
  < 0.001, the primary analyses keep the submitted allocation and the corrected allocation is
  reported as a sensitivity analysis.
* **Restricted data.** The certification record is held under the PA DOH data-access
  arrangement and cannot be redistributed; these scripts cannot be run end-to-end without it.
  No individual-level record is written by any script.
* **Public inputs.** County boundaries (Census cartographic boundary file, GeoJSON copy
  distributed by plotly) are downloaded automatically; ACS and 2020 Census data come from the
  Census Data API. `inputs_public/table1_submitted.csv` holds the submitted Table 1
  coefficients for the reproduction check.
* **Adult-use retail start dates** used for the border analysis (`rev_common.py`): NJ
  2022-04-21, NY 2022-12-29, MD 2023-07-01, OH 2024-08-06, DE 2025-08-01, WV none.

All permutation tests use a fixed seed (20261117); results are reproducible.

Portions of this code were drafted with the assistance of Anthropic Claude; all code was
reviewed by the authors.
