# Quant Research Agent

A Claude-powered scout that scans papers, repos, forums, and aggregators for genuine market edges and research ideas. Saves vetted findings to SQLite with structured metadata so a 6-month archive stays searchable.

## Design

**Hybrid pattern.** Source connectors are deterministic Python functions. Claude orchestrates: it decides which sources to query, evaluates results, deep-dives promising leads via `fetch_url`, and saves only what passes its skeptic filter. Then it writes a digest.

**Why this over a pure pipeline:** synthesis and selective deep-dives need a model in the loop. Pure pipeline = deluge of noise.

**Why this over a pure agent:** deterministic ingestion + a structured save schema means findings are queryable and the model can't hallucinate URLs.

```
┌──────────────┐     tool_use     ┌─────────────────┐
│  Claude       │ ───────────────▶ │ source connectors│
│  (Sonnet 4.6) │                  │  arxiv  github   │
│               │ ◀─────────────── │  reddit  HN      │
│  system: be   │   tool_result    │  quantocracy     │
│  skeptical    │                  │  fetch_url       │
└──────┬────────┘                  └─────────────────┘
       │ save_finding
       ▼
   SQLite (findings + dedup + sessions)
```

## Sources

| Source           | Tool                    | Notes |
|------------------|-------------------------|-------|
| arXiv q-fin      | `search_arxiv`          | Atom feed, no auth |
| Semantic Scholar | `search_semanticscholar`| Broad coverage: arXiv + SSRN + journals + conferences |
| SSRN             | `search_ssrn`           | SSRN-tagged papers via Semantic Scholar (no public SSRN API) |
| NBER             | `fetch_nber`            | Working papers RSS; macro / monetary / labor |
| Papers With Code | `search_paperswithcode` | Papers with open-source implementations |
| GitHub           | `search_github`         | REST API; set `GITHUB_TOKEN` to lift the 60 req/h limit to 5000/h |
| Quantocracy      | `fetch_quantocracy`     | RSS, curated quant blogs |
| Reddit           | `fetch_reddit`          | Public JSON, no auth |
| Hacker News      | `fetch_hn`              | Algolia API, no auth |
| Any URL          | `fetch_url`             | For deep dives, with retry/backoff |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then edit .env and fill in your keys
```

At minimum you need `ANTHROPIC_API_KEY`. Setting `GITHUB_TOKEN` is strongly recommended — without it `search_github` is throttled to 60 req/h, which a single daily scan can exhaust.

On Windows PowerShell, if you prefer env vars over a `.env` file:
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GITHUB_TOKEN      = "ghp_..."
```

## Daily flow — Cowork-native (primary)

Daily scans run inside Cowork using the registered scheduled task `quant-research-daily-scan` (cron `0 7 * * *`). The task reads `SCOUT_PROMPT.md` and drives `cowork_scout.py` over bash. **No API key, no token cost** — runs on your Max plan allowance.

```bash
# What the scheduled task does, end-to-end:
python cowork_scout.py search arxiv         "regime switching" --days 14
python cowork_scout.py search semanticscholar "dispersion trading" --year-from 2024
python cowork_scout.py search nber          --days 30
python cowork_scout.py search paperswithcode "transformer order book"
python cowork_scout.py search ssrn          "factor timing"
python cowork_scout.py search github        "vol surface python" --sort updated
python cowork_scout.py search quantocracy
python cowork_scout.py search reddit        algotrading --timeframe week
python cowork_scout.py search hn            "kdb redis market data"
python cowork_scout.py fetch "https://arxiv.org/abs/2403.xxxxx"
echo '{"url":"...","source":"arxiv","title":"...","summary":"...","edge_type":"model","relevance":0.78,"replicability":4,"key_insight":"..."}' \
    | python cowork_scout.py save
```

To re-run on demand: open the Scheduled section in Cowork → "Run now". To stop or edit the schedule: same place.

## Legacy API flow (optional)

`agent.py` + `run.py` are kept for the case where you want to pay API credits for an Opus deep-dive on a single topic. They're orthogonal to the daily scan and don't need to exist for the scheduled task to work.

```bash
# Default daily scan (API path; requires ANTHROPIC_API_KEY)
python run.py

# Preset themes
python run.py --topic regime
python run.py --topic vol
python run.py --topic microstructure
python run.py --topic macro
python run.py --topic tooling

# Free-form goal
python run.py "Find recent papers and repos on dispersion trading and index variance"

# Override model / turn budget
AGENT_MODEL=claude-opus-4-7 AGENT_MAX_TURNS=40 python run.py --topic regime
```

## Reading findings

```bash
python digest.py                              # flat list, last 7 days
python digest.py --clusters                   # grouped by idea, multi-source clusters first
python digest.py --days 30 --min 0.6          # last 30 days, relevance >= 0.6
python digest.py --type regime_classifier     # only regime classifiers
python digest.py --type model --clusters      # trading models, clustered
python digest.py --md > digest.md             # markdown export
```

## Schema

Findings are saved with:

- `edge_type` ∈ `{model, regime_classifier}`
  - `regime_classifier` — anything whose primary purpose is classifying/detecting market regimes (HMM, K-means, change-point, regime-switching).
  - `model` — everything else a trader would call a model: factors, signals, strategies, microstructure, execution, vol/options, risk, tradable ML, quant tooling.
  - Use `tags` for finer granularity (e.g. `["microstructure", "options"]`, `["hmm", "macro"]`).
