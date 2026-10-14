"""Paths and shared DuckDB setup.

Data lives in ~/cr_data, outside the iCloud-synced Desktop, so 21 GB of CSVs and the
Parquet files are not uploaded or evicted by iCloud while we query them.
Override with the CR_DATA environment variable.
"""
import os
from pathlib import Path

import duckdb

DATA_DIR = Path(os.environ.get("CR_DATA", Path.home() / "cr_data"))
ARCHIVE = DATA_DIR / "archive"
RAW_CSVS = sorted(ARCHIVE.glob("*/*.csv"))  # 10 battle files, one folder each
CARD_CSV = ARCHIVE / "CardMasterListSeason18_12082020.csv"
WINCONS_CSV = ARCHIVE / "Wincons.csv"
PARQUET_DIR = DATA_DIR / "parquet"          # one file per raw CSV, all rows
BATTLES_DIR = DATA_DIR / "battles"          # ladder only, deduplicated (build_battles.py)

PROJECT = Path(__file__).resolve().parents[1]
FIG_DIR = PROJECT / "figures"
OUT_DIR = PROJECT / "outputs"

# Trophy ladder. All three IDs give about +/-29.5 trophies per battle and take turns in
# time without overlapping, so they look like ladder under different rule sets.
# 72000023 (1,611 rows, about +/-23.5 trophies) is excluded.
LADDER_MODES = (72000006, 72000201, 72000044)


def connect(sample_pct: int | None = None) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with the standard views.

    Views and macros created:
      raw         : every row of every raw Parquet file (all modes, with duplicates, source_file)
      battles     : ladder battles, one row per battle key (built by build_battles.py)
      sampled(tag): SQL macro, true if the player is in the sample (always true with no sample)

    sample_pct: if set (1-100), keep a deterministic sample of players chosen by hashing
      their tag, and keep only battles that involve at least one sampled player. Sampling
      players rather than battles keeps each sampled player's full sequence; sampling
      battles would punch extra holes in every player's history. Sequence queries should
      also filter on sampled(tag) so opponents' partial histories are not analyzed.
    """
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("SET memory_limit='5GB'")
    con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    # Large window queries spill to disk; keep the spill files out of iCloud too.
    con.execute(f"SET temp_directory='{DATA_DIR / 'duckdb_tmp'}'")
    con.execute("SET max_temp_directory_size='6GB'")  # fail rather than fill the disk
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{PARQUET_DIR}/*.parquet')")
    pct = 100 if sample_pct is None else int(sample_pct)
    con.execute(f"CREATE MACRO sampled(tag) AS hash(tag) % 100 < {pct}")
    con.execute(f"""
        CREATE VIEW battles AS
        SELECT * FROM read_parquet('{BATTLES_DIR}/*.parquet')
        WHERE sampled(winner_tag) OR sampled(loser_tag)
    """)
    return con
