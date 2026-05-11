# Quant Research Scout

A Claude-powered scout that scans papers, repos, forums, and aggregators daily for genuine market edges, vets them against a skeptic prompt, and produces a self-contained PDF report you read in the morning.

## Architecture

Everything runs inside a single Cowork scheduled session. No host scripts, no SQLite, no embedding model, no live dashboard.

```
Cowork scheduled task (daily, Sonnet 4.6)
   |  searches 8 sources, vets candidates against the skeptic prompt
   |  pipes JSON findings to `cowork_scout.py save` (includes TLDR + ELI5)
   v
findings.jsonl  (one finding per line, plain text on the mount)
   |
   |  generate-pdf subcommand at end of run
   v
reports/YYYY-MM-DD.pdf  (digest + per-finding pages, self-contained)
   |
   v
your morning read (any PDF viewer)
```

Why this shape: Cowork's bash sandbox can write plain-text files to the mount reliably but cannot run SQLite there. Plain text JSONL is the storage primitive. PDF is the delivery format because it's self-contained, viewer-agnostic, and works offline.

## Models

| Layer | Model | Where set |
|---|---|---|
| Daily scan + TLDR/ELI5 inline at save | Claude Sonnet 4.6 | Cowork app preferences |
| Embeddings, host Python, API calls | — none | (not used) |

Sonnet writes the TLDR and ELI5 right at save time, in the same conversation it used to evaluate the paper. Adds ~200 tokens per finding to the daily Sonnet bill — trivial.

## Sources

| Source           | Notes |
|------------------|-------|
| arXiv q-fin      | HTTPS Atom feed |
| Semantic Scholar | Broad coverage: arXiv + SSRN + journals + conferences |
| SSRN             | Filtered through Semantic Scholar (no public SSRN API) |
| NBER             | Working papers RSS; tries 3 known URLs, uses whichever responds |
| GitHub           | Set `GITHUB_TOKEN` (zero-scope) to lift the 60 req/h cap to 5000/h |
| Quantocracy      | Curated quant blog index |
| Reddit           | Public JSON; r/algotrading, r/quant, etc. |
| Hacker News      | Algolia API |
| Papers With Code | API currently serves HTML; SKIPs gracefully |

## Quickstart

```powershell
cd "E:\Quantamental Models\Research Layer\Quantamental Research"
pip install -r requirements.txt
cp .env.example .env       # fill in GITHUB_TOKEN (recommended)
```

That's it. The scheduled task `quant-research-daily-scan` is already registered in Cowork; it fires at 07:10 local daily. To trigger a run on-demand: open Cowork → Scheduled sidebar → click "Run now".

## Daily flow

1. Wake up. The 07:10 scheduled task has already run.
2. Open `reports\YYYY-MM-DD.pdf` in any PDF viewer.
3. Cover page tells you finding count. Digest summarizes the day. One page per finding with TLDR, summary, key insight, ELI5, and tags.
4. `findings.jsonl` keeps an append-only archive across runs — you can grep it, query it, or reprocess it any way you like.

## Files

| File | Purpose |
|---|---|
| `cowork_scout.py` | CLI driving search, fetch, save, export, generate-pdf |
| `findings_store.py` | JSONL store: append, dedup, top_authors, find_related_by_tags |
| `pdf_report.py` | Renders findings + digest as a PDF via fpdf2 |
| `tools.py` | The 9 source connectors with retry + backoff |
| `SCOUT_PROMPT.md` | The agent's system prompt — single source of truth for the workflow |
| `findings.jsonl` | Your accumulating archive (one JSON object per line) |
| `reports/` | Daily PDF reports (`YYYY-MM-DD.pdf`) |
| `.env.example` | Template for `GITHUB_TOKEN` etc. |
| `requirements.txt` | `httpx`, `feedparser`, `fpdf2` — three pure-Python deps |

## Tuning

The agent assigns `topic_tags` from a stable vocabulary at save time. Two findings sharing a primary `topic_tag` end up clustered together (visible in the save-response `related` field and the digest). If clusters are too narrow, tighten the vocabulary in `SCOUT_PROMPT.md`.

`tags` is free-form for incidentals; `topic_tags` is the clustering primitive.

## Known limitations

- **No semantic similarity.** An earlier architecture used SQLite + bge-base-en-v1.5 for cosine clustering across findings. The pure-Cowork mode replaces that with tag-overlap clustering — coarser, but free and works inside the sandbox.
- **PDF is a snapshot of one run.** If you want a cross-run "what's hot this week" view, query `findings.jsonl` directly (it's append-only) or ask any Cowork session to summarize it.
- **Sonnet on scheduled tasks requires app-level setting.** There's no per-task model knob in Cowork. If you keep Cowork on Opus for interactive use, the scheduled task also runs on Opus. Set Cowork's default to Sonnet to preserve your Opus quota.

## Legacy files

Safe to delete after the PDF migration:
`storage.py`, `embeddings.py`, `refresh_dashboard.py`, `dashboard_template.html`, `agent.py`, `run.py`, `digest.py`, `host_ingest.ps1`, `register_ingest_schedule.ps1`, `register_schedule.ps1`, `run_daily.bat`, `smoke_test.py`, `dashboard_refreshed.html`, `_test_write.txt`, `pending_findings/`.

The retired Cowork artifact `quant-research-dashboard` now shows a redirect notice pointing at `reports/`; ignore it or delete via Cowork's Artifacts sidebar.
