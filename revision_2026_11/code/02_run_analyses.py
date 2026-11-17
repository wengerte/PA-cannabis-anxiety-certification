"""
02_run_analyses.py - statistical analyses requested by the reviewers.

Input : outputs/tables/county_year_panel.csv, pa_county_queen_neighbors.csv
        (from 01_build_panel.py), inputs_public/table1_submitted.csv
Output: outputs/tables/Revision_Results.xlsx (one sheet per analysis; the
        README sheet maps each sheet to the reviewer comment it answers)

Analyses
  A   Reproduction of submitted Table 1 from the panel (plus BH-FDR q values)
  S4  Global Moran's I of the certification rate by year (R1.2); LISA 2024
  S5  Negative binomial models with log(adult population) offset, IRR per SD,
      unadjusted and rurality-adjusted, with Moran's I of residuals
      (R1.2, R1.3); rurality-measure correlations; partial Spearman
  S6  Sensitivity analyses: excluding Philadelphia, excluding Forest,
      household-adult denominator, corrected ZIP allocation, valid-status
      certifications only (R1.4, R1.1); DFBETA influence for 2024 OLS fits
  LFP Labor force participation vs. age structure (R2.8)
  S7  Panel GEE measure x year interaction (R1.8); border exposure to
      adult-use sales: descriptive change, Mann-Whitney, two-way fixed-effects
      Poisson (R1.7, R2.9)
  SUM Consistency summary across years and methods (R1.5)

Run:  python 02_run_analyses.py            python 02_run_analyses.py --selftest
All permutation tests use a fixed seed, so results are reproducible.
"""

import argparse
import contextlib
import io
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.discrete.discrete_model import NegativeBinomial
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.outliers_influence import OLSInfluence

import rev_common as R

warnings.filterwarnings("ignore")


# =============================================================================
# Helpers
# =============================================================================

def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = ~np.isnan(x) & ~np.isnan(y)
    x, y = x[m], y[m]
    if len(x) < 5:
        return dict(n=len(x), r=np.nan, p_r=np.nan, rho=np.nan, p_rho=np.nan)
    r, pr = stats.pearsonr(x, y)
    rho, prho = stats.spearmanr(x, y)
    return dict(n=len(x), r=r, p_r=pr, rho=rho, p_rho=prho)


def partial_corr(x, y, z, method="spearman"):
    """Partial correlation of x and y controlling for z (one covariate)."""
    d = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    if method == "spearman":
        d = d.rank()
    rx = sm.OLS(d["x"], sm.add_constant(d["z"])).fit().resid
    ry = sm.OLS(d["y"], sm.add_constant(d["z"])).fit().resid
    r = np.corrcoef(rx, ry)[0, 1]
    n = len(d)
    t = r * np.sqrt((n - 3) / max(1e-12, 1 - r ** 2))
    p = 2 * stats.t.sf(abs(t), n - 3)
    return r, p, n


def zscore(s):
    s = pd.Series(s, dtype=float)
    return (s - s.mean()) / s.std(ddof=0)


def make_w(fips_order, nbr_df):
    from libpysal.weights import W
    nb = {f: [] for f in fips_order}
    for a, b in nbr_df[["county_fips", "neighbor_fips"]].itertuples(index=False):
        if a in nb and b in nb:
            nb[a].append(b)
    w = W(nb, id_order=list(fips_order), silence_warnings=True)
    w.transform = "r"
    return w


def moran(y, w):
    from esda.moran import Moran
    np.random.seed(R.SEED)
    m = Moran(np.asarray(y, float), w, permutations=999)
    return m.I, m.p_sim, m.z_sim


def nb_fit(counts, X, offset):
    """NB2 regression by maximum likelihood; returns (result, alpha)."""
    mod = NegativeBinomial(np.asarray(counts, float), X, loglike_method="nb2",
                           offset=np.asarray(offset, float))
    res = mod.fit(disp=0, maxiter=500, method="bfgs")
    if not res.mle_retvals.get("converged", True):
        res = mod.fit(disp=0, maxiter=2000, method="nm")
        res = mod.fit(start_params=res.params, disp=0, maxiter=500, method="bfgs")
    return res, float(res.params[-1])


