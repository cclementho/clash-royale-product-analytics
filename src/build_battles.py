"""Build the deduplicated ladder battles table (one row per battle) as Parquet.

Run after build_parquet.py:  ~/cr_data/.venv/bin/python src/build_battles.py

Battle key: battle_time plus the unordered pair of player tags. The daily files overlap
by about 3 hours (each starts near 21:00 UTC the day before), so the same battle can be
in two files. For each duplicated key we keep the copy from the earliest file (then the
lowest row_id) and drop the others.

Built for an 8 GB laptop: the duplicate search uses only the key columns, the result is
a small "rows to drop" list, and output is written one source file at a time with an
anti-join against that list. Nothing wide is ever sorted or windowed.
"""
import time

import duckdb

from config import BATTLES_DIR, DATA_DIR, LADDER_MODES, PARQUET_DIR


def main() -> None:
    BATTLES_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("SET memory_limit='5GB'")
    con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{DATA_DIR / 'duckdb_tmp'}'")
    con.execute("SET max_temp_directory_size='6GB'")
    t = time.time()

    # Rank copies of each key by (source_file, row_id); every copy after the first is dropped.
    con.execute(f"""
        CREATE TABLE drop_rows AS
        WITH k AS (
            SELECT source_file, row_id,
                   hash(battle_time, least(winner_tag, loser_tag), greatest(winner_tag, loser_tag)) AS key_hash,
                   battle_time, least(winner_tag, loser_tag) AS a, greatest(winner_tag, loser_tag) AS b
            FROM read_parquet('{PARQUET_DIR}/*.parquet')
            WHERE game_mode_id IN {LADDER_MODES}),
        dup_hash AS (SELECT key_hash FROM k GROUP BY 1 HAVING count(*) > 1),
        cand AS (SELECT k.* FROM k SEMI JOIN dup_hash USING (key_hash))
        SELECT source_file, row_id FROM cand
        QUALIFY row_number() OVER (PARTITION BY battle_time, a, b ORDER BY source_file, row_id) > 1
    """)
    n_drop = con.execute("SELECT count(*) FROM drop_rows").fetchone()[0]
    print(f"duplicate rows to drop: {n_drop:,} ({time.time() - t:.0f}s)", flush=True)

    files = [r[0] for r in con.execute(
        f"SELECT DISTINCT source_file FROM read_parquet('{PARQUET_DIR}/*.parquet') ORDER BY 1").fetchall()]
    total = 0
    for f in files:
        out = BATTLES_DIR / f"{f}.parquet"
        con.execute(f"""
            COPY (
                SELECT r.* FROM read_parquet('{PARQUET_DIR}/{f}.parquet') r
                ANTI JOIN drop_rows d ON r.source_file = d.source_file AND r.row_id = d.row_id
                WHERE r.game_mode_id IN {LADDER_MODES}
            ) TO '{out}' (FORMAT parquet, COMPRESSION zstd)
        """)
        n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
        total += n
        print(f"  {f}: {n:,} battles", flush=True)
    print(f"total battles after dedupe: {total:,} ({time.time() - t:.0f}s)")


if __name__ == "__main__":
    main()
