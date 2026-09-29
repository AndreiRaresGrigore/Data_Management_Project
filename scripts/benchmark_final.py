"""
Unified PostgreSQL vs Neo4j benchmark, with a TRUE cold start for every
query on every system.

Before measuring each query on each system, a full cold reset is run
(see cold_reset.py): both services are stopped, the OS page cache is
flushed, both services are restarted. Run 1 is therefore a genuine cold
start (empty database cache and empty OS cache); runs 2..N are warm.

Measurement is identical for both systems:
  - one fresh connection/session, opened after the reset and reused for
    all N runs of that query
  - every run fully materializes the result on the client
    (fetchall() for PostgreSQL, list() of records for Neo4j)
  - the number of returned rows is recorded, to check that both systems
    return equivalent results

Fairness choices (see report, Methodology):
  - every property used as a search key is indexed in both systems
  - Q4 traverses exactly 3 person-hops in both systems (6 edges in Cypher)
  - Q5 in PostgreSQL uses a breadth-first recursive CTE (UNION, each person
    kept once per level), algorithmically comparable to Neo4j's
    shortestPath(). The naive CTE that enumerates every simple path is
    also measured, as an extra reference (pg_naive_* columns), for
    distances 1-3 only: beyond that it is computationally infeasible.

Queries (with the number of graph edges each one traverses):
  Q1 shows by genre (1) - Q2 cast of a show (1) - Q3 directors who also
  acted (2) - Q4 actors at 3 hops (6) - Q5a-Q5e degrees of separation at
  real distance 1 to 5 (2, 4, 6, 8, 10)

Usage:
    python3 scripts/benchmark_final.py
The sudo password is asked once at the beginning.
"""

import os
import time
from pathlib import Path
import csv
import statistics
import psycopg2
from neo4j import GraphDatabase

from cold_reset import ensure_sudo, full_cold_reset

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
PG_CONFIG = {
    "host": "localhost",
    "dbname": "netflix_db",
    "user": "postgres",
    "password": os.environ.get("PG_PASSWORD", ""),
}

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_AUTH = (NEO4J_USER, NEO4J_PASSWORD)

N_RUNS = 6   # 1 cold + 5 warm
PG_STATEMENT_TIMEOUT_MS = 180_000
ROOT = Path(__file__).resolve().parent.parent   # repository root
OUTPUT_CSV = ROOT / "results" / "benchmark_final_results.csv"


# ---------------------------------------------------------------------------
# QUERY BUILDERS for Q6a/b/c (parametric on people and depth)
# ---------------------------------------------------------------------------

def degrees_sql_naive(person_a, person_b, sql_depth_limit):
    # Enumerates every simple path up to the limit (exponential). Reference only.
    return f"""
        WITH RECURSIVE path AS (
            SELECT ai1.person_id AS current_id, 0 AS depth, ARRAY[ai1.person_id] AS visited
            FROM people p JOIN acted_in ai1 ON p.person_id = ai1.person_id
            WHERE p.name = '{person_a}'
            UNION ALL
            SELECT ai2.person_id, path.depth + 1, visited || ai2.person_id
            FROM path
            JOIN acted_in ai1 ON path.current_id = ai1.person_id
            JOIN acted_in ai2 ON ai1.show_id = ai2.show_id
            WHERE ai2.person_id != ALL(path.visited) AND path.depth < {sql_depth_limit}
        )
        SELECT MIN(depth) AS degrees_of_separation FROM path
        JOIN people p ON path.current_id = p.person_id
        WHERE p.name = '{person_b}';
    """


def degrees_sql_bfs(person_a, person_b, sql_depth_limit):
    # Breadth-first search: UNION discards rows already produced, so each
    # person appears at most once per level. Main PostgreSQL implementation.
    return f"""
        WITH RECURSIVE bfs(person_id, depth) AS (
            SELECT person_id, 0 FROM people WHERE name = '{person_a}'
            UNION
            SELECT ai2.person_id, b.depth + 1
            FROM bfs b
            JOIN acted_in ai1 ON ai1.person_id = b.person_id
            JOIN acted_in ai2 ON ai2.show_id = ai1.show_id
            WHERE b.depth < {sql_depth_limit}
        )
        SELECT MIN(depth) AS degrees_of_separation FROM bfs
        JOIN people p ON p.person_id = bfs.person_id
        WHERE p.name = '{person_b}';
    """


def degrees_cypher(person_a, person_b, sql_depth_limit):
    # person-hop -> Cypher edge conversion (factor 2, empirically verified)
    cypher_depth = sql_depth_limit * 2
    return f"""
        MATCH path = shortestPath(
            (p1:Person {{name: '{person_a}'}})-[:ACTED_IN*..{cypher_depth}]-(p2:Person {{name: '{person_b}'}})
        )
        RETURN length(path) AS degrees_of_separation;
    """


