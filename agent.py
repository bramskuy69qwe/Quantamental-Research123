"""LEGACY API-based research scout — kept for optional Opus deep-dives.

Daily scans now run through Cowork using SCOUT_PROMPT.md + cowork_scout.py,
which uses your Max-plan allowance instead of API credits.

This file is preserved for one use case: running an Opus deep-dive on a
specific topic when you're willing to pay API costs. To use it:
    pip install anthropic
    set ANTHROPIC_API_KEY=sk-ant-...
    set AGENT_MODEL=claude-opus-4-6
    python run.py --topic regime

The full original implementation lived here; it has been retired to keep this
file small. The Cowork-native flow is the primary path now.
"""
import os
import json
from typing import Any
from anthropic import Anthropic

from tools import TOOL_FUNCS
from storage import (
    init_db, already_seen, mark_seen, save_finding, log_session,
    find_or_create_cluster,
)
from embeddings import embed, canonical_text
from pathlib import Path


MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "25"))
SIM_THRESHOLD = float(os.getenv("CLUSTER_SIM_THRESHOLD", "0.78"))

# System prompt is read from SCOUT_PROMPT.md so it's authored once.
_PROMPT_PATH = Path(__file__).resolve().parent / "SCOUT_PROMPT.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else (
    "You are a quant research scout. Save vetted findings via save_finding."
)


def _tool_defs() -> list[dict]:
    return [
        {"name": "search_arxiv", "description": "arXiv q-fin search.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "max_results": {"type": "integer", "default": 20},
             "days_back": {"type": "integer", "default": 14}}, "required": ["query"]}},
        {"name": "search_github", "description": "GitHub repo search.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "sort": {"type": "string", "default": "updated"},
             "max_results": {"type": "integer", "default": 15}}, "required": ["query"]}},
        {"name": "fetch_reddit", "description": "Top posts from a quant subreddit.",
         "input_schema": {"type": "object", "properties": {
             "subreddit": {"type": "string"}, "timeframe": {"type": "string", "default": "week"},
             "limit": {"type": "integer", "default": 25}}, "required": ["subreddit"]}},
        {"name": "fetch_quantocracy", "description": "Latest Quantocracy aggregator links.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "fetch_hn", "description": "Hacker News story search.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "days_back": {"type": "integer", "default": 30}},
             "required": ["query"]}},
        {"name": "fetch_nber", "description": "NBER working papers via RSS.",
         "input_schema": {"type": "object", "properties": {
             "days_back": {"type": "integer", "default": 30}}}},
        {"name": "search_paperswithcode", "description": "Papers With Code search.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "max_results": {"type": "integer", "default": 20}},
             "required": ["query"]}},
        {"name": "search_semanticscholar", "description": "Semantic Scholar search.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "max_results": {"type": "integer", "default": 20},
             "year_from": {"type": "integer"}}, "required": ["query"]}},
        {"name": "search_ssrn", "description": "SSRN papers via Semantic Scholar.",
         "input_schema": {"type": "object", "properties": {
             "query": {"type": "string"}, "max_results": {"type": "integer", "default": 20}},
             "required": ["query"]}},
        {"name": "fetch_url", "description": "Fetch and extract readable text from a URL.",
         "input_schema": {"type": "object", "properties": {
             "url": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}},
             "required": ["url"]}},
        {"name": "save_finding", "description": "Save a vetted research finding.",
         "input_schema": {"type": "object", "properties": {
             "url": {"type": "string"}, "source": {"type": "string"}, "title": {"type": "string"},
             "authors": {"type": "string"}, "published": {"type": "string"},
             "summary": {"type": "string"},
             "edge_type": {"type": "string", "enum": ["model", "regime_classifier"]},
             "relevance": {"type": "number"}, "replicability": {"type": "integer"},
             "key_insight": {"type": "string"},
             "tags": {"type": "array", "items": {"type": "string"}}},
             "required": ["url", "source", "title", "summary", "edge_type",
                          "relevance", "replicability", "key_insight"]}},
    ]


def _execute_tool(name: str, args: dict, conn) -> tuple[Any, int]:
    if name == "save_finding":
        if already_seen(conn, args["url"]):
            return {"status": "duplicate_url", "url": args["url"]}, 0
        text = canonical_text(args["title"], args["summary"], args["key_insight"])
        emb = embed(text)
        cluster = find_or_create_cluster(conn, emb, threshold=SIM_THRESHOLD)
        save_finding(conn, embedding=emb, cluster_id=cluster["cluster_id"], **args)
        mark_seen(conn, args["url"])
        return {"status": "saved", "url": args["url"], **cluster}, 1
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}, 0
    return fn(**args), 0


def _truncate(result, max_chars: int = 25000) -> str:
    s = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
    return s if len(s) <= max_chars else s[:max_chars] + f"\n...[truncated {len(s) - max_chars} chars]"


def run_agent(goal: str, db_path: str = "research.db", max_turns: int = MAX_TURNS, verbose: bool = True) -> dict:
    client = Anthropic()
    conn = init_db(db_path)
    tool_defs = _tool_defs()
    messages = [{"role": "user", "content": goal}]
    saved = 0
    final_text = []
    for turn in range(max_turns):
        resp = client.messages.create(model=MODEL, max_tokens=4096,
                                       system=SYSTEM_PROMPT, tools=tool_defs, messages=messages)
        text_this = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        if verbose:
            for t in text_this: print(t)
        if resp.stop_reason != "tool_use":
            final_text = text_this
            break
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use": continue
            if verbose:
                print(f"  -> {block.name}({json.dumps(block.input, default=str)[:140]})")
            try:
                result, inc = _execute_tool(block.name, block.input, conn)
                saved += inc
            except Exception as e:
                result = {"error": str(e), "tool": block.name}
            tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                 "content": _truncate(result)})
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})
    digest = "\n\n".join(final_text)
    session_id = log_session(conn, goal, saved, digest)
    if verbose:
        print(f"\nSession {session_id}: saved {saved} findings.")
    conn.close()
    return {"saved": saved, "digest": digest, "session_id": session_id}
