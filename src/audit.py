"""Phase 1 data audit. Every number in the audit write-up comes from this script.

Run:  ~/cr_data/.venv/bin/python src/audit.py                 (full data, sequences on 10% of players)
      ~/cr_data/.venv/bin/python src/audit.py --seq-sample 2  (faster, for development)

Battle-level checks (counts, nulls, duplicates, card levels) run on all rows.
Player-sequence checks need window functions over ~75M player-battle rows, which does not
fit in 8 GB of RAM, so they run on a random sample of players chosen by hashing the tag.
Every sampled player keeps their complete history, so per-player rates are unbiased.

Output is printed and saved to outputs/phase1_audit_seq<pct>.txt.
"""
import argparse
import io
from contextlib import redirect_stdout

import pandas as pd

from config import CARD_CSV, LADDER_MODES, OUT_DIR, WINCONS_CSV, connect

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)

# One row per (player, battle) for sampled players. Each battle gives a winner row and a loser row.
PLAYER_BATTLES_SQL = """
CREATE TABLE player_battles AS
SELECT winner_tag AS tag, battle_time AS t, 1 AS won,
       winner_starting_trophies AS trophies, winner_trophy_change AS trophy_change
FROM battles WHERE seq_sampled(winner_tag)
UNION ALL
SELECT loser_tag, battle_time, 0, loser_starting_trophies, loser_trophy_change
FROM battles WHERE seq_sampled(loser_tag)
"""

# Each player's battles in time order, with the next battle attached via LEAD.
# chain_intact: the next battle's starting trophies equal this battle's starting trophies
# plus this battle's trophy change. If a ladder battle happened in between and was not
# collected, the chain breaks. This is the direct test for missing battles.
SEQUENCES_SQL = """
CREATE TABLE seq AS
SELECT *,
       LEAD(t)        OVER w AS next_t,
       LEAD(trophies) OVER w AS next_trophies,
       epoch(LEAD(t) OVER w - t) / 60.0 AS gap_min,
       LEAD(trophies) OVER w = trophies + trophy_change AS chain_intact,
       row_number()   OVER w AS rn
FROM player_battles
WINDOW w AS (PARTITION BY tag ORDER BY t, trophies, won)  -- tie-break same-second rows so runs are reproducible
"""

GAP_BUCKET = """CASE WHEN gap_min = 0 THEN 'a: 0 (same second)' WHEN gap_min < 3 THEN 'b: (0,3)'
     WHEN gap_min < 5 THEN 'c: [3,5)' WHEN gap_min < 10 THEN 'd: [5,10)' WHEN gap_min < 30 THEN 'e: [10,30)'
     WHEN gap_min < 60 THEN 'f: [30,60)' WHEN gap_min < 180 THEN 'g: [60,180)'
     WHEN gap_min < 1440 THEN 'h: [3h,24h)' ELSE 'i: 24h+' END"""

# Steady-state collection period (see check B6): volume jumps ~20x on 2020-12-18 and the last
# file stops at 08:04 UTC on 2021-01-04.
STEADY = "t >= TIMESTAMPTZ '2020-12-19' AND t < TIMESTAMPTZ '2021-01-04'"

