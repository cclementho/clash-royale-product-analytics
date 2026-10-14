"""Phase 3: does the card level gap decide matches?

Run:  ~/cr_data/.venv/bin/python src/level_gap.py

Perspective: the data is tagged winner/loser. If we always called the winner "player A", the
outcome would be 1 in every row and the level gap would be computed from the winner's side, so any
model would learn the labeling, not the game. Instead each battle gets a random side assignment:
a hash of the battle key decides whether A is the winner or the loser. Then "A won" is a fair
coin across battles, and gap = A's average card level minus B's says nothing about who won
except through the game itself. Because the setup is symmetric, the fitted intercept should be
close to zero, which we check.

Full data vs samples:
  - Main logit and per-band logits: ALL 37.8M battles. The two regressors take few distinct values,
    so battles are grouped into (band, level gap, trophy difference) cells in SQL and a binomial GLM
    is fit on the cells. That is the same maximum-likelihood estimate as a logit on every battle.
    Standard errors are clustered by player A, using per-player score sums computed in SQL.
  - Skill-bias check: ALL players with 50+ battles, OLS computed from SQL sums (no sampling).
  - Deck-control robustness check: 5% battle sample (24 extra columns do not fit in 8 GB at full size).

Outputs: outputs/phase3_results.txt, phase3_main_model.csv, phase3_bands.csv, phase3_binned.csv
"""
import io
from contextlib import redirect_stdout

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.special import expit
from scipy.stats import chi2

from config import OUT_DIR, WINCONS_CSV, connect

pd.set_option("display.width", 200)

# Battle key as one string. Hash it with a different suffix per purpose. Do not use DuckDB's
# multi-argument hash(a, b, 'x'): it combines component hashes in a way that ties the parity of
# hash(..., 'side') to hash(..., 'sample'), which made every sampled battle land on one side.
KEY_STR = "battle_time::VARCHAR || '|' || least(winner_tag, loser_tag) || '|' || greatest(winner_tag, loser_tag)"
FLIP = f"(hash({KEY_STR} || '|side') % 2 = 0)"
SIGN = f"CASE WHEN {FLIP} THEN 1 ELSE -1 END"
DECK_SAMPLE_PCT = 5
FREQUENT_MIN_BATTLES = 50
TROPHY_BAND = """CASE WHEN avg_trophies < 4000 THEN 'a: <4000' WHEN avg_trophies < 4500 THEN 'b: 4000-4499'
    WHEN avg_trophies < 5000 THEN 'c: 4500-4999' WHEN avg_trophies < 5500 THEN 'd: 5000-5499'
    WHEN avg_trophies < 6000 THEN 'e: 5500-5999' ELSE 'f: 6000+' END"""

# Every battle with a random side assignment. A view, so each query streams from Parquet.
AB_VIEW_SQL = f"""
CREATE VIEW ab_all AS
WITH x AS (
    SELECT CASE WHEN {FLIP} THEN winner_tag ELSE loser_tag END AS a_tag,
           {FLIP}::INT AS a_won,
           {SIGN} * (winner_total_card_level - loser_total_card_level) / 8.0 AS level_gap,
           {SIGN} * (winner_starting_trophies - loser_starting_trophies) AS trophy_diff,
           (winner_starting_trophies + loser_starting_trophies) / 2.0 AS avg_trophies
    FROM battles)
SELECT *, {TROPHY_BAND} AS band FROM x
"""

CELLS_SQL = """
SELECT band, level_gap, trophy_diff, count(*) AS n, sum(a_won) AS wins
FROM ab_all GROUP BY ALL
"""

# Every battle from both sides: gap from that player's view and whether they won (descriptive only).
BINNED_SQL = """
WITH both_sides AS (
    SELECT (winner_total_card_level - loser_total_card_level) / 8.0 AS gap, 1 AS won FROM battles
    UNION ALL
    SELECT (loser_total_card_level - winner_total_card_level) / 8.0, 0 FROM battles)
SELECT round(gap * 4) / 4 AS gap_bin, count(*) AS player_battles, avg(won) AS win_rate
FROM both_sides GROUP BY 1 HAVING abs(gap_bin) <= 3 ORDER BY 1
"""

