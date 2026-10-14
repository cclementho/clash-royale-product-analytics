"""Phase 4: inputs for the experiment design (baselines, variances, power).

Run:  ~/cr_data/.venv/bin/python src/power.py
Output: outputs/phase4_power.txt

What comes from the data: level-gap shares (overall and by trophy band), win rates of
underleveled players, and the mean, SD and pre/post correlation of ladder battles per player
per day. What does not: retention. This dataset cannot measure it (Phase 1), so its baseline
is an assumption stated below and in the design doc.
"""
import io
from contextlib import redirect_stdout

import numpy as np
import pandas as pd
from scipy.stats import norm

import requeue
from config import OUT_DIR, connect

pd.set_option("display.width", 200)

ALPHA, POWER = 0.05, 0.80
Z = norm.ppf(1 - ALPHA / 2) + norm.ppf(POWER)  # 1.96 + 0.84
N_PER_ARM = [100_000, 500_000, 1_000_000, 2_000_000]
PRE = ("2020-12-19", "2020-12-26")   # 7 days used as the pre-period (enrollment + CUPED covariate)
POST = ("2020-12-26", "2021-01-02")  # 7 days used as the outcome period
RETENTION_BASELINES = [0.50, 0.70, 0.80]  # ASSUMED share of enrolled players active in the final test week
def level_coef() -> float:
    """Main-model logit coefficient per level (all battles), from outputs/phase3_main_model.csv."""
    m = pd.read_csv(OUT_DIR / "phase3_main_model.csv")
    return float(m.loc[m["term"] == "level_gap", "coef"].iloc[0])

GAP_BANDS_SQL = """
WITH g AS (
    SELECT abs(winner_total_card_level - loser_total_card_level) / 8.0 AS gap,
           (winner_starting_trophies + loser_starting_trophies) / 2.0 AS avg_trophies
    FROM battles)
SELECT CASE WHEN avg_trophies < 4000 THEN 'a: <4000' WHEN avg_trophies < 5000 THEN 'b: 4000-4999'
            WHEN avg_trophies < 6000 THEN 'c: 5000-5999' ELSE 'd: 6000+' END AS trophy_band,
       count(*) AS battles,
       round(100 * avg((gap = 0)::INT), 1) AS pct_gap_0,
       round(100 * avg((gap <= 0.5)::INT), 1) AS pct_gap_le_0_5,
       round(100 * avg((gap >= 1)::INT), 1) AS pct_gap_ge_1,
       round(100 * avg((gap >= 1.5)::INT), 1) AS pct_gap_ge_1_5,
       round(100 * avg((gap >= 2)::INT), 1) AS pct_gap_ge_2
FROM g GROUP BY ROLLUP (1) ORDER BY 1 NULLS LAST
"""

BELOW_5000_SQL = """
SELECT count(*) AS battles,
       round(100 * avg((abs(winner_total_card_level - loser_total_card_level) / 8.0 >= 1)::INT), 1) AS pct_gap_ge_1,
       round(100 * avg((abs(winner_total_card_level - loser_total_card_level) / 8.0 > 0.5)::INT), 1) AS pct_gap_gt_0_5
FROM battles WHERE (winner_starting_trophies + loser_starting_trophies) / 2.0 < 5000
"""

