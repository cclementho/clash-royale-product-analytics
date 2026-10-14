"""Phase 2: does losing change short-term re-queueing? (narrowed question 1)

Run:  ~/cr_data/.venv/bin/python src/requeue.py               (full well-covered group)
      ~/cr_data/.venv/bin/python src/requeue.py --dev-pct 10  (10% of players, for development)

Outcome: did the player start another ladder battle within X minutes (X = 10 primary, 30 robustness)?
Missing battles (Phase 1) make some outcomes unknown, so each battle is labelled:
  certain yes : next observed battle within X minutes. A missed battle in between would also be
                within X, so the answer is yes either way.
  certain no  : next observed battle more than X minutes later AND the trophy chain is intact,
                so no ladder battle happened in between.
  unknown     : next observed battle more than X minutes later with a broken chain (some battle
                happened, we do not know when), or no later battle at all.
Lower bound treats unknown as no; upper bound treats unknown as yes.

Population: "well-covered" players, selected on (a) at least 50 consecutive-battle pairs in the
steady period Dec 19 to Jan 3 and (b) at least 90% of those pairs with an intact trophy chain.
These are heavy ladder players whose battle logs were well captured. Results say nothing about
casual players or long-term churn.

Outputs: outputs/phase2_results.txt, outputs/phase2_*.csv
"""
import argparse
import io
from contextlib import redirect_stdout

import numpy as np
import pandas as pd
import statsmodels.api as sm

from config import OUT_DIR, connect

pd.set_option("display.width", 200)

STEADY_START, STEADY_END = "2020-12-19", "2021-01-04"
STEADY = f"t >= TIMESTAMPTZ '{STEADY_START}' AND t < TIMESTAMPTZ '{STEADY_END}'"
MIN_PAIRS, MIN_INTACT = 50, 0.90
SESSION_GAP_MIN = 60  # a gap this long starts a new session (reference-point and fatigue checks)
SEASON_END_CUTOFF = "2021-01-01"  # robustness: drop outcome battles on Jan 1 to Jan 3
N_BOOT = 2000

# Players with at least MIN_PAIRS + 1 battles in the steady period (a cheap superset of the
# well-covered group; the exact filter needs the sequence table below).
CANDIDATES_SQL = f"""
CREATE TABLE cand AS
SELECT tag FROM (
    SELECT winner_tag AS tag FROM battles
    WHERE battle_time >= TIMESTAMPTZ '{STEADY_START}' AND battle_time < TIMESTAMPTZ '{STEADY_END}'
    UNION ALL
    SELECT loser_tag FROM battles
    WHERE battle_time >= TIMESTAMPTZ '{STEADY_START}' AND battle_time < TIMESTAMPTZ '{STEADY_END}')
WHERE dev_sampled(tag)
GROUP BY tag HAVING count(*) > {MIN_PAIRS}
"""

# All battles of candidate players (whole 4 weeks, so LEAD/LAG can see across the period edges).
# A player in two battles at the same second is a collection artifact; those rows are dropped.
PLAYER_BATTLES_SQL = """
CREATE TABLE pb AS
WITH x AS (
    SELECT winner_tag AS tag, battle_time AS t, 1 AS won,
           winner_starting_trophies AS trophies, winner_trophy_change AS trophy_change
    FROM battles SEMI JOIN cand ON battles.winner_tag = cand.tag
    UNION ALL
    SELECT loser_tag, battle_time, 0, loser_starting_trophies, loser_trophy_change
    FROM battles SEMI JOIN cand ON battles.loser_tag = cand.tag)
SELECT * FROM x QUALIFY count(*) OVER (PARTITION BY tag, t) = 1
"""

# Per-player sequence. A "link" between two consecutive observed battles is intact when the later
# battle's starting trophies equal the earlier one's starting trophies plus its trophy change.
SEQUENCE_SQL = """
CREATE TABLE seq AS
SELECT tag, t, won, trophies, trophy_change,
       LEAD(t) OVER w AS next_t,
       epoch(LEAD(t) OVER w - t) / 60.0 AS gap_min,
       LEAD(trophies) OVER w = trophies + trophy_change AS next_link_intact,
       epoch(t - LAG(t) OVER w) / 60.0 AS prev_gap_min,
       LAG(won, 1) OVER w AS prev1_won,
       LAG(won, 2) OVER w AS prev2_won,
       LAG(trophies, 1) OVER w + LAG(trophy_change, 1) OVER w = trophies AS prev1_link_intact,
       LAG(trophies, 2) OVER w + LAG(trophy_change, 2) OVER w = LAG(trophies, 1) OVER w AS prev2_link_intact
FROM pb
WINDOW w AS (PARTITION BY tag ORDER BY t)
"""

