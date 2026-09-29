# PostgreSQL vs Neo4j on the Netflix Dataset — Project Documentation

This document walks through every phase of the project in the order it is executed, explaining what each step does and why it was designed that way. Every benchmark query is shown in both SQL and Cypher, with a description of what it asks and how each engine answers it.

**Assignment.** Select a dataset, a graph database tool, and a relational DBMS, and compare the results of analyses on the different systems, highlighting advantages and disadvantages of each approach in terms of efficiency. The focus is the ability to compare different technologies.

**Guiding principle.** The comparison is only meaningful if both systems hold the same data, answer the same questions, have equivalent indexing, and are measured in the same way. Most design choices below exist to guarantee one of these four conditions.

---

## 1. Project Layout

| Path | Role | Output |
|---|---|---|
| `data/raw/netflix_titles.csv` | Raw Kaggle dataset | — |
| `scripts/clean_netflix_data.py` | Cleans and normalizes the raw CSV | `data/clean/*.csv`, `data/clean/neo4j/*.csv` |
| `database/import_postgres.sql` | Relational schema, indexes, data load, statistics | populated `netflix_db` |
| `database/import_neo4j.cypher` | Constraints, indexes, nodes, relationships | populated graph |
| `scripts/cold_reset.py` | Clears every cache layer before a measurement | — |
| `scripts/benchmark_final.py` | Main benchmark, 9 queries on both systems | `results/benchmark_final_results.csv` |
| `scripts/generate_bar_chart.py`, `scripts/generate_line_chart.py` | Charts of the main benchmark | `figures/final_bar_chart.png`, `figures/final_line_chart.png` |

All scripts resolve paths from the repository root, so they can be launched from any folder. Database passwords are read from the `PG_PASSWORD` and `NEO4J_PASSWORD` environment variables and are never stored in the code.

---

## 2. Environment

PostgreSQL 16 and Neo4j were installed natively on Ubuntu 24.04 rather than in containers, so that measurements carry no virtualization overhead. Neo4j requires Java 21; with the preinstalled Java 17 the service started and immediately stopped, which was resolved by installing OpenJDK 21 and selecting it as the default.

Python runs inside a virtual environment (`venv`) so that `pandas`, `psycopg2` and the `neo4j` driver do not interfere with the system Python, which Ubuntu 24.04 protects by default.

The graphical tools (pgAdmin, Neo4j Browser) are used only to inspect data and schemas. They are never used for timing, because early tests showed that Neo4j Browser adds a roughly constant 50–100 ms of HTTP and rendering overhead, which hid the real differences on fast queries. All measurements go through the native drivers from Python.

---

## 3. The Dataset

The Netflix Movies and TV Shows dataset contains 8,807 titles with the columns `show_id, type, title, director, cast, country, date_added, release_year, rating, duration, listed_in, description`.

The file is a single denormalized table. The fields `cast`, `director`, `country` and `listed_in` (genres) hold several comma-separated values in one cell, for example:

```
s2,TV Show,Blood & Water,,"Ama Qamata, Khosi Ngema, Gail Mabalane, ...",South Africa,...
```

Neither a relational database nor a graph database can use this directly, so the first phase is normalization.

The dataset was chosen because it is naturally relational (titles, people, genres, countries) but also densely interconnected: thousands of actors share titles, which creates long chains of indirect connections. This makes it a fair ground for both paradigms.

---

## 4. Phase 1 — Data Cleaning (`scripts/clean_netflix_data.py`)

The script reads the raw CSV and produces two equivalent sets of files.

**Splitting multi-valued fields.** Each comma-separated cell is split into separate values, and each value becomes one row of a bridge table. The cast of one title becomes several `(show_id, person)` rows.

**Date conversion.** `"September 25, 2021"` becomes `2021-09-25`, so that PostgreSQL can store it as a `DATE`.

**Deduplication.** In a few titles the original data lists the same actor twice in the same cast cell. After splitting, this produces two identical `(show_id, person_id)` rows. The first import attempt failed with:

```
ERROR: duplicate key value violates unique constraint "acted_in_pkey"
DETAIL: Key (show_id, person_id)=(s1632, 25504) already exists.
```