def nb_irr(d, measure, adjust=None, w=None):
    """IRR per 1 SD of `measure` (and residual Moran's I) for one year's data."""
    cols = [measure] + ([adjust] if adjust else [])
    dd = d.dropna(subset=cols + ["certs_v1", "adult_pop"]).copy()
    X = pd.DataFrame({"z": zscore(dd[measure]).values})
    if adjust:
        X["adj"] = zscore(dd[adjust]).values
    X = sm.add_constant(X)
    res, alpha = nb_fit(dd["certs_v1"], X, np.log(dd["adult_pop"]))
    b, se = res.params["z"], res.bse["z"]
    out = dict(n=len(dd), IRR_per_SD=np.exp(b), CI_low=np.exp(b - 1.96 * se),
               CI_high=np.exp(b + 1.96 * se), p=res.pvalues["z"], alpha=alpha,
               SD_of_measure=dd[measure].std(ddof=0))
    if w is not None and len(dd) == len(d):
        glm = sm.GLM(dd["certs_v1"].values, X, family=sm.families.NegativeBinomial(alpha=alpha),
                     offset=np.log(dd["adult_pop"].values)).fit()
        out["resid_MoranI"], out["resid_Moran_p"], _ = moran(glm.resid_pearson, w)
    return out


def sig_stars(p):
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


# =============================================================================
# Analyses
# =============================================================================

def a_reproduction(P):
    t1 = pd.read_csv(os.path.join(R.INPUTS_DIR, "table1_submitted.csv"))
    rows = []
    for y in R.YEARS:
        d = P[P.year == y]
        for m in R.MEASURES:
            c = corr(d[m], d["rate_v1"])
            rows.append(dict(measure=m, domain=R.DOMAIN_OF[m], year=y, **c))
    A = pd.DataFrame(rows)
    for stat, col in (("pearson", "r"), ("spearman", "rho")):
        sub = t1[t1.stat == stat].rename(columns={"coef": f"table1_{col}"})[["measure", "year", f"table1_{col}"]]
        A = A.merge(sub, on=["measure", "year"], how="left")
        A[f"diff_{col}"] = (A[col].round(3) - A[f"table1_{col}"]).round(3)
    allp = np.r_[A["p_r"].values, A["p_rho"].values]
    q = multipletests(allp, method="fdr_bh")[1]
    A["q_BH_r"], A["q_BH_rho"] = q[:len(A)], q[len(A):]
    return A


def s4_moran(P, w_of):
    rows = []
    for y in R.YEARS:
        d = P[P.year == y].sort_values("county_fips")
        for col, lab in (("rate_v1", "Certification rate (primary)"),
                         ("rate_hh_adults", "Rate per household adults")):
            if d[col].notna().all():
                I, p, z = moran(d[col], w_of(d))
                rows.append(dict(year=y, variable=lab, MoranI=I, p_perm=p, z=z, permutations=999))
    return pd.DataFrame(rows)


def lisa_2024(P, w_of):
    from esda.moran import Moran_Local
    d = P[P.year == R.PRIMARY_YEAR].sort_values("county_fips").reset_index(drop=True)
    w = w_of(d)
    np.random.seed(R.SEED)
    try:
        lm = Moran_Local(d["rate_v1"].values, w, permutations=999, seed=R.SEED)
    except TypeError:
        lm = Moran_Local(d["rate_v1"].values, w, permutations=999)
    lab = {1: "High-High", 2: "Low-High", 3: "Low-Low", 4: "High-Low"}
    d["LISA_Ii"], d["LISA_p"] = lm.Is, lm.p_sim
    d["LISA_cluster"] = [lab[q] if p < 0.05 else "Not significant" for q, p in zip(lm.q, lm.p_sim)]
    return d[["county_fips", "county_name", "rate_v1", "LISA_Ii", "LISA_p", "LISA_cluster"]]


