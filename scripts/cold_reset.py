"""
Full cold reset shared by both benchmark scripts.

A genuinely cold measurement requires clearing three layers of caching:
  1. PostgreSQL's internal cache (shared_buffers)       -> service stop
  2. Neo4j's internal cache (page cache, JVM state)     -> service stop
  3. The operating system page cache, which keeps the
     database files in RAM even after a service restart -> drop_caches

Procedure: stop both services, flush the OS page cache while they are
down, start both services, then wait until each accepts connections.
Readiness is checked by opening a connection only (no query on the data),
so the probe does not load any table or graph data into memory.

Requires sudo. Call ensure_sudo() once at the start of the script: the
password is asked once, and every later sudo call refreshes the timestamp.
"""

import subprocess
import time

import psycopg2
from neo4j import GraphDatabase


def _run(cmd):
    subprocess.run(cmd, check=True)


def ensure_sudo():
    """Asks for the sudo password once, before the long benchmark starts."""
    print("Sudo is needed to restart services and flush the OS cache.")
    _run(["sudo", "-v"])


def _wait_postgres(pg_config, timeout_s):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            conn = psycopg2.connect(**pg_config)
            conn.close()
            return
        except psycopg2.OperationalError:
            time.sleep(1)
    raise RuntimeError("PostgreSQL did not become available in time")


def _wait_neo4j(uri, auth, timeout_s):
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            driver = GraphDatabase.driver(uri, auth=auth)
            driver.verify_connectivity()
            driver.close()
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Neo4j did not become available in time")


def full_cold_reset(pg_config, neo4j_uri, neo4j_auth, timeout_s=180):
    """Stops both services, flushes the OS page cache, restarts, waits."""
    t0 = time.time()
    print("  [cold reset] stopping PostgreSQL and Neo4j...")
    _run(["sudo", "systemctl", "stop", "postgresql", "neo4j"])

    print("  [cold reset] flushing OS page cache...")
    _run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"])

    print("  [cold reset] starting services...")
    _run(["sudo", "systemctl", "start", "postgresql", "neo4j"])

    _wait_postgres(pg_config, timeout_s)
    _wait_neo4j(neo4j_uri, neo4j_auth, timeout_s)
    print(f"  [cold reset] done in {time.time() - t0:.0f} s")
