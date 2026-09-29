"""
Cleans and normalizes the raw Netflix Shows dataset.

Splits the multi-valued fields (cast, director, country, listed_in) into
bridge-table rows, converts dates to ISO format, removes duplicate bridge
rows, and writes two equivalent sets of CSV files:
  data/clean/*.csv        -> for PostgreSQL (numeric IDs)
  data/clean/neo4j/*.csv  -> for Neo4j (names instead of IDs)

Usage (from the repository root):
    python3 scripts/clean_netflix_data.py
"""

import pandas as pd
import os
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent   # repository root
INPUT_CSV = str(ROOT / "data" / "raw" / "netflix_titles.csv")
OUTPUT_DIR = str(ROOT / "data" / "clean")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def clean_split(value):
    """Splits a comma-separated string into a list of trimmed values."""
    if pd.isna(value) or value.strip() == "":
        return []
    return [v.strip() for v in value.split(",") if v.strip() != ""]


def parse_date(value):
    """Converts 'September 25, 2021' -> '2021-09-25'. Returns None if missing or invalid."""
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def main():
    print(f"Reading dataset from: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    print(f"Rows read: {len(df)}")

    # -----------------------------------------------------------------------
    # 1. shows table
    # -----------------------------------------------------------------------
    shows = df[[
        "show_id", "type", "title", "release_year",
        "rating", "duration", "date_added", "description"
    ]].copy()

    shows["date_added"] = shows["date_added"].apply(parse_date)
    shows["title"] = shows["title"].fillna("")
    shows["rating"] = shows["rating"].fillna("")
    shows["duration"] = shows["duration"].fillna("")
    shows["description"] = shows["description"].fillna("")

    shows.to_csv(os.path.join(OUTPUT_DIR, "shows.csv"), index=False)
    print(f"shows.csv -> {len(shows)} rows")

    # -----------------------------------------------------------------------
    # 2. Extract multi-valued entities: people, genres, countries
    #    + relationships: acted_in, directed, listed_in, produced_in
    # -----------------------------------------------------------------------
    people_set = set()
    genres_set = set()
    countries_set = set()

    acted_in_rows = []
    directed_rows = []
    listed_in_rows = []
    produced_in_rows = []

    for _, row in df.iterrows():
        show_id = row["show_id"]

        actors = clean_split(row.get("cast", ""))
        directors = clean_split(row.get("director", ""))
        genres = clean_split(row.get("listed_in", ""))
        countries = clean_split(row.get("country", ""))

        for actor in actors:
            people_set.add(actor)
            acted_in_rows.append((show_id, actor))

        for director in directors:
            people_set.add(director)
            directed_rows.append((show_id, director))

        for genre in genres:
            genres_set.add(genre)
            listed_in_rows.append((show_id, genre))

        for country in countries:
            countries_set.add(country)
            produced_in_rows.append((show_id, country))

    # -----------------------------------------------------------------------
    # 3. Entity tables with sequential IDs (PostgreSQL) + name (Neo4j)
    # -----------------------------------------------------------------------
    people_df = pd.DataFrame(sorted(people_set), columns=["name"])
    people_df.insert(0, "person_id", range(1, len(people_df) + 1))

    genres_df = pd.DataFrame(sorted(genres_set), columns=["name"])
    genres_df.insert(0, "genre_id", range(1, len(genres_df) + 1))

    countries_df = pd.DataFrame(sorted(countries_set), columns=["name"])
    countries_df.insert(0, "country_id", range(1, len(countries_df) + 1))

    people_df.to_csv(os.path.join(OUTPUT_DIR, "people.csv"), index=False)
    genres_df.to_csv(os.path.join(OUTPUT_DIR, "genres.csv"), index=False)
    countries_df.to_csv(os.path.join(OUTPUT_DIR, "countries.csv"), index=False)

    print(f"people.csv -> {len(people_df)} rows")
    print(f"genres.csv -> {len(genres_df)} rows")
    print(f"countries.csv -> {len(countries_df)} rows")

    # name -> id maps, used to build the bridge tables
    person_id_map = dict(zip(people_df["name"], people_df["person_id"]))
    genre_id_map = dict(zip(genres_df["name"], genres_df["genre_id"]))
    country_id_map = dict(zip(countries_df["name"], countries_df["country_id"]))

    # -----------------------------------------------------------------------
    # 4. Bridge tables, PostgreSQL version (with IDs), deduplicated
    # -----------------------------------------------------------------------
    acted_in_df = pd.DataFrame(acted_in_rows, columns=["show_id", "name"]).drop_duplicates()
    acted_in_df["person_id"] = acted_in_df["name"].map(person_id_map)
    acted_in_df[["show_id", "person_id"]].to_csv(
        os.path.join(OUTPUT_DIR, "acted_in.csv"), index=False
    )

    directed_df = pd.DataFrame(directed_rows, columns=["show_id", "name"]).drop_duplicates()
    directed_df["person_id"] = directed_df["name"].map(person_id_map)
    directed_df[["show_id", "person_id"]].to_csv(
        os.path.join(OUTPUT_DIR, "directed.csv"), index=False
    )

    listed_in_df = pd.DataFrame(listed_in_rows, columns=["show_id", "name"]).drop_duplicates()
    listed_in_df["genre_id"] = listed_in_df["name"].map(genre_id_map)
    listed_in_df[["show_id", "genre_id"]].to_csv(
        os.path.join(OUTPUT_DIR, "listed_in.csv"), index=False
    )

    produced_in_df = pd.DataFrame(produced_in_rows, columns=["show_id", "name"]).drop_duplicates()
    produced_in_df["country_id"] = produced_in_df["name"].map(country_id_map)
    produced_in_df[["show_id", "country_id"]].to_csv(
        os.path.join(OUTPUT_DIR, "produced_in.csv"), index=False
    )

    print(f"acted_in.csv -> {len(acted_in_df)} rows")
    print(f"directed.csv -> {len(directed_df)} rows")
    print(f"listed_in.csv -> {len(listed_in_df)} rows")
    print(f"produced_in.csv -> {len(produced_in_df)} rows")

    # -----------------------------------------------------------------------
    # 5. Neo4j version: relationships with show_id + name (no numeric ID needed,
    #    Neo4j MATCHes nodes on show_id / name through uniqueness constraints)
    # -----------------------------------------------------------------------
    neo4j_dir = os.path.join(OUTPUT_DIR, "neo4j")
    os.makedirs(neo4j_dir, exist_ok=True)

    pd.DataFrame(acted_in_rows, columns=["show_id", "person_name"]).drop_duplicates().to_csv(
        os.path.join(neo4j_dir, "acted_in.csv"), index=False
    )
    pd.DataFrame(directed_rows, columns=["show_id", "person_name"]).drop_duplicates().to_csv(
        os.path.join(neo4j_dir, "directed.csv"), index=False
    )
    pd.DataFrame(listed_in_rows, columns=["show_id", "genre_name"]).drop_duplicates().to_csv(
        os.path.join(neo4j_dir, "listed_in.csv"), index=False
    )
    pd.DataFrame(produced_in_rows, columns=["show_id", "country_name"]).drop_duplicates().to_csv(
        os.path.join(neo4j_dir, "produced_in.csv"), index=False
    )

    # shows, people, genres, countries are the same for Neo4j (copied for clarity)
    shows.to_csv(os.path.join(neo4j_dir, "shows.csv"), index=False)
    people_df[["name"]].to_csv(os.path.join(neo4j_dir, "people.csv"), index=False)
    genres_df[["name"]].to_csv(os.path.join(neo4j_dir, "genres.csv"), index=False)
    countries_df[["name"]].to_csv(os.path.join(neo4j_dir, "countries.csv"), index=False)

    print("\nNeo4j files saved to:", neo4j_dir)
    print("\nCleaning completed.")


if __name__ == "__main__":
    main()