# Enrollment segments: each player's trophies at their first pre-window battle (pre-treatment) and
# their average card level over the pre window, compared with the median of players enrolled in the
# same 250-trophy bin. Only players enrolled below 5,000 trophies (where the cap binds).
SEGMENTS_SQL = """
WITH pb AS (
    SELECT winner_tag AS tag, battle_time AS t, winner_starting_trophies AS trophies,
           winner_total_card_level / 8.0 AS lvl FROM battles
    WHERE battle_time >= TIMESTAMPTZ '{pre0}' AND battle_time < TIMESTAMPTZ '{pre1}'
    UNION ALL
    SELECT loser_tag, battle_time, loser_starting_trophies, loser_total_card_level / 8.0 FROM battles
    WHERE battle_time >= TIMESTAMPTZ '{pre0}' AND battle_time < TIMESTAMPTZ '{pre1}'),
p AS (SELECT tag, arg_min(trophies, (t, trophies)) AS enroll_trophies, avg(lvl) AS avg_level FROM pb GROUP BY tag),
q AS (SELECT *, floor(enroll_trophies / 250) AS bin FROM p WHERE enroll_trophies < 5000),
m AS (SELECT bin, median(avg_level) AS med FROM q GROUP BY bin)
SELECT CASE WHEN avg_level - med <= -0.5 THEN 'a: underleveled (0.5+ below bin median)'
            WHEN avg_level - med >= 0.5 THEN 'c: over-leveled (0.5+ above bin median)'
            ELSE 'b: middle' END AS segment,
       count(*) AS players, round(100 * count(*) / sum(count(*)) OVER (), 1) AS pct_players
FROM q JOIN m USING (bin) GROUP BY 1 ORDER BY 1
"""

UNDERLEVELED_SQL = """
WITH s AS (
    SELECT (winner_total_card_level - loser_total_card_level) / 8.0 AS gap, 1 AS won FROM battles
    UNION ALL
    SELECT (loser_total_card_level - winner_total_card_level) / 8.0, 0 FROM battles)
SELECT CASE WHEN gap <= -1 THEN 'a: 1+ levels under' WHEN gap < -0.5 THEN 'b: 0.5-1 under'
            WHEN gap < 0 THEN 'c: 0-0.5 under' WHEN gap = 0 THEN 'd: even' ELSE 'e: over' END AS position,
       count(*) AS player_battles, round(100 * avg(won), 2) AS win_rate
FROM s GROUP BY 1 ORDER BY 1
"""


def daily_battles_sql(where_players: str = "TRUE") -> str:
    """Per-player ladder battles per day in the pre and post windows, for players active in the pre window.

    Players with no battles in the post window get 0, so the outcome includes players who stopped
    (or whose battles were not collected).
    """
    return f"""
    WITH pb AS (
        SELECT winner_tag AS tag, battle_time AS t FROM battles
        WHERE battle_time >= TIMESTAMPTZ '{PRE[0]}' AND battle_time < TIMESTAMPTZ '{POST[1]}'
        UNION ALL
        SELECT loser_tag, battle_time FROM battles
        WHERE battle_time >= TIMESTAMPTZ '{PRE[0]}' AND battle_time < TIMESTAMPTZ '{POST[1]}'),
    c AS (
        SELECT tag,
               count(*) FILTER (WHERE t < TIMESTAMPTZ '{PRE[1]}') / 7.0 AS pre_per_day,
               count(*) FILTER (WHERE t >= TIMESTAMPTZ '{POST[0]}') / 7.0 AS post_per_day
        FROM pb WHERE {where_players} GROUP BY tag)
    SELECT * FROM c WHERE pre_per_day > 0
    """


def summarize(d: pd.DataFrame, name: str) -> dict:
    """Mean, SD and pre/post correlation of battles per day, plus CUPED variance reduction."""
    rho = d["pre_per_day"].corr(d["post_per_day"])
    return {"population": name, "players": len(d), "mean_post": d["post_per_day"].mean(),
            "sd_post": d["post_per_day"].std(), "pct_zero_post": 100 * (d["post_per_day"] == 0).mean(),
            "rho_pre_post": rho, "cuped_var_reduction_pct": 100 * rho ** 2}


def mde_mean(sd: float, n: int, rho: float = 0.0) -> float:
    """Minimum detectable absolute difference in means, two arms of n each (optionally with CUPED)."""
    return Z * sd * np.sqrt(1 - rho ** 2) * np.sqrt(2 / n)


def mde_prop(p: float, n: int) -> float:
    """Minimum detectable absolute difference in a proportion with baseline p, two arms of n each."""
    return Z * np.sqrt(2 * p * (1 - p) / n)