# All players with 50+ battles; every one of their battles from their own side.
FOCAL_SQL = f"""
CREATE TABLE focal AS
SELECT tag FROM (SELECT winner_tag AS tag FROM battles UNION ALL SELECT loser_tag FROM battles)
GROUP BY tag HAVING count(*) >= {FREQUENT_MIN_BATTLES};
CREATE TABLE fp AS
SELECT winner_tag AS tag, 1.0 AS won,
       (winner_total_card_level - loser_total_card_level) / 8.0 AS level_gap,
       winner_starting_trophies - loser_starting_trophies AS trophy_diff
FROM battles SEMI JOIN focal ON battles.winner_tag = focal.tag
UNION ALL
SELECT loser_tag, 0.0, (loser_total_card_level - winner_total_card_level) / 8.0,
       loser_starting_trophies - winner_starting_trophies
FROM battles SEMI JOIN focal ON battles.loser_tag = focal.tag;
CREATE TABLE fp_dm AS
SELECT tag,
       won - avg(won) OVER (PARTITION BY tag) AS won,
       level_gap - avg(level_gap) OVER (PARTITION BY tag) AS level_gap,
       trophy_diff - avg(trophy_diff) OVER (PARTITION BY tag) AS trophy_diff
FROM fp;
"""


def wincon_ids() -> list[int]:
    """Card ids of the 24 win-condition cards in Wincons.csv (the 'Others' placeholder 99999999 is dropped)."""
    ids = pd.read_csv(WINCONS_CSV)["card_id"].astype(int)
    return ids[ids != 99999999].tolist()


def cluster_meat(con, source: str, cluster: str, scores: list[str], where: str = "TRUE") -> tuple[np.ndarray, int]:
    """Sum over clusters of the outer product of per-cluster score sums, computed entirely in SQL.

    scores: SQL expressions for each parameter's per-row score contribution. Returns (meat matrix,
    number of clusters). Nothing per-cluster is pulled into Python, so millions of clusters are fine.
    """
    k = len(scores)
    per = ", ".join(f"sum({s}) AS s{i}" for i, s in enumerate(scores))
    prods = ", ".join(f"sum(s{i} * s{j})" for i in range(k) for j in range(i, k))
    row = con.sql(f"""WITH g AS (SELECT {per} FROM {source} WHERE {where} GROUP BY {cluster})
                      SELECT {prods}, count(*) FROM g""").fetchone()
    m, idx = np.zeros((k, k)), 0
    for i in range(k):
        for j in range(i, k):
            m[i, j] = m[j, i] = row[idx]
            idx += 1
    return m, int(row[-1])


def logit_full(con, cells: pd.DataFrame, band: str | None = None) -> dict:
    """Logit of A winning on level_gap and trophy_diff over all battles (optionally one band).

    Point estimates: binomial GLM on the aggregated cells (identical to a per-battle logit).
    Covariance: sandwich clustered by player A, bread from the GLM, meat from SQL score sums.
    """
    c = cells if band is None else cells[cells["band"] == band]
    X = sm.add_constant(c[["level_gap", "trophy_diff"]].astype(float))
    y = np.column_stack([c["wins"], c["n"] - c["wins"]]).astype(float)
    res = sm.GLM(y, X, family=sm.families.Binomial()).fit()
    b0, b1, b2 = (float(v) for v in res.params)
    p = f"1 / (1 + exp(-({b0!r} + {b1!r} * level_gap + {b2!r} * trophy_diff)))"
    resid = f"(a_won - {p})"
    where = "TRUE" if band is None else f"band = '{band}'"
    meat, g = cluster_meat(con, "ab_all", "a_tag", [resid, f"level_gap * {resid}", f"trophy_diff * {resid}"], where)
    bread = np.asarray(res.cov_params())
    cov = bread @ meat @ bread * g / (g - 1)
    se = np.sqrt(np.diag(cov))
    return {"params": res.params.values, "se": se, "battles": int(c["n"].sum()), "clusters": g, "cov": cov}


def ols_sql(con, table: str, y: str, xs: list[str], cluster: str) -> tuple[np.ndarray, np.ndarray]:
    """OLS of y on xs from SQL sums, with SEs clustered by `cluster`. xs may include the literal '1.0'."""
    k = len(xs)
    sums = con.sql("SELECT " + ", ".join(
        [f"sum(({a}) * ({b}))" for a in xs for b in xs] + [f"sum(({a}) * {y})" for a in xs]) + f" FROM {table}").fetchone()
    xtx = np.array(sums[:k * k], dtype=float).reshape(k, k)
    xty = np.array(sums[k * k:], dtype=float)
    beta = np.linalg.solve(xtx, xty)
    e = f"({y} - (" + " + ".join(f"{float(b)!r} * ({x})" for b, x in zip(beta, xs)) + "))"
    meat, g = cluster_meat(con, table, cluster, [f"({x}) * {e}" for x in xs])
    inv = np.linalg.inv(xtx)
    cov = inv @ meat @ inv * g / (g - 1)
    return beta, np.sqrt(np.diag(cov))


