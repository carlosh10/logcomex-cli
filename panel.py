#!/usr/bin/env python3
"""Render reusable intel panels from a JSON payload."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.gridspec import GridSpec

PURPLE = "#5b3d8c"
ORANGE = "#e36b2c"
INK = "#2b2620"
MUTED = "#6f685e"
GRID = "#ddd6cc"
BG = "#f7f5f2"
PURPLES = ["#5b3d8c", "#7a5aa8", "#9a7cc0", "#c4a574", "#d4b896", "#c8c0d6"]
ORANGES = ["#c45c26", "#d4783d", "#e39a62", "#5b3d8c", "#8a6aad", "#e8c9b0"]


def money(v: float, axis: bool = False) -> str:
    av = abs(v)
    if av >= 1_000_000:
        return "%.1fM" % (v / 1e6) if axis else "US$ %.1fM" % (v / 1e6)
    if av >= 1000:
        return "%.0fk" % (v / 1e3) if axis else "US$ %.0fk" % (v / 1e3)
    return "%.0f" % v


def short_name(s: str, n: int = 18) -> str:
    s = (s or "").replace("S/A", "").replace("LTDA", "").replace(".", "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def style(ax) -> None:
    ax.set_facecolor(BG)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#b8b2a8")
    ax.spines["bottom"].set_color("#b8b2a8")
    ax.tick_params(colors="#4a453e", labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.7)


def month_label(key: str) -> str:
    months = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    if not key or len(key) < 7:
        return key or ""
    y, m = key[:4], int(key[5:7])
    return "%s/%s" % (months[m - 1], y[2:])


def header(fig, payload: dict) -> None:
    fig.text(0.10, 0.965, "Intel  ·  produto  ·  tempo  ·  quebra", fontsize=10, color=MUTED)
    fig.text(0.10, 0.928, payload.get("title") or "Universo  →  seleção", fontsize=17, color=INK)
    fig.text(0.10, 0.868, payload.get("subtitle") or "", fontsize=9.5, color=MUTED, va="top", linespacing=1.55)


def draw_series(ax, points: list, color: str, title: str, yfmt: str) -> None:
    style(ax)
    xs = list(range(len(points)))
    ys = [p["value"] for p in points]
    labels = [p["label"] for p in points]
    if not ys:
        ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
        return
    ax.fill_between(xs, ys, color=color, alpha=0.12)
    ax.plot(xs, ys, color=color, lw=2.3, marker="o", ms=4.5, mfc="#fff", mew=1.4)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    if yfmt == "M":
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: "%.0fM" % (v / 1e6)))
        ax.set_ylim(0, max(ys) * 1.12)
    else:
        ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: "%.0fk" % (v / 1e3)))
        ax.set_ylim(0, max(ys) * 1.18)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)


def draw_bars(ax, rows: list, title: str, color: str) -> None:
    style(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.7)
    ax.grid(axis="y", visible=False)
    names = [short_name(r["label"], 22) for r in rows]
    values = [r["value"] for r in rows]
    n = len(names)
    ys = list(range(n))
    ax.barh(ys, list(reversed(values)), color=color, height=0.58)
    ax.set_yticks(ys)
    ax.set_yticklabels(list(reversed(names)))
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: "%.0fk" % (v / 1e3)))
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    if not values:
        return
    xmax = max(values)
    ax.set_xlim(0, xmax * 1.38)
    for y, val in zip(ys, reversed(values)):
        ax.text(val + xmax * 0.04, y, "%.0fk" % (val / 1e3), va="center", fontsize=8, color="#4a453e")


def draw_stack(ax, months: list, series: list, title: str, palette: list) -> None:
    style(ax)
    xs = list(range(len(months)))
    labels = [month_label(m) for m in months]
    bottoms = [0.0] * len(months)
    handles = []
    for i, s in enumerate(series):
        vals = s.get("values") or []
        color = palette[i % len(palette)]
        h = ax.bar(xs, vals, bottom=bottoms, color=color, width=0.72, label=short_name(s.get("label"), 20))
        handles.append(h)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: "%.0fk" % (v / 1e3)))
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=8)
    ymax = max(bottoms) if bottoms else 1
    ax.set_ylim(0, ymax * 1.12)



def draw_lines(ax, months, series, title, palette):
    style(ax)
    xs = list(range(len(months)))
    labels = [month_label(m) for m in months]
    ymax = 0.0
    for i, s in enumerate(series):
        vals = s.get("values") or []
        if vals:
            ymax = max(ymax, max(vals))
        color = palette[i % len(palette)]
        ax.plot(xs, vals, color=color, lw=2.1, marker="o", ms=4, mfc="#fff", mew=1.3,
                label=short_name(s.get("label"), 20))
    ax.set_xticks(xs)
    ax.set_xticklabels(labels)
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: "%.0fk" % (v / 1e3)))
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=8)
    ax.set_ylim(0, (ymax * 1.18) if ymax else 1)


def render_lines(payload, out):
    fig = plt.figure(figsize=(11.6, 11.2))
    fig.patch.set_facecolor(BG)
    gs = GridSpec(3, 1, figure=fig, height_ratios=[0.9, 1.15, 1.15], hspace=0.58,
                  left=0.10, right=0.80, top=0.78, bottom=0.06)
    header(fig, payload)
    uni = payload["universe"]
    draw_series(fig.add_subplot(gs[0, 0]), uni["points"], PURPLE,
                "Universo no tempo     %s" % money(uni.get("total") or 0), "M")
    lines = payload.get("lines") or payload.get("stacks") or []
    if len(lines) >= 1:
        a = lines[0]
        draw_lines(fig.add_subplot(gs[1, 0]), a["months"], a["series"],
                   "Seleção no tempo · %s" % a["label"], PURPLES)
    if len(lines) >= 2:
        b = lines[1]
        draw_lines(fig.add_subplot(gs[2, 0]), b["months"], b["series"],
                   "Seleção no tempo · %s" % b["label"], ORANGES)
    fig.savefig(out, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def render_breaks(payload: dict, out: Path) -> None:
    fig = plt.figure(figsize=(11.4, 11.0))
    fig.patch.set_facecolor(BG)
    gs = GridSpec(3, 2, figure=fig, height_ratios=[1.0, 1.0, 1.12], hspace=0.55, wspace=0.32,
                  left=0.10, right=0.97, top=0.78, bottom=0.06)
    header(fig, payload)
    uni = payload["universe"]
    sel = payload["selection"]
    draw_series(fig.add_subplot(gs[0, :]), uni["points"], PURPLE,
                "Universo no tempo     %s" % money(uni.get("total") or 0), "M")
    draw_series(fig.add_subplot(gs[1, :]), sel["points"], ORANGE,
                "Zoom na seleção     mesmo eixo de tempo, outra escala     %s" % money(sel.get("total") or 0), "k")
    breaks = payload.get("breaks") or []
    if len(breaks) >= 1:
        draw_bars(fig.add_subplot(gs[2, 0]), breaks[0]["rows"], "Quebra · %s" % breaks[0]["label"], PURPLE)
    if len(breaks) >= 2:
        draw_bars(fig.add_subplot(gs[2, 1]), breaks[1]["rows"], "Quebra · %s" % breaks[1]["label"], ORANGE)
    fig.savefig(out, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def render_stacks(payload: dict, out: Path) -> None:
    fig = plt.figure(figsize=(11.6, 11.2))
    fig.patch.set_facecolor(BG)
    gs = GridSpec(3, 1, figure=fig, height_ratios=[0.9, 1.15, 1.15], hspace=0.58,
                  left=0.10, right=0.80, top=0.78, bottom=0.06)
    header(fig, payload)
    uni = payload["universe"]
    draw_series(fig.add_subplot(gs[0, 0]), uni["points"], PURPLE,
                "Universo no tempo     %s" % money(uni.get("total") or 0), "M")
    stacks = payload.get("stacks") or []
    if len(stacks) >= 1:
        a = stacks[0]
        draw_stack(fig.add_subplot(gs[1, 0]), a["months"], a["series"],
                   "Seleção no tempo · %s" % a["label"], PURPLES)
    if len(stacks) >= 2:
        b = stacks[1]
        draw_stack(fig.add_subplot(gs[2, 0]), b["months"], b["series"],
                   "Seleção no tempo · %s" % b["label"], ORANGES)
    fig.savefig(out, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: panel.py payload.json out.png", file=sys.stderr)
        raise SystemExit(2)
    payload = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    layout = payload.get("layout") or "universe-selection-breaks"
    if layout == "universe-selection-stacks":
        render_stacks(payload, out)
    elif layout == "universe-selection-lines":
        render_lines(payload, out)
    else:
        render_breaks(payload, out)
    print(str(out))


if __name__ == "__main__":
    main()