def rurality_columns(P):
    cols = []
    if P["nonmetro"].notna().all():
        cols.append(("nonmetro", "RUCC 2023 nonmetro (4-9) vs metro (1-3)"))
    if P["rucc_2023"].notna().all():
        cols.append(("rucc_2023", "RUCC 2023 ordinal (1-9)"))
    if P["pct_rural_2020"].notna().all():
        cols.append(("pct_rural_2020", "2020 Census % rural population"))
    return cols


def s5_nb(P, w_of, rur):
    rows = []
    for y in R.YEARS:
        d = P[P.year == y].sort_values("county_fips").reset_index(drop=True)
        w = w_of(d)
        for m in R.MEASURES:
            rows.append(dict(measure=m, domain=R.DOMAIN_OF[m], year=y, model="Unadjusted",
                             **nb_irr(d, m, None, w)))
            for col, lab in rur:
                if col == "rucc_2023":
                    continue                      # ordinal RUCC is reported as sensitivity only
                rows.append(dict(measure=m, domain=R.DOMAIN_OF[m], year=y,
                                 model=f"Adjusted: {lab}", **nb_irr(d, m, col, w)))
    return pd.DataFrame(rows)


def s5_rural_assoc(P, rur):
    rows = []
    for col, lab in rur:
        for y in R.YEARS:
            d = P[P.year == y]
            for m in R.MEASURES + ["rate_v1"]:
                c = corr(d[col], d[m])
                pr, pp, n = partial_corr(d[m], d["rate_v1"], d[col]) if m != "rate_v1" else (np.nan,) * 3
                rows.append(dict(rurality=lab, year=y,
                                 measure=("Certification rate" if m == "rate_v1" else m),
                                 rho_with_rurality=c["rho"], p_rho_with_rurality=c["p_rho"],
                                 partial_spearman_rate_given_rurality=pr, p_partial=pp))
    return pd.DataFrame(rows)


def s5_spatial_error(P, w_of):
    """Spatial-error regression of log(rate) on each standardized measure (ML).

    Sensitivity analysis for R1.2: residual spatial autocorrelation remained
    after the count models, so the association is re-estimated allowing for
    spatially autocorrelated errors."""
    from spreg import ML_Error
    rows = []
    for y in R.YEARS:
        d = P[P.year == y].sort_values("county_fips").reset_index(drop=True)
        w = w_of(d)
        yv = np.log(d["rate_v1"].values).reshape(-1, 1)
        for m in R.MEASURES:
            x = zscore(d[m]).values.reshape(-1, 1)
            ols = sm.OLS(yv.ravel(), sm.add_constant(x)).fit()
            with contextlib.redirect_stdout(io.StringIO()):
                me = ML_Error(yv, x, w=w, name_y="log_rate", name_x=["z"])
            b, se = float(me.betas[1][0]), float(me.std_err[1])
            p = float(me.z_stat[1][1])
            rows.append(dict(measure=m, domain=R.DOMAIN_OF[m], year=y,
                             OLS_beta_lograte_per_SD=ols.params[1], OLS_p=ols.pvalues[1],
                             SEM_beta_lograte_per_SD=b, SEM_rate_ratio_per_SD=np.exp(b),
                             SEM_CI_low=np.exp(b - 1.96 * se), SEM_CI_high=np.exp(b + 1.96 * se),
                             SEM_p=p, SEM_lambda=float(me.betas[-1][0])))
    return pd.DataFrame(rows)