`drop_duplicates()` keeps one copy. The effect is small (`acted_in` goes from 64,126 to 64,124 rows, `directed` from 6,978 to 6,977) but it matters twice: it prevents the PostgreSQL error, and it prevents Neo4j from silently creating two identical `ACTED_IN` edges, which would make the two systems' counts diverge.

**Two output formats.** The same data is written twice, because the two systems link entities differently.

`data/clean/acted_in.csv` (PostgreSQL) identifies people by numeric ID:
```
show_id,person_id
s2,1829
s2,20390
```

`data/clean/neo4j/acted_in.csv` (Neo4j) identifies people by name:
```
show_id,person_name
s2,Ama Qamata
s2,Khosi Ngema
```

PostgreSQL uses foreign keys, which point to a numeric primary key in another table. Neo4j has no foreign keys: to create an edge it must first find the two nodes, and in this model a `Person` node is uniquely identified by its `name`. The difference in the CSV files is a direct consequence of the two data models. `shows.csv` is identical in both, because `show_id` already exists in the source data and serves as key in both systems.

**Expected output:** 8,807 shows, 40,948 people, 42 genres, 122 countries, 64,124 `acted_in`, 6,977 `directed`, 19,323 `listed_in`, 10,012 `produced_in`.

---

## 5. Phase 2 — Relational Model (`database/import_postgres.sql`)

### Schema

```sql
CREATE TABLE shows (
    show_id VARCHAR(50) PRIMARY KEY, type VARCHAR(50), title TEXT,
    release_year INT, rating VARCHAR(50), duration VARCHAR(50),
    date_added DATE, description TEXT
);
CREATE TABLE people (person_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);
CREATE TABLE genres (genre_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);
CREATE TABLE countries (country_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);

CREATE TABLE acted_in (
    show_id VARCHAR(50) REFERENCES shows(show_id) ON DELETE CASCADE,
    person_id INT REFERENCES people(person_id) ON DELETE CASCADE,
    PRIMARY KEY (show_id, person_id)
);
-- directed, listed_in, produced_in follow the same pattern
```

The schema is in third normal form: four entity tables and four bridge tables for the many-to-many relationships.

**One `people` table for actors and directors.** Many people both act and direct, sometimes in the same title. A single table avoids duplicating them, and the role is expressed by which bridge table (`acted_in` or `directed`) links them. This mirrors the graph model, where one `Person` node has either `ACTED_IN` or `DIRECTED` edges.

**Composite primary keys** such as `(show_id, person_id)` forbid the same pair twice: a person acts in a given title only once.

**Foreign keys (`REFERENCES`)** make PostgreSQL reject a row that points to a non-existent show or person. This is native referential integrity, which Neo4j does not provide.

**`ON DELETE CASCADE`** removes bridge rows automatically when their show is deleted.

### Data loading

```sql
\copy shows(show_id, type, title, release_year, rating, duration, date_added, description)
  FROM './data/clean/shows.csv' WITH (FORMAT csv, HEADER true, NULL '');
\copy people(person_id, name) FROM './data/clean/people.csv' WITH (FORMAT csv, HEADER true);
-- ... one \copy per table ...
ANALYZE;
```

Entity tables are loaded before bridge tables, otherwise the foreign keys would reject rows pointing to data not yet loaded.

`\copy` (with the backslash) reads the file from the client side, as the current user. Running the script as `sudo -u postgres` failed with "Permission denied", because the `postgres` system user cannot enter the project folder inside the user's home. The script is therefore run as `psql -U postgres -h localhost`, from the repository root, since the paths are relative.

`NULL ''` turns empty cells into `NULL`, needed because some titles have no `date_added`.

`ANALYZE` computes the table statistics that the query planner uses to choose execution plans. After a bulk load the statistics are missing or wrong. This was verified experimentally: before running `ANALYZE`, one query took about 64 ms with 19 ms of planning; afterwards it took about 32 ms with 4.6 ms of planning. Skipping this step would have penalized PostgreSQL unfairly.

---

## 6. Indexing Strategy

### Why indexes

Without an index, finding rows that match a condition requires reading the whole table (a *sequential scan*). An index is an ordered structure (a B-tree in PostgreSQL) that lets the engine jump directly to the matching rows (an *index scan*). Indexes do not change what a query returns, only how fast it is found. Their cost is extra disk space and slower writes, since every insert must update them, so only columns actually used for searching are indexed.