COVERED_SQL = f"""
CREATE TABLE covered AS
SELECT tag, count(*) AS n_pairs, avg(next_link_intact::int) AS intact_rate
FROM seq WHERE {STEADY} AND next_t IS NOT NULL
GROUP BY tag HAVING count(*) >= {MIN_PAIRS} AND avg(next_link_intact::int) >= {MIN_INTACT}
"""

# One row per battle of a covered player in the steady period, with outcome labels and context.
# streak_len: length of the current same-result run ending at this battle (1, 2, or 3 = "3+").
# It is only defined when every link it relies on is intact; otherwise a missed battle could have
# a different result and the streak is unknown (NULL).
# Session reference point: trophies at the first battle after a gap of SESSION_GAP_MIN or more.
OUTCOMES_SQL = f"""
CREATE TABLE outcomes AS
WITH s AS (
    SELECT seq.*,
           sum((prev_gap_min IS NULL OR prev_gap_min >= {SESSION_GAP_MIN})::int)
               OVER (PARTITION BY tag ORDER BY t) AS session_id
    FROM seq SEMI JOIN covered USING (tag)),
s2 AS (
    SELECT *,
           first_value(trophies) OVER (PARTITION BY tag, session_id ORDER BY t) AS session_start_trophies,
           epoch(t - first_value(t) OVER (PARTITION BY tag, session_id ORDER BY t)) / 60.0 AS minutes_in_session,
           row_number() OVER (PARTITION BY tag, session_id ORDER BY t) AS battle_in_session,
           max(trophies + trophy_change) OVER (PARTITION BY tag, session_id ORDER BY t
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_session_high
    FROM s)
SELECT tag, t, won,
       CASE WHEN prev1_link_intact AND prev1_won = won THEN
                CASE WHEN prev2_link_intact AND prev2_won = won THEN 3
                     WHEN prev2_link_intact THEN 2 END
            WHEN prev1_link_intact THEN 1 END AS streak_len,
       next_t IS NOT NULL AND gap_min <= 10 AS yes10,
       next_t IS NULL OR (gap_min > 10 AND NOT next_link_intact) AS unk10,
       next_t IS NOT NULL AND gap_min <= 30 AS yes30,
       next_t IS NULL OR (gap_min > 30 AND NOT next_link_intact) AS unk30,
       battle_in_session, minutes_in_session,
       trophies + trophy_change < session_start_trophies AS below_session_start,
       trophies + trophy_change > greatest(session_start_trophies, coalesce(prior_session_high, 0)) AS new_session_high
FROM s2 WHERE {STEADY}
"""


def per_player(con, group_sql: str, where: str = "TRUE") -> pd.DataFrame:
    """Per-player, per-group counts of battles, certain-yes and unknown outcomes (10 and 30 min)."""
    return con.sql(f"""
        SELECT tag, {group_sql} AS grp, count(*) AS n,
               sum(yes10::int) AS yes10, sum(unk10::int) AS unk10,
               sum(yes30::int) AS yes30, sum(unk30::int) AS unk30
        FROM outcomes WHERE {where} GROUP BY 1, 2
    """).df()


def paired_diff(pp: pd.DataFrame, a: str, b: str, x: int, rng: np.random.Generator) -> dict:
    """Within-player difference in re-queue rate, group a minus group b, averaged over players.

    For each player with battles in both groups:
      lo = yes / n              (unknown counted as no)
      hi = (yes + unknown) / n  (unknown counted as yes)
    Three versions of the per-player difference:
      certain-only : lo_a - lo_b        (both groups scored the same conservative way)
      worst-case lower bound: lo_a - hi_b
      worst-case upper bound: hi_a - lo_b
    The worst-case pair brackets the true difference no matter how the unknowns resolve.
    Each player counts once, so heavy players do not dominate. 95% CIs come from resampling
    players (not battles), because a player's battles are not independent of each other.
    """
    w = pp.pivot_table(index="tag", columns="grp", values=["n", f"yes{x}", f"unk{x}"], aggfunc="sum")
    w = w.dropna(subset=[("n", a), ("n", b)])
    lo_a = w[(f"yes{x}", a)] / w[("n", a)]
    hi_a = (w[(f"yes{x}", a)] + w[(f"unk{x}", a)]) / w[("n", a)]
    lo_b = w[(f"yes{x}", b)] / w[("n", b)]
    hi_b = (w[(f"yes{x}", b)] + w[(f"unk{x}", b)]) / w[("n", b)]
    diffs = np.column_stack([lo_a - lo_b, lo_a - hi_b, hi_a - lo_b]) * 100
    idx = rng.integers(0, len(diffs), size=(N_BOOT, len(diffs)))
    boot = diffs[idx].mean(axis=1)  # N_BOOT x 3
    est = diffs.mean(axis=0)
    ci = np.percentile(boot, [2.5, 97.5], axis=0)
    return {
        "players": len(diffs),
        "rate_a_lo": lo_a.mean() * 100, "rate_a_hi": hi_a.mean() * 100,
        "rate_b_lo": lo_b.mean() * 100, "rate_b_hi": hi_b.mean() * 100,
        "diff_certain": est[0], "diff_certain_ci": (ci[0, 0], ci[1, 0]),
        "diff_worst_lo": est[1], "diff_worst_lo_ci_low": ci[0, 1],
        "diff_worst_hi": est[2], "diff_worst_hi_ci_high": ci[1, 2],
    }


