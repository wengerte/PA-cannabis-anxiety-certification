"""
01_build_panel.py - build the county-year analysis panel for the revision.

Addresses (data side): R1.1 (how certifications map to years; certifications
vs. persons; statewide vs. county totals), R1.2-R1.4 and R1.7 (inputs for the
new models), R2.9 (border geography).

Writes
  outputs/tables/county_year_panel.csv       67 counties x 6 years, all outcomes,
                                             denominators, measures, rurality,
                                             border attributes
  outputs/tables/pa_county_queen_neighbors.csv  queen-contiguity neighbor list
  outputs/tables/R1.1_certification_accounting.xlsx

Run
  python 01_build_panel.py                  # uses the Census API if a key is found
  python 01_build_panel.py --selftest       # checks the new allocation code

The certification record is restricted (PA DOH). Nothing in this script writes
individual-level records; every output is a county-, ZIP-, or state-level
aggregate.
"""

import argparse
import gc
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

import rev_common as R
V1 = R.V1

READ_COLS = ["Zipcode", "Certification Status", "Created Date", "Treatment Period"] + V1.SMC_COLS
TARGET = V1.fingerprint(R.CONDITION)
# US county boundaries (Census Bureau cartographic boundary file, GeoJSON copy
# distributed with plotly); used only for queen contiguity and state borders.
GEOJSON_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"


# =============================================================================
# Certification record
# =============================================================================

