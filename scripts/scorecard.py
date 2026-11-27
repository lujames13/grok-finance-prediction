#!/usr/bin/env python3
"""Friday scorecard: hit rate overall, by confidence, by reasoning type, and beta split.

Usage:
  cd /workspace/grok-finance-prediction
  uv run scripts/scorecard.py

Reads data/predictions.csv. Reasoning type comes from the vault note
frontmatter (Predictions/*.md) when present, else sync_vault's heuristic.
Hit rate excludes flat results. "Beta split" groups scored rows by whether the
benchmark rose or fell over the window and by predicted direction, plus the
share of each stock's move explained by the benchmark move.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_vault import infer_reasoning_type  # noqa: E402

CONF_ORDER = ["low", "low-medium", "medium", "medium-high", "high"]
CONF_ZH = {"low": "低", "low-medium": "中低", "medium": "中", "medium-high": "中高", "high": "高"}


def num(text: str) -> float | None:
    m = re.match(r"[\d.]+", (text or "").replace(",", ""))
    return float(m.group()) if m else None


def vault_reasoning() -> dict[str, str]:
    out: dict[str, str] = {}
    for md in (REPO / "Predictions").glob("*.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            meta = yaml.safe_load(text.split("---", 2)[1]) or {}
        except yaml.YAMLError:
            continue
        if meta.get("id") and meta.get("reasoning_type"):
            out[str(meta["id"])] = str(meta["reasoning_type"])
    return out


def rate(results: list[str]) -> str:
    h = results.count("hit")
    m = results.count("miss")
    f = results.count("flat")
    pct = f"{h / (h + m) * 100:.0f}%" if h + m else "n/a"
    return f"{h}/{h + m} hit ({pct}), flat {f}"


def main() -> None:
    rows = [r for r in csv.DictReader(open(REPO / "data" / "predictions.csv", encoding="utf-8"))
            if r["status"] == "scored" and r["result"] in ("hit", "miss", "flat")]
    reasons = vault_reasoning()
    print(f"Scored: {len(rows)}  Overall: {rate([r['result'] for r in rows])}")

    by_conf: dict[str, list[str]] = defaultdict(list)
    by_reason: dict[str, list[str]] = defaultdict(list)
    by_beta: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_conf[r["confidence"] or "low"].append(r["result"])
        by_reason[reasons.get(r["id"]) or infer_reasoning_type(r)].append(r["result"])
        b0, b1 = num(r["benchmark_ref"]), num(r["benchmark_exit"])
        bret = (b1 / b0 - 1) * 100 if b0 and b1 else 0.0
        mkt = "bench up" if bret > 0 else "bench down"
        by_beta[f"{mkt} / predicted {r['direction']}"].append(r["result"])
        by_beta[mkt].append(r["result"])

    print("\nBy confidence (calibration):")
    for c in CONF_ORDER:
        if c in by_conf:
            print(f"  {CONF_ZH[c]} ({c}): {rate(by_conf[c])}")
    print("\nBy reasoning type:")
    for k, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k}: {rate(v)}")
    print("\nBeta split (benchmark move over window x predicted direction):")
    for k in sorted(by_beta):
        print(f"  {k}: {rate(by_beta[k])}")

    # Average absolute stock move vs absolute benchmark move.
    s_abs, b_abs = [], []
    for r in rows:
        b0, b1 = num(r["benchmark_ref"]), num(r["benchmark_exit"])
        try:
            s_abs.append(abs(float(r["stock_return_pct"])))
        except ValueError:
            continue
        b_abs.append(abs((b1 / b0 - 1) * 100) if b0 and b1 else 0.0)
    if s_abs:
        print(f"\nAvg |stock move| {sum(s_abs)/len(s_abs):.2f}%  vs avg |benchmark move| {sum(b_abs)/len(b_abs):.2f}%")


if __name__ == "__main__":
    main()