def fmt(d: dict) -> str:
    """One readable line for a paired_diff result (percentage points)."""
    return (f"players={d['players']:,}  rate A [{d['rate_a_lo']:.1f}, {d['rate_a_hi']:.1f}]  "
            f"rate B [{d['rate_b_lo']:.1f}, {d['rate_b_hi']:.1f}]  "
            f"diff(certain-only) {d['diff_certain']:+.2f} pp (95% CI {d['diff_certain_ci'][0]:+.2f}, {d['diff_certain_ci'][1]:+.2f})  "
            f"worst-case bounds [{d['diff_worst_lo']:+.2f}, {d['diff_worst_hi']:+.2f}] "
            f"(CI-extended [{d['diff_worst_lo_ci_low']:+.2f}, {d['diff_worst_hi_ci_high']:+.2f}])")


# Fatigue controls: where the battle sits in its session, by count and by elapsed time.
FATIGUE_SQL = """
    CASE WHEN battle_in_session <= 2 THEN 'p' || battle_in_session::VARCHAR
         WHEN battle_in_session <= 5 THEN 'p3-5' WHEN battle_in_session <= 10 THEN 'p6-10'
         WHEN battle_in_session <= 20 THEN 'p11-20' ELSE 'p21+' END AS pos_band,
    CASE WHEN minutes_in_session < 15 THEN 'm0-15' WHEN minutes_in_session < 30 THEN 'm15-30'
         WHEN minutes_in_session < 60 THEN 'm30-60' WHEN minutes_in_session < 120 THEN 'm60-120'
         ELSE 'm120+' END AS min_band"""


def fe_lpm(df: pd.DataFrame, y: str, terms: list[str], controls: list[str]) -> sm.regression.linear_model.RegressionResultsWrapper:
    """Linear probability model with player fixed effects, SEs clustered by player.

    Fixed effects are absorbed by subtracting each player's mean from y and from every
    regressor (the within transformation), so each coefficient compares a player to themselves.
    terms: 0/1 columns of interest. controls: categorical columns turned into dummies
    (first level dropped). Battles are weighted equally here, unlike the player-averaged
    comparisons above, so compare models with and without controls to each other.
    """
    X = df[terms].astype(float)
    for c in controls:
        X = X.join(pd.get_dummies(df[c], prefix=c, drop_first=True, dtype=float))
    Y = df[y].astype(float)
    g = df["tag"]
    Xd = X - X.groupby(g).transform("mean")
    Yd = Y - Y.groupby(g).transform("mean")
    return sm.OLS(Yd, Xd).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(g)[0]})


def report_contrast(res, expr: str, label: str) -> str:
    """Point estimate and 95% CI (in pp) for a linear contrast of coefficients."""
    t = res.t_test(expr)
    est, (lo, hi) = float(t.effect[0]) * 100, (np.asarray(t.conf_int()).ravel() * 100)
    return f"{label:<44} {est:+6.2f} pp  (95% CI {lo:+.2f}, {hi:+.2f})"