def read_cert_year(path):
    """Read one year file (needed columns only), dropping Excel padding rows.

    Memory-lean: the ten condition fields are reduced to an AD flag and then
    dropped, so a full year fits comfortably in < 1 GB of RAM."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            df = pd.read_csv(path, dtype=str, low_memory=False, encoding=enc,
                             usecols=lambda c: c.strip().lstrip("\ufeff") in READ_COLS)
            break
        except UnicodeDecodeError:
            continue
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    df = df[~df.isna().all(axis=1)]                      # blank padding rows
    smc = [c for c in V1.SMC_COLS if c in df.columns]
    is_ad = np.zeros(len(df), dtype=bool)
    for c in smc:
        is_ad |= (df[c].map(lambda s: V1.fingerprint(s) if isinstance(s, str) else "") == TARGET).to_numpy()
    df = df.drop(columns=smc).copy()
    df["is_ad"] = is_ad
    df["created"] = pd.to_datetime(df["Created Date"], errors="coerce", format="mixed")
    df["tp_months"] = pd.to_numeric(df["Treatment Period"], errors="coerce")
    df = df.drop(columns=["Created Date", "Treatment Period"])
    return df


def zip5(z):
    """First five digits of a ZIP (handles ZIP+4 '17055-5453' and '182440101')."""
    if not isinstance(z, str):
        return None
    m = re.match(r"^\s*(\d{5})", z.strip())
    if m:
        return m.group(1)
    digits = re.sub(r"\D", "", z)
    return digits.zfill(5) if 0 < len(digits) <= 5 else None


def allocate_corrected(zip_series, hud):
    """ZIP->county allocation with two fixes relative to the submitted code.

    1. ZIP codes are reduced to their first five digits (ZIP+4 entries matched).
    2. For ZIP codes whose HUD residential ratios are all zero (business/PO-box
       ZIPs), the total-address ratio is used; if that is also zero the
       certifications are split equally. The submitted code gave such ZIPs a
       weight of 1.0 in every county they touch, counting them more than once.
    Returns (county_counts_float Series, n_unmatched, unmatched_by_zip Series).
    """
    z = zip_series.map(zip5)
    counts = z.value_counts(dropna=False)
    zc = counts.rename("n").reset_index().rename(columns={"index": "zip5", zip_series.name: "zip5"})
    zc.columns = ["zip5", "n"]
    h = hud[["zip", "county", "res_ratio"] + (["tot_ratio"] if "tot_ratio" in hud.columns else [])].copy()
    m = zc.merge(h, left_on="zip5", right_on="zip", how="left")
    unmatched = m[m["county"].isna()]
    m = m.dropna(subset=["county"]).copy()
    rs = m.groupby("zip5")["res_ratio"].transform("sum")
    w = m["res_ratio"] / rs
    if "tot_ratio" in m.columns:
        ts = m.groupby("zip5")["tot_ratio"].transform("sum")
        w = w.where(rs > 0, m["tot_ratio"] / ts)
    k = m.groupby("zip5")["county"].transform("count")
    w = w.where(w.notna() & np.isfinite(w), 1.0 / k)
    m["w"] = m["n"] * w
    out = m.groupby("county")["w"].sum()
    unm = unmatched.groupby(unmatched["zip5"].fillna("<missing>"))["n"].sum()
    return out, int(unmatched["n"].sum()), unm


def within_year_duplicate_bound(ad, year):
    """Describe the ways one person can contribute >1 certification in a calendar year.

    Without a patient identifier duplicates cannot be counted. Three mechanisms:
      (a) a short treatment period (<12 months) that ended before December 31,
          allowing a renewal created in the same year;
      (b) a certification that was canceled, created in error, or invalid and
          then re-issued;
      (c) an early renewal: renewals are created before the prior certification
          expires (the 2022 record holds 4,665 'Pending Renewal' AD certifications
          created in Nov-Dec), so a person whose prior 12-month certification was
          created early in the year can receive a renewal late in the same year.
    (a) and (b) are counted directly. For (c) only a theoretical maximum can be
    given: the number of 12-month certifications created in the first 60 days of
    the year (each could pair with at most one early renewal created in the last
    60 days). The March-October sensitivity analysis removes every such pair.
    """
    end_of_year = pd.Timestamp(year=year, month=12, day=31, hour=23, minute=59)
    ends = pd.Series(pd.NaT, index=ad.index, dtype="datetime64[ns]")
    for months, idx in ad.groupby("tp_months").groups.items():
        if pd.isna(months):
            continue
        ends.loc[idx] = ad.loc[idx, "created"] + pd.DateOffset(months=int(months))
    a_ = ends.notna() & (ends <= end_of_year)
    b_ = ad["Certification Status"].isin(["Canceled", "Created in Error", "Invalid"])
    doy = ad["created"].dt.dayofyear
    ndays = 366 if pd.Timestamp(year=year, month=12, day=31).dayofyear == 366 else 365
    first60_12 = int(((doy <= 60) & (ad["tp_months"] == 12)).sum())
    last60 = int((doy > ndays - 60).sum())
    c_max = min(first60_12, last60)
    return {
        "year": year,
        "ad_certifications": int(len(ad)),
        "a_short_period_ended_in_year": int(a_.sum()),
        "b_canceled_error_invalid": int(b_.sum()),
        "a_or_b_n": int((a_ | b_).sum()),
        "a_or_b_pct": 100 * float((a_ | b_).mean()),
        "c_12mo_created_first_60_days": first60_12,
        "c_created_last_60_days": last60,
        "c_theoretical_max_early_renewal_pairs": c_max,
        "c_theoretical_max_pct": 100 * c_max / len(ad),
        "pending_renewal_created_nov_dec": int((ad["Certification Status"].eq("Pending Renewal")
                                                & ad["created"].dt.month.isin([11, 12])).sum()),
        "treatment_period_12_months_pct": 100 * float((ad["tp_months"] == 12).mean()),
        "created_mar_oct_n": int(ad["created"].dt.month.between(3, 10).sum()),
    }


# =============================================================================
# ACS and other county covariates
# =============================================================================

EXTRA_ACS = {
    # Adults 18+ living in households (excludes group quarters: prisons,
    # dormitories, nursing facilities). Universe: population 18+ in households.
    "hh_adult_pop": "B09021_001E",
    # Total group-quarters population (all ages).
    "gq_pop": "B26001_001E",
}


def acs_from_api(year, key):
    var_ids = sorted(set(V1.required_acs_vars()) | set(EXTRA_ACS.values()))
    acs = V1.fetch_acs(year, key, var_ids)
    out = acs[["county_fips", "county_name", "adult_pop"]].copy()
    for name, var in EXTRA_ACS.items():
        out[name] = acs[var] if var in acs.columns else np.nan
    for _, label in R.MEASURE_LIST:
        out[label] = V1.compute_measure(acs, V1.MEASURES[R.DOMAIN_OF[label]][label]).values
    out["year"] = year
    out["acs_source"] = "Census API"
    return out


def acs_from_cache(year):
    """Fallback: ACS values cached in the workbook that produced Table 1."""
    path = os.path.join(R.PROJECT_DIR, "Manuscript_Figures", "Anxiety_Disorders",
                        "Anxiety_Disorders_AllMeasures_RawData.xlsx")
    if not hasattr(acs_from_cache, "_long"):
        acs_from_cache._long = pd.read_excel(path, sheet_name="Combined_Long",
                                             dtype={"county_fips": str})
    L = acs_from_cache._long
    L = L[(L["year"] == year) & (L["measure"].isin(R.MEASURES))]
    wide = L.pivot_table(index=["county_fips", "county_name"], columns="measure",
                         values="ses_value").reset_index()
    pop = L.groupby("county_fips")["adult_pop"].first()
    wide["adult_pop"] = wide["county_fips"].map(pop)
    for name in EXTRA_ACS:
        wide[name] = np.nan
    wide["year"] = year
    wide["acs_source"] = "cached workbook (Anxiety_Disorders_AllMeasures_RawData.xlsx)"
    return wide


def decennial_rural(key):
    """2020 Census (DHC table P2): percent of county population in rural areas."""
    import requests
    url = ("https://api.census.gov/data/2020/dec/dhc?get=NAME,P2_001N,P2_002N,P2_003N"
           f"&for=county:*&in=state:42&key={key}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    js = r.json()
    df = pd.DataFrame(js[1:], columns=js[0])
    df["county_fips"] = df["state"] + df["county"]
    for c in ("P2_001N", "P2_002N", "P2_003N"):
        df[c] = pd.to_numeric(df[c])
    df["pct_rural_2020"] = 100 * df["P2_003N"] / df["P2_001N"]
    return df[["county_fips", "pct_rural_2020"]]


def find_rucc():
    """USDA ERS 2023 RUCC file, if the user has placed one in the project or revision folder.

    Matched case-insensitively on file names containing 'ruc' or 'rural' + 'urban'
    (e.g., Ruralurbancontinuumcodes2023.csv as downloaded from ERS)."""
    for base in (R.REV_DIR, R.INPUTS_DIR, R.PROJECT_DIR):
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            low = fn.lower()
            if low.endswith((".csv", ".xlsx")) and not low.startswith("~$") and (
                    "rucc" in low or ("rural" in low and "urban" in low)):
                return os.path.join(base, fn)
    return None


def read_rucc(path):
    df = pd.read_excel(path, dtype=str) if path.endswith("xlsx") else \
        pd.read_csv(path, dtype=str, encoding="latin-1")
    cols = {c.lower(): c for c in df.columns}
    fips_col = next(cols[c] for c in cols if c in ("fips", "fips code", "fips_code"))
    df["county_fips"] = df[fips_col].str.zfill(5)
    if "attribute" in cols and "value" in cols:                 # ERS long format
        a, v = cols["attribute"], cols["value"]
        sub = df[df[a].str.upper().str.startswith("RUCC")]
        out = sub[["county_fips", v]].rename(columns={v: "rucc_2023"})
    else:                                                       # wide format
        rc = next(cols[c] for c in cols if c.startswith("rucc"))
        out = df[["county_fips", rc]].rename(columns={rc: "rucc_2023"})
    out["rucc_2023"] = pd.to_numeric(out["rucc_2023"], errors="coerce")
    out = out[out["county_fips"].str.startswith("42")].drop_duplicates("county_fips")
    out["nonmetro"] = (out["rucc_2023"] >= 4).astype(int)
    return out


def geography():
    """Queen contiguity among PA counties and borders with neighboring states."""
    from shapely.geometry import shape
    path = os.path.join(R.INPUTS_DIR, "us_counties_geojson.json")
    if not os.path.exists(path):          # public Census county boundaries (plotly mirror)
        import requests
        r = requests.get(GEOJSON_URL, timeout=120)
        r.raise_for_status()
        with open(path, "wb") as fh:
            fh.write(r.content)
    g = json.load(open(path))
    keep = {"42"} | set(R.STATE_FIPS)
    feats = [f for f in g["features"] if f["id"][:2] in keep]
    geo = {f["id"]: shape(f["geometry"]).buffer(0) for f in feats}
    pa = sorted(k for k in geo if k.startswith("42"))
    nbrs, border = {}, {}
    for a in pa:
        touching = [b for b, gb in geo.items() if b != a and geo[a].distance(gb) <= 1e-4]
        nbrs[a] = sorted(b for b in touching if b.startswith("42"))
        border[a] = sorted({R.STATE_FIPS[b[:2]] for b in touching if not b.startswith("42")})
    return nbrs, border


def exposure(states, year, rule):
    """1 if a bordering state had adult-use retail sales in `year`.

    rule='any'  - sales began on or before December 31 of that year
    rule='half' - sales began on or before July 1 of that year (>= ~6 months)
    """
    from datetime import date
    cutoff = date(year, 12, 31) if rule == "any" else date(year, 7, 1)
    return int(any(R.ADULT_USE_SALES.get(s) and R.ADULT_USE_SALES[s] <= cutoff for s in states))


# =============================================================================
# Main build
# =============================================================================

def build(data_dir, key, years=None, refresh=False):
    years = years or R.YEARS
    R.ensure_dirs()
    acc_state, acc_status, acc_tp, acc_month, dup_rows, unm_rows = [], [], [], [], [], []
    panel_parts = []

    cache_dir = os.path.join(R.TABLE_DIR, "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    for year in years:
        cpath = os.path.join(cache_dir, f"year_{year}.pkl")
        if os.path.exists(cpath) and not refresh:
            c = pd.read_pickle(cpath)
            acc_state.append(c["state"]); acc_status.append(c["status"]); acc_tp.append(c["tp"])
            acc_month.append(c["month"]); dup_rows.append(c["dup"]); unm_rows.append(c["unm"])
            panel_parts.append(c["acs"])
            print(f"{year}: loaded from cache", flush=True)
            continue
        n0 = len(panel_parts)
        print(f"{year}: reading certification record ...", flush=True)
        path = os.path.join(data_dir, f"certifications({year}).csv")
        df = read_cert_year(path)
        ad = df[df["is_ad"]].copy()
        hud = V1.load_hud(os.path.join(data_dir, f"PA_HUD_ZIP_COUNTY_{year}.csv"))
        if "tot_ratio" in hud.columns:
            hud["tot_ratio"] = pd.to_numeric(hud["tot_ratio"], errors="coerce").fillna(0)
        hud["res_ratio"] = pd.to_numeric(hud["res_ratio"], errors="coerce").fillna(0)

        # --- submitted allocation, reproduced exactly with the v1 functions ---
        v1_long = V1.load_certifications(path)
        v1 = V1.county_cert_counts(v1_long, hud, R.CONDITION).set_index("county_fips")["cert_count"]

        # --- March-October subset with the submitted allocation (removes any
        # early-renewal pair, which needs one certification in Jan-Feb and one in
        # Nov-Dec of the same year). Row indices identify the same certification
        # in both reads of the file; this is asserted.
        v1_ad_ids = set(v1_long.loc[v1_long["condition"].map(V1.fingerprint) == TARGET, "cert_id"])
        assert v1_ad_ids == set(df.index[df["is_ad"]]), "certification row alignment failed"
        maroct_ids = set(df.index[df["is_ad"] & df["created"].dt.month.between(3, 10)])
        v1_maroct = V1.county_cert_counts(v1_long[v1_long["cert_id"].isin(maroct_ids)], hud,
                                          R.CONDITION).set_index("county_fips")["cert_count"]

        # --- corrected allocation, all statuses and valid statuses only -------
        corr, n_unm, unm = allocate_corrected(ad["Zipcode"].rename("Zipcode"), hud)
        valid = ad[ad["Certification Status"].isin(R.VALID_STATUSES)]
        corr_valid, _, _ = allocate_corrected(valid["Zipcode"].rename("Zipcode"), hud)

        # --- accounting (R1.1) ------------------------------------------------
        zc = ad["Zipcode"].str.strip().str.zfill(5).value_counts()
        hz = set(hud["zip"])
        v1_unmatched = int(zc[~zc.index.isin(hz)].sum())
        ratio_sum = hud.groupby("zip")["res_ratio"].sum()
        zero_multi = hud[hud["zip"].isin(ratio_sum[ratio_sum == 0].index)].groupby("zip").size()
        zero_multi = zero_multi[zero_multi > 1]
        dup_extra = int(sum(zc.get(z, 0) * (k - 1) for z, k in zero_multi.items()))
        acc_state.append({
            "year": year,
            "all_certifications_statewide": int(len(df)),
            "AD_certifications_statewide": int(len(ad)),
            "AD_share_of_all_pct": 100 * len(ad) / len(df),
            "AD_certs_unmatched_ZIP_dropped_v1": v1_unmatched,
            "AD_certs_double_counted_zero_ratio_ZIPs_v1": dup_extra,
            "rounding_net_v1": int(v1.sum()) - (len(ad) - v1_unmatched + dup_extra),
            "county_sum_submitted_v1": int(v1.sum()),
            "AD_certs_unmatched_ZIP_corrected": n_unm,
            "county_sum_corrected_unrounded": float(corr.sum()),
            "county_sum_corrected_rounded": int(corr.round().sum()),
            "AD_certs_valid_status_statewide": int(len(valid)),
            "created_date_min": ad["created"].min(),
            "created_date_max": ad["created"].max(),
            "created_dates_outside_year": int((ad["created"].dt.year != year).sum()),
            "AD_created_before_2019_07_20": int((ad["created"] < pd.Timestamp(2019, 7, 20)).sum()),
        })
        st = ad["Certification Status"].value_counts().rename("n").reset_index()
        st.columns = ["status", "n"]
        st["year"] = year
        acc_status.append(st)
        tp = ad["tp_months"].value_counts().rename("n").reset_index()
        tp.columns = ["treatment_period_months", "n"]
        tp["year"] = year
        acc_tp.append(tp)
        mo = ad["created"].dt.month.value_counts().sort_index().rename("n").reset_index()
        mo.columns = ["month", "n"]
        mo["year"] = year
        acc_month.append(mo)
        dup_rows.append(within_year_duplicate_bound(ad, year))
        n_all = len(df)
        u = unm.rename("n").reset_index()
        u.columns = ["zip_as_recorded_first5", "n"]
        u["year"] = year
        unm_rows.append(u)

        # --- ACS ----------------------------------------------------------------
        acs = acs_from_api(year, key) if key else acs_from_cache(year)
        acs["certs_v1"] = acs["county_fips"].map(v1).fillna(0).astype(int)
        acs["certs_corrected"] = acs["county_fips"].map(corr.round()).fillna(0).astype(int)
        acs["certs_valid_status"] = acs["county_fips"].map(corr_valid.round()).fillna(0).astype(int)
        acs["certs_mar_oct"] = acs["county_fips"].map(v1_maroct).fillna(0).astype(int)
        panel_parts.append(acs)
        print(f"   AD certs {len(ad):,}  county sum v1 {int(v1.sum()):,}  "
              f"corrected {int(corr.round().sum()):,}  ACS: {acs['acs_source'].iloc[0]}", flush=True)
        pd.to_pickle({"state": acc_state[-1], "status": acc_status[-1], "tp": acc_tp[-1],
                      "month": acc_month[-1], "dup": dup_rows[-1], "unm": unm_rows[-1],
                      "acs": panel_parts[-1]}, cpath)
        del df, ad, valid, v1_long
        gc.collect()

    if sorted(years) != sorted(R.YEARS):
        print("Cached years:", sorted(int(f[5:9]) for f in os.listdir(cache_dir) if f.startswith("year_")),
              "- run again without --years (or with the remaining years) to assemble the panel.")
        return None

    panel = pd.concat(panel_parts, ignore_index=True)
    for col, num in (("rate_v1", "certs_v1"), ("rate_corrected", "certs_corrected"),
                     ("rate_valid_status", "certs_valid_status"), ("rate_mar_oct", "certs_mar_oct")):
        panel[col] = panel[num] / panel["adult_pop"] * R.RATE_PER
    panel["rate_hh_adults"] = panel["certs_v1"] / panel["hh_adult_pop"] * R.RATE_PER

    # --- rurality ---------------------------------------------------------------
    if key:
        try:
            panel = panel.merge(decennial_rural(key), on="county_fips", how="left")
        except Exception as e:                                   # noqa: BLE001
            print("   2020 Census rural share unavailable:", e)
            panel["pct_rural_2020"] = np.nan
    else:
        panel["pct_rural_2020"] = np.nan
    rp = find_rucc()
    if rp:
        print("   RUCC file:", os.path.basename(rp))
        panel = panel.merge(read_rucc(rp), on="county_fips", how="left")
    else:
        panel["rucc_2023"] = np.nan
        panel["nonmetro"] = np.nan

    # --- geography --------------------------------------------------------------
    nbrs, border = geography()
    panel["border_states"] = panel["county_fips"].map(lambda f: ",".join(border.get(f, [])))
    for s in R.STATE_FIPS.values():
        panel[f"borders_{s}"] = panel["county_fips"].map(lambda f, s=s: int(s in border.get(f, [])))
    panel["border_any_state"] = (panel["border_states"] != "").astype(int)
    panel["exposed_any"] = [exposure(border.get(f, []), y, "any") for f, y in
                            zip(panel["county_fips"], panel["year"])]
    panel["exposed_half"] = [exposure(border.get(f, []), y, "half") for f, y in
                             zip(panel["county_fips"], panel["year"])]

    panel = panel.sort_values(["year", "county_fips"]).reset_index(drop=True)
    first = ["year", "county_fips", "county_name", "adult_pop", "hh_adult_pop", "gq_pop",
             "certs_v1", "rate_v1", "certs_corrected", "rate_corrected",
             "certs_valid_status", "rate_valid_status", "certs_mar_oct", "rate_mar_oct", "rate_hh_adults",
             "pct_rural_2020", "rucc_2023", "nonmetro", "border_states", "border_any_state",
             "exposed_any", "exposed_half"]
    panel = panel[first + [c for c in panel.columns if c not in first]]
    panel.to_csv(os.path.join(R.TABLE_DIR, "county_year_panel.csv"), index=False)

    pd.DataFrame([(a, b) for a, bs in nbrs.items() for b in bs],
                 columns=["county_fips", "neighbor_fips"]).to_csv(
        os.path.join(R.TABLE_DIR, "pa_county_queen_neighbors.csv"), index=False)

    with pd.ExcelWriter(os.path.join(R.TABLE_DIR, "R1.1_certification_accounting.xlsx")) as xw:
        pd.DataFrame(acc_state).to_excel(xw, sheet_name="Statewide_vs_County", index=False)
        pd.DataFrame(dup_rows).to_excel(xw, sheet_name="WithinYear_Duplicate_Bound", index=False)
        pd.concat(acc_status).pivot_table(index="status", columns="year", values="n",
                                          fill_value=0).to_excel(xw, sheet_name="Status_by_Year")
        pd.concat(acc_tp).pivot_table(index="treatment_period_months", columns="year",
                                      values="n", fill_value=0).to_excel(xw, sheet_name="TreatmentPeriod_by_Year")
        pd.concat(acc_month).pivot_table(index="month", columns="year", values="n",
                                         fill_value=0).to_excel(xw, sheet_name="Created_Month_by_Year")
        pd.concat(unm_rows).to_excel(xw, sheet_name="Unmatched_ZIPs", index=False)
    print("Wrote county_year_panel.csv, pa_county_queen_neighbors.csv, "
          "R1.1_certification_accounting.xlsx")
    return panel


# =============================================================================
# Self-test for the new code (no restricted data needed)
# =============================================================================

def selftest():
    fails = []

    def check(name, cond):
        print(("PASS  " if cond else "FAIL  ") + name)
        if not cond:
            fails.append(name)

    check("zip5 handles ZIP+4 with hyphen", zip5("17055-5453") == "17055")
    check("zip5 handles 9-digit ZIP", zip5("182440101") == "18244")
    check("zip5 pads short numeric", zip5("1234") == "01234")
    hud = pd.DataFrame({
        "zip": ["11111", "22222", "22222", "33333", "33333", "44444", "44444"],
        "county": ["A", "A", "B", "A", "B", "A", "B"],
        "res_ratio": [1.0, 0.25, 0.75, 0.0, 0.0, 0.0, 0.0],
        "tot_ratio": [1.0, 0.5, 0.5, 0.2, 0.8, 0.0, 0.0],
    })
    z = pd.Series(["11111"] * 4 + ["22222"] * 8 + ["33333"] * 10 + ["44444"] * 2 + ["99999"],
                  name="Zipcode")
    out, nu, _ = allocate_corrected(z, hud)
    check("corrected allocation conserves matched total", abs(out.sum() - 24) < 1e-9)
    check("unmatched ZIP counted, not allocated", nu == 1)
    check("residential ratio split", abs(out["B"] - (6 + 8 + 1)) < 1e-9)  # 0.75*8 + 0.8*10 + 0.5*2
    check("zero residential ratio falls back to total ratio", abs(out["A"] - (4 + 2 + 2 + 1)) < 1e-9)
    ad = pd.DataFrame({
        "created": pd.to_datetime(["2024-01-15", "2024-08-01", "2024-02-20", "2024-02-01"]),
        "tp_months": [3, 3, 12, 6],
        "Certification Status": ["Expired", "Active", "Active", "Canceled"],
    })
    b = within_year_duplicate_bound(ad, 2024)
    check("duplicates (a): short periods ending in-year", b["a_short_period_ended_in_year"] == 3)
    check("duplicates (a) or (b)", b["a_or_b_n"] == 3)
    check("duplicates (c): 12-month certs created in first 60 days", b["c_12mo_created_first_60_days"] == 1)
    check("exposure rule 'any' (NJ in 2022)", exposure(["NJ"], 2022, "any") == 1)
    check("exposure rule 'half' (NY not in 2022, yes in 2023)",
          exposure(["NY"], 2022, "half") == 0 and exposure(["NY"], 2023, "half") == 1)
    check("exposure: WV never exposed", exposure(["WV"], 2024, "any") == 0)
    print("\nALL PASS" if not fails else f"\n{len(fails)} FAILURE(S)")
    return not fails


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=R.PROJECT_DIR)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--years", default=None,
                    help="e.g. 2019-2021: process only these years and cache them "
                         "(outputs/tables/_cache); a later run without --years assembles the panel")
    ap.add_argument("--refresh", action="store_true", help="ignore cached years and recompute")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    k = R.read_api_key()
    if not k:
        print("No Census API key found: using cached ACS values; household-adult "
              "denominator and 2020 rural share will be missing.\n")
    yrs = None
    if a.years:
        lo, hi = (int(x) for x in a.years.split("-")) if "-" in a.years else (int(a.years),) * 2
        yrs = [y for y in R.YEARS if lo <= y <= hi]
    build(a.data_dir, k, yrs, a.refresh)