BATTLE_QUERIES = {
    "B1. Row count and date range (all raw files, all modes)": """
        SELECT count(*) AS n_rows, count(DISTINCT source_file) AS files,
               min(battle_time) AS first_battle_utc, max(battle_time) AS last_battle_utc,
               round(epoch(max(battle_time) - min(battle_time)) / 86400, 1) AS days_covered
        FROM raw""",
    "B2. Per source file: rows and time range (each file overlaps the previous one)": """
        SELECT source_file, count(*) AS n_rows, min(battle_time) AS first_utc, max(battle_time) AS last_utc
        FROM raw GROUP BY 1 ORDER BY first_utc""",
    "B3. Game modes: all three large IDs give about +/-29.5 trophies (ladder)": """
        SELECT game_mode_id, count(*) AS n_rows,
               round(avg(winner_trophy_change), 1) AS avg_winner_trophy_change,
               round(avg(loser_trophy_change), 1)  AS avg_loser_trophy_change,
               min(battle_time)::DATE AS first_day, max(battle_time)::DATE AS last_day,
               count(tournament_tag) AS rows_with_tournament_tag
        FROM raw GROUP BY 1 ORDER BY 2 DESC""",
    "B4. Duplicates on the battle key (time + unordered player pair), ladder rows": f"""
        WITH k AS (
            SELECT count(*) AS copies, count(DISTINCT source_file) AS files
            FROM raw WHERE game_mode_id IN {LADDER_MODES}
            GROUP BY battle_time, least(winner_tag, loser_tag), greatest(winner_tag, loser_tag))
        SELECT sum(copies) AS ladder_rows, count(*) AS distinct_battle_keys,
               sum(copies) - count(*) AS duplicate_rows,
               count(*) FILTER (WHERE copies > 1) AS keys_with_copies,
               count(*) FILTER (WHERE files > 1) AS keys_spanning_two_files
        FROM k""",
    "B5. Rows removed by dedupe, per file (raw ladder rows minus kept rows)": f"""
        WITH r AS (SELECT source_file, count(*) AS raw_ladder FROM raw WHERE game_mode_id IN {LADDER_MODES} GROUP BY 1),
             b AS (SELECT source_file, count(*) AS kept FROM battles GROUP BY 1)
        SELECT source_file, raw_ladder, kept, raw_ladder - kept AS removed
        FROM r JOIN b USING (source_file) ORDER BY source_file""",
    "B6. Battles per day after dedupe (collection volume over time)": """
        SELECT battle_time::DATE AS day_utc, count(*) AS battles
        FROM battles GROUP BY 1 ORDER BY 1""",
    "B7. Card levels: min and max observed level per card": """
        WITH cl AS (
            SELECT unnest([winner_card1_id, winner_card2_id, winner_card3_id, winner_card4_id,
                           winner_card5_id, winner_card6_id, winner_card7_id, winner_card8_id]) AS id,
                   unnest([winner_card1_level, winner_card2_level, winner_card3_level, winner_card4_level,
                           winner_card5_level, winner_card6_level, winner_card7_level, winner_card8_level]) AS lvl
            FROM battles
            UNION ALL
            SELECT unnest([loser_card1_id, loser_card2_id, loser_card3_id, loser_card4_id,
                           loser_card5_id, loser_card6_id, loser_card7_id, loser_card8_id]),
                   unnest([loser_card1_level, loser_card2_level, loser_card3_level, loser_card4_level,
                           loser_card5_level, loser_card6_level, loser_card7_level, loser_card8_level])
            FROM battles),
        per_card AS (SELECT id, min(lvl) AS min_lvl, max(lvl) AS max_lvl FROM cl GROUP BY 1)
        SELECT min_lvl, max_lvl, count(*) AS cards FROM per_card GROUP BY 1, 2 ORDER BY 1""",
    "B8. Card level and trophy gaps between the two players in a battle": """
        SELECT quantile_cont(abs(winner_total_card_level - loser_total_card_level) / 8.0, [0.5, 0.9, 0.99]) AS abs_avg_level_gap_q50_q90_q99,
               quantile_cont(abs(winner_starting_trophies - loser_starting_trophies), [0.5, 0.9, 0.99]) AS abs_trophy_gap_q50_q90_q99
        FROM battles""",
}

