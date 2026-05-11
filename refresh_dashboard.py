"""Rewrite the dashboard artifact's embedded data block in one shot.

The dashboard HTML contains a single `<script id="research-data" ...>...</script>`
block with the current findings as JSON. This script reads that HTML, replaces
just that block with a fresh export from research.db, and writes the result.

The scheduled task uses this instead of doing the substitution by hand:

    cd "E:\\Quantamental Models\\Research Layer\\Quantamental Research"
    python refresh_dashboard.py --in  "<current artifact path>" \\
                                --out "<new file in outputs>"
    # then call mcp__cowork__update_artifact with --out as html_path.

Exits non-zero if it can't find the data block — that's a loud failure, not a
silent corruption. Safer than blind regex on hundreds of lines of HTML.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Make local imports work no matter where this is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from storage import init_db, export_all  # noqa: E402


# Match the single script tag with id="research-data" (single quotes also OK).
_SCRIPT_RE = re.compile(
    r'<script\s+id=["\']research-data["\'][^>]*>.*?</script>',
    re.DOTALL | re.IGNORECASE,
)


def _new_script_tag(data: dict) -> str:
    # Escape `</script>` if it ever appears in JSON (shouldn't, but cheap to guard).
    payload = json.dumps(data, default=str, ensure_ascii=False).replace("</", "<\\/")
    return f'<script id="research-data" type="application/json">{payload}</script>'


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the dashboard artifact's embedded JSON.")
    ap.add_argument("--in", dest="src", required=True, help="Path to current dashboard HTML")
    ap.add_argument("--out", required=True, help="Where to write the updated HTML")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "research.db"))
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: source HTML not found: {src}", file=sys.stderr)
        return 2

    html = src.read_text(encoding="utf-8")
    matches = _SCRIPT_RE.findall(html)
    if not matches:
        print("ERROR: no <script id=\"research-data\"> block found in source HTML. "
              "Did the template change?", file=sys.stderr)
        return 3
    if len(matches) > 1:
        print(f"ERROR: found {len(matches)} research-data blocks; expected exactly 1. "
              "Refusing to substitute blindly.", file=sys.stderr)
        return 4

    conn = init_db(args.db)
    try:
        data = export_all(conn)
    finally:
        conn.close()
    from datetime import datetime, timezone
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    updated = _SCRIPT_RE.sub(lambda m: _new_script_tag(data), html, count=1)
    Path(args.out).write_text(updated, encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "out": str(Path(args.out).resolve()),
        "findings": len(data.get("findings", [])),
        "clusters": len(data.get("clusters", [])),
        "top_authors": len(data.get("top_authors", [])),
        "generated_at": data["generated_at"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
