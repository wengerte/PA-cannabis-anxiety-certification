"""
rev_common.py - shared configuration for the Frontiers revision analyses.

Bhuiyan et al., "Sociodemographic Correlates of Medical Cannabis Certification
for Anxiety Disorders Across Pennsylvania Counties, 2019-2024"
(Frontiers in Psychiatry, manuscript 2035644), first revision, November 2026.

The submitted analysis lives in build_anxiety_dataset_v1.py, an unchanged copy
of the script published with the original submission. The revision scripts
import its measure definitions and helper functions so that every new analysis
uses exactly the same 17 sociodemographic measures and the same outcome.
"""

import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REV_DIR = os.path.dirname(HERE)                 # Revision_2026_11_Frontiers/
PROJECT_DIR = os.path.dirname(REV_DIR)          # folder holding the raw data
INPUTS_DIR = os.path.join(REV_DIR, "inputs_public")
OUT_DIR = os.path.join(REV_DIR, "outputs")
TABLE_DIR = os.path.join(OUT_DIR, "tables")
FIG_DIR = os.path.join(OUT_DIR, "figures")

sys.path.insert(0, HERE)
import build_anxiety_dataset_v1 as V1  # noqa: E402  (frozen copy of submitted code)

CONDITION = V1.CONDITION
YEARS = list(V1.STUDY_YEARS)            # 2019-2024
PRIMARY_YEAR = V1.PRIMARY_YEAR          # 2024
FULL_YEARS = [y for y in YEARS if y >= 2020]   # 2019 is a partial year (from July 20)
RATE_PER = V1.RATE_PER
SEED = 20261117                         # fixed seed for all permutation tests

# (domain, label) for the 17 measures, in Table 1 order
MEASURE_LIST = [(d, m) for d, ms in V1.MEASURES.items() for m in ms]
MEASURES = [m for _, m in MEASURE_LIST]
DOMAIN_OF = {m: d for d, m in MEASURE_LIST}

# Certification statuses treated as valid issued certifications in the
# status-restricted sensitivity analysis. Excluded: Created in Error, Invalid,
# Canceled, Pending Practitioner Approval.
VALID_STATUSES = {"Active", "Expired", "Expired - Aged", "Pending Renewal", "Inactive"}

# Adult-use retail sales start dates in neighboring states [verify each against
# the state regulator before citing]. West Virginia has no adult-use sales.
ADULT_USE_SALES = {
    "NJ": date(2022, 4, 21),
    "NY": date(2022, 12, 29),
    "MD": date(2023, 7, 1),
    "OH": date(2024, 8, 6),
    "DE": date(2025, 8, 1),
    "WV": None,
}
STATE_FIPS = {"34": "NJ", "36": "NY", "24": "MD", "39": "OH", "10": "DE", "54": "WV"}

PHILADELPHIA = "42101"
FOREST = "42053"


def short_name(label):
    s = re.sub(r"\([^)]*\)", "", label)
    s = re.sub(r"[^A-Za-z0-9 ]", "", s)
    return "_".join(p for p in s.split() if p)


def read_api_key():
    """Census API key from CENSUS_API_KEY or Revision_.../census_api_key.txt."""
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key.strip()
    path = os.path.join(REV_DIR, "census_api_key.txt")
    if os.path.exists(path):
        with open(path) as fh:
            txt = fh.read().strip()
            return txt or None
    return None


def ensure_dirs():
    for d in (INPUTS_DIR, OUT_DIR, TABLE_DIR, FIG_DIR):
        os.makedirs(d, exist_ok=True)
