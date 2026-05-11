"""CLI helper for Cowork-driven research scans (pure-Cowork architecture).

Storage is a single `findings.jsonl` in the project folder — no SQLite, no
embeddings. Every subcommand returns one JSON object on stdout for easy parsing.

Subcommands:
    search <source> ...        — query one of 9 sources
    fetch <url>                — readable extraction from any URL
    save                       — JSON on stdin -> append to findings.jsonl
    export                     — dump all findings/clusters/authors as JSON
    generate-pdf               — render today's findings + digest as a PDF report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 stdout on Windows so paper titles with Unicode (em-dashes, minus signs) survive.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tools import (  # noqa: E402
    search_arxiv, search_github, fetch_reddit, fetch_quantocracy, fetch_hn,
    fetch_nber, search_paperswithcode, search_semanticscholar, search_ssrn,
    fetch_url,
)
from findings_store import append_finding, export_all, iter_findings  # noqa: E402

DEFAULT_STORE = str(HERE / "findings.jsonl")
DEFAULT_REPORTS_DIR = str(HERE / "reports")

REQUIRED_SAVE_FIELDS = {
    "url", "source", "title", "summary", "edge_type",
    "relevance", "replicability", "key_insight",
}


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")


def cmd_search(args):
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
    _emit({
        "source": src,
        "count": len(data) if isinstance(data, list) else 0,
        "results": data,
    })
    return 0


def cmd_fetch(args):
    try:
        result = fetch_url(args.url, max_chars=args.max_chars)
    except Exception as e:
        _emit({"error": str(e), "url": args.url})
        return 1
    _emit(result)
    return 0


def cmd_save(args):
    raw = sys.stdin.read().strip()
    if not raw:
        _emit({"error": "save expects a JSON finding on stdin"})
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _emit({"error": f"invalid JSON: {e}"})
        return 2

    missing = REQUIRED_SAVE_FIELDS - set(data.keys())
    if missing:
        _emit({"error": f"missing required fields: {sorted(missing)}"})
        return 2

    result = append_finding(args.store, data)
    _emit(result)
    return 0


def cmd_export(args):
    data = export_all(args.store)
    _emit(data)
    return 0


def cmd_generate_pdf(args):
    """Render today's findings + the provided digest as a PDF."""
    from pdf_report import render_pdf  # local import: heavy fpdf2 only when needed
    from datetime import date

    scan_date = args.date or date.today().isoformat()

    # Filter findings by saved_at date prefix (defaults to today).
    findings = [
        f for f in iter_findings(args.store)
        if (f.get("saved_at") or "").startswith(scan_date)
    ]

    # Digest from --digest, --digest-file, or both empty.
    digest = ""
    if args.digest_file:
        digest = Path(args.digest_file).read_text(encoding="utf-8")
    elif args.digest:
        digest = args.digest

    if not findings and not digest:
        _emit({"error": f"no findings for {scan_date} and no digest provided"})
        return 5

    # Default --out: reports/<scan_date>.pdf
    out_path = args.out or str(Path(DEFAULT_REPORTS_DIR) / f"{scan_date}.pdf")
    written = render_pdf(findings, digest, out_path, scan_date=scan_date)
    _emit({
        "status": "ok",
        "out": written,
        "findings_included": len(findings),
        "scan_date": scan_date,
        "had_digest": bool(digest),
    })
    return 0


def main():
    ap = argparse.ArgumentParser(description="Cowork-driven research scout CLI")
    ap.add_argument("--store", default=DEFAULT_STORE,
                    help=f"Path to findings.jsonl (default: {DEFAULT_STORE})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Query a source")
    p_search.add_argument("source", choices=[
        "arxiv", "github", "reddit", "quantocracy", "hn",
        "nber", "paperswithcode", "semanticscholar", "ssrn",
    ])
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--max", type=int, default=20)
    p_search.add_argument("--days", type=int, default=14)
    p_search.add_argument("--timeframe", default="week",
                          choices=["hour", "day", "week", "month", "year", "all"])
    p_search.add_argument("--sort", default="updated", choices=["updated", "stars"])
    p_search.add_argument("--year-from", type=int)
    p_search.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser("fetch", help="Extract readable text from a URL")
    p_fetch.add_argument("url")
    p_fetch.add_argument("--max-chars", type=int, default=12000)
    p_fetch.set_defaults(func=cmd_fetch)

    p_save = sub.add_parser("save", help="Append a finding (JSON on stdin)")
    p_save.set_defaults(func=cmd_save)

    p_export = sub.add_parser("export", help="Dump findings.jsonl as one JSON blob")
    p_export.set_defaults(func=cmd_export)

    p_pdf = sub.add_parser("generate-pdf",
                           help="Render today's findings + digest as a PDF report")
    p_pdf.add_argument("--out", help=f"Output path; default: {DEFAULT_REPORTS_DIR}/<date>.pdf")
    p_pdf.add_argument("--digest", help="Digest text inline")
    p_pdf.add_argument("--digest-file", help="Path to a Markdown digest file")
    p_pdf.add_argument("--date", help="Override scan date (YYYY-MM-DD); default today")
    p_pdf.set_defaults(func=cmd_generate_pdf)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())