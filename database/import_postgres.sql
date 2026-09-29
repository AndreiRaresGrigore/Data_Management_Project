DROP TABLE IF EXISTS acted_in CASCADE;
DROP TABLE IF EXISTS directed CASCADE;
DROP TABLE IF EXISTS listed_in CASCADE;
DROP TABLE IF EXISTS produced_in CASCADE;
DROP TABLE IF EXISTS shows CASCADE;
DROP TABLE IF EXISTS people CASCADE;
DROP TABLE IF EXISTS genres CASCADE;
DROP TABLE IF EXISTS countries CASCADE;

CREATE TABLE shows (
    show_id VARCHAR(50) PRIMARY KEY, type VARCHAR(50), title TEXT,
    release_year INT, rating VARCHAR(50), duration VARCHAR(50),
    date_added DATE, description TEXT
);
CREATE TABLE people (person_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);
CREATE TABLE genres (genre_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);
CREATE TABLE countries (country_id INT PRIMARY KEY, name VARCHAR(255) NOT NULL);

CREATE TABLE acted_in (show_id VARCHAR(50) REFERENCES shows(show_id) ON DELETE CASCADE, person_id INT REFERENCES people(person_id) ON DELETE CASCADE, PRIMARY KEY (show_id, person_id));
CREATE TABLE directed (show_id VARCHAR(50) REFERENCES shows(show_id) ON DELETE CASCADE, person_id INT REFERENCES people(person_id) ON DELETE CASCADE, PRIMARY KEY (show_id, person_id));
CREATE TABLE listed_in (show_id VARCHAR(50) REFERENCES shows(show_id) ON DELETE CASCADE, genre_id INT REFERENCES genres(genre_id) ON DELETE CASCADE, PRIMARY KEY (show_id, genre_id));
CREATE TABLE produced_in (show_id VARCHAR(50) REFERENCES shows(show_id) ON DELETE CASCADE, country_id INT REFERENCES countries(country_id) ON DELETE CASCADE, PRIMARY KEY (show_id, country_id));

CREATE INDEX idx_listed_in_genre ON listed_in(genre_id);
CREATE INDEX idx_acted_in_person ON acted_in(person_id);
CREATE INDEX idx_directed_person ON directed(person_id);
CREATE INDEX idx_produced_in_country ON produced_in(country_id);
CREATE INDEX idx_shows_title ON shows(title);
CREATE INDEX idx_people_name ON people(name);
CREATE INDEX idx_genres_name ON genres(name);
CREATE INDEX idx_countries_name ON countries(name);

\copy shows(show_id, type, title, release_year, rating, duration, date_added, description) FROM './data/clean/shows.csv' WITH (FORMAT csv, HEADER true, NULL '');
\copy people(person_id, name) FROM './data/clean/people.csv' WITH (FORMAT csv, HEADER true);
\copy genres(genre_id, name) FROM './data/clean/genres.csv' WITH (FORMAT csv, HEADER true);
\copy countries(country_id, name) FROM './data/clean/countries.csv' WITH (FORMAT csv, HEADER true);
\copy acted_in(show_id, person_id) FROM './data/clean/acted_in.csv' WITH (FORMAT csv, HEADER true);
\copy directed(show_id, person_id) FROM './data/clean/directed.csv' WITH (FORMAT csv, HEADER true);
\copy listed_in(show_id, genre_id) FROM './data/clean/listed_in.csv' WITH (FORMAT csv, HEADER true);
\copy produced_in(show_id, country_id) FROM './data/clean/produced_in.csv' WITH (FORMAT csv, HEADER true);

ANALYZE;
