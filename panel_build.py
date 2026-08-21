#!/usr/bin/env python3
"""Build intel panel payloads from the current lx scope."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lx  # noqa: E402

DROP_FOR_UNIVERSE = {"query", "description", "keywords", "attribute"}
TEXT_FIELDS = {"text", "query", "description", "attr", "attribute"}


def _val(row: dict[str, Any]) -> float:
    v = row.get("fob") if row.get("fob") is not None else row.get("value")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _series_points(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = lx.pick_rows(payload)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        key = str(r.get("key") or r.get("label") or r.get("yearMonth") or "")
        if not key:
            continue
        out.append({"key": key, "label": _month_lbl(key), "value": _val(r)})
    out.sort(key=lambda x: x["key"])
    return out


def _month_lbl(key: str) -> str:
    months = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    if len(key) >= 7 and key[4] == "-":
        try:
            m = int(key[5:7])
            return "%s/%s" % (months[m - 1], key[2:4])
        except ValueError:
            return key
    return key


def _total(payload: Any) -> float:
    if not isinstance(payload, dict):
        return 0.0
    tot = payload.get("totals") or {}
    if isinstance(tot, dict) and tot.get("fob") is not None:
        try:
            return float(tot["fob"])
        except (TypeError, ValueError):
            pass
    try:
        return float(payload.get("total") or 0)
    except (TypeError, ValueError):
        return 0.0


def _agg_rows(payload: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = []
    for r in lx.pick_rows(payload):
        if not isinstance(r, dict):
            continue
        label = str(r.get("label") or r.get("key") or "")
        if not label:
            continue
        rows.append({"key": str(r.get("key") or label), "label": label, "value": _val(r)})
        if len(rows) >= limit:
            break
    return rows


def universe_scope(scope: dict[str, Any]) -> dict[str, Any]:
    filters = {k: v for k, v in (scope.get("filters") or {}).items() if k not in DROP_FOR_UNIVERSE}
    rules = []
    for r in scope.get("rules") or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("field") or "").lower() in TEXT_FIELDS:
            continue
        rules.append(r)
    out = {"entity": scope.get("entity") or "product", "filters": filters, "rules": rules}
    if scope.get("region"):
        out["region"] = scope["region"]
    return out


def fetch_series(scope: dict[str, Any]) -> tuple[Any, list[str]]:
    payload, warnings, _ = lx.fetch_analyses(scope, dimension="year_month", metric="fob", limit=24)
    return payload, warnings


def fetch_agg(scope: dict[str, Any], by: str, limit: int) -> tuple[Any, str, list[str]]:
    dim = lx.resolve_by(by, "agg", scope.get("entity") or "product")
    payload, warnings, used = lx.fetch_analyses(scope, dimension=dim, metric="fob", limit=limit)
    return payload, used, warnings


def fetch_party_series(scope: dict[str, Any], field: str, name: str) -> list[dict[str, Any]]:
    s = copy.deepcopy(scope)
    s.setdefault("filters", {})[field] = name
    payload, _ = fetch_series(s)
    return _series_points(payload)


def align(months: list[str], points: list[dict[str, Any]]) -> list[float]:
    by = {p["key"]: p["value"] for p in points}
    return [float(by.get(m, 0.0)) for m in months]


def selection_label(scope: dict[str, Any]) -> str:
    f = scope.get("filters") or {}
    if f.get("query"):
        return str(f["query"])
    if f.get("description"):
        return str(f["description"])
    for r in scope.get("rules") or []:
        if str(r.get("field") or "").lower() in TEXT_FIELDS:
            return str(r.get("value") or "")
    return "seleção"


def universe_label(scope: dict[str, Any]) -> str:
    f = scope.get("filters") or {}
    bits = []
    if f.get("ncm"):
        bits.append("NCM %s" % f["ncm"])
    if f.get("period"):
        bits.append(str(f["period"]))
    return " · ".join(bits) or "universo"


def share_pct(sel: float, uni: float) -> str:
    if not uni:
        return "—"
    return "%.1f%%" % (100.0 * sel / uni)


def others_values(months: list[str], total_pts: list[dict[str, Any]], parts: list[list[float]]) -> list[float]:
    tot = align(months, total_pts)
    out = []
    for i, t in enumerate(tot):
        s = sum(p[i] for p in parts)
        out.append(max(0.0, t - s))
    return out


def composition(scope: dict[str, Any], by: str, limit: int, months: list[str], sel_points: list[dict[str, Any]]) -> dict[str, Any]:
    payload, used, _ = fetch_agg(scope, by, limit)
    tops = _agg_rows(payload, limit)
    series = []
    part_vals = []
    for row in tops:
        pts = fetch_party_series(scope, used, row["key"])
        vals = align(months, pts)
        part_vals.append(vals)
        series.append({"label": row["label"], "values": vals})
    outros = others_values(months, sel_points, part_vals)
    if any(v > 1 for v in outros):
        series.append({"label": "Outros", "values": outros})
    return {"by": used, "label": by, "months": months, "series": series}


def build(layout: str, breaks: list[str], limit: int) -> dict[str, Any]:
    scope = lx.require_scope()
    uni_scope = universe_scope(scope)
    uni_payload, _ = fetch_series(uni_scope)
    sel_payload, _ = fetch_series(scope)
    uni_pts = _series_points(uni_payload)
    sel_pts = _series_points(sel_payload)
    uni_tot = _total(uni_payload)
    sel_tot = _total(sel_payload)
    months = [p["key"] for p in sel_pts] or [p["key"] for p in uni_pts]
    u_lbl = universe_label(uni_scope)
    s_lbl = selection_label(scope)
    base = {
        "entity": scope.get("entity") or "product",
        "metric": "fob",
        "universe": {"label": u_lbl, "total": uni_tot, "points": uni_pts},
        "selection": {"label": s_lbl, "total": sel_tot, "points": sel_pts},
        "subtitle": (
            "1. find    %s · %s ·  universo\n"
            "2. seleção    %s · %s ·  %s do universo\n"
            "3. composição    %s  |  %s"
            % (u_lbl, money_plain(uni_tot), s_lbl, money_plain(sel_tot), share_pct(sel_tot, uni_tot), breaks[0], breaks[1] if len(breaks) > 1 else "")
        ),
    }
    if layout == "universe-selection-stacks":
        base["layout"] = layout
        base["title"] = "Universo  →  seleção  →  composição no tempo"
        base["stacks"] = [
            composition(scope, breaks[0], limit, months, sel_pts),
            composition(scope, breaks[1] if len(breaks) > 1 else "exporter", limit, months, sel_pts),
        ]
        return base
    if layout == "universe-selection-lines":
        base["layout"] = layout
        base["title"] = "Universo  →  seleção  →  5 séries no tempo"
        la = composition(scope, breaks[0], limit, months, sel_pts)
        lb = composition(scope, breaks[1] if len(breaks) > 1 else "exporter", limit, months, sel_pts)
        for block in (la, lb):
            block["series"] = [s for s in block.get("series") or [] if (s.get("label") or "").lower() != "outros"]
            block["label"] = {"importer":"importador","exporter":"exportador"}.get(block.get("by"), block.get("label"))
        base["lines"] = [la, lb]
        return base
    # breaks
    ba, _u, _ = fetch_agg(scope, breaks[0], limit)
    bb, _u2, _ = fetch_agg(scope, breaks[1] if len(breaks) > 1 else "exporter", limit)
    base["layout"] = "universe-selection-breaks"
    base["title"] = "Universo  →  seleção  →  quebras"
    base["breaks"] = [
        {"by": breaks[0], "label": breaks[0], "rows": _agg_rows(ba, limit)},
        {"by": breaks[1] if len(breaks) > 1 else "exporter", "label": breaks[1] if len(breaks) > 1 else "exporter", "rows": _agg_rows(bb, limit)},
    ]
    base["subtitle"] = (
        "1. find    %s · %s\n"
        "2. seleção    %s · %s ·  %s do universo\n"
        "3. quebra    %s  |  %s"
        % (u_lbl, money_plain(uni_tot), s_lbl, money_plain(sel_tot), share_pct(sel_tot, uni_tot), breaks[0], breaks[1] if len(breaks) > 1 else "exporter")
    )
    return base


def money_plain(v: float) -> str:
    if abs(v) >= 1_000_000:
        return "US$ %.1fM" % (v / 1e6)
    if abs(v) >= 1000:
        return "US$ %.0fk" % (v / 1e3)
    return "US$ %.0f" % v


def render_file(payload: dict[str, Any], out: Path) -> Path:
    tmp = Path("/tmp/lx-panel-payload.json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    py = HERE / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path("/workspace/.venv-charts/bin/python")
    import subprocess
    subprocess.check_call([str(py), str(HERE / "panel.py"), str(tmp), str(out)])
    return out


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(prog="lx-panel")
    p.add_argument("--layout", default="universe-selection-breaks",
                   choices=["universe-selection-breaks", "universe-selection-stacks", "universe-selection-lines", "breaks", "stacks", "lines"])
    p.add_argument("--break", dest="breaks", default="importer,exporter")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--out", default="")
    args = p.parse_args()
    layout = {
        "breaks": "universe-selection-breaks",
        "stacks": "universe-selection-stacks",
        "lines": "universe-selection-lines",
    }.get(args.layout, args.layout)
    dims = [x.strip() for x in args.breaks.split(",") if x.strip()] or ["importer", "exporter"]
    if len(dims) == 1:
        dims.append("exporter")
    payload = build(layout, dims, args.limit)
    out = Path(args.out) if args.out else Path("/workspace") / ("intel-panel-%s.png" % layout.split("-")[-1])
    render_file(payload, out)
    print(json.dumps({"ok": True, "layout": layout, "out": str(out), "title": payload.get("title")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