def s6_sensitivity(P):
    variants = [
        ("Primary (submitted)", lambda d: d, "rate_v1"),
        ("Excluding Philadelphia", lambda d: d[d.county_fips != R.PHILADELPHIA], "rate_v1"),
        ("Excluding Forest", lambda d: d[d.county_fips != R.FOREST], "rate_v1"),
        ("Excluding Philadelphia and Forest",
         lambda d: d[~d.county_fips.isin([R.PHILADELPHIA, R.FOREST])], "rate_v1"),
        ("Denominator: adults in households (ACS B09021)", lambda d: d, "rate_hh_adults"),
        ("Corrected ZIP-to-county allocation", lambda d: d, "rate_corrected"),
        ("Valid-status certifications only", lambda d: d, "rate_valid_status"),
        ("Certifications created March-October only", lambda d: d, "rate_mar_oct"),
    ]
    rows = []
    for y in R.YEARS:
        base = P[P.year == y]
        for name, f, rate in variants:
            d = f(base)
            if d[rate].isna().all():
                continue
            for m in R.MEASURES:
                rows.append(dict(variant=name, measure=m, domain=R.DOMAIN_OF[m], year=y,
                                 **corr(d[m], d[rate])))
    S = pd.DataFrame(rows)
    prim = S[S.variant == "Primary (submitted)"][["measure", "year", "r", "rho"]].rename(
        columns={"r": "r_primary", "rho": "rho_primary"})
    S = S.merge(prim, on=["measure", "year"])
    S["delta_r"], S["delta_rho"] = S["r"] - S["r_primary"], S["rho"] - S["rho_primary"]
    return S


def s6_influence(P):
    d = P[P.year == R.PRIMARY_YEAR].reset_index(drop=True)
    thr = 2 / np.sqrt(len(d))
    rows = []
    for m in R.MEASURES:
        dd = d.dropna(subset=[m])
        res = sm.OLS(dd["rate_v1"], sm.add_constant(dd[m])).fit()
        dfb = OLSInfluence(res).dfbetas[:, 1]
        cook = OLSInfluence(res).cooks_distance[0]
        order = np.argsort(-np.abs(dfb))
        for i in order[:3]:
            rows.append(dict(measure=m, county=dd.loc[dd.index[i], "county_name"],
                             DFBETA_slope=dfb[i], Cooks_D=cook[i],
                             exceeds_2_over_sqrt_n=abs(dfb[i]) > thr, threshold=thr))
    return pd.DataFrame(rows)


def r28_lfp(P):
    lfp, p65, age = "Labor force participation rate (%)", "Population 65 and over (%)", "Median age (years)"
    rows = []
    for y in R.YEARS:
        d = P[P.year == y]
        c1, c2 = corr(d[lfp], d[p65]), corr(d[lfp], d[age])
        pr_s, pp_s, _ = partial_corr(d[lfp], d["rate_v1"], d[p65], "spearman")
        pr_p, pp_p, _ = partial_corr(d[lfp], d["rate_v1"], d[p65], "pearson")
        nb_a = nb_irr(d, lfp, p65)
        nb_u = nb_irr(d, lfp, None)
        rows.append(dict(year=y, rho_LFP_vs_pct65=c1["rho"], p1=c1["p_rho"],
                         r_LFP_vs_pct65=c1["r"], rho_LFP_vs_median_age=c2["rho"],
                         partial_spearman_rate_LFP_given_pct65=pr_s, p_partial_spearman=pp_s,
                         partial_pearson_rate_LFP_given_pct65=pr_p, p_partial_pearson=pp_p,
                         NB_IRR_LFP_unadjusted=nb_u["IRR_per_SD"], p_unadj=nb_u["p"],
                         NB_IRR_LFP_adjusted_pct65=nb_a["IRR_per_SD"], CI_low=nb_a["CI_low"],
                         CI_high=nb_a["CI_high"], p_adj=nb_a["p"]))
    return pd.DataFrame(rows)