### The direction problem

A primary key automatically creates an index. But an index on `(show_id, person_id)` is ordered by `show_id` first. It is efficient for "the actors of a given show" and useless for "the shows of a given actor", which is exactly the direction used by multi-hop queries: they jump from a person to their shows and from those shows to other people. Dedicated indexes on the second column are therefore needed.

### PostgreSQL indexes

```sql
CREATE INDEX idx_listed_in_genre ON listed_in(genre_id);
CREATE INDEX idx_acted_in_person ON acted_in(person_id);
CREATE INDEX idx_directed_person ON directed(person_id);
CREATE INDEX idx_produced_in_country ON produced_in(country_id);
CREATE INDEX idx_shows_title ON shows(title);
CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_genres_name ON genres(name);
CREATE INDEX idx_countries_name ON countries(name);
```

| Search | Index used |
|---|---|
| Actors / director of a show | primary key `(show_id, person_id)`, automatic |
| Shows of an actor or director | `idx_acted_in_person`, `idx_directed_person` |
| Shows of a genre / country | `idx_listed_in_genre`, `idx_produced_in_country` |
| Show by title | `idx_shows_title` |
| Person, genre, country by name | `idx_people_name`, `idx_genres_name`, `idx_countries_name` |

### Symmetry between the two systems

In Neo4j, every uniqueness constraint automatically creates an index, so `Person.name`, `Genre.name`, `Country.name` and `Show.show_id` are indexed by construction. An audit of the first version of the project found two asymmetries:

- PostgreSQL had no index on `people.name`, so every query starting from an actor's name scanned all 40,948 people. This penalized PostgreSQL on the multi-hop and pathfinding queries.
- Neo4j had no index on `Show.title`, so the "cast of a show" query scanned all 8,807 shows. This penalized Neo4j.

The rule adopted is: **every property used as a search key is indexed in both systems.** The last three PostgreSQL indexes above and the `show_title` index in Neo4j were added for this reason. The effect can be checked directly:

```sql
EXPLAIN SELECT * FROM people WHERE name = 'Ricky Gervais';
-- Index Scan using idx_people_name on people
```
```cypher
PROFILE MATCH (s:Show {title: 'Breaking Bad'}) RETURN s;
// NodeIndexSeek ... Indexes Used: show_title
```

---

## 7. Phase 3 — Graph Model (`database/import_neo4j.cypher`)

### Model

Nodes: `Show`, `Person`, `Genre`, `Country`.
Relationships: `(Person)-[:ACTED_IN]->(Show)`, `(Person)-[:DIRECTED]->(Show)`, `(Show)-[:LISTED_IN]->(Genre)`, `(Show)-[:PRODUCED_IN]->(Country)`.

The four relational bridge tables become four relationship types. There is no intermediate table: an edge is a physical object connecting two nodes directly. This is the structural difference the whole project measures.

### Constraints and indexes

```cypher
CREATE CONSTRAINT show_id_unique IF NOT EXISTS FOR (s:Show) REQUIRE s.show_id IS UNIQUE;
CREATE CONSTRAINT person_name_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE;
CREATE CONSTRAINT genre_name_unique IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE;
CREATE CONSTRAINT country_name_unique IF NOT EXISTS FOR (c:Country) REQUIRE c.name IS UNIQUE;
CREATE INDEX show_title IF NOT EXISTS FOR (s:Show) ON (s.title);
```

The constraints are created before loading any data. The relationship import looks up two nodes for each of about 100,000 rows; without indexes each lookup would scan every node, and the import would take hours instead of minutes.

### Loading

```cypher
LOAD CSV WITH HEADERS FROM 'file:///people.csv' AS row
MERGE (p:Person {name: row.name});

LOAD CSV WITH HEADERS FROM 'file:///acted_in.csv' AS row
MATCH (s:Show {show_id: row.show_id}), (p:Person {name: row.person_name})
MERGE (p)-[:ACTED_IN]->(s);
```

For security, Neo4j reads CSV files only from its own import folder, so the files are first copied into `/var/lib/neo4j/import/` and made readable with `chmod 644`.

Nodes are loaded before relationships, because the `MATCH` must find existing nodes.

