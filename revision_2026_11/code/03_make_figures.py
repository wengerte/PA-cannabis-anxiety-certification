"""
03_make_figures.py - figures for the revision (PNG + SVG, plus Prism-ready CSVs).

  Fig_R1_forest_2024        IRR per 1 SD for each measure, 2024: negative binomial
                            unadjusted, rurality-adjusted (when available), and the
                            spatial-error model (R1.2, R1.3) - candidate Figure 6
  Fig_R1_LISA_2024          Local Moran's I clusters of the 2024 rate (R1.2)
  Fig_R1_border_change      (A) border groups; (B) % change in rate 2022-2024 (R1.7, R2.9)

Run after 02_run_analyses.py:   python 03_make_figures.py
"""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
import numpy as np                                    # noqa: E402
import pandas as pd                                   # noqa: E402
from matplotlib.patches import Patch                  # noqa: E402
from shapely.geometry import shape                    # noqa: E402

import rev_common as R                                # noqa: E402

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "svg.fonttype": "none"})


def save(fig, name):
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(R.FIG_DIR, f"{name}.{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def pa_shapes():
    g = json.load(open(os.path.join(R.INPUTS_DIR, "us_counties_geojson.json")))
    return {f["id"]: shape(f["geometry"]) for f in g["features"] if f["id"].startswith("42")}


def draw(ax, shapes, colors, edge="white"):
    for fips, geom in shapes.items():
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in polys:
            x, y = p.exterior.xy
            ax.fill(x, y, color=colors.get(fips, "#dddddd"), ec=edge, lw=0.4)
    ax.set_aspect(1.3)
    ax.axis("off")


def forest(xl):
    S5 = pd.read_excel(xl, "S5_NB_IRR")
    SE = pd.read_excel(xl, "S5c_SpatialError")
    y = R.PRIMARY_YEAR
    series = [("Negative binomial, unadjusted", S5[(S5.year == y) & (S5.model == "Unadjusted")]
               .set_index("measure")[["IRR_per_SD", "CI_low", "CI_high"]], "#1f4e79", "o")]
    adj = S5[(S5.year == y) & S5.model.str.startswith("Adjusted")]
    for k, (model, sub) in enumerate(adj.groupby("model")):
        series.append((model.replace("Adjusted: ", "Negative binomial, adjusted for "),
                       sub.set_index("measure")[["IRR_per_SD", "CI_low", "CI_high"]],
                       ["#c55a11", "#7f6000"][k % 2], "s"))
    series.append(("Spatial-error model (log rate)",
                   SE[SE.year == y].set_index("measure")[["SEM_rate_ratio_per_SD", "SEM_CI_low", "SEM_CI_high"]]
                   .set_axis(["IRR_per_SD", "CI_low", "CI_high"], axis=1), "#548235", "^"))

    labels = R.MEASURES[::-1]
    fig, ax = plt.subplots(figsize=(6.5, 7))
    off = np.linspace(-0.25, 0.25, len(series))
    rows = []
    for (name, df, col, mk), o in zip(series, off):
        for i, m in enumerate(labels):
            r = df.loc[m]
            ax.errorbar(r.IRR_per_SD, i + o, xerr=[[r.IRR_per_SD - r.CI_low], [r.CI_high - r.IRR_per_SD]],
                        fmt=mk, color=col, ms=4, lw=1, capsize=0, label=name if i == 0 else None)
            rows.append(dict(model=name, measure=m, ratio_per_SD=r.IRR_per_SD,
                             CI_low=r.CI_low, CI_high=r.CI_high))
    ax.axvline(1, color="grey", lw=0.8, ls="--")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xscale("log")
    ax.set_xticks([0.8, 0.9, 1.0, 1.1, 1.2, 1.3])
    ax.set_xticklabels(["0.8", "0.9", "1.0", "1.1", "1.2", "1.3"])
    ax.set_xlabel(f"Rate ratio per 1 SD of measure ({y}; 95% CI)")
    for k in range(1, len(labels)):
        if R.DOMAIN_OF[labels[k]] != R.DOMAIN_OF[labels[k - 1]]:
            ax.axhline(k - 0.5, color="#cccccc", lw=0.6)
    ax.legend(loc="lower center", bbox_to_anchor=(0.4, -0.2), ncol=2, frameon=False, fontsize=8)
    save(fig, "Fig_R1_forest_2024")
    pd.DataFrame(rows).to_csv(os.path.join(R.FIG_DIR, "Fig_R1_forest_2024_PRISM.csv"), index=False)


def lisa(xl, shapes):
    L = pd.read_excel(xl, "S4b_LISA_2024", dtype={"county_fips": str})
    pal = {"High-High": "#b2182b", "Low-Low": "#2166ac", "High-Low": "#f4a582",
           "Low-High": "#92c5de", "Not significant": "#eeeeee"}
    colors = dict(zip(L.county_fips, L.LISA_cluster.map(pal)))
    fig, ax = plt.subplots(figsize=(6, 3.6))
    draw(ax, shapes, colors, edge="#999999")
    present = [k for k in pal if k in set(L.LISA_cluster)]
    ax.legend(handles=[Patch(color=pal[k], label=k) for k in present], loc="lower left",
              frameon=False, fontsize=8)
    ax.set_title(f"Local Moran's I clusters, AD certification rate {R.PRIMARY_YEAR} (P<0.05, 999 permutations)",
                 fontsize=9)
    save(fig, "Fig_R1_LISA_2024")


def border(xl, shapes):
    W = pd.read_excel(xl, "S7f_Border_County", dtype={"county_fips": str})
    gpal = {"Borders NJ/NY/MD (sales by mid-2023)": "#c55a11",
            "Borders OH/DE/WV only (no sales before Aug 2024)": "#f8cbad", "Interior": "#d9d9d9"}
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.6))
    draw(axs[0], shapes, dict(zip(W.county_fips, W.group.map(gpal))), edge="white")
    axs[0].legend(handles=[Patch(color=c, label=k) for k, c in gpal.items()], loc="upper center",
                  bbox_to_anchor=(0.5, 0.0), ncol=1, frameon=False, fontsize=7)
    axs[0].set_title("A. Border group", fontsize=9, loc="left")
    v = W.set_index("county_fips")["pct_change_2022_2024"]
    cmap = plt.get_cmap("RdYlBu")
    norm = matplotlib.colors.TwoSlopeNorm(vmin=min(-15, v.min()), vcenter=0, vmax=max(40, v.max()))
    draw(axs[1], shapes, {f: cmap(norm(x)) for f, x in v.items()}, edge="white")
    sm_ = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    fig.colorbar(sm_, ax=axs[1], fraction=0.03, pad=0.01, label="% change in rate, 2022-2024")
    axs[1].set_title("B. Change in AD certification rate, 2022-2024", fontsize=9, loc="left")
    save(fig, "Fig_R1_border_change")


if __name__ == "__main__":
    R.ensure_dirs()
    xl = pd.ExcelFile(os.path.join(R.TABLE_DIR, "Revision_Results.xlsx"))
    shp = pa_shapes()
    forest(xl)
    lisa(xl, shp)
    border(xl, shp)