def s7_panel(P, years, label):
    rows = []
    d0 = P[P.year.isin(years)].copy()
    d0["year_c"] = d0["year"] - np.mean(years)
    for m in R.MEASURES:
        d = d0.dropna(subset=[m]).copy()
        d["z"] = zscore(d[m]).values
        d["off"] = np.log(d["adult_pop"])
        # alpha from a pooled NB2 fit with year effects
        X = pd.get_dummies(d["year"], prefix="y", drop_first=True, dtype=float)
        X["z"] = d["z"].values
        X = sm.add_constant(X)
        _, alpha = nb_fit(d["certs_v1"], X, d["off"])
        mod = smf.gee("certs_v1 ~ C(year) + z + z:year_c", groups="county_fips", data=d,
                      offset=d["off"], family=sm.families.NegativeBinomial(alpha=alpha),
                      cov_struct=sm.cov_struct.Exchangeable())
        res = mod.fit()
        b, se = res.params["z:year_c"], res.bse["z:year_c"]
        bz = res.params["z"]
        rows.append(dict(window=label, measure=m, domain=R.DOMAIN_OF[m],
                         IRR_per_SD_at_mid_window=np.exp(bz),
                         IRR_per_SD_first_year=np.exp(bz + b * (years[0] - np.mean(years))),
                         IRR_per_SD_last_year=np.exp(bz + b * (years[-1] - np.mean(years))),
                         ratio_of_IRR_per_year=np.exp(b), CI_low=np.exp(b - 1.96 * se),
                         CI_high=np.exp(b + 1.96 * se), p_interaction=res.pvalues["z:year_c"],
                         alpha=alpha))
    return pd.DataFrame(rows)


def s7_border(P):
    P = P.copy()
    P["border_states"] = P["border_states"].fillna("")
    W = P.pivot_table(index=["county_fips", "county_name", "border_states"], columns="year",
                      values="rate_v1").reset_index()
    W["change_2022_2024"] = W[2024] - W[2022]
    W["pct_change_2022_2024"] = 100 * (W[2024] / W[2022] - 1)
    W["pct_change_2021_2024"] = 100 * (W[2024] / W[2021] - 1)

    def grp(s):
        st = set(s.split(",")) - {""}
        if st & {"NJ", "NY", "MD"}:
            return "Borders NJ/NY/MD (sales by mid-2023)"
        if st:
            return "Borders OH/DE/WV only (no sales before Aug 2024)"
        return "Interior"
    W["group"] = W["border_states"].map(grp)
    for s in ("NJ", "NY", "MD"):
        W[f"borders_{s}"] = W["border_states"].str.contains(s).astype(int)

    desc = (W.groupby("group")[["pct_change_2022_2024", "pct_change_2021_2024", 2019, 2020, 2024]]
            .agg(["median", "count"]))
    desc.columns = [f"{a}_{b}" for a, b in desc.columns]
    desc = desc.reset_index()
    for s in ("NJ", "NY", "MD"):
        sub = W[W[f"borders_{s}"] == 1]
        desc.loc[len(desc)] = {"group": f"  of which borders {s}",
                               "pct_change_2022_2024_median": sub["pct_change_2022_2024"].median(),
                               "pct_change_2022_2024_count": len(sub),
                               "pct_change_2021_2024_median": sub["pct_change_2021_2024"].median(),
                               "2019_median": sub[2019].median(), "2020_median": sub[2020].median(),
                               "2024_median": sub[2024].median()}

    tests = []
    exp_ = W[W.group.str.startswith("Borders NJ")]
    inte = W[W.group == "Interior"]
    for col in ("pct_change_2022_2024", "pct_change_2021_2024"):
        u = stats.mannwhitneyu(exp_[col], inte[col])
        tests.append(dict(comparison=f"{col}: NJ/NY/MD-border vs interior", U=u.statistic,
                          p=u.pvalue, median_exposed=exp_[col].median(), median_interior=inte[col].median(),
                          n_exposed=len(exp_), n_interior=len(inte)))
    ny = W[W.borders_NY == 1]
    rest = W[W.borders_NY == 0]
    for y in (2019, 2020, 2021):
        u = stats.mannwhitneyu(ny[y], rest[y])
        tests.append(dict(comparison=f"Rate {y} (before NY retail sales): NY-border vs all other",
                          U=u.statistic, p=u.pvalue, median_exposed=ny[y].median(),
                          median_interior=rest[y].median(), n_exposed=len(ny), n_interior=len(rest)))
    rank = W.copy()
    for y in (2019, 2020, 2024):
        rank[f"rank_{y}"] = rank[y].rank(ascending=True).astype(int)
    ny_ranks = rank[rank.borders_NY == 1][["county_name", 2019, "rank_2019", 2020, "rank_2020",
                                            2022, 2024, "rank_2024", "pct_change_2022_2024"]]

    fe_rows = []
    d = P[P.year.isin(R.FULL_YEARS)].copy()
    d["off"] = np.log(d["adult_pop"])
    for rule in ("exposed_any", "exposed_half"):
        res = smf.glm(f"certs_v1 ~ C(county_fips) + C(year) + {rule}", data=d, offset=d["off"],
                      family=sm.families.Poisson()).fit(
            cov_type="cluster", cov_kwds={"groups": pd.factorize(d["county_fips"])[0]})
        b, se = res.params[rule], res.bse[rule]
        fe_rows.append(dict(model="Two-way FE Poisson (county + year), 2020-2024, cluster-robust SE",
                            exposure_rule=("sales began any time in year" if rule == "exposed_any"
                                           else "sales began by July 1 of year"),
                            n_exposed_county_years=int(d[rule].sum()),
                            IRR=np.exp(b), CI_low=np.exp(b - 1.96 * se), CI_high=np.exp(b + 1.96 * se),
                            p=res.pvalues[rule]))
        # Robustness: county-specific linear trends (absorbs differential growth /
        # saturation that would otherwise load onto the exposure indicator)
        d["t"] = d["year"] - 2022
        row = dict(model="Same + county-specific linear trends (robustness)",
                   exposure_rule=("sales began any time in year" if rule == "exposed_any"
                                  else "sales began by July 1 of year"),
                   n_exposed_county_years=int(d[rule].sum()))
        try:
            res2 = smf.glm(f"certs_v1 ~ C(county_fips) + C(year) + C(county_fips):t + {rule}", data=d,
                           offset=d["off"], family=sm.families.Poisson()).fit(
                cov_type="cluster", cov_kwds={"groups": pd.factorize(d["county_fips"])[0]})
            b, se = res2.params[rule], res2.bse[rule]
            row.update(IRR=np.exp(b), CI_low=np.exp(b - 1.96 * se), CI_high=np.exp(b + 1.96 * se),
                       p=res2.pvalues[rule])
        except Exception as e:                                   # noqa: BLE001
            row.update(IRR=np.nan, CI_low=np.nan, CI_high=np.nan, p=np.nan, note=f"did not converge: {e}")
        fe_rows.append(row)
    return W, desc, pd.DataFrame(tests), ny_ranks, pd.DataFrame(fe_rows)