SEQ_QUERIES = {
    "S1. Sampled players and their battles": """
        SELECT count(DISTINCT tag) AS players, count(*) AS player_battles,
               round(count(*) / count(DISTINCT tag), 1) AS mean_battles_per_player
        FROM player_battles""",
    "S2. Battles per player": """
        WITH c AS (SELECT tag, count(*) AS n FROM player_battles GROUP BY 1)
        SELECT CASE WHEN n = 1 THEN '1' WHEN n <= 4 THEN '2-4' WHEN n <= 9 THEN '5-9' WHEN n <= 24 THEN '10-24'
                    WHEN n <= 99 THEN '25-99' ELSE '100+' END AS battles_per_player,
               count(*) AS players, round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_players,
               round(100.0 * sum(n) / sum(sum(n)) OVER (), 1) AS pct_player_battles
        FROM c GROUP BY 1 ORDER BY min(n)""",
    "S3. Distinct UTC days each player appears on, steady period Dec 19 to Jan 3 (16 days)": f"""
        WITH d AS (SELECT tag, count(DISTINCT t::DATE) AS days FROM player_battles WHERE {STEADY} GROUP BY 1)
        SELECT CASE WHEN days = 1 THEN '1' WHEN days <= 3 THEN '2-3' WHEN days <= 7 THEN '4-7'
                    WHEN days <= 14 THEN '8-14' ELSE '15-16' END AS active_days,
               count(*) AS players, round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_players
        FROM d GROUP BY 1 ORDER BY min(days)""",
    "S4. Same player in two different battles at the same second (impossible in play)": """
        SELECT count(*) AS player_seconds_with_2plus_battles
        FROM (SELECT tag, t FROM player_battles GROUP BY 1, 2 HAVING count(*) > 1)""",
    "S5. Gap to next observed battle vs trophy-chain continuity (steady period)": f"""
        SELECT {GAP_BUCKET} AS gap_to_next, count(*) AS pairs,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_pairs,
               round(100.0 * avg(chain_intact::int), 1) AS pct_chain_intact
        FROM seq WHERE next_t IS NOT NULL AND {STEADY} GROUP BY 1 ORDER BY 1""",
    "S6. Size of the trophy discrepancy when the chain breaks (about 30 = one missing battle)": f"""
        SELECT quantile_cont(abs(next_trophies - (trophies + trophy_change)), [0.25, 0.5, 0.75, 0.9]) AS q25_q50_q75_q90
        FROM seq WHERE next_t IS NOT NULL AND NOT chain_intact AND {STEADY}""",
    "S7. Chain continuity after a WIN vs after a LOSS, within gap bands (steady period)": f"""
        SELECT CASE WHEN gap_min < 10 THEN 'a: <10 min' WHEN gap_min < 60 THEN 'b: 10-60 min'
                    WHEN gap_min < 1440 THEN 'c: 1-24 h' ELSE 'd: 24h+' END AS gap_band,
               CASE WHEN won = 1 THEN 'after win' ELSE 'after loss' END AS prev_outcome,
               count(*) AS pairs, round(100.0 * avg(chain_intact::int), 1) AS pct_chain_intact
        FROM seq WHERE next_t IS NOT NULL AND gap_min > 0 AND {STEADY} GROUP BY 1, 2 ORDER BY 1, 2""",
    "S8. Chain continuity by day (is collection consistent over time?)": """
        SELECT t::DATE AS day_utc, count(*) AS pairs,
               round(100.0 * avg(chain_intact::int), 1) AS pct_chain_intact,
               round(100.0 * avg((gap_min < 10)::int), 1) AS pct_gap_under_10min
        FROM seq WHERE next_t IS NOT NULL GROUP BY 1 ORDER BY 1""",
    "S9. Lengths of unbroken chain runs (a spike at 24 links = 25 battles = one API battle log)": """
        WITH s AS (SELECT tag, rn, chain_intact FROM seq WHERE next_t IS NOT NULL),
        r AS (SELECT tag, chain_intact, rn - row_number() OVER (PARTITION BY tag, chain_intact ORDER BY rn) AS grp FROM s),
        runs AS (SELECT count(*) AS links FROM r WHERE chain_intact GROUP BY tag, grp)
        SELECT links, count(*) AS runs FROM runs WHERE links BETWEEN 20 AND 28 GROUP BY 1 ORDER BY 1""",
}


def null_counts(con) -> pd.Series:
    """Null count per column across all raw rows; only columns with nulls are returned."""
    cols = [r[0] for r in con.execute("DESCRIBE raw").fetchall()]
    sql = "SELECT " + ", ".join(f"count(*) - count({c}) AS {c}" for c in cols) + " FROM raw"
    s = con.sql(sql).df().T[0]
    return s[s > 0]


def run(seq_pct: int) -> str:
    """Run every audit query and return the printed report as a string."""
    con = connect()
    con.execute(f"CREATE MACRO seq_sampled(tag) AS hash(tag) % 100 < {int(seq_pct)}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        print(f"PHASE 1 AUDIT  (battle checks: all rows; sequence checks: {seq_pct}% of players)\n")
        print("B0. Schema")
        print(con.sql("SELECT column_name, column_type FROM (DESCRIBE raw)").df().to_string(index=False), "\n")
        print("B0b. Columns with nulls (all raw rows)")
        print(null_counts(con).to_string(), "\n")
        print(f"B0c. Reference files: {len(pd.read_csv(CARD_CSV, encoding='utf-8-sig'))} cards, "
              f"{len(pd.read_csv(WINCONS_CSV))} win-condition cards\n")
        for title, sql in BATTLE_QUERIES.items():
            print(title)
            out = con.sql(sql).df()
            print(out.to_string(index=False), "\n", flush=True)
            if title.startswith("B6."):
                out.to_csv(OUT_DIR / "phase1_battles_per_day.csv", index=False)  # used by figures.py
        con.execute(PLAYER_BATTLES_SQL)
        con.execute(SEQUENCES_SQL)
        for title, sql in SEQ_QUERIES.items():
            print(title)
            out = con.sql(sql).df()
            print(out.to_string(index=False), "\n", flush=True)
            if title.startswith("S5."):
                out.to_csv(OUT_DIR / "phase1_chain_by_gap.csv", index=False)  # used by figures.py
    return buf.getvalue()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq-sample", type=int, default=10, help="percent of players for sequence checks")
    args = ap.parse_args()
    report = run(args.seq_sample)
    print(report)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"phase1_audit_seq{args.seq_sample}.txt").write_text(report)