def run(dev_pct: int | None) -> tuple[str, pd.DataFrame]:
    """Build tables, run every Phase 2 comparison, return the report text and streak table."""
    con = connect()
    con.execute(f"CREATE MACRO dev_sampled(tag) AS hash(tag) % 100 < {100 if dev_pct is None else dev_pct}")
    for sql in (CANDIDATES_SQL, PLAYER_BATTLES_SQL, SEQUENCE_SQL, COVERED_SQL, OUTCOMES_SQL):
        con.execute(sql)
    rng = np.random.default_rng(18)
    buf = io.StringIO()
    with redirect_stdout(buf):
        print(f"PHASE 2 RESULTS  (dev_pct={dev_pct}; steady period {STEADY_START} to {STEADY_END} exclusive)\n")
        print("1. Population")
        print(con.sql("""
            SELECT (SELECT count(*) FROM cand) AS players_with_51plus_steady_battles,
                   (SELECT count(*) FROM covered) AS well_covered_players,
                   (SELECT round(median(n_pairs)) FROM covered) AS median_pairs_per_player,
                   (SELECT round(100 * avg(intact_rate), 1) FROM covered) AS mean_pct_links_intact,
                   (SELECT count(*) FROM outcomes) AS battles_analysed,
                   (SELECT round(100 * avg(won), 1) FROM outcomes) AS pct_wins
        """).df().to_string(index=False), "\n")

        print("2. Outcome labels, pooled over battles (percent)")
        print(con.sql("""
            SELECT CASE WHEN won = 1 THEN 'after win' ELSE 'after loss' END AS prev, count(*) AS battles,
                   round(100 * avg(yes10::int), 1) AS yes10, round(100 * avg((NOT yes10 AND NOT unk10)::int), 1) AS no10,
                   round(100 * avg(unk10::int), 1) AS unk10,
                   round(100 * avg(yes30::int), 1) AS yes30, round(100 * avg(unk30::int), 1) AS unk30
            FROM outcomes GROUP BY 1 ORDER BY 1
        """).df().to_string(index=False), "\n")

        pp = per_player(con, "CASE WHEN won = 1 THEN 'W' ELSE 'L' END")
        print("3. Within-player: after LOSS (A) minus after WIN (B), re-queue rate in pp")
        for x in (10, 30):
            print(f"  within {x} min: {fmt(paired_diff(pp, 'L', 'W', x, rng))}")
        print()

        print("4. Within-player by streak length: losing streak k (A) minus winning streak k (B), 10 min")
        pps = per_player(con, "CASE WHEN won = 1 THEN 'W' ELSE 'L' END || streak_len::VARCHAR",
                         "streak_len IS NOT NULL")
        rows = []
        for k in (1, 2, 3):
            d10 = paired_diff(pps, f"L{k}", f"W{k}", 10, rng)
            d30 = paired_diff(pps, f"L{k}", f"W{k}", 30, rng)
            label = f"{k}+" if k == 3 else str(k)
            print(f"  streak {label} (10 min): {fmt(d10)}")
            print(f"  streak {label} (30 min): {fmt(d30)}")
            rows.append({"streak": label, **{f"{key}_10": v for key, v in d10.items()},
                         **{f"{key}_30": v for key, v in d30.items()}})
        print("  (streak of 1 means the previous battle had the opposite result; 3+ means at least 3 in a row)\n")

        print("5. Re-queue rate by streak, averaged over players who have that streak type (10 min, percent)")
        cells = con.sql("""
            WITH c AS (
                SELECT tag, CASE WHEN won = 1 THEN 'W' ELSE 'L' END || streak_len::VARCHAR AS cell,
                       avg(yes10::int) AS lo, avg((yes10 OR unk10)::int) AS hi,
                       avg(yes30::int) AS lo30, avg((yes30 OR unk30)::int) AS hi30, count(*) AS n
                FROM outcomes WHERE streak_len IS NOT NULL GROUP BY 1, 2)
            SELECT cell, count(*) AS players, sum(n) AS battles,
                   round(100 * avg(lo), 2) AS rate10_lo, round(100 * avg(hi), 2) AS rate10_hi,
                   round(100 * avg(lo30), 2) AS rate30_lo, round(100 * avg(hi30), 2) AS rate30_hi
            FROM c GROUP BY 1 ORDER BY 1
        """).df()
        print(cells.to_string(index=False))
        print(con.sql("""SELECT round(100 * avg((streak_len IS NULL)::int), 1) AS pct_battles_streak_unknown
                         FROM outcomes""").df().to_string(index=False), "\n")

        print("6. Reference-point checks (battles that are 3rd or later in a session; session = no gap >= "
              f"{SESSION_GAP_MIN} min)")
        ref_l = per_player(con, "CASE WHEN below_session_start THEN 'below' ELSE 'at_or_above' END",
                           "won = 0 AND battle_in_session >= 3")
        ref_w = per_player(con, "CASE WHEN new_session_high THEN 'new_high' ELSE 'not_high' END",
                           "won = 1 AND battle_in_session >= 3")
        for x in (10, 30):
            print(f"  after a LOSS, below session-start trophies (A) vs at/above (B), {x} min: "
                  f"{fmt(paired_diff(ref_l, 'below', 'at_or_above', x, rng))}")
            print(f"  after a WIN, new session high (A) vs not (B), {x} min: "
                  f"{fmt(paired_diff(ref_w, 'new_high', 'not_high', x, rng))}")
        print()

        print("7. Fatigue check: player fixed effects, with and without session position + minutes-into-session controls")
        print("   (linear probability model, 10 min outcome, SEs clustered by player; 'lower' codes unknown as no,")
        print("    'upper' codes unknown as yes)")
        df = con.sql(f"""
            SELECT tag, won, streak_len, new_session_high, battle_in_session,
                   yes10::INT AS lower10, (yes10 OR unk10)::INT AS upper10,
                   (won = 0)::INT AS loss,
                   (won = 1 AND streak_len = 2)::INT AS w2, (won = 1 AND streak_len = 3)::INT AS w3,
                   (won = 0 AND streak_len = 1)::INT AS l1, (won = 0 AND streak_len = 2)::INT AS l2,
                   (won = 0 AND streak_len = 3)::INT AS l3,
                   {FATIGUE_SQL}
            FROM outcomes
        """).df()
        controls = ["pos_band", "min_band"]
        for y in ("lower10", "upper10"):
            print(f"  outcome = {y}")
            for ctl, name in (([], "no controls"), (controls, "fatigue controls")):
                r = fe_lpm(df, y, ["loss"], ctl)
                print("    " + report_contrast(r, "loss = 0", f"loss vs win, {name}"))
            st = df[df["streak_len"].notna()]
            for ctl, name in (([], "no controls"), (controls, "fatigue controls")):
                r = fe_lpm(st, y, ["w2", "w3", "l1", "l2", "l3"], ctl)  # baseline: win streak of 1
                print("    " + report_contrast(r, "w2 = 0", f"win streak 2 vs 1, {name}"))
                print("    " + report_contrast(r, "w3 = 0", f"win streak 3+ vs 1, {name}"))
                print("    " + report_contrast(r, "l3 - l1 = 0", f"loss streak 3+ vs 1, {name}"))
                print("    " + report_contrast(r, "l3 - w3 = 0", f"loss 3+ vs win 3+, {name}"))
            sh = df[(df["won"] == 1) & (df["battle_in_session"] >= 3)].assign(
                high=lambda d: d["new_session_high"].astype(int))
            for ctl, name in (([], "no controls"), (controls, "fatigue controls")):
                r = fe_lpm(sh, y, ["high"], ctl)
                print("    " + report_contrast(r, "high = 0", f"new session high (wins), {name}"))
        print()

        print(f"8. Season-end check: outcome battles before {SEASON_END_CUTOFF} only (Dec 19 to Dec 31), "
              "same players, 10 min")
        early = f"t < TIMESTAMPTZ '{SEASON_END_CUTOFF}'"
        pp_e = per_player(con, "CASE WHEN won = 1 THEN 'W' ELSE 'L' END", early)
        print(f"  loss vs win: {fmt(paired_diff(pp_e, 'L', 'W', 10, rng))}")
        pps_e = per_player(con, "CASE WHEN won = 1 THEN 'W' ELSE 'L' END || streak_len::VARCHAR",
                           f"streak_len IS NOT NULL AND {early}")
        for k in (1, 2, 3):
            label = f"{k}+" if k == 3 else str(k)
            print(f"  streak {label}: {fmt(paired_diff(pps_e, f'L{k}', f'W{k}', 10, rng))}")
        print(con.sql(f"""
            WITH c AS (
                SELECT tag, CASE WHEN won = 1 THEN 'W' ELSE 'L' END || streak_len::VARCHAR AS cell,
                       avg(yes10::int) AS lo, avg((yes10 OR unk10)::int) AS hi
                FROM outcomes WHERE streak_len IS NOT NULL AND {early} GROUP BY 1, 2)
            SELECT cell, count(*) AS players, round(100 * avg(lo), 2) AS rate10_lo, round(100 * avg(hi), 2) AS rate10_hi
            FROM c GROUP BY 1 ORDER BY 1
        """).df().to_string(index=False))
        print()
    return buf.getvalue(), cells


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-pct", type=int, default=None, help="percent of players, for development")
    args = ap.parse_args()
    report, cells = run(args.dev_pct)
    print(report)
    OUT_DIR.mkdir(exist_ok=True)
    suffix = "" if args.dev_pct is None else f"_dev{args.dev_pct}"
    (OUT_DIR / f"phase2_results{suffix}.txt").write_text(report)
    cells.to_csv(OUT_DIR / f"phase2_streak_rates{suffix}.csv", index=False)