def run() -> str:
    """Compute every Phase 4 input and return the report."""
    con = connect()
    buf = io.StringIO()
    with redirect_stdout(buf):
        print(f"PHASE 4 INPUTS  (alpha={ALPHA} two-sided, power={POWER}, z sum={Z:.3f})\n")

        print("1. Absolute card level gap by trophy band, all ladder battles (percent of battles)")
        print(con.sql(GAP_BANDS_SQL).df().fillna("all").to_string(index=False))
        print("   Today's matchmaking ignores card level, so these shares also estimate how many of today's")
        print("   trophy-matched pairings a cap would reject (e.g. a 1.0-level cap rejects the 'ge_1' share).\n")

        print("1b. Battles below 5,000 average trophies (where the cap binds): baseline for the manipulation check")
        print(con.sql(BELOW_5000_SQL).df().to_string(index=False), "\n")

        print("2. Predicted win probability of the favorite at a given gap (Phase 3 logit, trophy gap 0)")
        for g in (0.25, 0.5, 1.0, 1.5, 2.0):
            print(f"   gap {g:.2f}: favorite wins {100 / (1 + np.exp(-level_coef() * g)):.1f}%")
        print()

        print("3. Win rate by card level position, all battles from both sides")
        print(con.sql(UNDERLEVELED_SQL).df().to_string(index=False), "\n")

        print(f"4. Ladder battles per player per day. Pre window {PRE[0]} to {PRE[1]} (exclusive), "
              f"post window {POST[0]} to {POST[1]} (exclusive)")
        print("   Enrolled = any battle in the pre window. Observed counts undercount true battles (Phase 1).")
        all_players = con.sql(daily_battles_sql()).df()
        con.execute("CREATE MACRO dev_sampled(tag) AS TRUE")
        for sql in (requeue.CANDIDATES_SQL, requeue.PLAYER_BATTLES_SQL, requeue.SEQUENCE_SQL, requeue.COVERED_SQL):
            con.execute(sql)
        covered = con.sql(daily_battles_sql("tag IN (SELECT tag FROM covered)")).df()
        stats = pd.DataFrame([summarize(all_players, "all enrolled players"),
                              summarize(covered, "well-covered players (Phase 2)")])
        print(stats.round(3).to_string(index=False), "\n")

        print("5. MDE for battles per player per day (absolute and relative to the control mean)")
        rows = []
        for _, s in stats.iterrows():
            for n in N_PER_ARM:
                raw, cup = mde_mean(s["sd_post"], n), mde_mean(s["sd_post"], n, s["rho_pre_post"])
                rows.append({"population": s["population"], "n_per_arm": f"{n:,}",
                             "mde_abs": round(raw, 4), "mde_rel_pct": round(100 * raw / s["mean_post"], 2),
                             "mde_abs_cuped": round(cup, 4), "mde_rel_cuped_pct": round(100 * cup / s["mean_post"], 2)})
        print(pd.DataFrame(rows).to_string(index=False), "\n")

        print("6. MDE for retention (share of enrolled players active in the final test week), in percentage points")
        print("   ASSUMPTION: baseline retention is not measurable in this dataset; values below are assumed.")
        rows = [{"assumed_baseline": f"{p:.0%}", **{f"n={n:,}": round(100 * mde_prop(p, n), 3) for n in N_PER_ARM}}
                for p in RETENTION_BASELINES]
        print(pd.DataFrame(rows).to_string(index=False), "\n")

        print(f"6b. Pre-registered segments: players enrolled below 5,000 trophies (trophies at first battle in "
              f"{PRE[0]} to {PRE[1]}), by average card level vs the median of their 250-trophy enrollment bin")
        print(con.sql(SEGMENTS_SQL.format(pre0=PRE[0], pre1=PRE[1])).df().to_string(index=False), "\n")

        print("7. Why not randomize by region: design effect = 1 + (m - 1) * ICC for m players per cluster")
        print("   (ICC values are illustrative assumptions; region is not in this data)")
        for icc in (0.001, 0.01):
            for m in (10_000, 100_000):
                print(f"   ICC={icc}, m={m:,}: variance inflated {1 + (m - 1) * icc:,.0f}x")
        print()
    return buf.getvalue()


if __name__ == "__main__":
    report = run()
    print(report)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "phase4_power.txt").write_text(report)
