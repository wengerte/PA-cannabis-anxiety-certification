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
    """Read one year file (all columns needed), dropping Excel padding rows."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            df = pd.read_csv(path, dtype=str, low_memory=False, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    df = df[[c for c in READ_COLS if c in df.columns]]
    df = df[~df.isna().all(axis=1)].copy()          # blank padding rows
    df["created"] = pd.to_datetime(df["Created Date"], errors="coerce", format="mixed")
    df["tp_months"] = pd.to_numeric(df["Treatment Period"], errors="coerce")
    for c in V1.SMC_COLS:
        if c in df.columns:
            df[c] = df[c].map(lambda s: V1.fingerprint(s) if isinstance(s, str) else "")
    smc = [c for c in V1.SMC_COLS if c in df.columns]
    df["is_ad"] = (df[smc] == TARGET).any(axis=1)
    df["n_conditions"] = (df[smc] != "").sum(axis=1)
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
    """Upper bound on certifications that could duplicate a person within a year.

    A person can contribute a second certification created in the same calendar
    year only if (a) an earlier same-year certification ended (created date plus
    treatment period) before December 31, so a renewal could fall in the same
    year, or (b) a certification was canceled, created in error, or invalid and
    then re-issued. Counting every such certification as a potential duplicate
    gives a conservative upper bound; the true number is lower.
    """
    end_of_year = pd.Timestamp(year=year, month=12, day=31, hour=23, minute=59)
    ends = pd.Series(pd.NaT, index=ad.index, dtype="datetime64[ns]")
    for months, idx in ad.groupby("tp_months").groups.items():
        if pd.isna(months):
            continue
        ends.loc[idx] = ad.loc[idx, "created"] + pd.DateOffset(months=int(months))
    could_renew = ends.notna() & (ends <= end_of_year)
    reissue = ad["Certification Status"].isin(["Canceled", "Created in Error", "Invalid"])
    return {
        "year": year,
        "ad_certifications": int(len(ad)),
        "ended_before_dec31_could_renew_same_year": int(could_renew.sum()),
        "canceled_error_invalid_could_be_reissued": int(reissue.sum()),
        "upper_bound_potential_duplicates": int((could_renew | reissue).sum()),
        "upper_bound_pct": 100 * float((could_renew | reissue).mean()),
        "treatment_period_12_months_pct": 100 * float((ad["tp_months"] == 12).mean()),
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

def build(data_dir, key):
    R.ensure_dirs()
    acc_state, acc_status, acc_tp, acc_month, dup_rows, unm_rows = [], [], [], [], [], []
    panel_parts = []

    for year in R.YEARS:
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
        u = unm.rename("n").reset_index()
        u.columns = ["zip_as_recorded_first5", "n"]
        u["year"] = year
        unm_rows.append(u)

        # --- ACS ----------------------------------------------------------------
        acs = acs_from_api(year, key) if key else acs_from_cache(year)
        acs["certs_v1"] = acs["county_fips"].map(v1).fillna(0).astype(int)
        acs["certs_corrected"] = acs["county_fips"].map(corr.round()).fillna(0).astype(int)
        acs["certs_valid_status"] = acs["county_fips"].map(corr_valid.round()).fillna(0).astype(int)
        panel_parts.append(acs)
        print(f"   AD certs {len(ad):,}  county sum v1 {int(v1.sum()):,}  "
              f"corrected {int(corr.round().sum()):,}  ACS: {acs['acs_source'].iloc[0]}")

    panel = pd.concat(panel_parts, ignore_index=True)
    for col, num in (("rate_v1", "certs_v1"), ("rate_corrected", "certs_corrected"),
                     ("rate_valid_status", "certs_valid_status")):
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
             "certs_valid_status", "rate_valid_status", "rate_hh_adults",
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
        "created": pd.to_datetime(["2024-01-15", "2024-08-01", "2024-03-01", "2024-02-01"]),
        "tp_months": [3, 3, 12, 6],
        "Certification Status": ["Expired", "Active", "Active", "Canceled"],
    })
    b = within_year_duplicate_bound(ad, 2024)
    check("duplicate bound: short periods ending in-year", b["ended_before_dec31_could_renew_same_year"] == 3)
    check("duplicate bound: union with re-issue statuses", b["upper_bound_potential_duplicates"] == 3)
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
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    k = R.read_api_key()
    if not k:
        print("No Census API key found: using cached ACS values; household-adult "
              "denominator and 2020 rural share will be missing.\n")
    build(a.data_dir, k)