`MERGE` is used instead of `CREATE`. `MERGE` creates an element only if it does not exist yet, so the script can be re-run safely. In an early version that used `CREATE`, running the relationship import twice doubled every edge count.

### Index-free adjacency

Once a start node is found through an index, Neo4j no longer needs indexes: each node stores direct physical pointers to its own relationships. Moving from a person to their shows, and from a show to its actors, means following pointers. The cost of each hop depends on how many edges that node has, not on the total size of the graph. In PostgreSQL each hop is a join that consults an index over the whole bridge table.

---

## 8. Phase 4 — Consistency Check

Before any measurement, the counts are compared across the two systems:

```sql
SELECT COUNT(*) FROM shows;      -- 8807
SELECT COUNT(*) FROM acted_in;   -- 64124
```
```cypher
MATCH (n) RETURN labels(n)[0] AS type, count(*) ORDER BY type;
MATCH ()-[r]->() RETURN type(r) AS rel, count(*) ORDER BY rel;
```

Every entity and relationship count matches exactly: 8,807 titles, 40,948 people, 42 genres, 122 countries, 64,124 / 6,977 / 19,323 / 10,012 relationships.

---

## 9. Phase 5 — Benchmark Methodology

### Truly cold measurements (`scripts/cold_reset.py`)

A genuine cold start requires clearing three layers of memory, not just restarting the services:

1. PostgreSQL's internal cache (`shared_buffers`)
2. Neo4j's internal page cache and JVM state
3. The operating-system page cache, which keeps the database files in RAM even after a service restart

Before measuring each query on each system, the script stops both services, flushes the OS page cache while they are down (`echo 3 > /proc/sys/vm/drop_caches`), restarts them, and waits until they accept connections. The readiness check only opens a connection and never queries the data, so it does not reload anything into memory. The reset is repeated between PostgreSQL and Neo4j on the same query, otherwise the second system would find memory in a different state than the first.

### Runs and statistics

Each query runs six times per system: run 1 is the cold start, runs 2 to 6 are warm. The **median** of the warm runs is used in charts and tables, because the mean is sensitive to occasional outliers (in one earlier run a single execution took 7.2 s against a typical 4.5 s).

### Identical measurement for both systems

| Aspect | PostgreSQL | Neo4j |
|---|---|---|
| Connection | one fresh connection after the reset, reused for the 6 runs | one fresh driver and session after the reset, reused for the 6 runs |
| Result handling | `fetchall()`, full materialization | `list(session.run(...))`, full materialization |
| Transactions | `autocommit`, one transaction per query | one auto-commit transaction per query |
| Timing | `time.perf_counter()` around execution and fetch | same |

An earlier version fetched all PostgreSQL rows but discarded Neo4j rows with `consume()`, and opened a new Neo4j session at every run. Both asymmetries were removed.

The number of rows returned by each system is saved, and the script prints a warning if they differ. PostgreSQL queries have a 180-second safety timeout.

**Known asymmetry, declared rather than removed:** Neo4j caches the execution plan after the first run, while PostgreSQL re-plans every query string on every execution. Planning time (1–3 ms) is therefore included in PostgreSQL's warm times. This reflects how an application would normally submit queries, but it weighs relatively more on the fastest queries.

---

## 10. The Benchmark Queries

The queries are ordered from simple lookups to deep traversals. Instead of grouping them into categories, each query is described by an objective property: **the number of graph edges it traverses** (the length of its pattern in Cypher). This is the variable that drives the difference between the two systems: every edge crossed is one more join for PostgreSQL, and one more pointer to follow for Neo4j.

An earlier version of the project grouped the queries into three categories (A, B, C). They were dropped because their boundaries had been chosen after seeing the results, to produce a three-stage narrative, and did not add understanding beyond what the edge count already expresses.

| Query | What it asks | Edges traversed |
|---|---|---|
| Q1 | Titles of a genre | 1 |
| Q2 | Cast of a show | 1 |
| Q3 | Directors who also acted in their own title | 2 |
| Q4 | Actors reachable in exactly 3 co-acting hops | 6 |
| Q5a–Q5e | Degrees of separation at real distance 1 to 5 | 2, 4, 6, 8, 10 |

Q4 and Q5 differ in kind as well as in depth: Q4 enumerates *every* actor at a fixed distance, while Q5 searches for *one* shortest path and can stop as soon as it finds it. This is why Q4 at 6 edges can be heavier than Q5c at 6 edges.

