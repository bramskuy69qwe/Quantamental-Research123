"""CLI helper for Cowork-driven research scans.

Cowork (or any LLM with shell access) calls this script to orchestrate a scan
without needing the Anthropic SDK / API key. Each subcommand returns JSON to
stdout so the model can parse, evaluate, and decide what to save.

Usage:
    python cowork_scout.py search arxiv          "regime detection" --days 14 --max 20
    python cowork_scout.py search github         "vol forecasting"      --max 15
    python cowork_scout.py search reddit         algotrading           --timeframe week
    python cowork_scout.py search quantocracy
    python cowork_scout.py search hn             "options"             --days 30
    python cowork_scout.py search nber                                 --days 30
    python cowork_scout.py search paperswithcode "transformer trading"
    python cowork_scout.py search semanticscholar "dispersion trading" --year-from 2024
    python cowork_scout.py search ssrn           "factor investing"
    python cowork_scout.py fetch  "https://..."  --max-chars 12000

    # Save a finding (JSON on stdin):
    echo '{"url":"...","source":"arxiv","title":"...","summary":"...","edge_type":"model","relevance":0.8,"replicability":4,"key_insight":"..."}' \
        | python cowork_scout.py save

    # Optional: log a session digest at end of run.
    python cowork_scout.py log-session "daily scan 2026-05-11" --saved 3 --digest-file digest.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make imports work no matter where the script is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import (
    search_arxiv, search_github, fetch_reddit, fetch_quantocracy, fetch_hn,
    fetch_nber, search_paperswithcode, search_semanticscholar, search_ssrn,
    fetch_url,
)
from storage import (
    init_db, already_seen, mark_seen, save_finding,
    find_or_create_cluster, log_session, export_all,
    nearest_neighbors, known_authors_for,
)
from embeddings import embed, canonical_text


DEFAULT_DB = str(Path(__file__).resolve().parent / "research.db")


def _emit(obj) -> None:
    """Write JSON to stdout. Always single-line for easy parsing."""
    sys.stdout.write(json.dumps(obj, default=str, ensure_ascii=False))
    sys.stdout.write("\n")


def cmd_search(args: argparse.Namespace) -> int:
    src = args.source
    try:
        if src == "arxiv":
            data = search_arxiv(args.query, max_results=args.max, days_back=args.days)
        elif src == "github":
            data = search_github(args.query, sort=args.sort, max_results=args.max)
        elif src == "reddit":
            data = fetch_reddit(args.query, timeframe=args.timeframe, limit=args.max)
        elif src == "quantocracy":
            data = fetch_quantocracy()
        elif src == "hn":
            data = fetch_hn(args.query, days_back=args.days)
        elif src == "nber":
            data = fetch_nber(days_back=args.days)
        elif src == "paperswithcode":
            data = search_paperswithcode(args.query, max_results=args.max)
        elif src == "semanticscholar":
            data = search_semanticscholar(
                args.query, max_results=args.max, year_from=args.year_from,
            )
        elif src == "ssrn":
            data = search_ssrn(args.query, max_results=args.max)
        else:
            _emit({"error": f"unknown source: {src}"})
            return 2
    except Exception as e:
        _emit({"error": str(e), "source": src})
        return 1
    _emit({"source": src, "count": len(data) if isinstance(data, list) else 0, "results": data})
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        result = fetch_url(args.url, max_chars=args.max_chars)
    except Exception as e:
        _emit({"error": str(e), "url": args.url})
        return 1
    _emit(result)
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        _emit({"error": "save expects a JSON finding on stdin"})
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _emit({"error": f"invalid JSON: {e}"})
        return 2

    required = {"url", "source", "title", "summary", "edge_type", "relevance",
                "replicability", "key_insight"}
    missing = required - set(data.keys())
    if missing:
        _emit({"error": f"missing required fields: {sorted(missing)}"})
        return 2

    conn = init_db(args.db)
    try:
        if already_seen(conn, data["url"]):
            _emit({"status": "duplicate_url", "url": data["url"]})
            return 0

        text = canonical_text(data["title"], data["summary"], data["key_insight"])
        emb = embed(text)
        cluster = find_or_create_cluster(conn, emb, threshold=args.threshold)

        save_finding(conn, embedding=emb, cluster_id=cluster["cluster_id"], **data)
        mark_seen(conn, data["url"])

        # Cross-reference: top-3 most-similar prior findings outside this cluster.
        related = nearest_neighbors(
            conn, emb, k=3, exclude_cluster_id=cluster["cluster_id"],
        )
        # Author signal: which authors of this paper appear in prior high-rel findings.
        known = known_authors_for(conn, data.get("authors", ""))

        _emit({
            "status": "saved",
            "url": data["url"],
            "cluster_id": cluster["cluster_id"],
            "cluster_size": cluster["cluster_size"],
            "cluster_sources": cluster["cluster_sources"],
            "near_duplicate": cluster["near_duplicate"],
            "max_similarity": cluster["max_similarity"],
            "existing_titles_in_cluster": cluster["cluster_titles"],
            "related": related,
            "known_authors": known,
        })
    finally:
        conn.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Dump everything in research.db needed by the dashboard artifact."""
    conn = init_db(args.db)
    try:
        data = export_all(conn)
    finally:
        conn.close()
    # Add a generation timestamp so the dashboard can show "last refreshed".
    from datetime import datetime, timezone
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    _emit(data)
    return 0