def summary_consistency(A, S5, S6, S5c=None):
    rows = []
    for m in R.MEASURES:
        a = A[A.measure == m]
        u = S5[(S5.measure == m) & (S5.model == "Unadjusted")]
        adj = S5[(S5.measure == m) & (S5.model.str.startswith("Adjusted"))]
        row = dict(domain=R.DOMAIN_OF[m], measure=m,
                   years_sig_pearson=int((a.p_r < .05).sum()),
                   years_sig_spearman=int((a.p_rho < .05).sum()),
                   years_sig_NB_unadjusted=int((u.p < .05).sum()),
                   years_q_BH_lt_05_either=int(((a.q_BH_r < .05) | (a.q_BH_rho < .05)).sum()),
                   rho_2024=a[a.year == 2024].rho.iloc[0],
                   IRR_2024_unadjusted=u[u.year == 2024].IRR_per_SD.iloc[0])
        if S5c is not None:
            se = S5c[S5c.measure == m]
            row["years_sig_spatial_error"] = int((se.SEM_p < .05).sum())
            row["SEM_rate_ratio_2024"] = se[se.year == 2024].SEM_rate_ratio_per_SD.iloc[0]
        for model in sorted(adj.model.unique()):
            sub = adj[adj.model == model]
            row[f"years_sig_{model}"] = int((sub.p < .05).sum())
            row[f"IRR_2024_{model}"] = sub[sub.year == 2024].IRR_per_SD.iloc[0]
        for v in ("Excluding Philadelphia", "Excluding Forest",
                  "Denominator: adults in households (ACS B09021)",
                  "Certifications created March-October only"):
            sub = S6[(S6.measure == m) & (S6.variant == v)]
            if len(sub):
                row[f"years_sig_spearman_{v}"] = int((sub.p_rho < .05).sum())
                row[f"years_sig_pearson_{v}"] = int((sub.p_r < .05).sum())
        rows.append(row)
    return pd.DataFrame(rows)