### Simple lookups and joins

#### Q1 — Shows by genre

**What it asks:** all titles belonging to the genre "Documentaries" (869 rows).

```sql
SELECT s.title, s.release_year FROM shows s
JOIN listed_in li ON s.show_id = li.show_id
JOIN genres g ON li.genre_id = g.genre_id
WHERE g.name = 'Documentaries';
```
```cypher
MATCH (s:Show)-[:LISTED_IN]->(g:Genre {name: 'Documentaries'})
RETURN s.title, s.release_year;
```

**How it works.** SQL needs three tables: `genres` to turn the name into an ID, `listed_in` to find the matching shows, `shows` to get their titles. PostgreSQL finds the genre through `idx_genres_name`, the 869 bridge rows through `idx_listed_in_genre`, and each show through its primary key. In Cypher the genre is a node: Neo4j finds it through the constraint index and follows its incoming `LISTED_IN` edges. This is a single-hop query with a selective filter, the terrain where a mature relational optimizer is strongest.

#### Q2 — Cast of a show

**What it asks:** the actors of "Breaking Bad" (10 rows).

```sql
SELECT p.name FROM acted_in ai
JOIN people p ON ai.person_id = p.person_id
JOIN shows s ON ai.show_id = s.show_id
WHERE s.title = 'Breaking Bad';
```
```cypher
MATCH (p:Person)-[:ACTED_IN]->(s:Show {title: 'Breaking Bad'})
RETURN p.name;
```

**How it works.** Both systems find the show through their title index (`idx_shows_title`, `show_title`), then its actors: PostgreSQL through the `acted_in` primary key and a lookup in `people` for each name, Neo4j by following the show's incoming `ACTED_IN` edges. The Cypher pattern reads almost like the question itself, while SQL has to rebuild the link through the bridge table.

#### Q3 — Directors who also acted

**What it asks:** people who both directed and acted in the same title (355 rows).

```sql
SELECT DISTINCT p.name, s.title FROM directed d
JOIN acted_in a ON d.show_id = a.show_id AND d.person_id = a.person_id
JOIN people p ON d.person_id = p.person_id
JOIN shows s ON d.show_id = s.show_id;
```
```cypher
MATCH (p:Person)-[:DIRECTED]->(s:Show)<-[:ACTED_IN]-(p)
RETURN DISTINCT p.name, s.title;
```

**How it works.** The key condition is "same show *and* same person". In SQL it is written as a join on both columns; joining on `show_id` alone would return every director-actor combination of a show, which is a common mistake. In Cypher the constraint is expressed by reusing the variable `p` at both ends of the pattern. There is no filter, so both systems scan the whole directing relationship set: PostgreSQL through a hash join between `directed` and `acted_in`, Neo4j by expanding every `DIRECTED` edge and checking for a matching `ACTED_IN` edge.

### Multi-hop enumeration at fixed depth

#### Q4 — Actors at 3 hops

**What it asks:** every actor reachable from Leonardo DiCaprio through exactly three co-acting links: DiCaprio → a co-star → a co-star of theirs → a co-star of theirs.

```sql
SELECT DISTINCT p3.name FROM people p1
JOIN acted_in ai1 ON p1.person_id = ai1.person_id
JOIN acted_in ai2 ON ai1.show_id = ai2.show_id AND ai1.person_id != ai2.person_id
JOIN acted_in ai3 ON ai2.person_id = ai3.person_id AND ai3.show_id != ai1.show_id
JOIN acted_in ai4 ON ai3.show_id = ai4.show_id AND ai4.person_id != ai3.person_id
JOIN acted_in ai5 ON ai4.person_id = ai5.person_id AND ai5.show_id != ai3.show_id
JOIN acted_in ai6 ON ai5.show_id = ai6.show_id AND ai6.person_id != ai5.person_id
JOIN people p3 ON ai6.person_id = p3.person_id
WHERE p1.name = 'Leonardo DiCaprio' AND p3.person_id != p1.person_id;
```
```cypher
MATCH (p1:Person {name: 'Leonardo DiCaprio'})-[:ACTED_IN*6]-(p3:Person)
WHERE p1 <> p3 RETURN DISTINCT p3.name;
```