def deck_sample_sql() -> str:
    """5% battle sample with random sides and deck-difference features (24 win conditions + elixir)."""
    w_cards = ", ".join(f"winner_card{i}_id" for i in range(1, 9))
    l_cards = ", ".join(f"loser_card{i}_id" for i in range(1, 9))
    decks = ",\n".join(
        f"{SIGN} * (list_contains([{w_cards}], {c})::INT - list_contains([{l_cards}], {c})::INT) AS wc_{c}"
        for c in wincon_ids())
    return f"""
    CREATE TABLE ab_deck AS
    SELECT CASE WHEN {FLIP} THEN winner_tag ELSE loser_tag END AS a_tag, {FLIP}::INT AS a_won,
           {SIGN} * (winner_total_card_level - loser_total_card_level) / 8.0 AS level_gap,
           {SIGN} * (winner_starting_trophies - loser_starting_trophies) AS trophy_diff,
           {SIGN} * (winner_elixir_avg - loser_elixir_avg) AS elixir_diff,
           {decks}
    FROM battles WHERE hash({KEY_STR} || '|sample') % 100 < {DECK_SAMPLE_PCT}
    """


def sample_logit(df: pd.DataFrame, cols: list[str]):
    """Per-battle logit on the deck sample, SEs clustered by player A."""
    X = sm.add_constant(df[cols].astype(float))
    return sm.Logit(df["a_won"].astype(float), X).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(df["a_tag"])[0]})


def fmt_ci(est: float, se: float, scale: float = 1.0, digits: int = 3) -> str:
    """Estimate with a normal 95% CI."""
    return f"{est * scale:+.{digits}f} (95% CI {(est - 1.96 * se) * scale:+.{digits}f}, {(est + 1.96 * se) * scale:+.{digits}f})"