README_ROWS = [
    ("README", "This sheet", "-"),
    ("A_Reproduction", "Correlations recomputed from the panel vs. submitted Table 1 (diff columns should be 0); BH-FDR q values across all 204 tests (for information)", "R1.2"),
    ("S4_MoransI", "Global Moran's I (queen contiguity, row-standardized, 999 permutations) of the certification rate, each year", "R1.2"),
    ("S4b_LISA_2024", "Local Moran's I clusters, 2024 (for optional map)", "R1.2"),
    ("S5_NB_IRR", "Negative binomial (NB2) models, offset log(adult population): IRR per 1 SD of each measure, unadjusted and rurality-adjusted, with Moran's I of Pearson residuals", "R1.2, R1.3"),
    ("S5b_Rurality", "Spearman rho of each measure (and the rate) with rurality; partial Spearman of rate with each measure controlling for rurality", "R1.3"),
    ("S5c_SpatialError", "Spatial-error (ML) regression of log(rate) on each standardized measure; rate ratio per SD and spatial lambda (sensitivity for residual autocorrelation)", "R1.2"),
    ("S6_Sensitivity", "Pearson/Spearman under: excluding Philadelphia, excluding Forest, both, household-adult denominator, corrected ZIP allocation, valid-status certifications only, certifications created March-October only (removes possible same-year renewal pairs); deltas vs. primary", "R1.4, R1.1"),
    ("S6b_Influence_2024", "Top-3 DFBETA (slope) counties for each 2024 OLS fit; threshold 2/sqrt(n)", "R1.4"),
    ("R2.8_LFP_Age", "Labor force participation vs. % aged 65+; partial correlations and NB IRR for LFP adjusted for % 65+", "R2.8"),
    ("S7a_Panel_2020_2024", "NB GEE (exchangeable, clustered by county): ratio of IRR per year (measure x year interaction), 2020-2024", "R1.8"),
    ("S7a_Panel_2019_2024", "Same, including partial-year 2019", "R1.8"),
    ("S7b_Border_Desc", "Median rate change by border group (2022-2024, 2021-2024) and median rates", "R1.7, R2.9"),
    ("S7c_Border_Tests", "Mann-Whitney tests: change in border vs. interior counties; NY-border vs. other counties before NY sales", "R1.7, R2.9"),
    ("S7d_Border_FE", "Two-way fixed-effects Poisson: rate ratio for bordering a state with operating adult-use retail; robustness with county-specific linear trends", "R1.7"),
    ("S7e_NYborder_ranks", "NY-border county rates and statewide ranks (1 = lowest)", "R2.9"),
    ("S7f_Border_County", "County-level rates, change, and border group", "R1.7, R2.9"),
    ("SUM_Consistency", "Per measure: number of years significant under each method and sensitivity analysis", "R1.5"),
]


