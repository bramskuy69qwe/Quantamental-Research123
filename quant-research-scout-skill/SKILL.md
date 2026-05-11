---
name: quant-research-scout
description: Run the user's daily quant research scout — scans arXiv q-fin, Semantic Scholar, SSRN, NBER, GitHub, Quantocracy, Reddit, and Hacker News for genuine market edges across factor research, regime detection, market microstructure, volatility forecasting, options analytics, and quant tooling. Produces a self-contained PDF report at `reports/YYYY-MM-DD.pdf`. Use this skill whenever the user wants to run their research scout, fetch fresh quant papers/repos, do today's scan, check what's new in q-fin, find new market edges, generate a research report, or invokes `/quant-research-scout`. Use it even when the user phrases the request casually ("anything new today?", "any good papers this week?", "what's hot in vol research?") if the context indicates they mean the scout system at `E:\Quantamental Models\Research Layer\Quantamental Research`. The scout system has its own dedicated CLI (`cowork_scout.py`); do not try to fetch papers manually or recreate this workflow without using the skill.
---

# Quant Research Scout

This skill drives the user's quantamental research scout: search a sensible subset of 8 working sources, evaluate candidates with skepticism, save vetted findings (with inline TLDR + ELI5) to `findings.jsonl`, write a digest, and render a self-contained PDF report. The user reads the PDF in the morning.

## Project location (single source of truth)

```
E:\Quantamental Models\Research Layer\Quantamental Research
```

In bash inside Cowork: `/sessions/<session>/mnt/Quantamental Research`. The `cd` belongs in every command.

## How to run this skill

**The authoritative workflow lives in `SCOUT_PROMPT.md` at the project root. Read it before doing anything else.** It contains the full domain context, the two-bucket taxonomy (`model` vs `regime_classifier`), the `topic_tags` vocabulary, the skeptic filter, the JSON save schema, and the exact bash commands.

Below is an orientation so you know what shape the workflow takes — do not substitute this for actually reading `SCOUT_PROMPT.md`.

### Brief orientation

1. `cd` into the project folder.
2. **Read `SCOUT_PROMPT.md` end to end.**
3. Plan 4-7 source queries based on what's likely to surface novel ideas today. Vary keywords day-to-day. Sources available: `arxiv`, `semanticscholar`, `ssrn`, `nber`, `github`, `quantocracy`, `reddit`, `hn`. (`paperswithcode` SKIPs cleanly upstream — don't bother.)
4. Run searches:
   ```bash
   python cowork_scout.py search arxiv          "regime switching volatility" --days 14
   python cowork_scout.py search semanticscholar "dispersion trading" --year-from 2024
   python cowork_scout.py search github         "vol surface python" --sort updated
   python cowork_scout.py search quantocracy
   ```
5. Skim, filter aggressively. For genuinely promising leads, deep-read:
   ```bash
   python cowork_scout.py fetch "https://arxiv.org/abs/..."
   ```
6. **Save vetted findings** by piping JSON to `cowork_scout.py save`. Required fields: `url`, `source`, `title`, `summary`, `edge_type`, `relevance` (0-1), `replicability` (1-5), `key_insight`, **`tldr`** (one sentence, plain English), **`eli5`** (3-5 sentences, jargon-free, for a smart non-quant). Optional: `authors`, `published`, `tags`, `topic_tags` (recommended — the clustering primitive).

   The save response gives you `related` (prior findings sharing topic_tags) and `known_authors` signals — surface those in the digest if they fire.

7. **Write the digest** as your closing chat message AND save it as Markdown to `/tmp/digest.md`. Structure: top 3-5 ranked findings, multi-source convergence callouts, one concrete "next investigation", a couple of notable-but-borderline items with reasons.

8. **Generate the PDF report:**
   ```bash
   python cowork_scout.py generate-pdf --digest-file /tmp/digest.md
   ```

   PDF lands at `reports/YYYY-MM-DD.pdf` by default. Cover page + digest + one page per finding (title, URL, source/type/relevance/replicability badges, TLDR, summary, key insight, ELI5, tags).

9. Tell the user the PDF path. That's the deliverable.

## Quality bar

Three strong findings beats fifteen mediocre ones. Skepticism is the job — many "edges" online are noise, curve-fits, marketing, or recycled basics. The skeptic checklist in `SCOUT_PROMPT.md`'s "what counts" and "what does NOT count" sections is the bar; honor it.

If a scan produces zero savable items, that is a valid outcome. Still call `generate-pdf` with just `--digest-file` set to a brief "nothing notable today" Markdown — the PDF generator handles the empty-findings case cleanly, and the user still has a dated entry in `reports/`.

## What this skill is NOT

- Not a one-shot paper finder. For ad-hoc lookups ("find me papers about X right now"), the user can call `cowork_scout.py search` directly without going through the full workflow.
- Not a backtester or strategy implementer. The scout surfaces ideas; turning a finding into a strategy is a downstream task.
- Not a substitute for the scheduled task. The scheduled task `quant-research-daily-scan` runs this workflow at 07:10 daily automatically. This skill exists for on-demand runs (e.g., "do another scan now, focused on options" during the day).

## Trigger heuristics

Use this skill when the user:
- Types `/quant-research-scout` explicitly
- Asks for "today's research", "a new scan", "the morning report", "what's new in q-fin"
- Mentions wanting to run the scout, refresh findings, or check for new market edges
- Asks about a quant-research area ("any new HMM papers?", "what's the latest on vol surface modeling?") in a context where it's clear they mean a fresh scan against their sources, not your training knowledge

Do NOT use this skill when:
- The user is asking a factual question that doesn't require a fresh scan
- The user wants to query the existing `findings.jsonl` archive without adding new findings (use direct `cowork_scout.py export` or grep)
- The user is editing the scout's code or prompt (that's a code-edit task)