def run() -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fit every Phase 3 model; return the report, main-model table, band table and binned table."""
    con = connect()
    con.execute(AB_VIEW_SQL)
    buf = io.StringIO()
    with redirect_stdout(buf):
        print("PHASE 3 RESULTS  (main and per-band models: all battles; deck check: "
              f"{DECK_SAMPLE_PCT}% sample; skill-bias check: all players with {FREQUENT_MIN_BATTLES}+ battles)\n")

        print("1. Side assignment check, all battles")
        print(con.sql("""
            SELECT count(*) AS battles, round(100 * avg(a_won), 3) AS pct_a_won,
                   round(avg(level_gap), 4) AS mean_gap, round(stddev(level_gap), 3) AS sd_gap,
                   round(avg(trophy_diff), 3) AS mean_trophy_diff, round(stddev(trophy_diff), 2) AS sd_trophy_diff,
                   round(100 * avg((level_gap = 0)::INT), 1) AS pct_gap_zero,
                   round(100 * avg((abs(level_gap) >= 1)::INT), 1) AS pct_abs_gap_1plus,
                   round(100 * avg((abs(level_gap) > 2.5)::INT), 2) AS pct_abs_gap_over_2_5
            FROM ab_all""").df().to_string(index=False), "\n")

        binned = con.sql(BINNED_SQL).df()
        print("2. Win rate by level gap, all battles from both sides (gap rounded to 0.25 levels)")
        print(binned.to_string(index=False), "\n")

        cells = con.sql(CELLS_SQL).df()
        print(f"3. Logistic regression on all battles: P(A wins) ~ level_gap + trophy_diff "
              f"({len(cells):,} cells; SEs clustered by player A)")
        m = logit_full(con, cells)
        main = pd.DataFrame({"term": ["const", "level_gap", "trophy_diff"], "coef": m["params"], "se": m["se"]})
        main["ci_lo"], main["ci_hi"] = main["coef"] - 1.96 * main["se"], main["coef"] + 1.96 * main["se"]
        print(main.to_string(index=False))
        print(f"   battles: {m['battles']:,}; player clusters: {m['clusters']:,}\n")
        b0, b1, b2 = m["params"]
        print("   In plain terms (trophy difference 0):")
        for g in (-2, -1, -0.5, 0, 0.5, 1):
            print(f"     level gap {g:+.1f}: P(win) = {100 * expit(b0 + b1 * g):.1f}%")
        print(f"   Odds ratio per +1 level: {np.exp(b1):.2f}; per +10 trophies: {np.exp(10 * b2):.3f}\n")

        print("4. Level-gap effect by trophy band (separate full-data logit per band, clustered SEs)")
        print("   Bands come from SQL. Do not round-trip rows through pandas and back into DuckDB to label them:")
        print("   with preserve_insertion_order=false the rows come back reordered (an earlier version mislabeled")
        print("   68.5% of rows this way and wrongly found no difference between bands).")
        spread = con.sql("""SELECT band, round(stddev(level_gap), 3) AS sd_gap,
                                   round(100 * avg((abs(level_gap) >= 1)::INT), 2) AS pct_gap_1plus
                            FROM ab_all GROUP BY 1""").df()
        rows = []
        for band in sorted(cells["band"].unique()):
            r = logit_full(con, cells, band)
            c0, c1 = r["params"][0], r["params"][1]
            rows.append({"band": band, "battles": r["battles"], "coef": c1, "se": r["se"][1],
                         "p_win_minus1": 100 * expit(c0 - c1),
                         "p_win_minus1_lo": 100 * expit(c0 - (c1 + 1.96 * r["se"][1])),
                         "p_win_minus1_hi": 100 * expit(c0 - (c1 - 1.96 * r["se"][1])),
                         "p_win_minus_quarter": 100 * expit(c0 - 0.25 * c1),
                         "odds_ratio": np.exp(c1), "or_lo": np.exp(c1 - 1.96 * r["se"][1]),
                         "or_hi": np.exp(c1 + 1.96 * r["se"][1])})
        bands = pd.DataFrame(rows).merge(spread, on="band")
        print(bands.round(4).to_string(index=False))
        w = 1 / bands["se"] ** 2
        pooled = (w * bands["coef"]).sum() / w.sum()
        stat = (w * (bands["coef"] - pooled) ** 2).sum()
        dof = len(bands) - 1
        print(f"   Equality test across bands (Wald, clustered SEs, bands treated as independent): "
              f"chi2 = {stat:.1f}, df = {dof}, p {'< 1e-10' if chi2.sf(stat, dof) < 1e-10 else '= %.2g' % chi2.sf(stat, dof)}")
        print("   Where pct_gap_1plus is under 1%, p_win_minus1 is an extrapolation beyond the observed gaps.\n")

        print(f"5. Robustness: deck controls on a {DECK_SAMPLE_PCT}% battle sample "
              "(24 win-condition presence differences + average elixir difference)")
        con.execute(deck_sample_sql())
        df = con.sql("SELECT * FROM ab_deck").df()
        wc = [c for c in df.columns if c.startswith("wc_") and df[c].abs().sum() > 0]
        r0 = sample_logit(df, ["level_gap", "trophy_diff"])
        r1 = sample_logit(df, ["level_gap", "trophy_diff", "elixir_diff"] + wc)
        print(f"   sample battles: {len(df):,}")
        print(f"   level_gap coef, same sample, without deck controls: {fmt_ci(r0.params['level_gap'], r0.bse['level_gap'])}")
        print(f"   level_gap coef, same sample, with deck controls:    {fmt_ci(r1.params['level_gap'], r1.bse['level_gap'])}\n")

        print(f"6. Skill-bias check: all players with {FREQUENT_MIN_BATTLES}+ battles, every battle from their side, "
              "linear probability model, SEs clustered by player")
        con.execute(FOCAL_SQL)
        n_players, n_rows = con.execute("SELECT count(DISTINCT tag), count(*) FROM fp").fetchone()
        sd_all, sd_within = con.execute(
            "SELECT (SELECT stddev(level_gap) FROM fp), (SELECT stddev(level_gap) FROM fp_dm)").fetchone()
        print(f"   players: {n_players:,}; player-battles: {n_rows:,}")
        print(f"   sd of level gap: overall {sd_all:.3f}, within player {sd_within:.3f}")
        bp, sp = ols_sql(con, "fp", "won", ["1.0", "level_gap", "trophy_diff"], "tag")
        bf, sf = ols_sql(con, "fp_dm", "won", ["level_gap", "trophy_diff"], "tag")
        print(f"   pp per +1 level, pooled (no player effects): {fmt_ci(bp[1], sp[1], 100, 2)}")
        print(f"   pp per +1 level, player fixed effects:       {fmt_ci(bf[0], sf[0], 100, 2)}")
        print(f"   pp per +10 trophies, pooled: {fmt_ci(bp[2], sp[2], 1000, 2)}; "
              f"fixed effects: {fmt_ci(bf[1], sf[1], 1000, 2)}\n")
    return buf.getvalue(), main, bands, binned


if __name__ == "__main__":
    report, main, bands, binned = run()
    print(report)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "phase3_results.txt").write_text(report)
    main.to_csv(OUT_DIR / "phase3_main_model.csv", index=False)
    bands.to_csv(OUT_DIR / "phase3_bands.csv", index=False)
    binned.to_csv(OUT_DIR / "phase3_binned.csv", index=False)
