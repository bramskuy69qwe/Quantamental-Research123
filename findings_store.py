"""JSONL-based findings store. Pure-Cowork architecture: no SQLite, no embeddings.

Each finding is one JSON line in `findings.jsonl`. Plain text on the Windows
mount, which is the one storage primitive that works reliably from both the
Cowork sandbox and host Python. URL is the unique key; appends dedupe.

Clustering is tag-based (the agent assigns `topic_tags` at save time). It is
cheaper and coarser than the prior semantic-embedding approach, but it has zero
runtime dependencies and works inside the sandbox.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_finding(store_path: str, finding: dict) -> dict:
    """Append if URL not already in store. Returns {status, url, ...}."""
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing_urls: set[str] = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                u = json.loads(line).get("url")
                if u:
                    existing_urls.add(u)
            except json.JSONDecodeError:
                continue

    if finding.get("url") in existing_urls:
        return {"status": "duplicate_url", "url": finding["url"]}

    if "saved_at" not in finding:
        finding["saved_at"] = _now_iso()
    # Normalize: tags & topic_tags must be lists of strings, never None.
    finding["tags"] = finding.get("tags") or []
    finding["topic_tags"] = finding.get("topic_tags") or []

    related = find_related_by_tags(store_path, finding, k=3)
    known = known_authors_for(store_path, finding.get("authors", ""))

    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(finding, default=str, ensure_ascii=False) + "\n")

    return {
        "status": "saved",
        "url": finding["url"],
        "related": related,
        "known_authors": known,
        "total_in_store": len(existing_urls) + 1,
    }


def iter_findings(store_path: str) -> Iterator[dict]:
    p = Path(store_path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def all_findings(store_path: str) -> list[dict]:
    return list(iter_findings(store_path))


def top_authors(store_path: str, min_relevance: float = 0.7,
                min_count: int = 2) -> list[dict]:
    """Authors appearing in >= min_count high-relevance findings."""
    tally: dict[str, list[float]] = {}
    for f in iter_findings(store_path):
        if (f.get("relevance") or 0) < min_relevance:
            continue
        for a in (f.get("authors") or "").split(","):
            a = a.strip()
            if a:
                tally.setdefault(a, []).append(float(f["relevance"]))
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


def known_authors_for(store_path: str, authors_str: str,
                      min_relevance: float = 0.7, min_count: int = 2) -> list[dict]:
    if not authors_str:
        return []
    incoming = {a.strip() for a in authors_str.split(",") if a.strip()}
    return [a for a in top_authors(store_path, min_relevance, min_count)
            if a["author"] in incoming]


def find_related_by_tags(store_path: str, finding: dict, k: int = 3) -> list[dict]:
    """Find prior findings sharing topic_tags. Cheap stand-in for embedding sim."""
    incoming_tags = set(finding.get("topic_tags") or [])
    if not incoming_tags:
        return []
    incoming_url = finding.get("url")
    candidates = []
    for f in iter_findings(store_path):
        if f.get("url") == incoming_url:
            continue
        their_tags = set(f.get("topic_tags") or [])
        overlap = incoming_tags & their_tags
        if overlap:
            candidates.append({
                "url": f["url"],
                "title": f["title"],
                "source": f["source"],
                "edge_type": f.get("edge_type"),
                "relevance": f.get("relevance"),
                "shared_tags": sorted(overlap),
                "tag_overlap": len(overlap),
            })
    candidates.sort(
        key=lambda x: (x["tag_overlap"], x["relevance"] or 0),
        reverse=True,
    )
    return candidates[:k]


def export_all(store_path: str) -> dict:
    """Data shape for dashboard injection.

    Adds synthetic `cluster_id` per finding (derived from the primary topic_tag)
    so the existing dashboard's cluster view keeps working without changes.
    Findings without topic_tags get cluster_id = None.
    """
    findings = all_findings(store_path)

    # Tag frequency: findings sharing the same primary tag form a "cluster".
    primary_to_cluster: dict[str, int] = {}
    next_cluster = 1
    fetched_fallback = _now_iso()
    for f in findings:
        primary = (f.get("topic_tags") or [None])[0]
        if primary is None:
            f["cluster_id"] = None
        else:
            if primary not in primary_to_cluster:
                primary_to_cluster[primary] = next_cluster
                next_cluster += 1
            f["cluster_id"] = primary_to_cluster[primary]
        # Dashboard expects `fetched_at`; map from saved_at.
        if "fetched_at" not in f:
            f["fetched_at"] = f.get("saved_at", fetched_fallback)

    clusters = [
        {"id": cid, "label": tag, "created_at": None}
        for tag, cid in primary_to_cluster.items()
    ]

    # Tag frequency over ALL topic_tags (not just primary), for top_tags view.
    tag_counts: dict[str, int] = {}
    for f in findings:
        for t in (f.get("topic_tags") or []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(
        [{"tag": k, "count": v} for k, v in tag_counts.items() if v >= 2],
        key=lambda x: x["count"], reverse=True,
    )

    return {
        "findings": findings,
        "clusters": clusters,
        "sessions": [],  # not tracked in pure-Cowork mode; digests live in chat
        "top_authors": top_authors(store_path),
        "top_tags": top_tags,
        "generated_at": _now_iso(),
    }