- `relevance` ∈ [0, 1] vs. your domain
- `replicability` ∈ [1, 5] (1 = closed, 5 = open code with clear method)
- `key_insight`: one-sentence takeaway
- `summary`, `tags`, `url`, `source`, `title`, `authors`, `published`
- `embedding`: float32 BLOB of `title + summary + key_insight`, used for clustering
- `cluster_id`: int, links findings that are near-duplicates across sources

The structure is the value. Two top-level buckets answer the only question you actually ask at digest time — "is this a regime classifier I might integrate, or a model/idea I might trade?" — without forcing the agent to guess which of ten near-synonymous categories applies. Granularity stays in tags.

## Semantic clustering

Every saved finding is embedded (local `BAAI/bge-base-en-v1.5` by default — 768-dim, ~440MB, downloads once on first use) and linked to a `cluster_id`. On save, if cosine similarity to any existing finding is ≥ `CLUSTER_SIM_THRESHOLD` (default 0.78), the new finding joins that cluster. Otherwise a new cluster is created. Note: bge similarity scores run tighter than MiniLM's; if you see too few clusters merging, raise the threshold toward 0.82.

**Important:** clustering does NOT drop rows. URL-exact dedup prevents reposts; semantic clustering links distinct sources around the same idea. A paper appearing on arxiv, then a writeup on Quantocracy, then an implementation on GitHub → three saved rows, one cluster, three sources. That convergence is high signal.

The agent sees the cluster status in the `save_finding` result (`near_duplicate: true`, `cluster_sources: [...]`, `cluster_size: 3`) and can highlight high-source-count clusters in its digest.

Tune the threshold:
```bash
CLUSTER_SIM_THRESHOLD=0.85 python run.py   # tighter, fewer false merges
CLUSTER_SIM_THRESHOLD=0.70 python run.py   # looser, groups paraphrases more aggressively
```

First run downloads the embedding model (~440MB for bge-base, to `~/.cache/huggingface/`). Subsequent runs are offline-capable for embedding. To swap models, set `EMBED_MODEL` in `.env` — `all-MiniLM-L6-v2` for the lighter ~80MB option.

## Scheduling

**Windows (Task Scheduler).** A registration script is included.

```powershell
# From an elevated PowerShell, inside this folder:
powershell -ExecutionPolicy Bypass -File .\register_schedule.ps1
```

That registers a task called `QuantResearchScout` that runs `run_daily.bat` every day at 07:00 local. The batch script `cd`s into this folder, loads `.env` if present, and appends output to `logs\daily-YYYY-MM-DD.log`.

Useful follow-ups:
```powershell
Start-ScheduledTask     -TaskName "QuantResearchScout"     # run now
Get-ScheduledTaskInfo   -TaskName "QuantResearchScout"     # last result
Unregister-ScheduledTask -TaskName "QuantResearchScout" -Confirm:$false
```

To change the run time, edit the `-Time` parameter at the top of `register_schedule.ps1` (24h format, e.g. `"06:30"`) and re-run it.

**Linux / macOS (cron).**
```
0 7 * * *  cd /path/to/quantamental-research && python run.py --quiet
```

Once your v1.2 Redis stack lands, swap to a Redis-queued job triggered by your scheduler — fits the "async task queue" Redis role you've already scoped.

## v2 extension paths

Aligning with the broader risk engine roadmap:

- **More sources.** SSRN (web scrape, fragile), NBER (RSS), Papers With Code (API), Substack (per-author RSS), arXiv stat.ML cross-listings, Twitter/X (Nitter or paid API).
- **Citation graph.** When a saved arXiv paper is cited later, surface that. Connects to your "feedback engine" pillar.
- **Auto-replication scoring.** A second agent pass that, for replicability ≥ 4 findings in `model`, drafts pseudocode + data requirements. Feeds your backtester pipeline (v1.6).
- **Findings → strategy backlog.** Top-N clusters per quarter become structured "investigation cards" with hypothesis, data, success criteria.
- **ANN index for clusters.** Current find_or_create_cluster is O(n) over all stored embeddings. Fine for thousands; swap to FAISS or hnswlib when you hit ~100k findings.

## Where this fits in the 7-pillar architecture

This is orthogonal to the trading-loop pillars but feeds two of them:

- **Pillar 3 (macro agent):** the macro/regime presets surface inputs the macro agent should track.
- **Pillar 7 (ML layer):** the ml/factor presets feed your model backlog with vetted ideas instead of arxiv-firehose.

Run it as a separate process. Findings DB is a read-only artifact for the rest of the system.

## Notes & caveats

- **Rate limits.** Reddit has been tightening; an unauth User-Agent works but expect occasional 429s. GitHub unauth = 60 req/h; export `GITHUB_TOKEN` and add the `Authorization: Bearer` header in `tools.py` for 5000/h.
- **HTML extraction in `fetch_url` is crude.** For better extraction, swap in `trafilatura` or `readability-lxml`. Crude is fine for short blog posts and arxiv abstract pages; mediocre for paywalled junk.
- **Costs.** A daily scan with Sonnet 4.6 typically lands 15-25 tool calls and costs cents. Opus is ~5x for marginal quality gains on this task — keep Opus for `--topic` deep dives.