def cmd_log_session(args: argparse.Namespace) -> int:
    digest = ""
    if args.digest_file:
        digest = Path(args.digest_file).read_text(encoding="utf-8")
    elif args.digest:
        digest = args.digest
    conn = init_db(args.db)
    try:
        sid = log_session(conn, args.goal, args.saved, digest)
    finally:
        conn.close()
    _emit({"session_id": sid, "saved": args.saved})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Cowork-driven research scout CLI")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"SQLite path (default: {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # search
    p_search = sub.add_parser("search", help="Query a source")
    p_search.add_argument(
        "source",
        choices=["arxiv", "github", "reddit", "quantocracy", "hn",
                 "nber", "paperswithcode", "semanticscholar", "ssrn"],
    )
    p_search.add_argument("query", nargs="?", default="",
                          help="Search keywords (not used for quantocracy/nber)")
    p_search.add_argument("--max", type=int, default=20, help="Max results")
    p_search.add_argument("--days", type=int, default=14,
                          help="Lookback window in days (arxiv, hn, nber)")
    p_search.add_argument("--timeframe", default="week",
                          choices=["hour", "day", "week", "month", "year", "all"],
                          help="Reddit timeframe")
    p_search.add_argument("--sort", default="updated",
                          choices=["updated", "stars"], help="GitHub sort")
    p_search.add_argument("--year-from", type=int,
                          help="Minimum publication year (semanticscholar)")
    p_search.set_defaults(func=cmd_search)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch and extract readable text from a URL")
    p_fetch.add_argument("url")
    p_fetch.add_argument("--max-chars", type=int, default=12000)
    p_fetch.set_defaults(func=cmd_fetch)

    # save
    p_save = sub.add_parser("save", help="Save a finding (JSON on stdin) to the DB")
    p_save.add_argument("--threshold", type=float, default=0.78,
                        help="Cosine sim threshold for cluster join")
    p_save.set_defaults(func=cmd_save)

    # export
    p_export = sub.add_parser("export", help="Dump research.db as JSON for the dashboard")
    p_export.set_defaults(func=cmd_export)

    # log-session
    p_log = sub.add_parser("log-session", help="Record a session row in the DB")
    p_log.add_argument("goal")
    p_log.add_argument("--saved", type=int, default=0)
    p_log.add_argument("--digest", default="")
    p_log.add_argument("--digest-file")
    p_log.set_defaults(func=cmd_log_session)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