**How it works.** Each hop between two people passes through a show, so it costs two uses of `acted_in` in SQL (person → show, show → person) and two edges in Cypher. Three hops means six joins in SQL and `*6` in Cypher. The `!=` conditions prevent the query from immediately going back to the show or person it came from. Each join multiplies the number of intermediate rows, which is why this query is much heavier than Q1–Q3.

**Design correction.** An earlier version used only four joins in SQL (two hops) while Cypher used `*6` (three hops), so the two systems answered different questions: 2,760 actors in SQL against about 13,000 in Cypher. The SQL was extended to six joins, and now returns 13,326 actors.

The two formulations are very close but not identical: Cypher forbids reusing the same edge anywhere in the path, while the SQL conditions only check adjacent steps. On this dataset the difference has no effect: both systems return exactly 13,326 actors.

### Degrees of separation (shortest path)

**What it asks:** the minimum number of co-acting links connecting two actors. The query is run on five actor pairs, chosen so that their real distance is exactly 1, 2, 3, 4 and 5.

| Query | Pair | Real distance |
|---|---|---|
| Q5a | Samuel L. Jackson ↔ Robert De Niro | 1 |
| Q5b | Morgan Freeman ↔ Sandra Bullock | 2 |
| Q5c | Ricky Gervais ↔ Rowan Atkinson | 3 |
| Q5d | Tom Hanks ↔ Audrey Hepburn | 4 |
| Q5e | Tom Cruise ↔ Audrey Hepburn | 5 |

Distances were computed on the actual data with a breadth-first search over the co-acting graph. For each pair it was checked that no path exists one level below the stated distance. Distances 4 and 5 connect classic Hollywood (Audrey Hepburn), which in this catalogue is linked to modern actors only through long chains.

#### Cypher

```cypher
MATCH path = shortestPath(
    (p1:Person {name: 'Ricky Gervais'})-[:ACTED_IN*..6]-(p2:Person {name: 'Rowan Atkinson'})
)
RETURN length(path) AS degrees_of_separation;
```

`shortestPath()` is a native bidirectional breadth-first search: it expands simultaneously from both actors and stops as soon as the two frontiers meet.

**Unit conversion.** `length(path)` counts edges, and each person-to-person hop is two edges (Person → Show → Person). A distance of 3 in SQL therefore corresponds to 6 in Cypher, and the Cypher limit is always twice the SQL one (`*..2`, `*..4`, … `*..10`). This was verified in two ways: the Neo4j Cypher Manual defines the length of a path as its number of relationships, and on 15 control pairs the relation "Cypher = 2 × SQL" held in all 9 cases where both systems found a path.

#### SQL, main implementation: breadth-first recursive CTE

```sql
WITH RECURSIVE bfs(person_id, depth) AS (
    SELECT person_id, 0 FROM people WHERE name = 'Ricky Gervais'
    UNION
    SELECT ai2.person_id, b.depth + 1
    FROM bfs b
    JOIN acted_in ai1 ON ai1.person_id = b.person_id
    JOIN acted_in ai2 ON ai2.show_id = ai1.show_id
    WHERE b.depth < 3
)
SELECT MIN(depth) AS degrees_of_separation FROM bfs
JOIN people p ON p.person_id = bfs.person_id
WHERE p.name = 'Rowan Atkinson';
```

A recursive CTE has a base case (the starting actor at depth 0) and a recursive step that is repeated on its own output: from every person reached so far, it finds their shows and then every actor of those shows, one level deeper. The recursion stops at the depth limit, and the final `SELECT` returns the smallest depth at which the target actor appears. `UNION` (instead of `UNION ALL`) discards rows already produced, so each person is kept at most once per level and the cost grows with the number of reachable people, not with the number of paths. This makes it algorithmically comparable to Neo4j's breadth-first search.

#### SQL, reference implementation: naive recursive CTE

