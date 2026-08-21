"""Local named looks and dashboards for lx.

Storage is THIS machine only: ~/.config/lx/looks and ~/.config/lx/dashboards.
Not the workspace, not the tenant, not Carlos's Logcomex user. Helmuth cannot
see these. There is no tenant sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lx
from panel_build import DROP_FOR_UNIVERSE, TEXT_FIELDS, universe_scope

LOOKS_DIR = lx.LOOKS_DIR
DASH_DIR = lx.DASH_DIR

PAGE_KEYS = {"limit", "cursor", "sort"}

PANEL_LAYOUTS = {
    "breaks": "universe-selection-breaks",
    "stacks": "universe-selection-stacks",
    "lines": "universe-selection-lines",
}

BUILTIN_LOOKS: dict[str, dict[str, Any]] = {
    name: {
        "name": name,
        "storage": "local",
        "kind": "panel",
        "layout": name,
        "selection": {},
        "breaks": ["importer", "exporter"],
    }
    for name in ("breaks", "stacks", "lines")
}


def ensure_dirs() -> None:
    lx._ensure_cfg()


def look_path(name: str) -> Path:
    return LOOKS_DIR / f"{lx._safe_scope_name(name)}.json"


def dash_path(name: str) -> Path:
    return DASH_DIR / f"{lx._safe_scope_name(name)}.json"


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    ensure_dirs()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    path.chmod(0o600)
    return path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def text_selection(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in (scope.get("filters") or {}).items()
        if k in DROP_FOR_UNIVERSE and v not in (None, "", [])
    }


def text_rules(scope: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in scope.get("rules") or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("field") or "").lower() in TEXT_FIELDS:
            out.append(r)
    return out


def explore_from_scope(scope: dict[str, Any]) -> dict[str, Any]:
    uni = universe_scope(scope)
    filters = {
        k: v
        for k, v in (uni.get("filters") or {}).items()
        if k not in PAGE_KEYS
    }
    return {"filters": filters, "rules": list(uni.get("rules") or [])}


def list_looks() -> list[str]:
    ensure_dirs()
    return sorted(p.stem for p in LOOKS_DIR.glob("*.json"))


def list_dashboards() -> list[str]:
    ensure_dirs()
    return sorted(p.stem for p in DASH_DIR.glob("*.json"))


def load_look(name: str) -> dict[str, Any]:
    n = lx._safe_scope_name(name)
    path = look_path(n)
    if path.exists():
        data = _read_json(path)
        if not isinstance(data, dict):
            lx.fail({"error": "invalid_look_file", "name": n})
        return data
    if n in BUILTIN_LOOKS:
        return dict(BUILTIN_LOOKS[n])
    lx.fail({"error": "look_not_found", "name": n, "hint": "built-in: breaks|stacks|lines or lx look save NAME"})


def save_look(look: dict[str, Any]) -> Path:
    return _write_json(look_path(look["name"]), look)


def remove_look(name: str) -> None:
    path = look_path(name)
    if path.exists():
        path.unlink()


def load_dashboard(name: str) -> dict[str, Any]:
    n = lx._safe_scope_name(name)
    path = dash_path(n)
    if not path.exists():
        lx.fail({"error": "dashboard_not_found", "name": n})
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("looks"):
        lx.fail({"error": "invalid_dashboard_file", "name": n})
    return data


def save_dashboard(dash: dict[str, Any]) -> Path:
    return _write_json(dash_path(dash["name"]), dash)


def remove_dashboard(name: str) -> None:
    path = dash_path(name)
    if path.exists():
        path.unlink()


def _parse_breaks(raw: str | list[str] | None) -> list[str]:
    if isinstance(raw, list):
        dims = [str(x).strip() for x in raw if str(x).strip()]
    else:
        dims = [x.strip() for x in (raw or "importer,exporter").split(",") if x.strip()]
    if not dims:
        dims = ["importer", "exporter"]
    if len(dims) == 1:
        dims.append("exporter")
    return dims


def look_from_scope(
    name: str,
    scope: dict[str, Any],
    *,
    view: str | None = None,
    layout: str | None = None,
    by: str | None = None,
    metric: str | None = "fob",
    limit: int | None = None,
    breaks: str | list[str] | None = None,
) -> dict[str, Any]:
    n = lx._safe_scope_name(name)
    look: dict[str, Any] = {
        "name": n,
        "storage": "local",
        "selection": text_selection(scope),
    }
    rules = text_rules(scope)
    if rules:
        look["rules"] = rules
    if view:
        look["kind"] = "view"
        look["view"] = view
        if by:
            look["by"] = by
        look["metric"] = metric or "fob"
        look["limit"] = 10 if limit is None else limit
    else:
        look["kind"] = "panel"
        look["layout"] = layout or "breaks"
        look["breaks"] = _parse_breaks(breaks)
    return look


def resolve_look_token(
    token: str,
    fill_selection: dict[str, Any],
    fill_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    n = lx._safe_scope_name(token.strip())
    path = look_path(n)
    if path.exists():
        data = _read_json(path)
        if not isinstance(data, dict):
            lx.fail({"error": "invalid_look_file", "name": n})
        look = {
            "name": data.get("name") or n,
            "kind": data.get("kind") or "view",
        }
        for key in ("view", "layout", "by", "metric", "limit", "breaks"):
            if data.get(key) not in (None, "", []):
                look[key] = data[key]
        sel = data.get("selection") or {}
        look["selection"] = dict(sel) if sel else dict(fill_selection)
        rules = data.get("rules") or []
        if not rules and fill_rules and not sel:
            rules = list(fill_rules)
        if rules:
            look["rules"] = rules
        return look
    if n in BUILTIN_LOOKS:
        look = dict(BUILTIN_LOOKS[n])
        look["selection"] = dict(fill_selection)
        if fill_rules:
            look["rules"] = list(fill_rules)
        return look
    lx.fail({
        "error": "look_not_found",
        "name": n,
        "hint": "built-in: breaks|stacks|lines or save a look first (lx look save NAME)",
    })


def dashboard_from_scope(
    name: str,
    scope: dict[str, Any],
    tokens: list[str],
) -> dict[str, Any]:
    n = lx._safe_scope_name(name)
    fill = text_selection(scope)
    fill_rules = text_rules(scope)
    looks = [resolve_look_token(tok, fill, fill_rules) for tok in tokens]
    dash: dict[str, Any] = {
        "name": n,
        "storage": "local",
        "entity": scope.get("entity") or "product",
        "explore": explore_from_scope(scope),
        "looks": looks,
    }
    if scope.get("region"):
        dash["region"] = scope["region"]
    return dash


def merge_scope(
    dash: dict[str, Any],
    look: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ov = overrides or {}
    explore = dash.get("explore") or {}
    filters = {k: v for k, v in (explore.get("filters") or {}).items() if k not in PAGE_KEYS}
    if ov.get("period"):
        filters["period"] = ov["period"]
    if ov.get("ncm"):
        filters["ncm"] = ov["ncm"]
    sel = dict(look.get("selection") or {})
    if ov.get("text"):
        sel["query"] = ov["text"]
    filters.update(sel)
    rules = list(explore.get("rules") or []) + list(look.get("rules") or [])
    scope: dict[str, Any] = {
        "entity": dash.get("entity") or "product",
        "filters": filters,
        "rules": rules,
    }
    if dash.get("region"):
        scope["region"] = dash["region"]
    return scope


def effective_explore(dash: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    ov = overrides or {}
    explore = dash.get("explore") or {}
    filters = dict(explore.get("filters") or {})
    if ov.get("period"):
        filters["period"] = ov["period"]
    if ov.get("ncm"):
        filters["ncm"] = ov["ncm"]
    return {"filters": filters, "rules": list(explore.get("rules") or [])}


def run_view_look(scope: dict[str, Any], look: dict[str, Any]) -> dict[str, Any]:
    entity = scope.get("entity") or "product"
    kind = look.get("view") or "rows"
    metric = look.get("metric") or "fob"
    limit = look.get("limit")
    if limit is None:
        limit = 40 if kind == "graph" else 25
    if kind == "rows":
        payload, warnings = lx.fetch_rows(scope, {"limit": limit})
        data = lx.compact_view_data("rows", entity, payload, limit)
        env = lx.make_envelope(
            entity=entity, scope=scope, payload=payload, data=data, extra_warnings=warnings,
        )
    elif kind == "graph":
        payload, warnings = lx.fetch_graph(scope, metric=metric, limit=limit)
        data = lx.compact_view_data("graph", entity, payload, limit)
        env = lx.make_envelope(
            entity=entity, scope=scope, payload=payload, data=data,
            extra_warnings=warnings, metric=metric,
        )
    else:
        dimension = lx.resolve_by(look.get("by"), kind, entity)
        payload, warnings, used = lx.fetch_analyses(
            scope, dimension=dimension, metric=metric, limit=limit,
        )
        data = lx.compact_view_data(kind, entity, payload, limit)
        env = lx.make_envelope(
            entity=entity, scope=scope, payload=payload, data=data,
            extra_warnings=warnings, by=used, metric=metric,
        )
    block: dict[str, Any] = {
        "ok": env.get("ok"),
        "name": look.get("name"),
        "kind": "view",
        "view": kind,
        "totals": env.get("totals"),
        "coverage": env.get("coverage"),
        "data": env.get("data"),
        "warnings": env.get("warnings") or [],
    }
    if env.get("by"):
        block["by"] = env["by"]
    if env.get("metric"):
        block["metric"] = env["metric"]
    if not env.get("ok"):
        block["error"] = env.get("error")
        block["status"] = env.get("status")
        block["body"] = env.get("body")
    return block


def show_dashboard(
    dash: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
    out_dir: Path,
    panel_build: Any,
) -> dict[str, Any]:
    warnings: list[str] = []
    looks_out: list[dict[str, Any]] = []
    ok = True
    out_dir.mkdir(parents=True, exist_ok=True)
    for look in dash.get("looks") or []:
        if not isinstance(look, dict):
            continue
        name = look.get("name") or "look"
        kind = look.get("kind") or "panel"
        scope = merge_scope(dash, look, overrides)
        try:
            if kind == "view":
                block = run_view_look(scope, look)
                block["name"] = name
                looks_out.append(block)
                if not block.get("ok"):
                    ok = False
                continue
            layout_key = look.get("layout") or "breaks"
            layout = PANEL_LAYOUTS.get(layout_key, layout_key)
            dims = _parse_breaks(look.get("breaks"))
            limit = look.get("limit") or 5
            payload = panel_build.build(layout, dims, limit, scope=scope)
            png = out_dir / f"{dash.get('name')}-{name}.png"
            panel_build.render_file(payload, png)
            uni = payload.get("universe") or {}
            sel = payload.get("selection") or {}
            looks_out.append({
                "name": name,
                "kind": "panel",
                "layout": layout_key,
                "out": str(png),
                "title": payload.get("title"),
                "totals": {"universe": uni.get("total"), "selection": sel.get("total")},
            })
        except SystemExit:
            raise
        except Exception as exc:
            ok = False
            warnings.append(f"{name}: {exc}")
            looks_out.append({"name": name, "kind": kind, "ok": False, "error": str(exc)})
    env: dict[str, Any] = {
        "ok": ok,
        "name": dash.get("name"),
        "storage": "local",
        "entity": dash.get("entity"),
        "explore": effective_explore(dash, overrides),
        "looks": looks_out,
        "warnings": warnings,
    }
    if dash.get("region"):
        env["region"] = dash["region"]
    return env