# ---------------------------------------------------------------------------
# QUERY SET: (name, edges, sql, cypher[, naive_sql])
# 'edges' = number of graph edges the query traverses (path length in Cypher).
# Queries are ordered from simple lookups to deep traversals.
# ---------------------------------------------------------------------------
QUERIES = [
    (
        "Q1_shows_by_genre", 1,
        """SELECT s.title, s.release_year FROM shows s
           JOIN listed_in li ON s.show_id = li.show_id
           JOIN genres g ON li.genre_id = g.genre_id
           WHERE g.name = 'Documentaries';""",
        """MATCH (s:Show)-[:LISTED_IN]->(g:Genre {name: 'Documentaries'})
           RETURN s.title, s.release_year;""",
    ),
    (
        "Q2_cast_of_show", 1,
        """SELECT p.name FROM acted_in ai
           JOIN people p ON ai.person_id = p.person_id
           JOIN shows s ON ai.show_id = s.show_id
           WHERE s.title = 'Breaking Bad';""",
        """MATCH (p:Person)-[:ACTED_IN]->(s:Show {title: 'Breaking Bad'})
           RETURN p.name;""",
    ),
    (
        "Q3_directors_who_also_acted", 2,
        """SELECT DISTINCT p.name, s.title FROM directed d
           JOIN acted_in a ON d.show_id = a.show_id AND d.person_id = a.person_id
           JOIN people p ON d.person_id = p.person_id
           JOIN shows s ON d.show_id = s.show_id;""",
        """MATCH (p:Person)-[:DIRECTED]->(s:Show)<-[:ACTED_IN]-(p)
           RETURN DISTINCT p.name, s.title;""",
    ),
    (
        "Q4_actors_at_3_hops", 6,
        """SELECT DISTINCT p3.name FROM people p1
           JOIN acted_in ai1 ON p1.person_id = ai1.person_id
           JOIN acted_in ai2 ON ai1.show_id = ai2.show_id AND ai1.person_id != ai2.person_id
           JOIN acted_in ai3 ON ai2.person_id = ai3.person_id AND ai3.show_id != ai1.show_id
           JOIN acted_in ai4 ON ai3.show_id = ai4.show_id AND ai4.person_id != ai3.person_id
           JOIN acted_in ai5 ON ai4.person_id = ai5.person_id AND ai5.show_id != ai3.show_id
           JOIN acted_in ai6 ON ai5.show_id = ai6.show_id AND ai6.person_id != ai5.person_id
           JOIN people p3 ON ai6.person_id = p3.person_id
           WHERE p1.name = 'Leonardo DiCaprio' AND p3.person_id != p1.person_id;""",
        """MATCH (p1:Person {name: 'Leonardo DiCaprio'})-[:ACTED_IN*6]-(p3:Person)
           WHERE p1 <> p3 RETURN DISTINCT p3.name;""",
    ),
    (
        "Q5a_degrees_of_separation_dist1", 2,
        degrees_sql_bfs("Samuel L. Jackson", "Robert De Niro", 1),
        degrees_cypher("Samuel L. Jackson", "Robert De Niro", 1),
        degrees_sql_naive("Samuel L. Jackson", "Robert De Niro", 1),
    ),
    (
        "Q5b_degrees_of_separation_dist2", 4,
        degrees_sql_bfs("Morgan Freeman", "Sandra Bullock", 2),
        degrees_cypher("Morgan Freeman", "Sandra Bullock", 2),
        degrees_sql_naive("Morgan Freeman", "Sandra Bullock", 2),
    ),
    (
        "Q5c_degrees_of_separation_dist3", 6,
        degrees_sql_bfs("Ricky Gervais", "Rowan Atkinson", 3),
        degrees_cypher("Ricky Gervais", "Rowan Atkinson", 3),
        degrees_sql_naive("Ricky Gervais", "Rowan Atkinson", 3),
    ),
    # Distances 4 and 5: the naive CTE is not measured (at depth 4 it already
    # exhausted disk space in an earlier run), only BFS vs shortestPath.
    (
        "Q5d_degrees_of_separation_dist4", 8,
        degrees_sql_bfs("Tom Hanks", "Audrey Hepburn", 4),
        degrees_cypher("Tom Hanks", "Audrey Hepburn", 4),
    ),
    (
        "Q5e_degrees_of_separation_dist5", 10,
        degrees_sql_bfs("Tom Cruise", "Audrey Hepburn", 5),
        degrees_cypher("Tom Cruise", "Audrey Hepburn", 5),
    ),
]


# ---------------------------------------------------------------------------
# MEASUREMENT (identical structure for both systems)
# ---------------------------------------------------------------------------

