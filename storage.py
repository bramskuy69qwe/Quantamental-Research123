"""SQLite storage for research findings + URL dedup + semantic clustering."""
import sqlite3
import hashlib
import json
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id            TEXT PRIMARY KEY,
    url           TEXT UNIQUE NOT NULL,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    authors       TEXT,
    published     TEXT,
    fetched_at    TEXT DEFAULT (datetime('now')),
    summary       TEXT NOT NULL,
    edge_type     TEXT NOT NULL,
    relevance     REAL NOT NULL,
    replicability INTEGER NOT NULL,
    key_insight   TEXT NOT NULL,
    tags          TEXT,
    raw           TEXT,
    embedding     BLOB,
    cluster_id    INTEGER,
    eli5          TEXT,
    tldr          TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_relevance  ON findings(relevance DESC);
CREATE INDEX IF NOT EXISTS idx_findings_edge_type  ON findings(edge_type);
CREATE INDEX IF NOT EXISTS idx_findings_fetched    ON findings(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_cluster    ON findings(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_finding_id TEXT,
    label                TEXT,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seen_urls (
    url     TEXT PRIMARY KEY,
    seen_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT (datetime('now')),
    goal       TEXT,
    saved      INTEGER DEFAULT 0,
    digest     TEXT
);
"""


# Map legacy edge_type values to the new two-bucket taxonomy.
_LEGACY_EDGE_MAP = {
    "regime":         "regime_classifier",
    "factor":         "model",
    "microstructure": "model",
    "ml":             "model",
    "execution":      "model",
    "risk":           "model",
    "macro":          "model",
    "options":        "model",
    "vol":            "model",
    "tooling":        "model",
    "other":          "model",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migrations: add columns to existing DBs, remap legacy enum values."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)").fetchall()}
    if "embedding" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN embedding BLOB")
    if "cluster_id" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN cluster_id INTEGER")
    if "eli5" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN eli5 TEXT")
    if "tldr" not in cols:
        conn.execute("ALTER TABLE findings ADD COLUMN tldr TEXT")
    for old, new in _LEGACY_EDGE_MAP.items():
        conn.execute("UPDATE findings SET edge_type = ? WHERE edge_type = ?", (new, old))
    conn.commit()


def init_db(path: str = "research.db") -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def already_seen(conn: sqlite3.Connection, url: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen_urls WHERE url = ?", (url,))
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, url: str) -> None:
    conn.execute("INSERT OR IGNORE INTO seen_urls (url) VALUES (?)", (url,))
    conn.commit()


def save_finding(conn: sqlite3.Connection, **kw) -> str:
    fid = hashlib.sha256(kw["url"].encode()).hexdigest()[:16]
    tags = kw.get("tags") or []
    if isinstance(tags, list):
        tags = ",".join(tags)
    raw = kw.get("raw", "")
    raw = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    conn.execute(
        """
        INSERT OR REPLACE INTO findings
            (id, url, source, title, authors, published, summary,
             edge_type, relevance, replicability, key_insight, tags, raw,
             embedding, cluster_id, eli5, tldr)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fid, kw["url"], kw["source"], kw["title"],
            kw.get("authors", ""), kw.get("published", ""),
            kw["summary"], kw["edge_type"], float(kw["relevance"]),
            int(kw["replicability"]), kw["key_insight"], tags, raw,
            kw.get("embedding"), kw.get("cluster_id"),
            kw.get("eli5"), kw.get("tldr"),
        ),
    )
    conn.commit()
    return fid


def find_or_create_cluster(conn: sqlite3.Connection, embedding: bytes,
                           threshold: float = 0.78) -> dict:
    """Find nearest existing cluster by cosine similarity, or create a new one.

    Returns:
        cluster_id        — int
        near_duplicate    — bool, True if joined an existing cluster
        max_similarity    — float, best similarity found (0 if no prior findings)
        cluster_size      — int, total members AFTER this new finding joins
        cluster_sources   — list[str], distinct sources already in the cluster
        cluster_titles    — list[str], titles of existing members (preview)
    """
    from embeddings import cosine  # local import to avoid heavy load at import time

    rows = conn.execute(
        "SELECT cluster_id, source, title, embedding "
        "FROM findings WHERE embedding IS NOT NULL AND cluster_id IS NOT NULL"
    ).fetchall()

    best_sim = 0.0
    best_cluster = None
    for r in rows:
        sim = cosine(embedding, r["embedding"])
        if sim > best_sim:
            best_sim = sim
            best_cluster = r["cluster_id"]

    if best_sim >= threshold and best_cluster is not None:
        members = conn.execute(
            "SELECT source, title FROM findings WHERE cluster_id = ?", (best_cluster,)
        ).fetchall()
        return {
            "cluster_id": best_cluster,
            "near_duplicate": True,
            "max_similarity": round(best_sim, 4),
            "cluster_size": len(members) + 1,
            "cluster_sources": sorted({m["source"] for m in members}),
            "cluster_titles": [m["title"] for m in members][:5],
        }

    cur = conn.execute("INSERT INTO clusters (canonical_finding_id) VALUES (NULL)")
    conn.commit()
    return {
        "cluster_id": cur.lastrowid,
        "near_duplicate": False,
        "max_similarity": round(best_sim, 4),
        "cluster_size": 1,
        "cluster_sources": [],
        "cluster_titles": [],
    }