def run():
    P = pd.read_csv(os.path.join(R.TABLE_DIR, "county_year_panel.csv"), dtype={"county_fips": str})
    N = pd.read_csv(os.path.join(R.TABLE_DIR, "pa_county_queen_neighbors.csv"),
                    dtype={"county_fips": str, "neighbor_fips": str})
    w_of = lambda d: make_w(d["county_fips"].tolist(), N)          # noqa: E731
    rur = rurality_columns(P)
    print("Rurality covariates available:", [l for _, l in rur] or "none (provide API key or RUCC file)")

    print("A  reproduction ..."); A = a_reproduction(P)
    print("   max |diff| vs Table 1: r =", np.nanmax(np.abs(A.diff_r)), " rho =", np.nanmax(np.abs(A.diff_rho)))
    print("S4 Moran's I ..."); S4 = s4_moran(P, w_of); L = lisa_2024(P, w_of)
    print("S5 NB models ..."); S5 = s5_nb(P, w_of, rur)
    S5c = s5_spatial_error(P, w_of)
    S5b = s5_rural_assoc(P, rur) if rur else pd.DataFrame({"note": ["No rurality data yet"]})
    print("S6 sensitivity ..."); S6 = s6_sensitivity(P); S6b = s6_influence(P)
    print("R2.8 LFP ..."); LFP = r28_lfp(P)
    print("S7 panel GEE ..."); G1 = s7_panel(P, R.FULL_YEARS, "2020-2024"); G2 = s7_panel(P, R.YEARS, "2019-2024")
    print("S7 border ..."); BW, BD, BT, NYR, BFE = s7_border(P)
    SUM = summary_consistency(A, S5, S6, S5c)

    out = os.path.join(R.TABLE_DIR, "Revision_Results.xlsx")
    with pd.ExcelWriter(out) as xw:
        pd.DataFrame(README_ROWS, columns=["sheet", "contents", "reviewer comment"]).to_excel(
            xw, sheet_name="README", index=False)
        for name, df in (("A_Reproduction", A), ("S4_MoransI", S4), ("S4b_LISA_2024", L),
                         ("S5_NB_IRR", S5), ("S5b_Rurality", S5b), ("S5c_SpatialError", S5c), ("S6_Sensitivity", S6),
                         ("S6b_Influence_2024", S6b), ("R2.8_LFP_Age", LFP),
                         ("S7a_Panel_2020_2024", G1), ("S7a_Panel_2019_2024", G2),
                         ("S7b_Border_Desc", BD), ("S7c_Border_Tests", BT), ("S7d_Border_FE", BFE),
                         ("S7e_NYborder_ranks", NYR), ("S7f_Border_County", BW),
                         ("SUM_Consistency", SUM)):
            df.to_excel(xw, sheet_name=name, index=False)
    print("Wrote", out)


# =============================================================================
# Self-test
# =============================================================================

def selftest():
    fails = []

    def check(name, cond):
        print(("PASS  " if cond else "FAIL  ") + name)
        if not cond:
            fails.append(name)

    rng = np.random.default_rng(1)
    z = rng.normal(size=500); x = z + rng.normal(size=500); y = z + rng.normal(size=500)
    r, p, n = partial_corr(x, y, z, "pearson")
    check("partial correlation removes shared covariate", abs(r) < 0.15 and n == 500)
    # Moran's I: a 6x6 lattice with a smooth gradient is strongly autocorrelated
    from libpysal.weights import lat2W
    w = lat2W(6, 6, rook=False); w.transform = "r"
    grad = np.add.outer(np.arange(6), np.arange(6)).ravel().astype(float)
    I, pI, _ = moran(grad, w)
    check("Moran's I detects a spatial gradient", I > 0.5 and pI < 0.01)
    # NB regression recovers a known rate ratio
    n = 4000; xs = rng.normal(size=n); pop = rng.integers(5_000, 50_000, n)
    mu = pop * 0.02 * np.exp(0.3 * xs)
    cnt = rng.negative_binomial(10, 10 / (10 + mu))
    res, a = nb_fit(cnt, sm.add_constant(pd.DataFrame({"z": xs})), np.log(pop))
    check("NB2 recovers log rate ratio 0.3", abs(res.params["z"] - 0.3) < 0.03)
    check("NB2 recovers alpha 0.1", abs(a - 0.1) < 0.03)
    print("\nALL PASS" if not fails else f"\n{len(fails)} FAILURE(S)")
    return not fails


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    run()
