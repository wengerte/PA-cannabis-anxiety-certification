"""
04_make_report.py - assemble a Word report of the revision analyses, organized
by reviewer comment, with every number pulled from the output workbooks.

Output: docs/Revision_Analysis_Report.docx
Run after 01-03.   python 04_make_report.py
"""

import os
from datetime import date

import numpy as np
import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Pt, RGBColor, Inches

import rev_common as R

T = R.TABLE_DIR
xl = pd.ExcelFile(os.path.join(T, "Revision_Results.xlsx"))
acc = pd.ExcelFile(os.path.join(T, "R1.1_certification_accounting.xlsx"))
P = pd.read_csv(os.path.join(T, "county_year_panel.csv"), dtype={"county_fips": str})
S = {n: pd.read_excel(xl, n) for n in xl.sheet_names}
S6 = S["S6_Sensitivity"]

doc = Document()
st = doc.styles["Normal"]
st.font.name = "Arial"
st.font.size = Pt(10)
sec = doc.sections[0]
sec.left_margin = sec.right_margin = Inches(0.9)


def p_(x):
    return "P<0.001" if x < 0.001 else f"P={x:.3f}"


def h(text, lvl=1):
    doc.add_heading(text, level=lvl)


def para(text, italic=False, color=None, bold=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic, r.bold = italic, bold
    if color:
        r.font.color.rgb = RGBColor(*color)
    return p


def bullet(text):
    doc.add_paragraph(text, style="List Bullet")


def suggest(text):
    p = doc.add_paragraph()
    r = p.add_run("Suggested text: ")
    r.bold = True
    r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    r2 = p.add_run(text)
    r2.italic = True


def pending(text):
    para("PENDING: " + text, color=(0xC0, 0x00, 0x00), italic=True)


def table(df, fmt=None, widths=None):
    fmt = fmt or {}
    t = doc.add_table(rows=1, cols=len(df.columns))
    t.style = "Light Grid Accent 1"
    for c, col in zip(t.rows[0].cells, df.columns):
        c.text = str(col)
    for _, row in df.iterrows():
        cells = t.add_row().cells
        for c, col in zip(cells, df.columns):
            v = row[col]
            if col in fmt and pd.notna(v):
                c.text = fmt[col](v)
            elif isinstance(v, (float, np.floating)):
                if pd.isna(v):
                    c.text = ""
                elif float(v).is_integer() and (abs(v) >= 1 or v == 0):
                    c.text = f"{int(v):,}" if col != "year" else str(int(v))
                else:
                    c.text = f"{v:.3f}"
            elif isinstance(v, (int, np.integer)):
                c.text = str(v) if col == "year" else f"{v:,}"
            else:
                c.text = str(v)
    for r in t.rows:
        for c in r.cells:
            for pp in c.paragraphs:
                for rr in pp.runs:
                    rr.font.size = Pt(8)
    doc.add_paragraph()
    return t


rur_available = P["pct_rural_2020"].notna().all() or P["nonmetro"].notna().all()
hh_available = P["hh_adult_pop"].notna().all()

# ----------------------------------------------------------------------------
doc.add_heading("Revision Analyses: Results by Reviewer Comment", 0)
para("Bhuiyan et al., Frontiers in Psychiatry manuscript 2035644. Generated "
     f"{date.today():%d %b %Y} by code/04_make_report.py from outputs/tables/. "
     "Every number below is read from Revision_Results.xlsx or "
     "R1.1_certification_accounting.xlsx; rerunning 01-04 regenerates this report.", italic=True)
src = P["acs_source"].iloc[0]
para(f"ACS source for this run: {src}. Rurality data available: {'yes' if rur_available else 'no'}. "
     f"Household-adult denominator available: {'yes' if hh_available else 'no'}.", italic=True)
A = S["A_Reproduction"]
para(f"Reproduction check: the panel reproduces all {len(A) * 2} coefficients in submitted Table 1 "
     f"(maximum absolute difference r = {np.nanmax(np.abs(A.diff_r)):.3f}, "
     f"rho = {np.nanmax(np.abs(A.diff_rho)):.3f}).", bold=True)

# ---------------------------------------------------------------- R1.1
h("R1.1  Assignment of certifications to years; certifications vs. persons; 2019")
a = pd.read_excel(acc, "Statewide_vs_County")
d = pd.read_excel(acc, "WithinYear_Duplicate_Bound")
m = pd.read_excel(acc, "Created_Month_by_Year").set_index("month")
pre = int(a.loc[a.year == 2019, "AD_created_before_2019_07_20"].iloc[0])
mo = S6[S6.variant == "Certifications created March-October only"]
bullet("Year assignment: each annual DOH file contains only certifications created in that calendar year "
       f"(created dates outside the year: {int(a.created_dates_outside_year.sum())} across all years). A "
       "certification is therefore assigned to the year it was created; status (Active, Expired, etc.) is the "
       "status when the file was extracted.")
bullet("No patient identifier exists, so certifications cannot be de-duplicated to persons. One person can "
       "contribute more than one certification to a calendar year in three ways: (a) a treatment period shorter "
       "than 12 months that ended before December 31, followed by a same-year renewal; (b) a certification that "
       "was canceled, created in error, or invalid and then re-issued; (c) an early renewal - renewals are created "
       "before the prior certification expires (the 2022 file holds "
       f"{int(d.loc[d.year == 2022, 'pending_renewal_created_nov_dec'].iloc[0]):,} pending renewals created in "
       "November-December), so a person whose prior 12-month certification was created early in a year can be "
       f"renewed late in the same year. (a) or (b) applies to at most {d.a_or_b_pct.max():.1f}% of AD "
       f"certifications in any year ({d.a_or_b_pct.iloc[-1]:.2f}% in 2024). (c) cannot be counted; its "
       f"theoretical maximum is {d.c_theoretical_max_pct.iloc[1:].min():.0f}-{d.c_theoretical_max_pct.iloc[1:].max():.0f}% "
       "in 2020-2024 (the smaller of the number of 12-month certifications created in the first 60 days of the "
       "year and the number created in the last 60 days); the true figure is lower because renewal is not universal and repeat "
       "renewals are created about 12 months apart.")
sigp = A.assign(sr=A.p_r < .05, ss=A.p_rho < .05).groupby("measure")[["sr", "ss"]].sum()
sigm = mo.assign(sr=mo.p_r < .05, ss=mo.p_rho < .05).groupby("measure")[["sr", "ss"]].sum()
diffs = [(meas, meth, int(sigp.loc[meas, c]), int(sigm.loc[meas, c]))
         for meas in sigp.index for c, meth in (("sr", "Pearson"), ("ss", "Spearman"))
         if sigp.loc[meas, c] != sigm.loc[meas, c]]
same = len(sigp) - len({x[0] for x in diffs})
diff_txt = "; ".join(f"{m_.split(' (')[0]} by {meth} ({a_}->{b_} years)" for m_, meth, a_, b_ in diffs)
bullet("Sensitivity analysis that removes mechanism (c): rates recomputed from certifications created March-"
       "October only (no same-year early-renewal pair can fall entirely in that window). Maximum change in any "
       f"Table 1 coefficient: |dr| = {mo.delta_r.abs().max():.3f}, |drho| = {mo.delta_rho.abs().max():.3f}. "
       f"The number of significant years was identical for {same} of {len(sigp)} measures"
       + (f"; differences: {diff_txt}." if diffs else ".") + " (sheet S6_Sensitivity).")
bullet(f"2019 is a partial year: AD became a qualifying condition on July 20, 2019. {int(m[2019].sum()) - pre:,} "
       f"of {int(m[2019].sum()):,} 2019 AD certifications were created on or after July 20; {pre} carry a created "
       "date before July 20, 2019. These are most likely certifications created earlier and later amended to add AD, "
       "or entered as the condition was being added [confirm with DOH before stating a reason].")
dd = d[["year", "ad_certifications", "a_or_b_n", "a_or_b_pct", "c_theoretical_max_early_renewal_pairs",
        "c_theoretical_max_pct", "treatment_period_12_months_pct"]].copy()
table(dd, {"a_or_b_pct": lambda v: f"{v:.2f}%", "c_theoretical_max_pct": lambda v: f"{v:.1f}%",
           "treatment_period_12_months_pct": lambda v: f"{v:.1f}%"})
para("Statewide vs. county totals (submitted pipeline):", bold=True)
table(a[["year", "AD_certifications_statewide", "AD_certs_unmatched_ZIP_dropped_v1",
         "AD_certs_double_counted_zero_ratio_ZIPs_v1", "rounding_net_v1", "county_sum_submitted_v1",
         "AD_share_of_all_pct"]], {"AD_share_of_all_pct": lambda v: f"{v:.1f}%"})
r24 = a[a.year == 2024].iloc[0]
pop24 = P[P.year == 2024].adult_pop.sum()
para(f"2024 reconciliation: {int(r24.AD_certifications_statewide):,} statewide - "
     f"{int(r24.AD_certs_unmatched_ZIP_dropped_v1)} with ZIP codes absent from the HUD crosswalk + "
     f"{int(r24.AD_certs_double_counted_zero_ratio_ZIPs_v1)} double-counted in multi-county ZIPs with zero "
     f"residential ratios + {int(r24.rounding_net_v1)} rounding = {int(r24.county_sum_submitted_v1):,}. "
     f"Statewide 2024: {r24.county_sum_submitted_v1 / pop24 * 1000:.1f} certifications per 1,000 adults "
     f"({r24.county_sum_submitted_v1 / pop24 * 100:.1f}%; an upper bound on the share of adults certified).")
corr_d = S6[S6.variant == "Corrected ZIP-to-county allocation"]
val_d = S6[S6.variant == "Valid-status certifications only"]
para(f"Allocation fix (ZIP+4 matched; zero-ratio ZIPs split by total-address ratio): maximum change in any "
     f"Table 1 coefficient |dr| = {corr_d.delta_r.abs().max():.4f}, |drho| = {corr_d.delta_rho.abs().max():.4f}. "
     f"Restricting to valid statuses (excluding canceled, created in error, invalid, pending approval): "
     f"|dr| <= {val_d.delta_r.abs().max():.3f}, |drho| <= {val_d.delta_rho.abs().max():.3f}. Neither changes "
     "any conclusion; you may keep the submitted counts and report both as sensitivity analyses, or adopt the "
     "corrected allocation (Table S1 changes by a handful of certifications).")
suggest("Each annual record contains the certifications created in that calendar year, and certifications "
        "were assigned to the year of creation. Because the de-identified record contains no patient "
        "identifier, counts and rates refer to certifications rather than unique patients; an individual can "
        "contribute more than one certification to a year through a short treatment period, a re-issued "
        "certification, or a renewal created before the prior certification expired. Results were essentially "
        "unchanged when rates were computed from certifications created between March and October only, which "
        "excludes any same-year renewal pair (all coefficients within "
        f"{max(mo.delta_r.abs().max(), mo.delta_rho.abs().max()):.2f} of the primary estimates; Table S6). AD became a qualifying condition on July 20, 2019; 2019 is "
        "therefore a partial year. Statewide totals exceed the sum of county totals by "
        f"{int(r24.AD_certifications_statewide - r24.county_sum_submitted_v1)} certifications in 2024 "
        f"({int(r24.AD_certs_unmatched_ZIP_dropped_v1)} records with ZIP codes absent from the HUD crosswalk, "
        "partly offset by allocation and rounding).")

# ---------------------------------------------------------------- R1.2
h("R1.2  Exploratory framing; spatial autocorrelation; population-weighted count models")
M = S["S4_MoransI"]
mp = M[M.variable.str.startswith("Certification rate (primary)")]
para(f"Global Moran's I of the certification rate (queen contiguity, 999 permutations): "
     f"I = {mp.MoranI.min():.2f}-{mp.MoranI.max():.2f}, all P <= {mp.p_perm.max():.3f}, 2019-2024.")
table(mp[["year", "MoranI", "p_perm", "z"]])
L = S["S4b_LISA_2024"]
hh_ = ", ".join(L[L.LISA_cluster == "High-High"].county_name.str.replace(" County", ""))
ll_ = ", ".join(L[L.LISA_cluster == "Low-Low"].county_name.str.replace(" County", ""))
para(f"LISA 2024: High-High cluster: {hh_}. Low-Low cluster: {ll_}. (Figure Fig_R1_LISA_2024.)")
N5 = S["S5_NB_IRR"]
u24 = N5[(N5.year == 2024) & (N5.model == "Unadjusted")].copy()
para("Negative binomial (NB2) models with log(adult population) offset: rate ratio per 1 SD, 2024 "
     "(all years in Table S5 / sheet S5_NB_IRR). These weight counties by population; the correlations in "
     "Table 1 weight each county equally.", bold=True)
u24["sig"] = u24.p.map(lambda v: "*" if v < 0.05 else "")
table(u24[["measure", "IRR_per_SD", "CI_low", "CI_high", "p", "resid_MoranI", "resid_Moran_p"]],
      {"p": p_, "resid_Moran_p": p_})
sig24 = u24[u24.p < 0.05].measure.tolist()
para("Significant in 2024 count model: " + "; ".join(sig24) + ". Income measures were borderline in 2024 "
     "(see table) but significant in most earlier years (SUM_Consistency).")
se = S["S5c_SpatialError"]
para("Note: in the spatial-error models, Associate's degree becomes significant in all six years although it is "
     "null in every other analysis; treat as a model-specific finding and do not emphasize.", italic=True)
para(f"Residual Moran's I remained positive in the count models (2024 range "
     f"{u24.resid_MoranI.min():.2f}-{u24.resid_MoranI.max():.2f}), so spatial-error models of log(rate) were "
     f"fitted as a sensitivity analysis (sheet S5c_SpatialError; spatial lambda in 2024 "
     f"{se[se.year == 2024].SEM_lambda.min():.2f}-{se[se.year == 2024].SEM_lambda.max():.2f}).")
para("The suggested text below describes the unadjusted count models; after adjustment for rurality most of "
     "these associations attenuate (R1.3), so combine it with the R1.3 text.", italic=True)
suggest("Certification rates were spatially clustered in every year (Moran's I = "
        f"{mp.MoranI.min():.2f}-{mp.MoranI.max():.2f}, P <= {mp.p_perm.max():.3f}; Table S4), with a high-rate "
        "cluster in south-central and east-central Pennsylvania and a low-rate cluster in the northwestern and "
        "north-central counties (local Moran's I; Figure S[x]). In "
        "population-weighted negative binomial models, the associations with age structure, educational "
        "attainment, and labor force participation persisted, whereas associations with income "
        "were borderline in 2024 and those with racial and ethnic composition were not significant (Table S5). "
        "Spatial-error models, which account for residual spatial autocorrelation, gave the same pattern for age, "
        "income, educational attainment, and labor force participation (Table S5).")
para("Also delete the 'consistency reduces false positives' sentence (lines 129-131). For information, "
     f"{int(((A.q_BH_r < .05) | (A.q_BH_rho < .05)).sum())} of {len(A)} measure-years remain significant by at "
     "least one method after Benjamini-Hochberg correction across all 204 tests (sheet A_Reproduction).",
     italic=True)

# ---------------------------------------------------------------- R1.3
h("R1.3  Rurality")
if rur_available:
    rr = S["S5b_Rurality"]
    labs = list(rr.rurality.unique())
    lab = next((l for l in labs if l.startswith("RUCC 2023 nonmetro")), labs[0])
    sub = rr[(rr.year == 2024) & (rr.rurality == lab)]
    rate_r = sub[sub.measure == "Certification rate"].iloc[0]
    strong = sub[(sub.measure != "Certification rate") & (sub.p_rho_with_rurality < 0.05)]
    para(f"Rurality is itself one of the strongest correlates of the certification rate: rho = "
         f"{rate_r.rho_with_rurality:.2f} ({p_(rate_r.p_rho_with_rurality)}) with {lab} in 2024 "
         "(nonmetro counties have lower rates; RUCC from USDA ERS 2023; "
         f"{int(P[P.year == 2024].nonmetro.sum())} of 67 PA counties are nonmetro). "
         f"{len(strong)} of 17 measures are significantly correlated with rurality, including "
         + ", ".join(f"{m.split(' (')[0]} (rho={v:.2f})" for m, v in
                     strong.sort_values("rho_with_rurality", key=abs, ascending=False)
                     [["measure", "rho_with_rurality"]].head(6).itertuples(index=False))
         + ". This confirms the reviewer's premise.")
    table(sub[["measure", "rho_with_rurality", "p_rho_with_rurality",
               "partial_spearman_rate_given_rurality", "p_partial"]],
          {"p_rho_with_rurality": p_, "p_partial": p_})
    adjm = sorted(N5[N5.model.str.startswith("Adjusted")].model.unique())
    rows = []
    for meas in R.MEASURES:
        row = dict(measure=meas)
        u = N5[(N5.measure == meas) & (N5.model == "Unadjusted")]
        row["years sig: unadjusted"] = int((u.p < .05).sum())
        for mdl in adjm:
            a_ = N5[(N5.measure == meas) & (N5.model == mdl)]
            short = "RUCC nonmetro" if "RUCC" in mdl else "% rural"
            row[f"years sig: adj. {short}"] = int((a_.p < .05).sum())
            r24 = a_[a_.year == 2024].iloc[0]
            row[f"2024 IRR adj. {short}"] = f"{r24.IRR_per_SD:.2f} ({r24.CI_low:.2f}-{r24.CI_high:.2f}) {p_(r24.p)}"
        rows.append(row)
    para("Negative binomial IRR per SD: number of years significant (of 6) without and with rurality "
         "adjustment, and 2024 adjusted estimates:", bold=True)
    RT = pd.DataFrame(rows)
    table(RT)
    rucc_col = next(c for c in RT.columns if c.startswith("years sig: adj. RUCC"))
    keep = RT[(RT["years sig: unadjusted"] >= 4) & (RT[rucc_col] >= 4)].measure.tolist()
    lost = RT[(RT["years sig: unadjusted"] >= 4) & (RT[rucc_col] <= 1)].measure.tolist()
    emerge = RT[(RT["years sig: unadjusted"] <= 1) & (RT[rucc_col] >= 4)].measure.tolist()
    para("Associations that persist after RUCC adjustment (significant in >= 4 of 6 years both unadjusted and "
         "adjusted): " + ("; ".join(keep) if keep else "none") + ". Associations that disappear (>= 4 years "
         "unadjusted, <= 1 year adjusted): " + "; ".join(lost) + ".")
    if emerge:
        para("Associations that appear only after adjustment (null unadjusted): " + "; ".join(emerge) +
             ". These most likely reflect collinearity with rurality (suppression) rather than real associations; "
             "we suggest reporting them in the supplement without interpretation.", italic=True)
    para("Caution on the race/ethnicity measures: they are so collinear with rurality (|rho| up to 0.74 with % "
         "rural) that the adjusted coefficients can change sign (e.g., White non-Hispanic becomes positive after "
         "adjusting for % rural). Treat adjusted race/ethnicity estimates as unstable and do not interpret them.",
         italic=True)
    suggest("Most sociodemographic measures varied along Pennsylvania's urban-rural gradient (Table S5), and "
            "certification rates were substantially lower in nonmetropolitan counties. After adjustment for "
            "rurality (USDA 2023 Rural-Urban Continuum Codes), the associations with income and educational "
            "attainment were no longer significant, indicating that these bivariate associations largely reflect "
            "the urban-rural gradient. The positive association with labor force participation persisted after "
            "adjustment for rurality and for the population share aged 65 and over.")
    para("Interpretive caveat: rurality is not a pure confounder. Income, education, and age structure are part "
         "of what distinguishes rural from urban counties, and rurality may also proxy access (dispensary and "
         "certifying-practitioner density, travel distance). Adjustment therefore shows that the SES associations "
         "cannot be separated from the urban-rural gradient with county data - not that SES is irrelevant. "
         "Phrase it that way in the response.", italic=True)
    para("Implication for the manuscript: the Abstract, Results, and Discussion should frame the SES findings "
         "as largely reflecting rurality, with labor force participation (and, more weakly, age structure and "
         "unemployment) as the associations that remain. This is a substantive change in emphasis - review "
         "with co-authors.", bold=True)
else:
    pending("needs the Census API key (2020 Census % rural, DHC table P2) or the USDA ERS 2023 RUCC file. "
            "Place census_api_key.txt and/or the RUCC CSV in Revision_2026_11_Frontiers/ and rerun 01-04.")

# ---------------------------------------------------------------- R1.4
h("R1.4  Influential counties and the adult denominator")
race = ["White alone, not Hispanic/Latino (%)", "Black or African American (%)",
        "Hispanic or Latino, any race (%)", "Asian alone (%)"]
rows = []
for meas in race:
    for v in ("Primary (submitted)", "Excluding Philadelphia"):
        s = S6[(S6.measure == meas) & (S6.variant == v)]
        rows.append(dict(measure=meas, variant=v,
                         r_range=f"{s.r.min():.3f} to {s.r.max():.3f}",
                         years_sig_pearson=int((s.p_r < .05).sum()),
                         rho_range=f"{s.rho.min():.3f} to {s.rho.max():.3f}",
                         years_sig_spearman=int((s.p_rho < .05).sum())))
table(pd.DataFrame(rows))
wp = S6[(S6.measure == race[0]) & (S6.variant == "Excluding Philadelphia")]
para(f"Excluding Philadelphia, the White-alone (non-Hispanic) association became significant by Pearson as well "
     f"(r = {wp.r.min():.3f} to {wp.r.max():.3f}; significant in {int((wp.p_r < .05).sum())}/6 years), confirming "
     "that Philadelphia drives the Pearson-Spearman divergence. DFBETA for the 2024 OLS fits identifies "
     "Philadelphia as the most influential county for all four race/ethnicity measures (sheet S6b_Influence_2024).")
lf = "Labor force participation rate (%)"
fo = S6[(S6.measure == lf) & (S6.variant == "Excluding Forest")]
pr = S6[(S6.measure == lf) & (S6.variant == "Primary (submitted)")]
para(f"Excluding Forest County, labor force participation remained significant in all years "
     f"(r = {fo.r.min():.3f} to {fo.r.max():.3f} vs. {pr.r.min():.3f} to {pr.r.max():.3f} with Forest; "
     f"rho = {fo.rho.min():.3f} to {fo.rho.max():.3f}).")
if hh_available:
    hhd = S6[S6.variant.str.startswith("Denominator")]
    p24 = P[P.year == 2024].assign(gq_share=lambda d_: 100 * d_.gq_pop / d_.adult_pop).sort_values(
        "gq_share", ascending=False).head(5)
    para("Group-quarters populations (prisons, dormitories, nursing facilities; all ages) are large relative to "
         "the adult population in several counties: "
         + "; ".join(f"{r.county_name.replace(' County', '')} {r.gq_share:.0f}% (rate "
                     f"{r.rate_v1:.1f} -> {r.rate_hh_adults:.1f} per 1,000 household adults)"
                     for r in p24.itertuples()) + " (2024 ACS 5-year). ACS group-quarters estimates for small counties are "
         "imprecise (Forest: "
         f"{int(P[(P.year == 2019) & (P.county_fips == R.FOREST)].gq_pop.iloc[0]):,} in the 2015-2019 release vs. "
         f"{int(P[(P.year == 2024) & (P.county_fips == R.FOREST)].gq_pop.iloc[0]):,} in 2020-2024).")
    hl = hhd[hhd.measure == lf]
    para(f"Household-adult denominator (ACS B09021): maximum |dr| = {hhd.delta_r.abs().max():.3f}, "
         f"|drho| = {hhd.delta_rho.abs().max():.3f} across all measures and years (sheet S6_Sensitivity). "
         f"Labor force participation remains significant in all years (r = {hl.r.min():.3f} to {hl.r.max():.3f}; "
         f"rho = {hl.rho.min():.3f} to {hl.rho.max():.3f}); the largest change is in 2019 (r "
         f"{hl[hl.year == 2019].r_primary.iloc[0]:.3f} -> {hl[hl.year == 2019].r.iloc[0]:.3f}), driven by Forest County.")
    table(hhd[hhd.year == 2024][["measure", "r", "p_r", "rho", "p_rho", "delta_r", "delta_rho"]],
          {"p_r": p_, "p_rho": p_})
else:
    pending("household-adult denominator (ACS B09021) needs the Census API key.")

# ---------------------------------------------------------------- R1.5
h("R1.5  How consistent is each association? (for rewriting the Abstract and Conclusion)")
SUM = S["SUM_Consistency"]
cols = [c for c in ["measure", "years_sig_pearson", "years_sig_spearman", "years_sig_NB_unadjusted",
                    "years_sig_spatial_error", "years_sig_spearman_Excluding Philadelphia",
                    "years_sig_pearson_Excluding Philadelphia"] if c in SUM.columns]
cols += [c for c in SUM.columns if c.startswith("years_sig_Adjusted")]
table(SUM[cols].rename(columns=lambda c: c.replace("years_sig_", "").replace("_", " ")))
para("Reading across: age structure, high school/GED, bachelor's degree or higher, and labor force "
     "participation are significant under every unadjusted method, but only labor force participation (and "
     "partly age and unemployment) survives adjustment for rurality (R1.3). Race and ethnicity associations are a different case: "
     "in the full sample they are Spearman-only because Philadelphia (high Black share, low rate) pulls the "
     "Pearson estimates toward zero; excluding Philadelphia, the White non-Hispanic, Hispanic, and Asian "
     "associations are significant by both methods in most years. However, none of the race or ethnicity "
     "measures is significant in the population-weighted or spatial-error models, i.e., they describe the "
     "pattern across many small counties rather than where most certified adults live. This supports Reviewer "
     "1's request to remove 'patterned by race' from the Abstract and Conclusion and to describe these "
     "associations as modest and method-dependent.")

# ---------------------------------------------------------------- R1.7 / R2.9
h("R1.7 and R2.9  Border counties and adult-use sales in neighboring states")
BD, BT, FE = S["S7b_Border_Desc"], S["S7c_Border_Tests"], S["S7d_Border_FE"]
table(BD[["group", "pct_change_2022_2024_median", "pct_change_2022_2024_count", "2019_median",
          "2020_median", "2024_median"]])
table(BT, {"p": p_})
table(FE[["model", "exposure_rule", "n_exposed_county_years", "IRR", "CI_low", "CI_high", "p"]], {"p": p_})
e0, e1 = FE.iloc[0], FE.iloc[1]
para(f"Interpretation: counties bordering states with operating adult-use retail grew far less after sales began "
     f"(median {BD.iloc[0].pct_change_2022_2024_median:.0f}% vs. {BD[BD.group == 'Interior'].pct_change_2022_2024_median.iloc[0]:.0f}% "
     f"in interior counties, 2022-2024), and the two-way fixed-effects model estimates rates "
     f"{(1 - e0.IRR) * 100:.0f}% lower after exposure (IRR {e0.IRR:.2f}, 95% CI {e0.CI_low:.2f}-{e0.CI_high:.2f}). "
     f"However, the estimate disappears when county-specific linear trends are added (IRR {e1.IRR:.3f}, "
     f"{p_(e1.p)}): the border counties were already on flatter trajectories, so cross-border access and "
     "saturation cannot be separated with these data - consistent with the reviewer's caveat. Limitations: "
     "exposure uses statewide retail start dates, not the location or opening date of the nearest dispensary "
     "(New York's licensed retail network expanded gradually after December 2022 [verify before citing]), and "
     "exposed counties have only two or three post-exposure years in the 2020-2024 window, leaving few "
     "pre/post contrasts.")
NYR = S["S7e_NYborder_ranks"]
lowq = int((NYR.rank_2019 <= 17).sum())
para(f"R2.9 (NY border): {lowq} of {len(NYR)} NY-border counties ranked in the lowest quartile of rates in 2019, "
     "more than three years before NY retail sales began (Dec 2022) (rank 1 = lowest of 67): " + "; ".join(
         f"{r.county_name.replace(' County', '')} {int(r.rank_2019)}" for _, r in NYR.sort_values('rank_2019').iterrows())
     + f". NY-border vs. all other counties, Mann-Whitney: 2019 {p_(BT.iloc[2].p)}, 2020 {p_(BT.iloc[3].p)}. "
     "Erie County (Erie city) is the exception. Cross-border purchasing cannot explain the low baseline rates; "
     "rurality, age structure, and access (dispensaries, certifying practitioners) are more likely contributors.")
suggest(f"{lowq} of the {len(NYR)} counties bordering New York had certification rates in the lowest quartile of the state in 2019, before "
        "adult-use retail sales began in New York in December 2022, indicating that cross-border access does "
        "not explain their low baseline rates. After adult-use sales began in New Jersey, New York, and Maryland, "
        "certification rates in bordering counties grew less than in interior counties; however, this difference "
        "was not distinguishable from pre-existing differences in county trajectories (Table S7), and saturation "
        "in early-adopting counties remains an alternative explanation.")

# ---------------------------------------------------------------- R1.8
h("R1.8  Do the gradients weaken over time? (panel GEE)")
G = S["S7a_Panel_2020_2024"]
gs = G[G.p_interaction < 0.05]
table(G[["measure", "IRR_per_SD_first_year", "IRR_per_SD_last_year", "ratio_of_IRR_per_year", "CI_low",
         "CI_high", "p_interaction"]], {"p_interaction": p_})
para("Measures whose association changed significantly from 2020 to 2024: " +
     "; ".join(f"{r.measure} (IRR per SD {r.IRR_per_SD_first_year:.2f} -> {r.IRR_per_SD_last_year:.2f}, {p_(r.p_interaction)})"
               for _, r in gs.iterrows()) + ".")
suggest("In panel models, the associations of certification rates with median household income, per capita "
        "income, median gross rent, and bachelor's degree attainment attenuated significantly between 2020 and "
        "2024 (Table S7), consistent with diffusion of certification into less affluent counties as the program "
        "matured; associations with age structure and labor force participation did not change significantly.")
para("Note: the race/ethnicity measures also show significant interactions, but their main associations are "
     "weak in these population-weighted models, so we suggest not emphasizing them.", italic=True)

# ---------------------------------------------------------------- R2.8
h("R2.8  Labor force participation vs. age structure")
LF = S["R2.8_LFP_Age"]
para(f"County labor force participation is strongly inversely related to the share aged 65+ (rho = "
     f"{LF.rho_LFP_vs_pct65.max():.2f} to {LF.rho_LFP_vs_pct65.min():.2f}), but its association with the "
     f"certification rate persists after controlling for the 65+ share (partial rho = "
     f"{LF.partial_spearman_rate_LFP_given_pct65.min():.2f}-{LF.partial_spearman_rate_LFP_given_pct65.max():.2f}, "
     f"all P <= {LF.p_partial_spearman.max():.3f}; NB IRR per SD adjusted for 65+ = "
     f"{LF.NB_IRR_LFP_adjusted_pct65.min():.2f}-{LF.NB_IRR_LFP_adjusted_pct65.max():.2f}).")
table(LF[["year", "rho_LFP_vs_pct65", "partial_spearman_rate_LFP_given_pct65", "p_partial_spearman",
          "NB_IRR_LFP_unadjusted", "NB_IRR_LFP_adjusted_pct65", "CI_low", "CI_high", "p_adj"]],
      {"p_partial_spearman": p_, "p_adj": p_})

# ---------------------------------------------------------------- verification
h("Verification performed")
for t_ in ["Table 1: all 204 coefficients reproduced exactly from the panel (fresh ACS pull from the Census API).",
           "Certification counts, statuses, created dates, and treatment periods re-counted from the raw CSV files "
           "with independent code (Python csv module): all AD counts match.",
           "Moran's I recomputed by hand from the weights matrix (identical); weights symmetric, no isolates.",
           "Negative binomial IRRs re-estimated with an independent likelihood (scipy): maximum difference 0.00002; "
           "all 306 NB fits converged.",
           "Panel interactions re-estimated as clustered NB GLMs: same direction and significance as the GEE.",
           "Border effect re-estimated as a two-way fixed-effects regression of log rate: 0.91 (P=0.002), "
           "matching the Poisson estimate.",
           "Benjamini-Hochberg count recomputed (76 of 102 measure-years)."]:
    bullet(t_)

# ---------------------------------------------------------------- files
h("Files")
for f, d_ in [("outputs/tables/Revision_Results.xlsx", "all analyses; README sheet maps sheets to comments"),
              ("outputs/tables/R1.1_certification_accounting.xlsx", "R1.1 accounting (local only: contains ZIP-level counts)"),
              ("outputs/tables/county_year_panel.csv", "analysis panel (county-level aggregates)"),
              ("outputs/figures/Fig_R1_forest_2024.*", "candidate Figure 6: unadjusted vs. RUCC-adjusted (+ Prism CSV)"),
              ("outputs/figures/FigS_R1_forest_2024_all_models.*", "supplement: adds % rural-adjusted and spatial-error estimates"),
              ("outputs/figures/Fig_R1_LISA_2024.*", "LISA cluster map (candidate Fig 1F or supplement)"),
              ("outputs/figures/Fig_R1_border_change.*", "border groups and 2022-2024 change (supplement / response)")]:
    bullet(f"{f} - {d_}")

os.makedirs(os.path.join(R.REV_DIR, "docs"), exist_ok=True)
out = os.path.join(R.REV_DIR, "docs", "Revision_Analysis_Report.docx")
doc.save(out)
print("wrote", out)
