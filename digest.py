"""Print or export a digest of recent findings.

Modes:
    flat       — one row per finding (default)
    clusters   — one row per idea-cluster, showing all sources that hit it

Examples:
    python digest.py                          # flat, last 7 days, all
    python digest.py --clusters               # grouped by idea
    python digest.py --days 30 --min 0.6      # last 30 days, relevance >= 0.6
    python digest.py --type regime_classifier
    python digest.py --type model --clusters
    python digest.py --md > digest.md
"""
import argparse
from storage import init_db, recent_findings, cluster_view


def _stars(n) -> str:
    n = max(0, min(5, int(n or 0)))
    return "★" * n + "☆" * (5 - n)


def format_finding(row, markdown: bool = False) -> str:
    rel = f"{row['relevance']:.2f}"
    rep = _stars(row["replicability"])
    cluster = row["cluster_id"] if row["cluster_id"] is not None else "-"
    if markdown:
        return (
            f"### [{row['title']}]({row['url']})\n"
            f"**{row['source']}** · *{row['edge_type']}* · "
            f"relevance {rel} · replicability {rep} · cluster #{cluster}\n\n"
            f"{row['summary']}\n\n"
            f"> **Key insight:** {row['key_insight']}\n"
        )
    return (
        f"[{row['source']:11s}] {row['title']}\n"
        f"  type={row['edge_type']:18s} rel={rel}  rep={rep}  cluster=#{cluster}\n"
        f"  {row['summary']}\n"
        f"  insight: {row['key_insight']}\n"
        f"  {row['url']}\n"
    )


def format_cluster(row, markdown: bool = False) -> str:
    sources = (row["sources"] or "").split(",")
    sources = sorted({s.strip() for s in sources if s.strip()})
    titles = (row["titles"] or "").split(" || ")
    urls = (row["urls"] or "").split(" || ")
    rel = f"{row['max_relevance']:.2f}"
    rep = _stars(row["max_replicability"])

    if markdown:
        out = [
            f"### Cluster #{row['cluster_id']} — {row['size']} source{'s' if row['size'] > 1 else ''}",
            f"**{row['edge_type']}** · relevance {rel} · replicability {rep} · "
            f"first seen {row['first_seen']}",
            "",
            f"**Sources:** {', '.join(sources)}",
            "",
            f"> {row['top_insight']}",
            "",
            "**Members:**",
        ]
        for t, u in zip(titles, urls):
            out.append(f"- [{t.strip()}]({u.strip()})")
        return "\n".join(out) + "\n"

    out = [
        f"=== Cluster #{row['cluster_id']} | {row['size']} source(s) | {row['edge_type']} | "
        f"rel={rel} rep={rep} ===",
        f"  sources: {', '.join(sources)}",
        f"  insight: {row['top_insight']}",
    ]
    for t, u in zip(titles, urls):
        out.append(f"    · {t.strip()}  ({u.strip()})")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="research.db")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min", dest="min_rel", type=float, default=0.0)
    ap.add_argument("--type", dest="edge_type",
                    choices=["model", "regime_classifier"],
                    help="Filter by edge_type")
    ap.add_argument("--clusters", action="store_true",
                    help="Group by idea-cluster instead of flat list")
    ap.add_argument("--md", action="store_true", help="Markdown output")
    args = ap.parse_args()

    conn = init_db(args.db)

    if args.clusters:
        rows = cluster_view(conn, days=args.days, min_relevance=args.min_rel)
        if args.edge_type:
            rows = [r for r in rows if r["edge_type"] == args.edge_type]
        header = (f"# Cluster digest — last {args.days} days"
                  if args.md
                  else f"== Cluster digest ({len(rows)} clusters, last {args.days}d, rel ≥ {args.min_rel}) ==")
        print(header + "\n")
        for r in rows:
            print(format_cluster(r, markdown=args.md))
    else:
        rows = recent_findings(conn, days=args.days, min_relevance=args.min_rel)
        if args.edge_type:
            rows = [r for r in rows if r["edge_type"] == args.edge_type]
        header = (f"# Research digest — last {args.days} days\n\n_{len(rows)} findings, relevance ≥ {args.min_rel}_"
                  if args.md
                  else f"== Digest ({len(rows)} findings, last {args.days}d, rel ≥ {args.min_rel}) ==")
        print(header + "\n")
        for r in rows:
            print(format_finding(r, markdown=args.md))

    conn.close()


if __name__ == "__main__":
    main()
