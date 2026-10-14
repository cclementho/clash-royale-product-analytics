"""All README figures, drawn from the CSVs the analysis scripts write to outputs/.

Run last:  ~/cr_data/.venv/bin/python src/figures.py

  figures/audit_collection.png          Phase 1: collection volume and trophy-chain continuity
  figures/requeue_by_streak.png         Phase 2: re-queue within 10 min by streak, with bounds
  figures/win_rate_by_level_gap.png     Phase 3: win rate vs card level gap, with the logit fit
  figures/level_gap_by_trophy_band.png  Phase 3: effect per level vs how often big gaps happen

Colors are the two validated categorical slots (blue, orange) on a light surface; text stays in
neutral ink so color only marks identity.
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import expit

from config import FIG_DIR, OUT_DIR

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def new_fig(w: float = 7.2, h: float = 4.6, ncols: int = 1, **kw):
    """Figure with the shared surface color."""
    fig, axes = plt.subplots(1, ncols, figsize=(w, h), dpi=200, **kw)
    fig.patch.set_facecolor(SURFACE)
    return fig, axes


def style(ax, grid_axis: str = "y") -> None:
    """Recessive grid and axes, no box."""
    ax.set_facecolor(SURFACE)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5, length=0)


def titles(fig, ax, title: str, subtitle: str) -> None:
    """Left-aligned title (the finding) and a smaller subtitle (the data)."""
    fig.suptitle(title, x=0.02, ha="left", fontsize=13, color=INK, fontweight="bold")
    ax.set_title(subtitle, loc="left", fontsize=8.5, color=INK2)


def audit_collection() -> None:
    """Two panels: battles per day (collection regime change) and chain continuity by gap length."""
    day = pd.read_csv(OUT_DIR / "phase1_battles_per_day.csv", parse_dates=["day_utc"])
    chain = pd.read_csv(OUT_DIR / "phase1_chain_by_gap.csv")
    chain = chain[~chain["gap_to_next"].str.startswith("a:")]  # same-second artifacts
    chain["label"] = chain["gap_to_next"].str.split(": ").str[1].str.replace("[", "").str.replace(")", "") \
        .str.replace("(", "").str.replace(",", " to ")
    fig, (a1, a2) = new_fig(9.6, 4.4, ncols=2, gridspec_kw={"width_ratios": [1, 1.25]})
    style(a1)
    style(a2)
    a1.bar(day["day_utc"], day["battles"] / 1e6, color=BLUE, width=0.8)
    a1.set_ylabel("Battles per day (millions)", color=INK2, fontsize=9)
    a1.set_title("Collection grew about 20x on Dec 18", loc="left", fontsize=10, color=INK)
    a1.tick_params(axis="x", rotation=45)
    a1.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%b %d"))
    x = np.arange(len(chain))
    a2.bar(x, chain["pct_chain_intact"], color=BLUE, width=0.7)
    for xi, v in zip(x, chain["pct_chain_intact"]):
        a2.text(xi, v + 1.5, f"{v:.0f}%", ha="center", fontsize=8, color=INK)
    a2.set_xticks(x, ["0-3 min", "3-5 min", "5-10 min", "10-30 min", "30-60 min", "1-3 h", "3-24 h", "24 h+"])
    a2.tick_params(axis="x", rotation=45)
    a2.set_ylim(0, 110)
    a2.set_ylabel("Next battle's trophies match (%)", color=INK2, fontsize=9)
    a2.set_title("Long gaps are mostly missing battles", loc="left", fontsize=10, color=INK)
    fig.suptitle("The data comes from 25-battle logs, so long gaps are not real breaks", x=0.02, ha="left",
                 fontsize=13, color=INK, fontweight="bold")
    fig.text(0.02, 0.005, "Right: if no battle is missing, a player's next starting trophies equal this battle's "
             "starting trophies plus its trophy change. 10% player sample, Dec 19 to Jan 3.",
             fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG_DIR / "audit_collection.png", facecolor=SURFACE)


def requeue_by_streak() -> None:
    """Re-queue within 10 min after loss vs win streaks; bars span lower to upper bound."""
    df = pd.read_csv(OUT_DIR / "phase2_streak_rates.csv")
    df["result"], df["k"] = df["cell"].str[0], df["cell"].str[1].astype(int)
    fig, ax = new_fig()
    style(ax)
    for result, color, offset, name in (("L", ORANGE, -0.06, "After a loss"), ("W", BLUE, 0.06, "After a win")):
        d = df[df["result"] == result].sort_values("k")
        x, mid = d["k"] + offset, (d["rate10_lo"] + d["rate10_hi"]) / 2
        ax.plot(x, mid, color=color, linewidth=2, zorder=2)
        ax.vlines(x, d["rate10_lo"], d["rate10_hi"], color=color, linewidth=9, zorder=3, capstyle="round")
        ax.text(3 + offset + 0.12, mid.iloc[-1], name, color=INK, va="center", fontsize=10)
    ax.set_xticks([1, 2, 3], ["1", "2", "3+"])
    ax.set_xlim(0.7, 3.75)
    ax.set_ylim(55, 80)
    ax.set_xlabel("Length of the current streak (same result in a row)", color=INK2, fontsize=9.5)
    ax.set_ylabel("Starts another ladder battle within 10 min (%)", color=INK2, fontsize=9.5)
    titles(fig, ax, "Re-queueing falls with win streaks and stays flat with loss streaks",
           "24,791 heavy ladder players, Dec 19 to Jan 3. Bars span the lower and upper bound\n"
           "from battles whose next battle was not collected. Rates averaged over players.")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "requeue_by_streak.png", facecolor=SURFACE)


def win_rate_by_level_gap() -> None:
    """Observed win rate by level gap (all battles, both sides) with the full-data logit curve."""
    b = pd.read_csv(OUT_DIR / "phase3_binned.csv")
    m = pd.read_csv(OUT_DIR / "phase3_main_model.csv").set_index("term")["coef"]
    const, gap = m["const"], m["level_gap"]
    x = np.linspace(-3, 3, 200)
    fig, ax = new_fig()
    style(ax, "both")
    ax.plot(x, 100 * expit(const + gap * x), color=INK2, linewidth=2, zorder=2)
    ax.scatter(b["gap_bin"], 100 * b["win_rate"], s=40, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    ax.annotate("Observed (all 37.8M battles,\nboth sides, 0.25-level bins)", xy=(-1.5, 28.9), xytext=(-2.95, 45),
                fontsize=9, color=INK, arrowprops=dict(arrowstyle="-", color=INK2, linewidth=0.8))
    ax.annotate("Logistic fit, all battles\n(trophy gap = 0)", xy=(1.5, 100 * expit(const + gap * 1.5)),
                xytext=(1.75, 58), fontsize=9, color=INK, arrowprops=dict(arrowstyle="-", color=INK2, linewidth=0.8))
    for g in (-1, 1):
        p = 100 * expit(const + gap * g)
        ax.text(g - 0.12, p + 1.5, f"{p:.0f}%", ha="right", fontsize=9, color=INK)
    ax.set_xlim(-3.2, 3.2)
    ax.set_ylim(15, 85)
    ax.set_xlabel("Average card level gap (your deck minus opponent's)", color=INK2, fontsize=9.5)
    ax.set_ylabel("Win rate (%)", color=INK2, fontsize=9.5)
    titles(fig, ax, "A one-level card deficit drops win rate from 50% to about 36%",
           "Ladder battles Dec 7 2020 to Jan 4 2021. 0.97% of battles have a gap beyond 2.5 levels.")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "win_rate_by_level_gap.png", facecolor=SURFACE)


def level_gap_by_trophy_band() -> None:
    """Two panels sharing the band axis: odds ratio per level (with CI) and share of 1+ level gaps."""
    d = pd.read_csv(OUT_DIR / "phase3_bands.csv")
    d["label"] = d["band"].str.split(": ").str[1].str.replace("-", " to ")
    y = np.arange(len(d))[::-1]
    fig, (a1, a2) = new_fig(9.2, 4.2, ncols=2, sharey=True)
    for ax in (a1, a2):
        style(ax, "x")
    a1.hlines(y, d["or_lo"], d["or_hi"], color=BLUE, linewidth=2.5)
    a1.scatter(d["odds_ratio"], y, s=55, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    for yi, v in zip(y, d["odds_ratio"]):
        a1.text(v, yi + 0.25, f"{v:.2f}", ha="center", fontsize=8, color=INK)
    a1.set_xlim(1.4, 2.6)
    a1.set_yticks(y, d["label"])
    a1.set_ylabel("Average trophies of the two players", color=INK2, fontsize=9)
    a1.set_title("Odds of winning per +1 level (95% CI)", loc="left", fontsize=10, color=INK)
    a2.barh(y, d["pct_gap_1plus"], color=BLUE, height=0.6)
    for yi, v in zip(y, d["pct_gap_1plus"]):
        a2.text(v + 0.8, yi, f"{v:.1f}%" if v >= 1 else f"{v:.2f}%", va="center", fontsize=8, color=INK)
    a2.set_xlim(0, 40)
    a2.set_title("Battles with a gap of 1+ level", loc="left", fontsize=10, color=INK)
    fig.suptitle("Card levels matter at every trophy range, but big gaps only happen below 5,000", x=0.02,
                 ha="left", fontsize=13, color=INK, fontweight="bold")
    fig.text(0.02, 0.01, "Separate logistic regression per band on all 37.8M battles, SEs clustered by player. "
             "Bands differ (Wald test p < 1e-10).", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(FIG_DIR / "level_gap_by_trophy_band.png", facecolor=SURFACE)


if __name__ == "__main__":
    FIG_DIR.mkdir(exist_ok=True)
    for f in (audit_collection, requeue_by_streak, win_rate_by_level_gap, level_gap_by_trophy_band):
        f()
        print(f"wrote {f.__name__}")
