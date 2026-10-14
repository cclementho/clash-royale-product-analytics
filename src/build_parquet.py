"""Convert the raw battle CSVs to Parquet with snake_case names and one fixed schema.

Run once:  ~/cr_data/.venv/bin/python src/build_parquet.py

Why Parquet: the 10 CSVs total about 21 GB, and every query against a CSV re-parses all
of it. Parquet is columnar and compressed, so DuckDB reads only the columns a query needs.

Why a fixed schema: type inference runs per file. If one file had, say, a stray text value
in a numeric column, inference would quietly pick a different type for that file. Casting
every file to the same types makes any such problem fail loudly instead.
"""
import re
import time

import duckdb

from config import PARQUET_DIR, RAW_CSVS

VARCHAR_COLS = {"winner_tag", "loser_tag", "winner_clan_tag", "loser_clan_tag", "tournament_tag",
                "winner_princess_towers_hp", "loser_princess_towers_hp",
                "winner_cards_list", "loser_cards_list"}


def snake(col: str) -> str:
    """'winner.card1.level' -> 'winner_card1_level'; the unnamed index column -> 'row_id'."""
    if col in ("", "column00"):
        return "row_id"
    return col.replace(".", "_").replace("average_startingTrophies", "avg_starting_trophies") \
        .replace("startingTrophies", "starting_trophies").replace("trophyChange", "trophy_change") \
        .replace("kingTowerHitPoints", "king_tower_hp").replace("princessTowersHitPoints", "princess_towers_hp") \
        .replace("badgeId", "badge_id").replace("battleTime", "battle_time").replace("gameMode", "game_mode") \
        .replace("tournamentTag", "tournament_tag").replace("totalcard", "total_card") \
        .replace("elixir_average", "elixir_avg")


def canonical_type(name: str) -> str:
    """The type every file is cast to, chosen by column name."""
    if name in VARCHAR_COLS:
        return "VARCHAR"
    if name == "battle_time":
        return "TIMESTAMPTZ"
    if name == "row_id" or re.search(r"card\d_(id|level)$|_count$|total_card_level$", name):
        return "BIGINT"
    return "DOUBLE"


def main() -> None:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='5GB'")
    con.execute("SET preserve_insertion_order=false")
    for csv in RAW_CSVS:
        out = PARQUET_DIR / (csv.stem.lower() + ".parquet")
        if out.exists():
            print(f"skip {out.name} (exists)")
            continue
        src = f"read_csv_auto('{csv}', sample_size=-1)"
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        select = ", ".join(f'CAST("{c}" AS {canonical_type(snake(c))}) AS {snake(c)}' for c in cols)
        t = time.time()
        con.execute(f"""COPY (SELECT {select}, '{csv.stem.lower()}' AS source_file FROM {src})
                        TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
        print(f"wrote {out.name} in {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