def measure_postgres(sql, n_runs):
    """Cold reset must already be done. Opens one fresh connection."""
    conn = psycopg2.connect(**PG_CONFIG)
    conn.autocommit = True   # each query runs in its own transaction
    cur = conn.cursor()
    cur.execute(f"SET statement_timeout = {PG_STATEMENT_TIMEOUT_MS};")
    times, n_rows = [], None
    try:
        for i in range(n_runs):
            start = time.perf_counter()
            cur.execute(sql)
            rows = cur.fetchall()                    # full materialization
            times.append((time.perf_counter() - start) * 1000)
            n_rows = len(rows)
            tag = "COLD" if i == 0 else "warm"
            print(f"  PostgreSQL [{tag}] run {i+1}: {times[-1]:.2f} ms")
    finally:
        cur.close()
        conn.close()
    return times, n_rows


def measure_neo4j(cypher, n_runs):
    """Cold reset must already be done. Opens one fresh driver and session."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    times, n_rows = [], None
    try:
        with driver.session() as session:
            for i in range(n_runs):
                start = time.perf_counter()
                records = list(session.run(cypher))  # full materialization
                times.append((time.perf_counter() - start) * 1000)
                n_rows = len(records)
                tag = "COLD" if i == 0 else "warm"
                print(f"  Neo4j      [{tag}] run {i+1}: {times[-1]:.2f} ms")
    finally:
        driver.close()
    return times, n_rows


def summarize(times):
    cold, warm = times[0], times[1:]
    return (
        round(cold, 2),
        round(statistics.mean(warm), 2) if warm else None,
        round(statistics.median(warm), 2) if warm else None,
    )


def main():
    ensure_sudo()
    n_resets = sum(3 if len(q) == 5 else 2 for q in QUERIES)
    print(f"\n{len(QUERIES)} queries x 2 systems = {n_resets} cold resets "
          f"(roughly 20-40 s each, plus query time).\n")

    results = []
    for q in QUERIES:
        name, edges, sql, cypher = q[:4]
        naive_sql = q[4] if len(q) == 5 else None
        print(f"\n=== {name} ({edges} edges) ===")
        row = {"query": name, "edges": edges}

        try:
            full_cold_reset(PG_CONFIG, NEO4J_URI, NEO4J_AUTH)
            pg_times, pg_rows = measure_postgres(sql, N_RUNS)
            row["pg_cold_ms"], row["pg_warm_avg_ms"], row["pg_warm_median_ms"] = summarize(pg_times)
            row["pg_rows"] = pg_rows
        except Exception as e:
            print(f"  ERROR (PostgreSQL) on {name}: {e}")

        if naive_sql is not None:
            print("  -- PostgreSQL, naive CTE (reference) --")
            try:
                full_cold_reset(PG_CONFIG, NEO4J_URI, NEO4J_AUTH)
                nv_times, nv_rows = measure_postgres(naive_sql, N_RUNS)
                (row["pg_naive_cold_ms"], row["pg_naive_warm_avg_ms"],
                 row["pg_naive_warm_median_ms"]) = summarize(nv_times)
                row["pg_naive_rows"] = nv_rows
            except Exception as e:
                print(f"  ERROR (PostgreSQL naive) on {name}: {e}")

        try:
            full_cold_reset(PG_CONFIG, NEO4J_URI, NEO4J_AUTH)
            neo_times, neo_rows = measure_neo4j(cypher, N_RUNS)
            row["neo4j_cold_ms"], row["neo4j_warm_avg_ms"], row["neo4j_warm_median_ms"] = summarize(neo_times)
            row["neo4j_rows"] = neo_rows
        except Exception as e:
            print(f"  ERROR (Neo4j) on {name}: {e}")

        if row.get("pg_rows") is not None and row.get("neo4j_rows") is not None:
            if row["pg_rows"] != row["neo4j_rows"]:
                print(f"  WARNING: row count mismatch "
                      f"(PostgreSQL {row['pg_rows']} vs Neo4j {row['neo4j_rows']})")
        results.append(row)

    fieldnames = [
        "query", "edges",
        "pg_cold_ms", "pg_warm_avg_ms", "pg_warm_median_ms", "pg_rows",
        "neo4j_cold_ms", "neo4j_warm_avg_ms", "neo4j_warm_median_ms", "neo4j_rows",
        "pg_naive_cold_ms", "pg_naive_warm_avg_ms", "pg_naive_warm_median_ms", "pg_naive_rows",
    ]
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n\nResults saved to: {OUTPUT_CSV}")
    print("\n--- SUMMARY (warm median) ---")
    for r in results:
        print(f"{r['query']:35s} | PG: {r.get('pg_warm_median_ms')} ms "
              f"({r.get('pg_rows')} rows) | Neo4j: {r.get('neo4j_warm_median_ms')} ms "
              f"({r.get('neo4j_rows')} rows)")


if __name__ == "__main__":
    main()