```sql
WITH RECURSIVE path AS (
    SELECT ai1.person_id AS current_id, 0 AS depth, ARRAY[ai1.person_id] AS visited
    FROM people p JOIN acted_in ai1 ON p.person_id = ai1.person_id
    WHERE p.name = 'Ricky Gervais'
    UNION ALL
    SELECT ai2.person_id, path.depth + 1, visited || ai2.person_id
    FROM path
    JOIN acted_in ai1 ON path.current_id = ai1.person_id
    JOIN acted_in ai2 ON ai1.show_id = ai2.show_id
    WHERE ai2.person_id != ALL(path.visited) AND path.depth < 3
)
SELECT MIN(depth) AS degrees_of_separation FROM path
JOIN people p ON path.current_id = p.person_id
WHERE p.name = 'Rowan Atkinson';
```

This version carries a `visited` array for every path to avoid cycles, and uses `UNION ALL`. It enumerates **every simple path** up to the limit, and the number of paths grows exponentially with depth. It also has no way to stop once the target is found. It was the first implementation used in the project.

**Why both are measured.** An audit showed that comparing the naive CTE with `shortestPath()` meant comparing an exponential algorithm with an efficient one, so part of the observed gap came from the query formulation, not from the database architecture. The BFS version returns exactly the same distance for every pair, and in the final benchmark it is 11×, 31× and 81× faster than the naive version at distances 1, 2 and 3 (warm medians: 0.6 against 7.3 ms, 4.8 against 148.6 ms, 59.7 against 4,842 ms). The BFS is now the main PostgreSQL implementation; the naive version is kept in the `pg_naive_*` columns as a reference for distances 1 to 3. At distances 4 and 5 it is not run at all: at depth 4 it had already exhausted the disk space for temporary files in an earlier run.

**A factor to keep in mind.** Because each distance uses a different pair, the time also depends on how well connected the starting actor is, not only on the distance. An actor with few titles opens a narrow search. Distances 4 and 5 share the same target actor (Audrey Hepburn), which makes those two cases more directly comparable.

---

## 11. Fairness Audit — Summary of Corrections

| Issue found | Effect | Correction |
|---|---|---|
| No index on `people.name` in PostgreSQL | PostgreSQL scanned 40,948 people in every multi-hop query | `idx_people_name` (plus genre and country names) |
| No index on `Show.title` in Neo4j | Neo4j scanned 8,807 shows in Q2 | `show_title` index |
| Q4: 2 hops in SQL, 3 in Cypher | the two systems answered different questions | SQL extended to 6 joins |
| Q5: naive CTE vs `shortestPath()` | exponential algorithm vs breadth-first search | BFS as main implementation, naive as reference |
| Neo4j results discarded, PostgreSQL fetched | unequal client-side work | both fully materialized |
| New Neo4j session at every run | extra overhead for Neo4j | one session per measurement block |
| "Cold" run read from a warm OS cache | cold times were only partially cold | full reset of the three cache layers |
| Mean sensitive to outliers | a single slow run distorted averages | median of warm runs |
| No check that both systems return the same results | the Q4 mismatch went unnoticed | row counts saved and compared |

---

## 12. Charts

`generate_bar_chart.py` draws grouped bars for all nine queries (warm median, log scale); each label shows the number of edges the query traverses. `generate_line_chart.py` draws the same data as two lines and shades the interval where they cross, i.e. where the faster system changes. In both, small diamonds in a darker shade of each system's colour mark the cold run.

A logarithmic scale is required because the values span more than three orders of magnitude.

---

## 13. Running the Project from Scratch

All commands are run from the repository root.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export PG_PASSWORD='your-postgres-password'
export NEO4J_PASSWORD='your-neo4j-password'

# 1. Clean the data
python3 scripts/clean_netflix_data.py

# 2. PostgreSQL
psql -U postgres -h localhost -d postgres -c "DROP DATABASE IF EXISTS netflix_db;"
psql -U postgres -h localhost -d postgres -c "CREATE DATABASE netflix_db;"
psql -U postgres -h localhost -d netflix_db -f database/import_postgres.sql

