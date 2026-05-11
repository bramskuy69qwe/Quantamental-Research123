"""CLI: run a research session.

Examples:
    python run.py                              # daily default scan
    python run.py "Investigate options dispersion trading recent literature"
    python run.py --topic regime               # preset
"""
import sys
import argparse
from agent import run_agent


PRESETS = {
    "daily": (
        "Daily scan. Cover: arXiv q-fin (last 7 days, queries on 'volatility', 'regime', "
        "'microstructure'), Quantocracy latest, r/algotrading top of week, r/quant top of week, "
        "and one GitHub search for trending quant repos. Be ruthless on quality. "
        "Save only genuine edges or substantive ideas. End with a digest."
    ),
    "regime": (
        "Deep scan on regime detection and macro regime modeling: arXiv (HMM, change-point, "
        "regime switching), GitHub (open implementations), Quantocracy. Surface concrete "
        "methods I could integrate into a real-time risk engine."
    ),
    "vol": (
        "Deep scan on volatility forecasting, vol surface modeling, and vol risk premium: "
        "arXiv last 30 days, GitHub, Quantocracy. Prioritize methods with out-of-sample "
        "evidence and open code."
    ),
    "microstructure": (
        "Deep scan on market microstructure, order book signals, and execution: arXiv, "
        "GitHub, r/algotrading. Look for empirical work on liquidity, spread dynamics, "
        "and queue-position models."
    ),
    "macro": (
        "Deep scan on macro regime indicators (DXY, VIX, rates curve), cross-asset signals, "
        "and macro factor research: arXiv, Quantocracy, NBER-adjacent posts. Surface "
        "indicators a real-time macro agent should track."
    ),
    "tooling": (
        "Scan for new Python tooling, infrastructure, and frameworks for real-time quant "
        "systems: GitHub (sort by updated, queries on 'redis trading', 'fastapi trading', "
        "'async market data'), Hacker News last 30 days. Open source only."
    ),
}


def main():
    ap = argparse.ArgumentParser(description="Quant research agent")
    ap.add_argument("goal", nargs="*", help="Free-form research goal")
    ap.add_argument("--topic", choices=list(PRESETS), help="Preset research theme")
    ap.add_argument("--db", default="research.db", help="SQLite path")
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.topic:
        goal = PRESETS[args.topic]
    elif args.goal:
        goal = " ".join(args.goal)
    else:
        goal = PRESETS["daily"]

    print(f"[goal] {goal}\n")
    result = run_agent(goal, db_path=args.db, max_turns=args.max_turns, verbose=not args.quiet)
    sys.exit(0 if result["saved"] >= 0 else 1)


if __name__ == "__main__":
    main()
