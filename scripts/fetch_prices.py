#!/usr/bin/env python3
"""Fetch recent daily closes from Yahoo Finance.

Usage:
  cd /workspace/grok-finance-prediction
  uv run scripts/fetch_prices.py
  uv run scripts/fetch_prices.py --from-csv
  uv run scripts/fetch_prices.py --from-csv --open-only
  uv run scripts/fetch_prices.py NVDA 2330.TW 0050.TW
  uv run scripts/fetch_prices.py --range 10d --last 5

Default watchlist is the usual 0050 / TSMC / AI-semi set used in daily reports.
`--from-csv` adds tickers and benchmarks from data/predictions.csv (Yahoo
suffixes are inferred). `--open-only` limits that to still-open predictions.
No third-party packages; uses the standard library only.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO_ROOT / "data" / "predictions.csv"
TAIPEI = ZoneInfo("Asia/Taipei")

# Merged universe from the old px.py + px2.py watchlists, plus later names.
DEFAULT_SYMBOLS = [
    "0050.TW",
    "2330.TW",
    "00631L.TW",
    "2317.TW",
    "2308.TW",
    "2303.TW",
    "2454.TW",
    "2383.TW",
    "3711.TW",
    "3661.TW",
    "3443.TW",
    "3374.TW",
    "3081.TWO",
    "6669.TW",
    "2882.TW",
    "3017.TW",
    "TSM",
    "NVDA",
    "AMD",
    "AVGO",
    "MRVL",
    "GOOGL",
    "AMZN",
    "CRWV",
    "AAOI",
    "QQQ",
    "^SOX",
    "^IXIC",
    "^GSPC",
    "^TWII",
    "^TNX",
    "TWD=X",
]

# Bare TW tickers (no suffix) that should be queried as Yahoo .TW symbols.
_TW_BARE = {
    "0050",
    "00631L",
    "2330",
    "2303",
    "2308",
    "2317",
    "2383",
    "2454",
    "2882",
    "3017",
    "3374",
    "3443",
    "3661",
    "3711",
    "6669",
}


def to_yahoo(ticker: str) -> str:
    """Map a predictions.csv ticker or alias to a Yahoo Finance symbol."""
    symbol = ticker.strip()
    if not symbol:
        return symbol
    if symbol in {"0050", "00631L"} or symbol in _TW_BARE:
        return f"{symbol}.TW"
    return symbol


def symbols_from_csv(csv_path: Path, open_only: bool) -> list[str]:
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")
    found: list[str] = []
    seen: set[str] = set()
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if open_only and (row.get("status") or "").strip() != "open":
                continue
            for key in ("ticker", "benchmark"):
                raw = (row.get(key) or "").strip()
                if not raw:
                    continue
                symbol = to_yahoo(raw)
                if symbol not in seen:
                    seen.add(symbol)
                    found.append(symbol)
    return found


def fetch_chart(symbol: str, range_: str, timeout: float) -> dict:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?range={urllib.parse.quote(range_)}&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        error = (payload.get("chart") or {}).get("error") or "no result"
        raise RuntimeError(error)
    return results[0]


def format_row(symbol: str, chart: dict, last: int) -> str:
    meta = chart.get("meta") or {}
    price = meta.get("regularMarketPrice")
    timestamps = chart.get("timestamp") or []
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    points: list[str] = []
    for ts, close in list(zip(timestamps, closes))[-last:]:
        day = datetime.datetime.fromtimestamp(ts, tz=TAIPEI).strftime("%m-%d")
        px = "—" if close is None else f"{round(close, 2)}"
        points.append(f"{day}:{px}")
    trail = ", ".join(points) if points else "(no daily bars)"
    return f"{symbol:12} last={price}    {trail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch recent Yahoo Finance daily closes")
    parser.add_argument("symbols", nargs="*", help="Yahoo symbols or bare tickers (overrides the default list unless --from-csv is also set)")
    parser.add_argument("--from-csv", action="store_true", help="Include tickers and benchmarks from data/predictions.csv")
    parser.add_argument("--open-only", action="store_true", help="With --from-csv, only still-open predictions")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--range", default="10d", help="Yahoo range string, default 10d")
    parser.add_argument("--last", type=int, default=5, help="How many recent daily closes to print")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args(argv)

    symbols: list[str] = []
    seen: set[str] = set()

    def add_all(items: list[str]) -> None:
        for raw in items:
            symbol = to_yahoo(raw)
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)

    if args.symbols:
        add_all(args.symbols)
    if args.from_csv:
        add_all(symbols_from_csv(args.csv, args.open_only))
    if not symbols:
        add_all(DEFAULT_SYMBOLS)

    errors = 0
    for symbol in symbols:
        try:
            chart = fetch_chart(symbol, args.range, args.timeout)
            print(format_row(symbol, chart, args.last))
        except Exception as exc:  # noqa: BLE001 — surface each symbol's failure and continue
            errors += 1
            print(f"{symbol:12} ERR {exc}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