# 3. Neo4j
sudo rm -f /var/lib/neo4j/import/*.csv
sudo cp data/clean/neo4j/*.csv /var/lib/neo4j/import/
sudo chmod 644 /var/lib/neo4j/import/*.csv
cat database/import_neo4j.cypher | cypher-shell -u neo4j -p "$NEO4J_PASSWORD"

# 4. Consistency and index checks
psql -U postgres -h localhost -d netflix_db -c "SELECT COUNT(*) FROM acted_in;"
cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH ()-[r:ACTED_IN]->() RETURN count(r);"
psql -U postgres -h localhost -d netflix_db -c "EXPLAIN SELECT * FROM people WHERE name = 'Ricky Gervais';"
cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "PROFILE MATCH (s:Show {title: 'Breaking Bad'}) RETURN s;"

# 5. Main benchmark and charts (asks for the sudo password once, about 15-20 minutes)
python3 scripts/benchmark_final.py
python3 scripts/generate_bar_chart.py
python3 scripts/generate_line_chart.py
```

The computer should not be used during step 5, since any other activity refills the OS cache and alters the cold measurements.

---

## 14. Limitations

- The dataset is moderate in size (about 100,000 relationships). Larger graphs would likely widen the gap on deep traversals.
- Everything runs on a single local machine with default memory settings, without replication, clustering or partitioning.
- Specialised PostgreSQL graph extensions (pgRouting, Apache AGE) were not tested.
- In the degrees-of-separation queries each distance uses a different actor pair, so the time also reflects the connectivity of the chosen actors, not only the distance. A dedicated experiment keeping one pair fixed and varying only the search depth would isolate the effect of depth.
- Q4's SQL and Cypher formulations differ in how they prevent revisiting edges; on this dataset they return identical results, but this is not guaranteed on every graph.
- PostgreSQL re-plans each query on every execution while Neo4j caches plans; this is included in the measured times.
- Five warm runs per query is a small sample; a stricter protocol would use more repetitions and report the variance.

---

## 15. Results

Warm median of five runs after a true cold start, in milliseconds (`results/benchmark_final_results.csv`). Factor = slower median / faster median.

| Query | Edges | PostgreSQL | Neo4j | Faster |
|---|:---:|---:|---:|---|
| Q1 — titles of a genre | 1 | 2.4 | 28.1 | PostgreSQL 12× |
| Q2 — cast of a show | 1 | 0.3 | 6.5 | PostgreSQL 22× |
| Q3 — directors who also acted | 2 | 12.2 | 47.1 | PostgreSQL 3.9× |
| Q4 — actors at 3 hops | 6 | 118.2 | 215.9 | PostgreSQL 1.8× |
| Q5a — degrees of separation, distance 1 | 2 | 0.6 | 5.3 | PostgreSQL 8.8× |
| Q5b — degrees of separation, distance 2 | 4 | 4.8 | 5.6 | PostgreSQL 1.2× |
| Q5c — degrees of separation, distance 3 | 6 | 59.7 | 6.0 | Neo4j 10× |
| Q5d — degrees of separation, distance 4 | 8 | 206.7 | 6.0 | Neo4j 34× |
| Q5e — degrees of separation, distance 5 | 10 | 361.5 | 6.3 | Neo4j 57× |

**Lookups and shallow joins (Q1–Q3, Q5a–Q5b).** PostgreSQL is faster, by up to 22×. Single-hop, selective queries are where a mature relational optimizer with B-tree indexes excels, while Neo4j carries a fixed cost in pattern matching and session handling that weighs heavily when the work itself is small.

**Deep path searches (Q5c–Q5e).** The faster system changes between 4 and 6 edges. From there Neo4j's time stays around 6 ms however deep the search goes, while PostgreSQL's grows at every level, reaching 57× slower at distance 5. Every SQL hop is another join over the bridge table; every graph hop follows a stored pointer.

**Enumerating versus searching (Q4 vs Q5c).** Both traverse 6 edges, yet PostgreSQL wins Q4 and Neo4j wins Q5c. Q4 must list all 13,326 actors reachable at exactly three hops, so Neo4j has to expand every path of that length. Q5c looks for a single shortest path and stops as soon as the two search frontiers meet. The number of edges alone does not decide the winner: it also matters whether the query enumerates or searches.

**Query formulation.** The naive recursive CTE, which enumerates every simple path, returns the same distances as the breadth-first version but is 81× slower at distance 3 (4,842 ms against 59.7 ms), and infeasible beyond it. Comparing it with Neo4j would have made Neo4j look about 800× faster at distance 3 instead of 10×.

The main conclusion is that neither system is universally better. The right choice depends on the workload: filters, aggregations and shallow joins favour the relational model, while searches that navigate relationships to an arbitrary depth favour the graph model. In SQL, the way a traversal is written can matter as much as the choice of database.
