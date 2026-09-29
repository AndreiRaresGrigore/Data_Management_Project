MATCH (n) DETACH DELETE n;

DROP CONSTRAINT show_id_unique IF EXISTS;
DROP CONSTRAINT person_name_unique IF EXISTS;
DROP CONSTRAINT genre_name_unique IF EXISTS;
DROP CONSTRAINT country_name_unique IF EXISTS;
DROP INDEX show_title IF EXISTS;

CREATE CONSTRAINT show_id_unique IF NOT EXISTS FOR (s:Show) REQUIRE s.show_id IS UNIQUE;
CREATE CONSTRAINT person_name_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE;
CREATE CONSTRAINT genre_name_unique IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE;
CREATE CONSTRAINT country_name_unique IF NOT EXISTS FOR (c:Country) REQUIRE c.name IS UNIQUE;

// Symmetric with idx_shows_title in PostgreSQL (used by Q2)
CREATE INDEX show_title IF NOT EXISTS FOR (s:Show) ON (s.title);

LOAD CSV WITH HEADERS FROM 'file:///shows.csv' AS row
MERGE (s:Show {show_id: row.show_id})
SET s.type = row.type,
    s.title = row.title,
    s.release_year = toInteger(row.release_year),
    s.rating = row.rating,
    s.duration = row.duration,
    s.date_added = row.date_added,
    s.description = row.description;

LOAD CSV WITH HEADERS FROM 'file:///people.csv' AS row MERGE (p:Person {name: row.name});
LOAD CSV WITH HEADERS FROM 'file:///genres.csv' AS row MERGE (g:Genre {name: row.name});
LOAD CSV WITH HEADERS FROM 'file:///countries.csv' AS row MERGE (c:Country {name: row.name});

LOAD CSV WITH HEADERS FROM 'file:///acted_in.csv' AS row
MATCH (s:Show {show_id: row.show_id}), (p:Person {name: row.person_name})
MERGE (p)-[:ACTED_IN]->(s);

LOAD CSV WITH HEADERS FROM 'file:///directed.csv' AS row
MATCH (s:Show {show_id: row.show_id}), (p:Person {name: row.person_name})
MERGE (p)-[:DIRECTED]->(s);

LOAD CSV WITH HEADERS FROM 'file:///listed_in.csv' AS row
MATCH (s:Show {show_id: row.show_id}), (g:Genre {name: row.genre_name})
MERGE (s)-[:LISTED_IN]->(g);

LOAD CSV WITH HEADERS FROM 'file:///produced_in.csv' AS row
MATCH (s:Show {show_id: row.show_id}), (c:Country {name: row.country_name})
MERGE (s)-[:PRODUCED_IN]->(c);