def cluster_view(conn: sqlite3.Connection, days: int = 7,
                 min_relevance: float = 0.0) -> list:
    """One row per cluster with aggregated source list. Sorted by size, then relevance."""
    return conn.execute(
        """
        SELECT
            cluster_id,
            COUNT(*)                          AS size,
            MAX(relevance)                    AS max_relevance,
            MAX(replicability)                AS max_replicability,
            GROUP_CONCAT(DISTINCT source)     AS sources,
            GROUP_CONCAT(title, ' || ')       AS titles,
            GROUP_CONCAT(url, ' || ')         AS urls,
            MIN(fetched_at)                   AS first_seen,
            MAX(edge_type)                    AS edge_type,
            (SELECT key_insight FROM findings f2
             WHERE f2.cluster_id = findings.cluster_id
             ORDER BY relevance DESC LIMIT 1) AS top_insight
        FROM findings
        WHERE fetched_at >= datetime('now', ?)
          AND relevance >= ?
          AND cluster_id IS NOT NULL
        GROUP BY cluster_id
        ORDER BY size DESC, max_relevance DESC
        """,
        (f"-{days} days", min_relevance),
    ).fetchall()


def recent_findings(conn: sqlite3.Connection, days: int = 7, min_relevance: float = 0.0):
    return conn.execute(
        """
        SELECT * FROM findings
        WHERE fetched_at >= datetime('now', ?)
          AND relevance >= ?
        ORDER BY relevance DESC, fetched_at DESC
        """,
        (f"-{days} days", min_relevance),
    ).fetchall()


def log_session(conn: sqlite3.Connection, goal: str, saved: int, digest: str = "") -> int:
    cur = conn.execute(
        (goal, saved, digest),
    )
    conn.commit()
    return cur.lastrowid


def export_all(conn: sqlite3.Connection) -> dict:
    """Dump everything needed by the dashboard. Embeddings are excluded (binary, large)."""
    findings = [dict(r) for r in conn.execute(
        "SELECT id, url, source, title, authors, published, fetched_at, "
        "summary, edge_type, relevance, replicability, key_insight, tags, "
        "cluster_id, eli5, tldr FROM findings ORDER BY fetched_at DESC"
    ).fetchall()]
    clusters = [dict(r) for r in conn.execute(
        "SELECT id, label, created_at FROM clusters"
    ).fetchall()]
    sessions = [dict(r) for r in conn.execute(
        "SELECT id, started_at, goal, saved, digest FROM sessions "
        "ORDER BY started_at DESC LIMIT 50"
    ).fetchall()]
    authors = top_authors(conn)
    return {
        "findings": findings,
        "clusters": clusters,
        "sessions": sessions,
        "top_authors": authors,
    }


# ---------- Author tracking ----------

def top_authors(conn: sqlite3.Connection, min_relevance: float = 0.7,
                min_count: int = 2) -> list[dict]:
    """Return authors who appear in >= min_count high-relevance findings.

    The `authors` column is a comma-separated string; we split and tally.
    Sorted by count desc, then by average relevance desc.
    """
    rows = conn.execute(
        "SELECT authors, relevance FROM findings WHERE relevance >= ? AND authors != ''",
        (min_relevance,),
    ).fetchall()
    tally: dict[str, list[float]] = {}
    for r in rows:
        for a in (r["authors"] or "").split(","):
            a = a.strip()
            if not a:
                continue
            tally.setdefault(a, []).append(float(r["relevance"]))
    out = []
    for author, rels in tally.items():
        if len(rels) < min_count:
            continue
        out.append({
            "author": author,
            "count": len(rels),
            "avg_relevance": round(sum(rels) / len(rels), 3),
            "max_relevance": round(max(rels), 3),
        })
    out.sort(key=lambda x: (x["count"], x["avg_relevance"]), reverse=True)
    return out


def known_authors_for(conn: sqlite3.Connection, authors_str: str,
                      min_relevance: float = 0.7, min_count: int = 2) -> list[dict]:
    """Filter top_authors() down to those who appear in `authors_str`."""
    if not authors_str:
        return []
    incoming = {a.strip() for a in authors_str.split(",") if a.strip()}
    return [a for a in top_authors(conn, min_relevance, min_count) if a["author"] in incoming]


# ---------- Cross-reference (nearest neighbors over embeddings) ----------

def nearest_neighbors(conn: sqlite3.Connection, embedding: bytes,
                      k: int = 3, exclude_cluster_id: int | None = None) -> list[dict]:
    """Return top-k most-similar prior findings by cosine, regardless of cluster.

    Useful for "this new paper is similar to these older ones" hints at save time.
    If `exclude_cluster_id` is set, members of that cluster are skipped (typically
    the cluster the new finding is joining — we already report on it elsewhere).
    """
    if not embedding:
        return []
    from embeddings import cosine
    rows = conn.execute(
        "SELECT id, url, title, source, edge_type, relevance, cluster_id, "
        "       embedding FROM findings WHERE embedding IS NOT NULL"
    ).fetchall()
    scored = []
    for r in rows:
        if exclude_cluster_id is not None and r["cluster_id"] == exclude_cluster_id:
            continue
        sim = cosine(embedding, r["embedding"])
        if sim <= 0:
            continue
        scored.append({
            "id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "source": r["source"],
            "edge_type": r["edge_type"],
            "relevance": r["relevance"],
            "cluster_id": r["cluster_id"],
            "similarity": round(sim, 4),
        })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:k]
        })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:k]
