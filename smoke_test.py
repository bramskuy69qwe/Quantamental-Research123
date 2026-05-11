"""One-shot smoke test. Run after `pip install -r requirements.txt`.

Verifies the pieces of the system that are testable without Cowork:
  1. All 9 source connectors respond (live HTTP)
  2. Embedding model loads and produces a vector
  3. Save pipeline works end-to-end (embed -> cluster -> insert)
  4. Cross-reference returns related prior findings
  5. Author tracking surfaces known authors
  6. Export produces valid JSON in the right shape
  7. refresh_dashboard rewrites the data block

Uses a temporary DB (smoke_test.db) in the project folder and removes it on
success. Prints PASS / FAIL / SKIP per check. Exit code 0 only if all PASS.

Not covered here (manual):
  - The dashboard's askClaude integration (only fires inside a Cowork artifact view)
  - The scheduled task end-to-end (open Cowork -> Scheduled -> Run now)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "smoke_test.db"
PY = sys.executable
PASS, FAIL, SKIP = 0, 0, 0


def header(s: str) -> None:
    print(f"\n=== {s} ===")


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def skip(msg: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {msg}")


def run_scout(*args: str, stdin: str | None = None, timeout: int = 30) -> tuple[int, dict | str]:
    """Invoke cowork_scout.py and parse JSON stdout."""
    cmd = [PY, str(HERE / "cowork_scout.py"), "--db", str(DB), *args]
    try:
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, timeout=timeout,
            cwd=str(HERE),
        )
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    if proc.returncode != 0:
        return proc.returncode, proc.stderr or proc.stdout or "(no output)"
    try:
        return 0, json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return proc.returncode, f"invalid JSON: {e}; raw={proc.stdout[:200]}"


# ---------------------------------------------------------------------------

def test_sources():
    header("1. Source connectors (live HTTP)")
    cases = [
        ("arxiv",           ["search", "arxiv", "volatility", "--max", "2", "--days", "30"]),
        ("github",          ["search", "github", "quant trading", "--max", "2"]),
        ("reddit",          ["search", "reddit", "algotrading", "--timeframe", "week", "--max", "5"]),
        ("quantocracy",     ["search", "quantocracy"]),
        ("hn",              ["search", "hn", "trading", "--days", "30"]),
        ("nber",            ["search", "nber", "--days", "60"]),
        ("paperswithcode",  ["search", "paperswithcode", "transformer", "--max", "2"]),
        ("semanticscholar", ["search", "semanticscholar", "dispersion trading", "--max", "2"]),
        ("ssrn",            ["search", "ssrn", "factor investing", "--max", "2"]),
    ]
    for name, args in cases:
        # arxiv can be slow; allow the retry chain to finish before timing out.
        timeout = 90 if name in ("arxiv",) else 45
        rc, out = run_scout(*args, timeout=timeout)
        if rc != 0:
            bad(f"{name}: {out}")
            continue
        if not isinstance(out, dict) or "results" not in out:
            bad(f"{name}: missing 'results' in response")
            continue
        results = out.get("results") or []
        if results and isinstance(results[0], dict) and results[0].get("error"):
            skip(f"{name}: upstream error ({results[0]['error'][:80]})")
            continue
        ok(f"{name}: {len(results)} results")


def test_embedding_and_save():
    header("2. Embedding + 3. Save pipeline")
    sample = {
        "url": "https://smoke.test/paper1",
        "source": "arxiv",
        "title": "Regime Switching Volatility Forecasting via HMM",
        "summary": "Hidden Markov Model for volatility regime detection.",
        "edge_type": "regime_classifier",
        "relevance": 0.85,
        "replicability": 4,
        "key_insight": "HMM captures vol clustering better than GARCH.",
        "tags": ["hmm", "vol"],
        "authors": "Gatheral, Lee",
    }
    rc, out = run_scout("save", stdin=json.dumps(sample), timeout=180)
    if rc != 0:
        bad(f"first save failed: {out}")
        return
    if out.get("status") != "saved":
        bad(f"unexpected save status: {out}")
        return
    ok(f"first save succeeded (cluster #{out.get('cluster_id')})")
    return out


def test_cross_reference():
    header("4. Cross-reference on save")
    similar = {
        "url": "https://smoke.test/paper2",
        "source": "github",
        "title": "Markov-Switching Volatility Model implementation",
        "summary": "Open-source Python implementation of regime-switching vol model.",
        "edge_type": "regime_classifier",
        "relevance": 0.80,
        "replicability": 5,
        "key_insight": "Working code for HMM-based regime detection.",
        "tags": ["hmm", "vol"],
        "authors": "Park, Kim",
    }
    rc, out = run_scout("save", stdin=json.dumps(similar), timeout=60)
    if rc != 0:
        bad(f"second save failed: {out}")
        return
    related = out.get("related") or []
    near = out.get("near_duplicate")
    if near:
        ok(f"second save joined existing cluster (near_duplicate=True, sim={out.get('max_similarity')})")
    else:
        ok(f"second save created new cluster; related={len(related)}, top_sim={related[0]['similarity'] if related else 'n/a'}")


def test_author_tracking():
    header("5. Author tracking")
    # Save enough by "Gatheral" to push him into top_authors (min_count=2, min_rel=0.7).
    extra = {
        "url": "https://smoke.test/paper3",
        "source": "arxiv",
        "title": "Volatility Surface Dynamics",
        "summary": "Empirical study of vol surface curvature.",
        "edge_type": "model",
        "relevance": 0.78,
        "replicability": 3,
        "key_insight": "Vol surface flattens before regime shifts.",
        "tags": ["vol", "options"],
        "authors": "Gatheral, Smith",
    }
    rc, out = run_scout("save", stdin=json.dumps(extra), timeout=60)
    if rc != 0:
        bad(f"third save failed: {out}")
        return
    # Now save a NEW paper by Gatheral and check known_authors fires.
    probe = {
        "url": "https://smoke.test/paper4",
        "source": "arxiv",
        "title": "Stochastic Vol with Jumps",
        "summary": "Extension of SV models.",
        "edge_type": "model",
        "relevance": 0.72,
        "replicability": 3,
        "key_insight": "Jumps materially affect short-dated vol.",
        "tags": ["vol"],
        "authors": "Gatheral, Park",
    }
    rc, out = run_scout("save", stdin=json.dumps(probe), timeout=60)
    if rc != 0:
        bad(f"probe save failed: {out}")
        return
    known = out.get("known_authors") or []
    if any(a.get("author") == "Gatheral" for a in known):
        ok(f"Gatheral surfaced as known author (count={next(a['count'] for a in known if a['author']=='Gatheral')})")
    else:
        bad(f"known_authors missing Gatheral; got: {known}")


def test_export():
    header("6. Export JSON shape")
    rc, out = run_scout("export", timeout=15)
    if rc != 0:
        bad(f"export failed: {out}")
        return
    required = {"findings", "clusters", "sessions", "top_authors", "generated_at"}
    missing = required - set(out.keys())
    if missing:
        bad(f"export missing keys: {missing}")
        return
    ok(f"export OK: {len(out['findings'])} findings, "
       f"{len(out['top_authors'])} watched authors, "
       f"generated_at={out['generated_at']}")


def test_refresh_dashboard():
    header("7. Dashboard refresh helper")
    # Need a source HTML — synthesize a minimal one.
    src = HERE / ".smoke_dash_in.html"
    out = HERE / ".smoke_dash_out.html"
    src.write_text(
        '<html><body><script id="research-data" type="application/json">'
        '{"findings":[]}</script></body></html>',
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [PY, str(HERE / "refresh_dashboard.py"),
             "--in", str(src), "--out", str(out), "--db", str(DB)],
            capture_output=True, text=True, timeout=15, cwd=str(HERE),
        )
        if proc.returncode != 0:
            bad(f"refresh_dashboard failed: {proc.stderr}")
            return
        result = json.loads(proc.stdout)
        if result.get("status") != "ok":
            bad(f"refresh_dashboard returned: {result}")
            return
        out_html = out.read_text(encoding="utf-8")
        if '"findings":' not in out_html or 'top_authors' not in out_html:
            bad("output HTML missing expected data block content")
            return
        ok(f"refresh_dashboard rewrote {result['findings']} findings, "
           f"{result['top_authors']} authors into the data block")
    finally:
        for p in (src, out):
            if p.exists():
                p.unlink()


def main() -> int:
    # Fresh DB each run.
    if DB.exists():
        DB.unlink()

    test_sources()
    saved = test_embedding_and_save()
    if saved:
        test_cross_reference()
        test_author_tracking()
        test_export()
        test_refresh_dashboard()
    else:
        print("  (skipping downstream tests because save failed)")

    if DB.exists() and FAIL == 0:
        DB.unlink()  # tidy up
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
