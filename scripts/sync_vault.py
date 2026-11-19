#!/usr/bin/env python3
"""Sync data/predictions.csv into this Obsidian vault (Predictions/*.md).

Creates missing prediction notes. For existing notes, updates only score-related
frontmatter (status, result, exit prices, returns, scored_at, tags) and the
「## 結果與檢討」section, leaving the rest of the body untouched.

Optional sidecar: data/notes/<id>.json
  { "sources": [{"title": "...", "url": "https://..."}],
    "numbers": "...",
    "lesson": "..." }

Usage (canonical):
  cd /workspace/grok-finance-prediction
  uv run scripts/sync_vault.py
  uv run scripts/sync_vault.py --no-push

The legacy entrypoint `python3 /workspace/finance/sync_vault.py` is a thin
wrapper that execs the same command via uv.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

TAIPEI = ZoneInfo("Asia/Taipei")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO_ROOT / "data" / "predictions.csv"
DEFAULT_VAULT = REPO_ROOT
DEFAULT_SIDECAR_DIR = REPO_ROOT / "data" / "notes"

URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
PRICE_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)(?:\s*\((.*)\))?\s*$")
ID_SESSION_RE = re.compile(r"-(AM|PM)-")

FRONTMATTER_KEYS = [
    "id",
    "ticker",
    "name",
    "market",
    "direction",
    "confidence",
    "horizon",
    "made_at",
    "session",
    "due_date",
    "ref_price",
    "benchmark",
    "benchmark_ref",
    "reasoning_type",
    "status",
    "result",
    "exit_price",
    "benchmark_exit",
    "stock_return",
    "excess_return",
    "scored_at",
    "tags",
]

SCORE_KEYS = (
    "status",
    "result",
    "exit_price",
    "benchmark_exit",
    "stock_return",
    "excess_return",
    "scored_at",
    "tags",
)

# Frozen labels for the seed 23 rows (inferred from each thesis).
# New rows use infer_reasoning_type() heuristics.
REASONING_OVERRIDES = {
    "20261114-PM-1": "rotation",
    "20261114-PM-2": "mean-reversion",
    "20261114-PM-3": "dilution",
    "20261115-AM-1": "supply-chain",
    "20261115-AM-2": "beta-direction",
    "20261115-PM-1": "rotation",
    "20261115-PM-2": "mean-reversion",
    "20261116-AM-1": "supply-chain",
    "20261116-AM-2": "rotation",
    "20261116-PM-1": "beta-direction",
    "20261116-PM-2": "counterparty",
    "20261116-PM-3": "rotation",
    "20261117-AM-1": "policy",
    "20261117-PM-1": "rotation",
    "20261117-PM-2": "pair-trade",
    "20261118-AM-1": "beta-direction",
    "20261118-AM-2": "supply-chain",
    "20261118-AM-3": "dilution",
    "20261118-PM-1": "counterparty",
    "20261119-AM-1": "rotation",
    "20261119-AM-2": "policy",
    "20261119-PM-1": "policy",
    "20261119-PM-2": "mean-reversion",
}

CONF_ZH = {
    "low": "低",
    "low-medium": "中低",
    "medium": "中",
    "medium-high": "中高",
    "high": "高",
}

DIR_ZH = {
    "up": "表現贏",
    "down": "表現輸",
}

RESULT_ZH = {
    "hit": "命中（hit）",
    "miss": "未中（miss）",
    "flat": "持平（flat）",
}

I18N_PATH = Path(__file__).with_name("prediction_i18n.json")


class VaultDumper(yaml.SafeDumper):
    """Stable, Unicode-friendly YAML for Obsidian frontmatter."""

    def ignore_aliases(self, data):  # type: ignore[override]
        return True


def _represent_none(dumper: yaml.Dumper, _data: None) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


def _represent_str(dumper: yaml.Dumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    # Keep ISO dates / datetimes unquoted for Dataview.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?", data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _represent_date(dumper: yaml.Dumper, data: date) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", data.isoformat())


def _represent_datetime(dumper: yaml.Dumper, data: datetime) -> yaml.Node:
    if data.second == 0 and data.microsecond == 0:
        stamp = data.strftime("%Y-%m-%d %H:%M")
    else:
        stamp = data.strftime("%Y-%m-%d %H:%M:%S")
    return dumper.represent_scalar("tag:yaml.org,2002:str", stamp, style="")


VaultDumper.add_representer(type(None), _represent_none)
VaultDumper.add_representer(str, _represent_str)
VaultDumper.add_representer(date, _represent_date)
VaultDumper.add_representer(datetime, _represent_datetime)


def dump_frontmatter(data: dict[str, Any]) -> str:
    ordered: dict[str, Any] = {}
    for key in FRONTMATTER_KEYS:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value
    dumped = yaml.dump(
        ordered,
        Dumper=VaultDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return dumped


def load_i18n() -> dict[str, dict[str, str]]:
    if not I18N_PATH.is_file():
        return {}
    return json.loads(I18N_PATH.read_text(encoding="utf-8"))


def parse_number(raw: str | None) -> int | float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    match = PRICE_RE.match(text) or re.match(r"^\s*([+-]?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    token = match.group(1)
    if "." in token:
        value = float(token)
        return int(value) if value.is_integer() else value
    return int(token)


def parse_price_note(raw: str | None) -> str | None:
    if raw is None:
        return None
    match = PRICE_RE.match(str(raw).strip())
    if match and match.lastindex and match.lastindex >= 2:
        note = match.group(2)
        return note.strip() if note else None
    return None


def zh_price_note(note: str | None) -> str | None:
    if not note:
        return None
    text = note
    text = text.replace("2nd limit-down", "連續第 2 根跌停")
    text = text.replace("limit-down", "跌停")
    text = text.replace("limit-up", "漲停")
    text = re.sub(r"\bclose\b", "收盤", text)
    return text


def fmt_price(value: int | float | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) >= 100):
        return f"{int(value):,}"
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if "." in text:
        whole, frac = text.split(".", 1)
        sign = ""
        if whole.startswith("-"):
            sign = "-"
            whole = whole[1:]
        whole = f"{int(whole):,}" if whole else "0"
        return f"{sign}{whole}.{frac}"
    return text


def fmt_pct(value: int | float | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    sign = "−" if number < 0 else ""
    return f"{sign}{abs(number):.2f}%"


def split_made_at(raw: str, pred_id: str) -> tuple[str, str]:
    text = (raw or "").strip()
    day = text[:10] if len(text) >= 10 else text
    session = ""
    id_match = ID_SESSION_RE.search(pred_id or "")
    if id_match:
        session = id_match.group(1)
    elif len(text) >= 16:
        try:
            hour = int(text[11:13])
            session = "AM" if hour < 12 else "PM"
        except ValueError:
            session = ""
    return day, session


def predict_filename(row: dict[str, Any]) -> str:
    return f"{row['id']} {row['ticker']} {row['name']}.md"


def infer_reasoning_type(row: dict[str, str]) -> str:
    pred_id = row.get("id") or ""
    if pred_id in REASONING_OVERRIDES:
        return REASONING_OVERRIDES[pred_id]
    text = f"{row.get('thesis', '')} {row.get('trigger_news', '')}".lower()
    if "mean-reversion" in text or "mean reversion" in text:
        return "mean-reversion"
    if "pair view" in text or "pair with" in text or "(pair" in text:
        return "pair-trade"
    if any(k in text for k in ("atm", "dilution", "convertible bond", "share supply")):
        return "dilution"
    if "2x" in text or "index direction" in text or "amplifies index" in text:
        return "beta-direction"
    if "counterparty" in text:
        return "counterparty"
    if any(
        k in text
        for k in (
            "bis ",
            "bis rule",
            "export-control",
            "export curbs",
            "congress",
            "permitting",
            "credit-limit",
            "credit limit",
            "prepayment",
        )
    ):
        return "policy"
    if any(
        k in text
        for k in (
            "supply-chain",
            "supply chain",
            "osat",
            "cowos",
            "packaging",
            "assembler",
            " ccl",
        )
    ):
        return "supply-chain"
    if any(k in text for k in ("rotat", "gpu to asic", "money rotating", "資金轉向")):
        return "rotation"
    if "forced selling" in text or "融資" in text or "斷頭" in text:
        return "beta-direction"
    if "financing" in text:
        return "counterparty"
    return "rotation"


def build_tags(meta: dict[str, Any]) -> list[str]:
    confidence = meta.get("confidence") or "low"
    status = meta.get("status") or "open"
    result = meta.get("result")
    if status == "scored" and result in ("hit", "miss", "flat"):
        pred_tag = f"pred/{result}"
    else:
        pred_tag = "pred/open"
    market = str(meta.get("market") or "").lower()
    direction = str(meta.get("direction") or "").lower()
    reason = meta.get("reasoning_type") or "rotation"
    return [
        "prediction",
        f"conf/{confidence}",
        pred_tag,
        f"market/{market}",
        f"dir/{direction}",
        f"reason/{reason}",
    ]


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return URL_RE.findall(text)


def load_sidecar(sidecar_dir: Path, pred_id: str) -> dict[str, Any]:
    path = sidecar_dir / f"{pred_id}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"sidecar {path} must be a JSON object")
    return data


def row_to_meta(row: dict[str, str], reasoning_type: str | None = None) -> dict[str, Any]:
    made_at, session = split_made_at(row.get("made_at") or "", row.get("id") or "")
    reason = reasoning_type or infer_reasoning_type(row)
    status = (row.get("status") or "open").strip() or "open"
    result_raw = (row.get("result") or "").strip()
    result = result_raw if result_raw else None
    scored_at = (row.get("scored_at") or "").strip() or None
    due_raw = (row.get("due_date") or "").strip()[:10]
    meta: dict[str, Any] = {
        "id": row["id"],
        "ticker": row["ticker"],
        "name": row["name"],
        "market": row["market"],
        "direction": row["direction"],
        "confidence": row["confidence"],
        "horizon": int(row["horizon_trading_days"]),
        "made_at": date.fromisoformat(made_at) if made_at else None,
        "session": session,
        "due_date": date.fromisoformat(due_raw) if due_raw else None,
        "ref_price": parse_number(row.get("ref_price")),
        "benchmark": row["benchmark"],
        "benchmark_ref": parse_number(row.get("benchmark_ref")),
        "reasoning_type": reason,
        "status": status,
        "result": result,
        "exit_price": parse_number(row.get("exit_price")),
        "benchmark_exit": parse_number(row.get("benchmark_exit")),
        "stock_return": parse_number(row.get("stock_return_pct")),
        "excess_return": parse_number(row.get("excess_return_pct")),
        "scored_at": scored_at,
    }
    meta["tags"] = build_tags(meta)
    return meta


def benchmark_return(
    stock_return: int | float | None,
    excess_return: int | float | None,
    benchmark_ref: int | float | None,
    benchmark_exit: int | float | None,
) -> float | None:
    if benchmark_ref not in (None, 0) and benchmark_exit is not None:
        return (float(benchmark_exit) - float(benchmark_ref)) / float(benchmark_ref) * 100.0
    if stock_return is not None and excess_return is not None:
        return float(stock_return) - float(excess_return)
    return None


def render_numbers_section(row: dict[str, str], meta: dict[str, Any], extra: str | None) -> str:
    ref_note = zh_price_note(parse_price_note(row.get("ref_price")))
    bm_note = zh_price_note(parse_price_note(row.get("benchmark_ref")))
    made = (row.get("made_at") or "").strip()
    session = meta.get("session") or ""
    lines = ["## 數值依據", ""]
    ref_line = f"- 參考價：{fmt_price(meta['ref_price'])}"
    if ref_note:
        ref_line += f"（{ref_note}）"
    lines.append(ref_line)
    bm_line = f"- 基準：{meta['benchmark']} 參考價 {fmt_price(meta['benchmark_ref'])}"
    if bm_note:
        bm_line += f"（{bm_note}）"
    lines.append(bm_line)
    made_display = made or (
        meta["made_at"].isoformat() if isinstance(meta.get("made_at"), date) else str(meta.get("made_at") or "")
    )
    made_line = f"- 預測建立：{made_display}"
    if session:
        made_line += f"（{session}）"
    lines.append(made_line)
    due = meta["due_date"]
    due_display = due.isoformat() if isinstance(due, date) else str(due)
    lines.append(f"- 到期日：{due_display}")
    lines.append(f"- 預測期間：{meta['horizon']} 個交易日")
    if extra and extra.strip():
        lines.append("")
        lines.append(extra.strip())
    return "\n".join(lines).rstrip() + "\n"


def render_news_section(news_text: str, sidecar: dict[str, Any]) -> str:
    lines = ["## 觸發新聞", "", news_text.strip() if news_text.strip() else "（無）"]
    urls_in_csv = extract_urls(news_text)
    sources = sidecar.get("sources") or []
    link_lines: list[str] = []
    seen: set[str] = set(urls_in_csv)
    for url in urls_in_csv:
        link_lines.append(f"- {url}")
    if isinstance(sources, list):
        for src in sources:
            if not isinstance(src, dict):
                continue
            url = str(src.get("url") or "").strip()
            title = str(src.get("title") or url).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            link_lines.append(f"- [{title}]({url})")
    if link_lines:
        lines.append("")
        lines.extend(link_lines)
    else:
        lines.append("")
        lines.append("（原始紀錄未保存連結）")
    return "\n".join(lines).rstrip() + "\n"


def render_result_section(row: dict[str, str], meta: dict[str, Any], lesson: str | None) -> str:
    lines = ["## 結果與檢討", ""]
    if meta.get("status") != "scored" or not meta.get("result"):
        lines.append("尚未到期計分。")
    else:
        bm_ret = benchmark_return(
            meta.get("stock_return"),
            meta.get("excess_return"),
            meta.get("benchmark_ref"),
            meta.get("benchmark_exit"),
        )
        result_label = RESULT_ZH.get(str(meta["result"]), str(meta["result"]))
        if meta.get("scored_at"):
            lines.append(f"- 計分時間：{meta['scored_at']}")
        lines.append(
            f"- 出場價：{fmt_price(meta.get('exit_price'))}；"
            f"基準出場：{meta['benchmark']} {fmt_price(meta.get('benchmark_exit'))}"
        )
        bm_txt = fmt_pct(bm_ret) if bm_ret is not None else "—"
        lines.append(f"- 個股報酬：{fmt_pct(meta.get('stock_return'))}；基準報酬：{bm_txt}")
        lines.append(f"- 超額報酬（excess return）：{fmt_pct(meta.get('excess_return'))}")
        lines.append(f"- 結果：{result_label}")
    if lesson and str(lesson).strip():
        lines.append("")
        lines.append(str(lesson).strip())
    return "\n".join(lines).rstrip() + "\n"


def render_new_body(
    row: dict[str, str],
    meta: dict[str, Any],
    sidecar: dict[str, Any],
    i18n: dict[str, dict[str, str]],
) -> str:
    zh = i18n.get(row["id"], {})
    conf_label = CONF_ZH.get(str(meta["confidence"]), str(meta["confidence"]))
    dir_label = DIR_ZH.get(str(meta["direction"]), str(meta["direction"]))
    summary = (
        f"預測{meta['name']}（{meta['ticker']}）在 {meta['horizon']} 個交易日內"
        f"{dir_label} {meta['benchmark']}（信心：{conf_label}）。"
    )
    thesis = (zh.get("thesis") or row.get("thesis") or "").strip()
    news = (zh.get("trigger_news") or row.get("trigger_news") or "").strip()
    invalidation = (zh.get("invalidation") or row.get("invalidation") or "").strip()
    numbers_extra = sidecar.get("numbers")
    lesson = sidecar.get("lesson")

    parts = [
        "## 預測\n\n" + summary + "\n",
        render_numbers_section(row, meta, numbers_extra if isinstance(numbers_extra, str) else None),
        "## 推論理由\n\n" + (thesis or "（無）") + "\n",
        render_news_section(news, sidecar),
        "## 推論失效條件\n\n" + (invalidation or "（無）") + "\n",
        render_result_section(row, meta, lesson if isinstance(lesson, str) else None),
    ]
    return "\n".join(parts).rstrip() + "\n"


def parse_note(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path} has no YAML frontmatter")
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        raise ValueError(f"{path} frontmatter is not closed")
    fm_text = rest[:end]
    body = rest[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    meta = yaml.safe_load(fm_text) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path} frontmatter is not a mapping")
    return normalize_loaded_meta(meta), body


def normalize_loaded_meta(meta: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(meta.items()):
        if isinstance(value, datetime):
            if key in ("made_at", "due_date"):
                meta[key] = value.date()
            elif value.second == 0 and value.microsecond == 0:
                meta[key] = value.strftime("%Y-%m-%d %H:%M")
            else:
                meta[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, date) and key in ("made_at", "due_date"):
            meta[key] = value
        elif isinstance(value, float) and value.is_integer() and key in (
            "ref_price",
            "benchmark_ref",
            "exit_price",
            "benchmark_exit",
            "horizon",
        ):
            meta[key] = int(value)
    return meta


def replace_section(body: str, heading: str, new_section: str) -> str:
    pattern = re.compile(
        rf"(^|\n){re.escape(heading)}[^\n]*\n.*?(?=\n## |\Z)",
        flags=re.DOTALL,
    )
    section = new_section.rstrip() + "\n"
    if pattern.search(body):
        def _sub(match: re.Match[str]) -> str:
            prefix = match.group(1)
            return prefix + section

        return pattern.sub(_sub, body, count=1)
    # Heading missing: append.
    return body.rstrip() + "\n\n" + section


def write_note(path: Path, meta: dict[str, Any], body: str) -> None:
    content = "---\n" + dump_frontmatter(meta) + "---\n\n" + body.lstrip("\n").rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def index_existing_notes(pred_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not pred_dir.is_dir():
        return found
    for path in sorted(pred_dir.glob("*.md")):
        try:
            meta, _ = parse_note(path)
        except (ValueError, yaml.YAMLError):
            continue
        pred_id = meta.get("id")
        if pred_id:
            found[str(pred_id)] = path
    return found


def empty_equal(a: Any, b: Any) -> bool:
    def _norm(v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, date) and not isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M") if v.second == 0 else v.isoformat(sep=" ", timespec="seconds")
        return v

    return _norm(a) == _norm(b)


def sync_notes(
    csv_path: Path,
    vault: Path,
    sidecar_dir: Path,
) -> list[str]:
    pred_dir = vault / "Predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"no rows in {csv_path}")

    i18n = load_i18n()
    existing = index_existing_notes(pred_dir)
    changes: list[str] = []

    for row in rows:
        pred_id = row["id"]
        sidecar = load_sidecar(sidecar_dir, pred_id)
        path = existing.get(pred_id)
        if path is None:
            meta = row_to_meta(row)
            body = render_new_body(row, meta, sidecar, i18n)
            path = pred_dir / predict_filename(row)
            write_note(path, meta, body)
            existing[pred_id] = path
            changes.append(f"create {path.name}")
            continue

        old_meta, body = parse_note(path)
        reason = old_meta.get("reasoning_type") or infer_reasoning_type(row)
        new_meta = row_to_meta(row, reasoning_type=str(reason))
        # Existing notes: only overwrite score-related frontmatter + tags.
        updated = dict(old_meta)
        changed_keys = []
        for key in SCORE_KEYS:
            if not empty_equal(updated.get(key), new_meta.get(key)):
                updated[key] = new_meta[key]
                changed_keys.append(key)
        # Keep key order consistent.
        ordered = {}
        for key in FRONTMATTER_KEYS:
            if key in updated:
                ordered[key] = updated[key]
        for key, value in updated.items():
            if key not in ordered:
                ordered[key] = value

        lesson = sidecar.get("lesson") if isinstance(sidecar.get("lesson"), str) else None
        new_result = render_result_section(row, new_meta, lesson)
        new_body = replace_section(body, "## 結果與檢討", new_result)
        # Normalize trailing newline only; do not touch other sections.
        if not new_body.endswith("\n"):
            new_body += "\n"

        old_text = path.read_text(encoding="utf-8")
        write_note(path, ordered, new_body)
        new_text = path.read_text(encoding="utf-8")
        if new_text != old_text:
            why = ", ".join(changed_keys) if changed_keys else "結果與檢討"
            changes.append(f"update {path.name} ({why})")

    return changes


def ensure_git_identity(repo: Path) -> None:
    def _get(key: str) -> str:
        out = subprocess.run(
            ["git", "config", "--get", key],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    if not _get("user.name"):
        subprocess.run(
            ["git", "config", "user.name", "James Lu"],
            cwd=repo,
            check=True,
        )
    if not _get("user.email"):
        subprocess.run(
            ["git", "config", "user.email", "lujames13@users.noreply.github.com"],
            cwd=repo,
            check=True,
        )


def git_porcelain(repo: Path) -> str:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def git_authed(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run git with a one-shot Authorization header. Never log the header or PAT."""
    pat = os.environ.get("GH_FINANCE_PAT") or ""
    if not pat:
        raise SystemExit("error: GH_FINANCE_PAT is not set")
    basic = base64.b64encode(f"x-access-token:{pat}".encode("ascii")).decode("ascii")
    header = f"Authorization: Basic {basic}"
    cmd = ["git", "-c", f"http.extraHeader={header}", *args]
    try:
        return subprocess.run(
            cmd,
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        err = err.replace(pat, "***").replace(basic, "***").replace(header, "***")
        raise SystemExit(f"git {' '.join(args)} failed (exit {exc.returncode}): {err}") from None


def head_sha(repo: Path) -> str | None:
    out = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def needs_push(repo: Path) -> bool:
    if head_sha(repo) is None:
        return False
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if upstream.returncode != 0:
        return True
    ahead = subprocess.run(
        ["git", "rev-list", "--count", "@{u}..HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(ahead.stdout.strip() or "0") > 0


def commit_and_maybe_push(repo: Path, do_push: bool) -> str | None:
    ensure_git_identity(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    committed = False
    if git_porcelain(repo).strip():
        now = datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
        message = f"predictions: sync {now}"
        subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True)
        committed = True
    sha = head_sha(repo)
    if do_push and sha and needs_push(repo):
        git_authed(repo, ["push", "-u", "origin", "HEAD:main"])
        print(f"pushed {sha}")
        return sha
    if committed:
        return sha
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync predictions.csv into the Obsidian vault")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--sidecar-dir", type=Path, default=DEFAULT_SIDECAR_DIR)
    parser.add_argument("--no-push", action="store_true", help="Commit locally if needed, but do not push")
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")
    args.sidecar_dir.mkdir(parents=True, exist_ok=True)

    changes = sync_notes(args.csv, args.vault, args.sidecar_dir)
    if changes:
        print(f"synced {len(changes)} note change(s):")
        for line in changes:
            print(f"  {line}")
    else:
        print("no note changes")

    sha = commit_and_maybe_push(args.vault, do_push=not args.no_push)
    if sha and not args.no_push:
        print(f"committed and pushed {sha}")
    elif sha:
        print(f"committed {sha} (--no-push)")
    else:
        print("git: working tree clean, nothing to commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
