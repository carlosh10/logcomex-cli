#!/usr/bin/env python3
"""lx — Logcomex platform CLI for Grok Bot agents.

Talks to https://platform.logcomex.ai (OpenAPI 3.1, logcomex.ai API v1.1.0).
JSON on stdout. Secrets live in ~/.config/lx/, never in argv if avoidable.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import LWPCookieJar
from pathlib import Path
from typing import Any

BASE = os.environ.get("LX_BASE", "https://platform.logcomex.ai").rstrip("/")
CFG = Path(os.environ.get("LX_HOME", Path.home() / ".config" / "lx"))
COOKIE_PATH = CFG / "cookies.txt"
SESSION_PATH = CFG / "session.json"
CURRENT_SCOPE_PATH = CFG / "current-scope.json"
SCOPES_DIR = CFG / "scopes"
LOOKS_DIR = CFG / "looks"
DASH_DIR = CFG / "dashboards"
UA = "lx-cli/0.2 (grok-bot)"

INCLUDE_FIELD_MAP = {
    "product": {
        "text": "query", "query": "query", "description": "description",
        "ncm": "ncm", "country": "origin_country", "origin": "origin_country",
        "origin_country": "origin_country", "importer": "importer", "party": "importer",
        "exporter": "exporter", "attr": "attribute", "attribute": "attribute",
        "place": "destination_port_name", "port": "destination_port_name", "period": "period",
    },
    "company": {"text": "query", "query": "query", "name": "query", "party": "query"},
    "shipment": {
        "text": "query", "query": "query", "ncm": "ncm", "hs": "hs_code", "hs_code": "hs_code",
        "country": "country", "origin": "origin_country", "origin_country": "origin_country",
        "importer": "importer", "party": "importer", "consignee": "consignee",
        "exporter": "exporter", "shipper": "shipper", "container": "container",
        "vessel": "vessel_or_flight", "place": "destination_port_name",
        "port": "destination_port_name", "flow": "flow", "period": "period",
    },
}

BY_ALIASES = {
    "month": "year_month", "year_month": "year_month", "importer": "importer",
    "exporter": "exporter", "origin": "origin_country", "origin_country": "origin_country",
    "ncm": "ncm", "country": "origin_country", "consignee": "consignee",
    "shipper": "shipper", "product": "product",
}

# Product origin_country wants a name; ISO-2 CL returns 200 with 0 rows.
ISO_ORIGIN = {
    "CL": "Chile", "AR": "Argentina", "UY": "Uruguai", "PY": "Paraguai",
    "CO": "Colombia", "PE": "Peru", "BR": "Brasil", "MX": "Mexico",
    "US": "Estados Unidos", "CN": "China", "IT": "Italia", "FR": "Franca",
    "ES": "Espanha", "PT": "Portugal", "DE": "Alemanha", "ZA": "Africa do Sul",
    "AU": "Australia", "NZ": "Nova Zelandia",
}

PRODUCT_ROW_KEYS = (
    "yearMonth", "ncm", "hsCode", "brand", "model", "importer", "exporter",
    "originCountry", "acquisitionCountry", "commercialUnit", "fob", "mean",
    "p20", "median", "p80", "label", "value", "key",
)
COMPANY_ROW_KEYS = (
    "entityId", "name", "code", "country", "city", "profile", "segment",
    "fob", "shipments", "weight", "activityLevel", "hasProfile",
)
SHIPMENT_ROW_KEYS = (
    "id", "date", "yearMonth", "ncm", "hsCode", "importer", "exporter",
    "consignee", "shipper", "originCountry", "destinationCountry",
    "originPort", "destinationPort", "container", "vessel", "vesselOrFlight",
    "fob", "cif", "freight", "flow", "label", "value", "key",
)
AGG_ROW_KEYS = ("key", "label", "value", "missing")

PRODUCT_FILTER_KEYS = {
    "query", "description", "keywords", "attribute", "ncm", "commercial_unit",
    "importer", "exporter", "manufacturer", "notify", "origin_country",
    "destination_port_name", "year", "month", "year_start", "month_start",
    "year_end", "month_end", "sort", "cursor", "limit", "period",
}
PRODUCT_ANALYSES_KEYS = (PRODUCT_FILTER_KEYS | {"dimension", "metric"}) - {"sort", "cursor"}
PRODUCT_GRAPH_KEYS = {
    "description", "keywords", "attribute", "ncm", "commercial_unit",
    "importer", "exporter", "manufacturer", "notify", "origin_country",
    "importer_state", "fob_min", "fob_max", "year", "month", "year_start",
    "month_start", "year_end", "month_end", "period", "metric", "limit", "language",
}
COMPANY_FILTER_KEYS = {"query", "sort", "cursor", "limit"}
SHIPMENT_BR_KEYS = {
    "sort", "cursor", "limit", "flow", "transport_mode", "query", "participant",
    "shipment", "ncm", "consignee", "shipper", "freight_forwarder",
    "international_freight_forwarder", "carrier", "notify", "origin_country",
    "destination_country", "origin_port", "destination_port", "loading_terminal",
    "discharge_terminal", "origin_port_name", "loading_port_name",
    "discharge_port_name", "destination_port_name", "payment_type",
    "shipment_type", "cargo_category", "state", "cargo_type", "container",
    "vessel_or_flight", "period", "operation_date_start", "operation_date_end",
    "year_month", "year", "month", "year_start", "month_start", "year_end",
    "month_end", "fob_min", "fob_max", "dimension", "metric", "language",
}
SHIPMENT_LATAM_KEYS = {
    "country", "dataset", "sort", "cursor", "limit", "flow", "query", "hs_code",
    "ncm", "importer", "exporter", "counterparty", "origin_country",
    "destination_country", "transport_mode", "carrier", "port", "customs",
    "customs_broker", "warehouse", "cargo_type", "incoterm", "customs_regime",
    "period", "operation_date_start", "operation_date_end", "year_month",
    "year", "month", "year_start", "month_start", "year_end", "month_end",
    "fob_min", "fob_max", "dimension", "metric", "language",
}


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


class _RejectArgvOtp(argparse.Action):
    """Refuse `lx login --code`; OTP must not appear on argv (history / ps)."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("OTP is not taken on argv; use --code-file PATH or enter it at the prompt")


def out(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def fail(payload: dict[str, Any], code: int = 1) -> None:
    out({"ok": False, **payload})
    raise SystemExit(code)


def load_session() -> dict[str, Any]:
    if not SESSION_PATH.exists():
        return {}
    try:
        return json.loads(SESSION_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save_session(data: dict[str, Any]) -> None:
    CFG.mkdir(parents=True, mode=0o700, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    SESSION_PATH.chmod(0o600)


def cookie_jar() -> LWPCookieJar:
    CFG.mkdir(parents=True, mode=0o700, exist_ok=True)
    jar = LWPCookieJar(str(COOKIE_PATH))
    if COOKIE_PATH.exists():
        try:
            # Reuse only still-valid cookies: skip discarded (session) and expired.
            jar.load(ignore_discard=False, ignore_expires=False)
        except OSError:
            pass
    return jar


def save_jar(jar: LWPCookieJar) -> None:
    # Persist only still-valid cookies; do not keep discarded or expired ones.
    jar.save(ignore_discard=False, ignore_expires=False)
    COOKIE_PATH.chmod(0o600)


def resolve_panel_out(explicit: str, layout: str) -> Path:
    if explicit:
        return Path(explicit)
    return Path.cwd() / ("intel-panel-%s.png" % layout.rsplit("-", 1)[-1])


def resolve_dashboard_out(explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    return Path.cwd()


def opener(jar: LWPCookieJar) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    auth: bool = True,
    fatal: bool = True,
) -> Any:
    jar = cookie_jar()
    op = opener(jar)
    pairs: list[tuple[str, str]] = []
    if query:
        for k, v in query.items():
            if v is None or v == "" or v == []:
                continue
            if isinstance(v, bool):
                pairs.append((k, "true" if v else "false"))
            elif isinstance(v, list):
                for x in v:
                    if x not in (None, ""):
                        pairs.append((k, str(x)))
            else:
                pairs.append((k, str(v)))
    url = BASE + path
    if pairs:
        url += "?" + urllib.parse.urlencode(pairs)
    headers = {
        "Accept": "application/json",
        "User-Agent": UA,
        "Origin": BASE,
        "Referer": BASE + "/",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    sess = load_session()
    token = sess.get("token") or os.environ.get("LX_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with op.open(req, timeout=90) as resp:
            raw = resp.read()
            save_jar(jar)
            if not raw:
                return {"ok": True, "status": resp.status}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "text": raw.decode("utf-8", "replace")[:4000]}
    except urllib.error.HTTPError as e:
        raw = e.read()
        save_jar(jar)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw.decode("utf-8", "replace")[:2000]}
        if e.code in (401, 403) and auth:
            die(json.dumps({
                "error": "unauthenticated",
                "status": e.code,
                "hint": "Run: lx login --email you@logcomex.com   then enter the OTP at the prompt or --code-file PATH",
                "body": payload,
            }, ensure_ascii=False), 2)
        err = {"error": "http", "status": e.code, "path": path, "body": payload}
        if fatal:
            die(json.dumps(err, ensure_ascii=False), 1)
        return err
    except urllib.error.URLError as e:
        err = {"error": "network", "detail": str(e.reason)}
        if fatal:
            die(json.dumps(err, ensure_ascii=False), 1)
        return err


def cmd_health(_: argparse.Namespace) -> None:
    out(request("GET", "/api/health", auth=False))


def _secret_from_file(path: str, *, what: str) -> str:
    text = Path(path).read_text().strip()
    if not text:
        die(f"{what}-file empty")
    return text


def _prompt_otp() -> str:
    try:
        code = getpass.getpass("OTP code: ").strip()
    except (EOFError, OSError):
        die("OTP is not taken on argv; use --code-file PATH or enter it at the prompt")
    if not code:
        die("empty OTP")
    return code


def _otp_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "code_file", None):
        return _secret_from_file(args.code_file, what="code")
    return _prompt_otp()


def _complete_otp_login(email: str, code: str) -> None:
    res = request("POST", "/api/auth/logcomex/email-otp/verify", body={"email": email, "code": code}, auth=False)
    _store_login(email, res)
    me = _whoami_quiet()
    _remember_workspace(me)
    out({"ok": True, "method": "otp", "me": me})


def cmd_login(args: argparse.Namespace) -> None:
    email = (args.email or "").strip().lower()
    if not email:
        die("lx login --email you@company.com [--code-file PATH | --password-file PATH]")
    if args.password_file:
        pw = _secret_from_file(args.password_file, what="password")
        if len(pw) < 10:
            die("password-file too short")
        res = request("POST", "/api/auth/login", body={"email": email, "password": pw}, auth=False)
        _store_login(email, res)
        me = _whoami_quiet()
        _remember_workspace(me)
        out({"ok": True, "method": "password", "me": me})
        return
    if args.code_file:
        _complete_otp_login(email, _otp_from_args(args))
        return
    res = request("POST", "/api/auth/logcomex/email-otp/request", body={"email": email}, auth=False)
    save_session({**load_session(), "email": email, "otp_pending": True})
    if sys.stdin.isatty():
        _complete_otp_login(email, _prompt_otp())
        return
    out({
        "ok": True,
        "otp_sent": True,
        "email": email,
        "next": f"lx login --email {email} --code-file PATH",
        "server": res,
    })


def _store_login(email: str, res: Any) -> None:
    sess = load_session()
    sess["email"] = email
    sess.pop("otp_pending", None)
    if isinstance(res, dict):
        for k in ("token", "access_token", "accessToken"):
            if res.get(k):
                sess["token"] = res[k]
        user = res.get("user") or res.get("me") or {}
        if isinstance(user, dict) and user.get("id"):
            sess["user"] = user
    save_session(sess)


def _remember_workspace(me: Any) -> None:
    if not isinstance(me, dict):
        return
    sess = load_session()
    name = me.get("tenantName") or me.get("tenant_name")
    tid = me.get("tenantId") or me.get("tenant_id")
    if tid or name:
        sess["workspace"] = {"id": tid, "name": name, "slug": me.get("tenantSlug")}
        save_session(sess)


def _whoami_quiet() -> Any:
    try:
        return request("GET", "/api/auth/me")
    except SystemExit:
        return None


def cmd_whoami(_: argparse.Namespace) -> None:
    me = request("GET", "/api/auth/me")
    sess = load_session()
    out({"me": me, "session_email": sess.get("email"), "workspace": sess.get("workspace")})


def cmd_logout(_: argparse.Namespace) -> None:
    try:
        request("POST", "/api/auth/logout", body={}, auth=False)
    except SystemExit:
        pass
    if COOKIE_PATH.exists():
        COOKIE_PATH.unlink()
    if SESSION_PATH.exists():
        SESSION_PATH.unlink()
    out({"ok": True})


def cmd_ws(args: argparse.Namespace) -> None:
    if args.ws_cmd == "ls":
        out(request("GET", "/api/workspaces"))
        return
    if args.ws_cmd == "use":
        target = (args.name or "").strip()
        if not target:
            die("lx ws use <name-or-id>")
        workspaces = request("GET", "/api/workspaces")
        rows = _as_list(workspaces)
        match = None
        t = target.lower()
        for w in rows:
            wid = str(w.get("id") or w.get("tenantId") or w.get("tenant_id") or "")
            name = str(w.get("name") or "")
            if t == wid.lower() or t == name.lower() or t in name.lower():
                match = w
                break
        if not match:
            die(json.dumps({"error": "workspace_not_found", "wanted": target, "available": [
                {"id": r.get("id") or r.get("tenantId"), "name": r.get("name")} for r in rows
            ]}, ensure_ascii=False))
        tid = str(match.get("id") or match.get("tenantId") or match.get("tenant_id"))
        res = request("POST", f"/api/workspaces/{urllib.parse.quote(tid)}/switch")
        sess = load_session()
        sess["workspace"] = {"id": tid, "name": match.get("name")}
        save_session(sess)
        out({"ok": True, "workspace": sess["workspace"], "server": res})
        return
    die("lx ws ls | lx ws use <name>")


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("items", "data", "results", "workspaces", "records", "rows", "details"):
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _common_query(args: argparse.Namespace, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {}
    for key in (
        "query", "ncm", "limit", "period", "sort", "cursor", "flow",
        "importer", "exporter", "origin_country", "container", "hs_code",
        "country", "year", "month", "year_start", "month_start", "year_end", "month_end",
        "participant", "shipment", "consignee", "shipper", "carrier",
        "destination_port", "origin_port", "vessel_or_flight", "dataset",
        "description", "commercial_unit",
    ):
        if hasattr(args, key):
            val = getattr(args, key)
            if val is not None and val != "":
                q[key] = val
    if getattr(args, "port", None):
        q.setdefault("destination_port_name", args.port)
        q.setdefault("port", args.port)
    if extra:
        q.update(extra)
    return q


def cmd_ncm(args: argparse.Namespace) -> None:
    q = _common_query(args)
    if args.code and not q.get("ncm"):
        q["ncm"] = args.code
    out(request("GET", "/api/market-intelligence/products", query=q))


def cmd_company(args: argparse.Namespace) -> None:
    if args.company_cmd == "get":
        if not args.id:
            die("lx company get <entity_id>")
        out(request("GET", f"/api/market-intelligence/companies/{urllib.parse.quote(args.id)}"))
        return
    q = _common_query(args)
    if args.q and not q.get("query"):
        q["query"] = args.q
    out(request("GET", "/api/market-intelligence/companies", query=q))


def cmd_shipments(args: argparse.Namespace) -> None:
    region = args.region
    q = _common_query(args)
    if region == "latam":
        if not q.get("country"):
            die("lx shipments latam --country AR|CL|CO|PE")
        path = "/api/market-intelligence/shipments-latam"
    elif region == "mx":
        path = "/api/market-intelligence/shipments-mexico"
    else:
        path = "/api/market-intelligence/shipments"
        if args.container:
            q["container"] = args.container
        if args.vessel:
            q["vessel_or_flight"] = args.vessel
    out(request("GET", path, query=q))


def cmd_comexstat(args: argparse.Namespace) -> None:
    if args.id:
        out(request("GET", f"/api/market-intelligence/market-scopes/{urllib.parse.quote(args.id)}"))
        return
    q = _common_query(args)
    if args.q and not q.get("query"):
        q["query"] = args.q
    out(request("GET", "/api/market-intelligence/market-scopes", query=q))


def cmd_tracking(args: argparse.Namespace) -> None:
    if args.tracking_cmd == "list":
        q = {"limit": args.limit or 25, "offset": args.offset or 0}
        if args.reference:
            q["reference"] = args.reference
        out(request("GET", "/api/operations/tracking/shipments", query=q))
        return
    q = {}
    if args.modal:
        q["modal"] = args.modal
    out(request("GET", "/api/operations/tracking/control-tower", query=q))


def cmd_ocr(_: argparse.Namespace) -> None:
    out(request("GET", "/api/files/document-conference-dashboard"))


def cmd_analyses(args: argparse.Namespace) -> None:
    path = {
        "ncm": "/api/market-intelligence/products/analyses",
        "company": "/api/market-intelligence/company-analyses",
        "shipments": "/api/market-intelligence/shipments/analyses",
        "latam": "/api/market-intelligence/shipments-latam/analyses",
        "mx": "/api/market-intelligence/shipments-mexico/analyses",
    }[args.kind]
    out(request("GET", path, query=_common_query(args)))


def _ensure_cfg() -> None:
    CFG.mkdir(parents=True, mode=0o700, exist_ok=True)
    SCOPES_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    LOOKS_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    DASH_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)


def _safe_scope_name(name: str) -> str:
    n = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", n or ""):
        fail({"error": "invalid_scope_name", "name": name, "hint": "use letters, digits, . _ -"})
    return n


def load_current_scope() -> dict[str, Any] | None:
    if not CURRENT_SCOPE_PATH.exists():
        return None
    try:
        data = json.loads(CURRENT_SCOPE_PATH.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def save_current_scope(scope: dict[str, Any]) -> None:
    _ensure_cfg()
    CURRENT_SCOPE_PATH.write_text(json.dumps(scope, indent=2, ensure_ascii=False) + "\n")
    CURRENT_SCOPE_PATH.chmod(0o600)


def named_scope_path(name: str) -> Path:
    return SCOPES_DIR / f"{_safe_scope_name(name)}.json"


def require_scope() -> dict[str, Any]:
    scope = load_current_scope()
    if not scope or not scope.get("entity"):
        fail({
            "error": "no_scope",
            "hint": "lx find product|company|shipment [filters]   then   lx view / lx rule / lx watch",
        })
    return scope


def envelope_scope(scope: dict[str, Any] | None) -> dict[str, Any] | None:
    if not scope:
        return None
    out_s: dict[str, Any] = {
        "entity": scope.get("entity"),
        "filters": scope.get("filters") or {},
        "rules": scope.get("rules") or [],
    }
    if scope.get("region"):
        out_s["region"] = scope["region"]
    return out_s


def parse_include(raw: str) -> tuple[str, str]:
    s = (raw or "").strip()
    if not s:
        fail({"error": "empty_include", "hint": '--include "field: value"'})
    for sep in (":", "="):
        if sep in s:
            field, value = s.split(sep, 1)
            field, value = field.strip().lower(), value.strip()
            if field and value:
                return field, value
    parts = s.split(None, 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        fail({
            "error": "bad_include",
            "got": raw,
            "hint": '--include "field: value"  or  field=value  or  field value',
        })
    return parts[0].strip().lower(), parts[1].strip()


def expand_origin_country(value: str, entity: str, region: str | None) -> tuple[str, str | None]:
    raw = (value or "").strip()
    if entity == "shipment" and (region or "") == "latam":
        return raw, None
    if entity != "product":
        return raw, None
    code = raw.upper()
    if len(code) == 2 and code in ISO_ORIGIN and raw.isalpha():
        mapped = ISO_ORIGIN[code]
        return mapped, (
            f"mapped country {raw} -> origin_country={mapped} "
            f"(ISO code {code} returns 0 rows on products)"
        )
    return raw, None


def apply_rules_to_query(scope: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    entity = scope.get("entity") or "product"
    fmap = INCLUDE_FIELD_MAP.get(entity, {})
    q = dict(scope.get("filters") or {})
    warnings: list[str] = []
    rule_keys: list[str] = []
    for rule in scope.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        verb = (rule.get("verb") or "include").lower()
        if verb != "include":
            warnings.append(f"skipped rule {rule.get('name')}: verb {verb} is not include")
            continue
        field = str(rule.get("field") or "").strip().lower()
        value = rule.get("value")
        if not field or value in (None, ""):
            continue
        api_field = fmap.get(field, field)
        val = str(value)
        if api_field == "origin_country":
            val, note = expand_origin_country(val, entity, scope.get("region"))
            if note:
                warnings.append(note)
        if api_field == "query" and entity == "product":
            q["query"] = val
            q.pop("description", None)
            rule_keys.append("query")
        elif api_field == "attribute":
            existing = q.get("attribute")
            if isinstance(existing, list):
                q["attribute"] = existing + [val]
            elif existing:
                q["attribute"] = [existing, val]
            else:
                q["attribute"] = [val]
            rule_keys.append("attribute")
        else:
            q[api_field] = val
            rule_keys.append(api_field)
    return q, warnings, rule_keys


def filter_query(q: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {k: v for k, v in q.items() if k in allowed and v not in (None, "", [])}


def shipment_paths(region: str | None) -> dict[str, str]:
    r = (region or "br").lower()
    if r == "latam":
        base = "/api/market-intelligence/shipments-latam"
    elif r == "mx":
        base = "/api/market-intelligence/shipments-mexico"
    else:
        base = "/api/market-intelligence/shipments"
    return {"rows": base, "analyses": base + "/analyses", "graph": base + "/graph"}


def shipment_allowed(region: str | None) -> set[str]:
    if (region or "br").lower() in ("latam", "mx"):
        return set(SHIPMENT_LATAM_KEYS)
    return set(SHIPMENT_BR_KEYS)


def normalize_shipment_query(q: dict[str, Any], region: str | None) -> dict[str, Any]:
    out_q = dict(q)
    r = (region or "br").lower()
    if r in ("latam", "mx") and out_q.get("ncm") and not out_q.get("hs_code"):
        out_q["hs_code"] = out_q["ncm"]
    if r in ("latam", "mx") and out_q.get("destination_port_name") and not out_q.get("port"):
        out_q["port"] = out_q["destination_port_name"]
    if r == "br" and out_q.get("port") and not out_q.get("destination_port_name"):
        out_q["destination_port_name"] = out_q["port"]
    if r == "br" and out_q.get("vessel") and not out_q.get("vessel_or_flight"):
        out_q["vessel_or_flight"] = out_q["vessel"]
    return out_q


def pick_rows(payload: dict[str, Any]) -> list[Any]:
    for k in ("details", "items", "rows", "data", "records"):
        v = payload.get(k)
        if isinstance(v, list):
            return v
    return []


def project_row(row: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(row, dict):
        return row
    out_r: dict[str, Any] = {}
    for k in keys:
        v = row.get(k)
        if v not in (None, "", [], {}):
            out_r[k] = v
    if "fob" not in out_r:
        for alt in ("mean", "value", "p80", "median"):
            if row.get(alt) not in (None, ""):
                out_r["fob"] = row[alt]
                break
    return out_r


def compact_totals(totals: Any) -> Any:
    if not isinstance(totals, dict):
        return totals
    return {k: v for k, v in totals.items() if v is not None}


def compact_coverage(cov: Any) -> Any:
    if not isinstance(cov, dict):
        return cov
    keep = ("yearMonthStart", "yearMonthEnd", "records", "returned", "partial")
    return {k: cov[k] for k in keep if k in cov}


def compact_graph(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    nodes_out: list[dict[str, Any]] = []
    for n in payload.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        nodes_out.append({
            "id": n.get("id"),
            "label": n.get("label"),
            "type": n.get("type"),
            "value": n.get("value"),
        })
        if len(nodes_out) >= limit:
            break
    keep = {n.get("id") for n in nodes_out}
    edges_out: list[dict[str, Any]] = []
    for e in payload.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src = e.get("source") or e.get("from")
        tgt = e.get("target") or e.get("to")
        if src not in keep or tgt not in keep:
            continue
        edge: dict[str, Any] = {"from": src, "to": tgt, "value": e.get("value")}
        if e.get("type"):
            edge["type"] = e["type"]
        edges_out.append(edge)
        if len(edges_out) >= limit:
            break
    return {"nodes": nodes_out, "edges": edges_out}


def add_partial_warning(coverage: Any, pagination: Any, data_len: int, warnings: list[str], next_cursor: Any = None) -> None:
    """Warn on a truncated page. Do not compare coverage.returned vs coverage.records
    on analyses: those are bucket count vs universe row count."""
    cov = coverage if isinstance(coverage, dict) else {}
    pag = pagination if isinstance(pagination, dict) else {}
    msg = "partial page; do not sum data as the universe — use totals/agg/series"
    if msg in warnings:
        return
    if cov.get("partial"):
        warnings.append(msg)
        return
    if next_cursor or pag.get("nextCursor"):
        warnings.append(msg)
        return
    try:
        pret = pag.get("returned")
        ptot = pag.get("totalRecords")
        if pret is not None and ptot is not None and int(pret) < int(ptot):
            warnings.append(msg)
            return
    except (TypeError, ValueError):
        pass
    # rows list with no pagination: compare page length to coverage.records
    if not pag and data_len and cov.get("records") is not None:
        try:
            if int(data_len) < int(cov["records"]):
                warnings.append(msg)
        except (TypeError, ValueError):
            pass


def is_http_error(res: Any) -> bool:
    return isinstance(res, dict) and res.get("error") in ("http", "network")


def request_dropping_rules(path: str, query: dict[str, Any], rule_keys: list[str]) -> tuple[Any, list[str]]:
    extras: list[str] = []
    q = dict(query)
    dropped: list[str] = []
    res: Any = None
    for _ in range(len(rule_keys) + 1):
        res = request("GET", path, query=q, fatal=False)
        if not is_http_error(res):
            if dropped:
                extras.append("dropped failing rule params from query (kept in scope): " + ", ".join(dropped))
            return res, extras
        status = res.get("status")
        if status not in (400, 422) or not rule_keys:
            return res, extras
        key = None
        for candidate in reversed(rule_keys):
            if candidate in q and candidate not in dropped:
                key = candidate
                break
        if not key:
            return res, extras
        q.pop(key, None)
        dropped.append(key)
        extras.append(f"retrying without {key} after HTTP {status}")
    return res, extras


def make_envelope(
    *,
    entity: str,
    scope: dict[str, Any] | None,
    payload: Any,
    data: Any,
    extra_warnings: list[str] | None = None,
    by: str | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    contract = None
    coverage = None
    totals = None
    nxt = None
    if isinstance(payload, dict) and not is_http_error(payload):
        contract = payload.get("contract") or payload.get("schema")
        coverage = compact_coverage(payload.get("coverage"))
        totals = compact_totals(payload.get("totals"))
        if totals is None and payload.get("total") is not None:
            tot = payload.get("total")
            totals = compact_totals(tot) if isinstance(tot, dict) else {"records": tot}
        nxt = payload.get("nextCursor")
        pag = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        if nxt is None:
            nxt = pag.get("nextCursor")
        server_w = payload.get("warnings") or []
        if isinstance(server_w, list):
            warnings.extend(str(w) for w in server_w if w)
        data_len = len(data) if isinstance(data, list) else (
            len((data or {}).get("nodes") or []) if isinstance(data, dict) else 0
        )
        add_partial_warning(payload.get("coverage"), pag, data_len, warnings, nxt)
    if extra_warnings:
        for w in extra_warnings:
            if w and w not in warnings:
                warnings.append(w)
    env: dict[str, Any] = {
        "ok": not is_http_error(payload),
        "contract": contract,
        "entity": entity,
        "scope": envelope_scope(scope) if scope else None,
        "coverage": coverage,
        "totals": totals,
        "data": data,
        "next": nxt,
        "warnings": warnings,
    }
    if by:
        env["by"] = by
    if metric:
        env["metric"] = metric
    if is_http_error(payload):
        env["error"] = payload.get("error")
        env["status"] = payload.get("status")
        env["body"] = payload.get("body")
        env["ok"] = False
    return env


def resolve_by(raw: str | None, kind: str, entity: str) -> str:
    if raw:
        key = raw.strip().lower()
        if key not in BY_ALIASES:
            fail({
                "error": "unknown_by",
                "got": raw,
                "hint": "month|year_month|importer|exporter|origin|origin_country|ncm|country|consignee",
            })
        dim = BY_ALIASES[key]
    elif kind == "series":
        dim = "year_month"
    elif entity == "shipment":
        dim = "importer"
    else:
        dim = "importer"
    return "year_month" if dim == "month" else dim


def intel_query(scope: dict[str, Any], extra: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    q, warnings, rule_keys = apply_rules_to_query(scope)
    if extra:
        for k, v in extra.items():
            if v is not None and v != "":
                q[k] = v
    if scope.get("entity") == "shipment":
        q = normalize_shipment_query(q, scope.get("region"))
    return q, warnings, rule_keys


def company_view_unsupported(kind: str) -> None:
    fail({
        "error": "unsupported",
        "entity": "company",
        "kind": kind,
        "hint": "only rows and profile work for company without backend; /company-analyses is saved chat jobs, not an aggregate",
    })


def fetch_rows(scope: dict[str, Any], extra: dict[str, Any] | None = None) -> tuple[Any, list[str]]:
    q, warnings, rule_keys = intel_query(scope, extra)
    entity = scope.get("entity")
    if entity == "company":
        q = filter_query(q, COMPANY_FILTER_KEYS)
        return request("GET", "/api/market-intelligence/companies", query=q, fatal=False), warnings
    if entity == "shipment":
        region = scope.get("region") or "br"
        if region == "latam" and not q.get("country"):
            fail({"error": "country_required", "hint": "lx find shipment --region latam --country CL|AR|CO|PE"})
        q = filter_query(q, shipment_allowed(region))
        res, extra_w = request_dropping_rules(shipment_paths(region)["rows"], q, rule_keys)
        return res, warnings + extra_w
    q = filter_query(q, PRODUCT_FILTER_KEYS)
    res, extra_w = request_dropping_rules("/api/market-intelligence/products", q, rule_keys)
    return res, warnings + extra_w


def fetch_analyses(scope: dict[str, Any], *, dimension: str, metric: str, limit: int | None) -> tuple[Any, list[str], str]:
    extra: dict[str, Any] = {"dimension": dimension, "metric": metric}
    if limit is not None:
        extra["limit"] = limit
    q, warnings, rule_keys = intel_query(scope, extra)
    entity = scope.get("entity")
    if entity == "company":
        company_view_unsupported("agg/series")
    if entity == "shipment":
        region = scope.get("region") or "br"
        if region == "latam" and not q.get("country"):
            fail({"error": "country_required", "hint": "find shipment --region latam --country CL"})
        path = shipment_paths(region)["analyses"]
        q1 = filter_query(q, shipment_allowed(region))
        res, extra_w = request_dropping_rules(path, q1, rule_keys)
        warnings.extend(extra_w)
        if is_http_error(res) and res.get("status") in (400, 422) and dimension == "importer":
            q2 = dict(q1)
            q2["dimension"] = "consignee"
            res2 = request("GET", path, query=q2, fatal=False)
            if not is_http_error(res2):
                warnings.append("dimension=importer 400'd; used consignee")
                return res2, warnings, "consignee"
        return res, warnings, dimension
    if q.get("query") and q.get("description"):
        q.pop("description", None)
    q = filter_query(q, PRODUCT_ANALYSES_KEYS)
    res, extra_w = request_dropping_rules("/api/market-intelligence/products/analyses", q, rule_keys)
    return res, warnings + extra_w, dimension


def fetch_graph(scope: dict[str, Any], *, metric: str, limit: int) -> tuple[Any, list[str]]:
    extra: dict[str, Any] = {"metric": metric, "limit": limit}
    q, warnings, rule_keys = intel_query(scope, extra)
    entity = scope.get("entity")
    if entity == "company":
        company_view_unsupported("graph")
    if entity == "shipment":
        region = scope.get("region") or "br"
        if region == "latam" and not q.get("country"):
            fail({"error": "country_required", "hint": "find shipment --region latam --country CL"})
        q = filter_query(q, shipment_allowed(region))
        res, extra_w = request_dropping_rules(shipment_paths(region)["graph"], q, rule_keys)
        return res, warnings + extra_w
    if q.get("query") and q.get("description"):
        q.pop("description", None)
    q = filter_query(q, PRODUCT_GRAPH_KEYS)
    res, extra_w = request_dropping_rules("/api/market-intelligence/products/graph", q, rule_keys)
    return res, warnings + extra_w


def compact_view_data(kind: str, entity: str, payload: Any, limit: int) -> Any:
    if is_http_error(payload) or not isinstance(payload, dict):
        return None
    if kind == "graph":
        return compact_graph(payload, limit)
    rows = pick_rows(payload)
    if kind in ("agg", "series"):
        return [project_row(r, AGG_ROW_KEYS) for r in rows[:limit]]
    if entity == "company":
        return [project_row(r, COMPANY_ROW_KEYS) for r in rows[:limit]]
    if entity == "shipment":
        return [project_row(r, SHIPMENT_ROW_KEYS) for r in rows[:limit]]
    return [project_row(r, PRODUCT_ROW_KEYS) for r in rows[:limit]]


def build_find_scope(args: argparse.Namespace) -> dict[str, Any]:
    entity = args.entity
    rules = []
    if getattr(args, "keep_rules", False):
        prev = load_current_scope()
        if prev:
            rules = list(prev.get("rules") or [])
    filters: dict[str, Any] = {}
    scope: dict[str, Any] = {"entity": entity, "filters": filters, "rules": rules}
    text = getattr(args, "text", None) or getattr(args, "text_pos", None)

    if entity == "company":
        if text:
            filters["query"] = text
        if args.limit is not None:
            filters["limit"] = args.limit
        return scope

    if entity == "shipment":
        region = getattr(args, "region", None) or "br"
        scope["region"] = region
        if text:
            filters["query"] = text
        if getattr(args, "ncm", None):
            filters["ncm"] = args.ncm
        if getattr(args, "country", None):
            filters["country"] = args.country
        if getattr(args, "container", None):
            filters["container"] = args.container
        if getattr(args, "vessel", None):
            filters["vessel_or_flight"] = args.vessel
        if getattr(args, "port", None):
            if region in ("latam", "mx"):
                filters["port"] = args.port
            else:
                filters["destination_port_name"] = args.port
        if getattr(args, "flow", None):
            filters["flow"] = args.flow
        if getattr(args, "period", None):
            filters["period"] = args.period
        if args.limit is not None:
            filters["limit"] = args.limit
        if getattr(args, "cursor", None):
            filters["cursor"] = args.cursor
        if region == "latam" and not filters.get("country"):
            fail({"error": "country_required", "hint": "lx find shipment --region latam --country CL|AR|CO|PE"})
        return scope

    if text:
        filters["query"] = text
    if getattr(args, "ncm", None):
        filters["ncm"] = args.ncm
    if getattr(args, "importer", None):
        filters["importer"] = args.importer
    if getattr(args, "exporter", None):
        filters["exporter"] = args.exporter
    if getattr(args, "country", None):
        filters["origin_country"] = args.country
    for key in ("period", "sort", "cursor", "year", "month", "year_start", "month_start", "year_end", "month_end"):
        val = getattr(args, key, None)
        if val is not None and val != "":
            filters[key] = val
    if args.limit is not None:
        filters["limit"] = args.limit
    return scope


def cmd_find(args: argparse.Namespace) -> None:
    if not args.entity:
        scope = load_current_scope()
        out({
            "ok": True,
            "scope": envelope_scope(scope),
            "hint": (
                "lx find product|company|shipment [filters]   then   lx view rows|agg|series|graph"
                if not scope else
                "current scope set — lx view rows|agg|series|graph   or   lx rule add NAME --include 'field: value'"
            ),
        })
        return
    scope = build_find_scope(args)
    save_current_scope(scope)
    extra: dict[str, Any] = {}
    if args.limit is not None:
        extra["limit"] = args.limit
    if getattr(args, "cursor", None):
        extra["cursor"] = args.cursor
    payload, warnings = fetch_rows(scope, extra)
    if getattr(args, "raw", False):
        out(payload)
        return
    if is_http_error(payload):
        fail({
            "error": payload.get("error"),
            "status": payload.get("status"),
            "path": payload.get("path"),
            "body": payload.get("body"),
            "scope": envelope_scope(scope),
            "warnings": warnings,
        })
    data = compact_view_data("rows", scope["entity"], payload, args.limit or 25)
    out(make_envelope(entity=scope["entity"], scope=scope, payload=payload, data=data, extra_warnings=warnings))


def cmd_scope(args: argparse.Namespace) -> None:
    cmd = args.scope_cmd
    if cmd == "ls":
        _ensure_cfg()
        names = sorted(p.stem for p in SCOPES_DIR.glob("*.json"))
        out({"ok": True, "scopes": names, "current": envelope_scope(load_current_scope())})
        return
    if cmd == "show":
        name = getattr(args, "name", None)
        if name:
            path = named_scope_path(name)
            if not path.exists():
                fail({"error": "scope_not_found", "name": name})
            out({"ok": True, "name": name, "scope": json.loads(path.read_text())})
            return
        out({"ok": True, "path": str(CURRENT_SCOPE_PATH), "scope": envelope_scope(load_current_scope())})
        return
    if cmd == "save":
        scope = require_scope()
        path = named_scope_path(args.name)
        _ensure_cfg()
        path.write_text(json.dumps(scope, indent=2, ensure_ascii=False) + "\n")
        path.chmod(0o600)
        out({"ok": True, "saved": args.name, "path": str(path), "scope": envelope_scope(scope)})
        return
    if cmd == "use":
        path = named_scope_path(args.name)
        if not path.exists():
            fail({"error": "scope_not_found", "name": args.name})
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not data.get("entity"):
            fail({"error": "invalid_scope_file", "name": args.name})
        save_current_scope(data)
        out({"ok": True, "using": args.name, "scope": envelope_scope(data)})
        return
    if cmd == "rm":
        path = named_scope_path(args.name)
        if path.exists():
            path.unlink()
        out({"ok": True, "removed": args.name})
        return
    fail({"error": "unknown_scope_cmd", "hint": "lx scope ls|show|save NAME|use NAME|rm NAME"})


def _reject_exclude(verb: str, raw: str | None) -> None:
    fail({
        "error": "unsupported_verb",
        "verb": verb,
        "got": raw,
        "hint": (
            "backend cannot apply does-not-include to the full universe; "
            "do not silently filter the page and pretend totals changed"
        ),
    })


def cmd_rule(args: argparse.Namespace) -> None:
    cmd = args.rule_cmd
    if cmd == "ls":
        scope = load_current_scope()
        out({"ok": True, "scope": envelope_scope(scope), "rules": (scope or {}).get("rules") or []})
        return
    if cmd == "rm":
        scope = require_scope()
        name = args.name
        scope["rules"] = [r for r in (scope.get("rules") or []) if not (isinstance(r, dict) and r.get("name") == name)]
        save_current_scope(scope)
        out({"ok": True, "removed": name, "scope": envelope_scope(scope)})
        return
    if cmd != "add":
        fail({"error": "unknown_rule_cmd", "hint": "lx rule add NAME --include 'field: value' | ls | rm NAME"})
    if getattr(args, "does_not_include", None):
        _reject_exclude("does-not-include", args.does_not_include)
    if getattr(args, "exclude", None):
        _reject_exclude("exclude", args.exclude)
    if not args.include:
        fail({"error": "need_include", "hint": "lx rule add NAME --include 'field: value'"})
    scope = require_scope()
    field, value = parse_include(args.include)
    verb = "include"
    rule = {"name": args.name, "verb": verb, "field": field, "value": value}
    rules = [r for r in (scope.get("rules") or []) if not (isinstance(r, dict) and r.get("name") == args.name)]
    rules.append(rule)
    scope["rules"] = rules
    save_current_scope(scope)
    out({"ok": True, "added": rule, "scope": envelope_scope(scope)})


def cmd_view(args: argparse.Namespace) -> None:
    scope = require_scope()
    entity = scope.get("entity") or "product"
    kind = args.kind
    metric = args.metric or "fob"
    limit = args.limit if args.limit is not None else (40 if kind == "graph" else 25)
    if kind == "rows":
        extra: dict[str, Any] = {"limit": limit}
        if args.cursor:
            extra["cursor"] = args.cursor
        payload, warnings = fetch_rows(scope, extra)
        if args.raw:
            out(payload)
            return
        data = compact_view_data("rows", entity, payload, limit)
        env = make_envelope(entity=entity, scope=scope, payload=payload, data=data, extra_warnings=warnings)
        if not env["ok"]:
            fail(env)
        out(env)
        return
    if kind == "graph":
        payload, warnings = fetch_graph(scope, metric=metric, limit=limit)
        if args.raw:
            out(payload)
            return
        data = compact_view_data("graph", entity, payload, limit)
        env = make_envelope(entity=entity, scope=scope, payload=payload, data=data, extra_warnings=warnings, metric=metric)
        if not env["ok"]:
            fail(env)
        out(env)
        return
    dimension = resolve_by(args.by, kind, entity)
    payload, warnings, used = fetch_analyses(scope, dimension=dimension, metric=metric, limit=limit)
    if args.raw:
        out(payload)
        return
    data = compact_view_data(kind, entity, payload, limit)
    env = make_envelope(
        entity=entity, scope=scope, payload=payload, data=data,
        extra_warnings=warnings, by=used, metric=metric,
    )
    if not env["ok"]:
        fail(env)
    out(env)


def _view_compact_block(kind: str, scope: dict[str, Any]) -> dict[str, Any]:
    entity = scope.get("entity") or "product"
    metric = "fob"
    if kind == "series":
        payload, warnings, used = fetch_analyses(scope, dimension="year_month", metric=metric, limit=24)
        data = compact_view_data("series", entity, payload, 24)
        env = make_envelope(entity=entity, scope=scope, payload=payload, data=data, extra_warnings=warnings, by=used, metric=metric)
    else:
        dimension = resolve_by(None, "agg", entity)
        payload, warnings, used = fetch_analyses(scope, dimension=dimension, metric=metric, limit=10)
        data = compact_view_data("agg", entity, payload, 10)
        env = make_envelope(entity=entity, scope=scope, payload=payload, data=data, extra_warnings=warnings, by=used, metric=metric)
    return {
        "ok": env.get("ok"),
        "by": env.get("by"),
        "metric": env.get("metric"),
        "coverage": env.get("coverage"),
        "totals": env.get("totals"),
        "data": env.get("data"),
        "warnings": env.get("warnings"),
        **({"error": env.get("error"), "status": env.get("status"), "body": env.get("body")} if not env.get("ok") else {}),
    }


def cmd_watch(_: argparse.Namespace) -> None:
    scope = require_scope()
    series = _view_compact_block("series", scope)
    agg = _view_compact_block("agg", scope)
    out({
        "ok": bool(series.get("ok") and agg.get("ok")),
        "scope": envelope_scope(scope),
        "series": series,
        "agg": agg,
    })


def cmd_profile(args: argparse.Namespace) -> None:
    kind = args.kind
    raw = getattr(args, "raw", False)
    if kind == "company":
        eid = args.id
        if not eid:
            fail({"error": "need_id", "hint": "lx profile company ENTITY_ID"})
        payload = request("GET", f"/api/market-intelligence/companies/{urllib.parse.quote(eid)}", fatal=False)
        if raw:
            out(payload)
            return
        if is_http_error(payload):
            fail({"error": payload.get("error"), "status": payload.get("status"), "body": payload.get("body")})
        data = project_row(payload, COMPANY_ROW_KEYS) if isinstance(payload, dict) else payload
        if isinstance(payload, dict) and isinstance(payload.get("artifacts"), list):
            arts = []
            for a in payload["artifacts"][:8]:
                if isinstance(a, dict):
                    arts.append({k: a.get(k) for k in ("id", "title", "kind", "section") if a.get(k)})
            if isinstance(data, dict) and arts:
                data["artifacts"] = arts
        out(make_envelope(entity="company", scope=None, payload=payload, data=data))
        return
    if kind == "product":
        ncm = args.ncm or args.id
        if not ncm:
            fail({"error": "need_ncm", "hint": "lx profile product --ncm CODE"})
        q = {"ncm": ncm, "period": getattr(args, "period", None) or "12m", "limit": args.limit or 5}
        payload = request("GET", "/api/market-intelligence/products", query=q, fatal=False)
        if raw:
            out(payload)
            return
        if is_http_error(payload):
            fail({"error": payload.get("error"), "status": payload.get("status"), "body": payload.get("body")})
        data = compact_view_data("rows", "product", payload, q["limit"])
        fake_scope = {"entity": "product", "filters": {"ncm": ncm, "period": q["period"]}, "rules": []}
        out(make_envelope(entity="product", scope=fake_scope, payload=payload, data=data))
        return
    token = args.id
    if not token:
        fail({"error": "need_token", "hint": "lx profile shipment TOKEN"})
    payload = request(
        "GET",
        f"/api/operations/tracking/shipments/{urllib.parse.quote(token)}",
        fatal=False,
    )
    if raw:
        out(payload)
        return
    if is_http_error(payload):
        fail({
            "error": "unsupported",
            "entity": "shipment",
            "hint": "tracking detail GET /api/operations/tracking/shipments/{token} is unavailable or failed",
            "status": payload.get("status"),
            "body": payload.get("body"),
        })
    data = payload
    if isinstance(payload, dict):
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        shipment = payload.get("shipment") if isinstance(payload.get("shipment"), dict) else {}
        route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
        tl = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
        events = []
        for ev in tl[:8]:
            if isinstance(ev, dict):
                events.append({k: ev.get(k) for k in ("description", "dateTime", "actualAt", "status", "eventSlug") if ev.get(k)})
        data = {
            "token": shipment.get("token") or summary.get("token") or payload.get("token"),
            "reference": summary.get("reference") or shipment.get("reference"),
            "status": summary.get("status") or payload.get("status"),
            "modal": summary.get("modal") or shipment.get("modal"),
            "eta": summary.get("eta") or (route.get("dates") or {}).get("eta") if isinstance(route.get("dates"), dict) else summary.get("eta"),
            "etd": summary.get("etd"),
            "carrier": route.get("carrier"),
            "origin": (route.get("origin") or {}).get("name") if isinstance(route.get("origin"), dict) else route.get("origin"),
            "destination": (route.get("destination") or {}).get("name") if isinstance(route.get("destination"), dict) else route.get("destination"),
            "transportDocument": summary.get("transportDocument") or shipment.get("transportDocument"),
            "timeline": events,
        }
        data = {k: v for k, v in data.items() if v not in (None, "", [], {})}
    out(make_envelope(entity="shipment", scope=None, payload=payload, data=data))


def add_period_flags(p: argparse.ArgumentParser, *, default_period: str | None = "latest", default_limit: int | None = 25) -> None:
    p.add_argument("--period", default=default_period, choices=["latest", "3m", "6m", "12m", "all"])
    p.add_argument("--limit", type=int, default=default_limit)
    p.add_argument("--sort")
    p.add_argument("--cursor")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)
    p.add_argument("--year-start", dest="year_start", type=int)
    p.add_argument("--month-start", dest="month_start", type=int)
    p.add_argument("--year-end", dest="year_end", type=int)
    p.add_argument("--month-end", dest="month_end", type=int)



def cmd_panel(args: argparse.Namespace) -> None:
    import panel_build
    layout = {
        "breaks": "universe-selection-breaks",
        "stacks": "universe-selection-stacks",
        "lines": "universe-selection-lines",
    }.get(args.layout, args.layout)
    dims = [x.strip() for x in (args.breaks or "importer,exporter").split(",") if x.strip()] or ["importer", "exporter"]
    if len(dims) == 1:
        dims.append("exporter")
    payload = panel_build.build(layout, dims, args.limit or 5)
    out = resolve_panel_out(args.out, layout)
    panel_build.render_file(payload, out)
    env = {"ok": True, "layout": layout, "out": str(out), "title": payload.get("title"), "selection": (payload.get("selection") or {}).get("label")}
    out_json = dict(env)
    # keep png path for the agent to attach
    print(json.dumps(out_json, ensure_ascii=False, indent=2))


def cmd_look(args: argparse.Namespace) -> None:
    import catalog
    catalog.ensure_dirs()
    cmd = args.look_cmd
    if cmd == "ls":
        out({
            "ok": True,
            "storage": "local",
            "looks": catalog.list_looks(),
            "builtin": list(catalog.BUILTIN_LOOKS),
        })
        return
    if cmd == "show":
        data = catalog.load_look(args.name)
        out({"ok": True, "storage": data.get("storage") or "local", "look": data})
        return
    if cmd == "rm":
        name = _safe_scope_name(args.name)
        if name in catalog.BUILTIN_LOOKS and not catalog.look_path(name).exists():
            fail({"error": "builtin_look", "name": name, "hint": "built-in looks cannot be removed"})
        catalog.remove_look(name)
        out({"ok": True, "removed": name, "storage": "local"})
        return
    if cmd != "save":
        fail({"error": "unknown_look_cmd", "hint": "lx look save NAME --view|--layout | ls | show NAME | rm NAME"})
    if bool(getattr(args, "view", None)) == bool(getattr(args, "layout", None)):
        fail({
            "error": "need_view_or_layout",
            "hint": "lx look save NAME --view rows|agg|series|graph  OR  --layout breaks|stacks|lines",
        })
    scope = require_scope()
    look = catalog.look_from_scope(
        args.name,
        scope,
        view=args.view,
        layout=args.layout,
        by=getattr(args, "by", None),
        metric=getattr(args, "metric", None) or "fob",
        limit=getattr(args, "limit", None),
        breaks=getattr(args, "breaks", None),
    )
    path = catalog.save_look(look)
    out({"ok": True, "saved": look["name"], "path": str(path), "storage": "local", "look": look})


def cmd_dashboard(args: argparse.Namespace) -> None:
    import catalog
    catalog.ensure_dirs()
    cmd = args.dashboard_cmd
    if cmd == "ls":
        out({"ok": True, "storage": "local", "dashboards": catalog.list_dashboards()})
        return
    if cmd == "rm":
        name = _safe_scope_name(args.name)
        catalog.remove_dashboard(name)
        out({"ok": True, "removed": name, "storage": "local"})
        return
    if cmd == "save":
        scope = require_scope()
        tokens = [x.strip() for x in (args.looks or "breaks,stacks,lines").split(",") if x.strip()]
        if not tokens:
            tokens = ["breaks", "stacks", "lines"]
        dash = catalog.dashboard_from_scope(args.name, scope, tokens)
        path = catalog.save_dashboard(dash)
        out({"ok": True, "saved": dash["name"], "path": str(path), "storage": "local", "dashboard": dash})
        return
    if cmd == "show":
        import panel_build
        dash = catalog.load_dashboard(args.name)
        overrides: dict[str, Any] = {}
        if getattr(args, "period", None):
            overrides["period"] = args.period
        if getattr(args, "ncm", None):
            overrides["ncm"] = args.ncm
        if getattr(args, "text", None):
            overrides["text"] = args.text
        out_dir = resolve_dashboard_out(args.out)
        env = catalog.show_dashboard(dash, overrides=overrides, out_dir=out_dir, panel_build=panel_build)
        if not env.get("ok"):
            fail(env)
        out(env)
        return
    fail({"error": "unknown_dashboard_cmd", "hint": "lx dashboard save NAME | ls | show NAME | rm NAME"})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lx",
        description="Logcomex platform CLI for agents. Prefer find/scope/rule/view/profile/watch/panel/look/dashboard.",
        allow_abbrev=False,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="unauthenticated health")
    sub.add_parser("whoami", help="current user + workspace")
    sub.add_parser("logout")

    login = sub.add_parser("login", help="email OTP (prompt or --code-file; never on argv)")
    login.add_argument("--email", required=True)
    login.add_argument(
        "--code",
        action=_RejectArgvOtp,
        help=argparse.SUPPRESS,
    )
    login.add_argument("--code-file", dest="code_file", help="file containing OTP; never pass the code on argv")
    login.add_argument("--password-file", help="file containing password; never pass the password on argv")

    ws = sub.add_parser("ws", help="list or switch workspace")
    ws_sub = ws.add_subparsers(dest="ws_cmd", required=True)
    ws_sub.add_parser("ls")
    ws_use = ws_sub.add_parser("use")
    ws_use.add_argument("name")

    find = sub.add_parser("find", help="set current scope and print compact rows")
    find.add_argument("entity", nargs="?", choices=["product", "company", "shipment"], help="omit to show current scope")
    find.add_argument("text_pos", nargs="?", help="search text (company) or query")
    find.add_argument("--text", help="free-text / query")
    find.add_argument("--ncm")
    find.add_argument("--importer")
    find.add_argument("--exporter")
    find.add_argument("--country", help="origin country (product) or latam country ISO")
    find.add_argument("--region", choices=["br", "latam", "mx"], help="shipment region")
    find.add_argument("--container")
    find.add_argument("--vessel")
    find.add_argument("--port")
    find.add_argument("--flow", choices=["import", "export"])
    find.add_argument("--keep-rules", dest="keep_rules", action="store_true", help="do not clear rules when rewriting scope")
    find.add_argument("--raw", action="store_true", help="dump full server JSON")
    add_period_flags(find)

    sc = sub.add_parser("scope", help="local named scopes in ~/.config/lx/scopes/")
    sc_sub = sc.add_subparsers(dest="scope_cmd", required=True)
    sc_sub.add_parser("ls", help="list named scopes")
    sc_show = sc_sub.add_parser("show", help="show current or named scope")
    sc_show.add_argument("name", nargs="?")
    sc_save = sc_sub.add_parser("save", help="save current scope as NAME")
    sc_save.add_argument("name")
    sc_use = sc_sub.add_parser("use", help="load named scope as current")
    sc_use.add_argument("name")
    sc_rm = sc_sub.add_parser("rm", help="delete named scope")
    sc_rm.add_argument("name")

    ru = sub.add_parser("rule", help="include-rules on current scope (mapped to API params)")
    ru_sub = ru.add_subparsers(dest="rule_cmd", required=True)
    ru_add = ru_sub.add_parser("add", help="append include rule; exclude is rejected")
    ru_add.add_argument("name")
    ru_add.add_argument("--include", help='field: value  (text, ncm, country, importer, exporter, attr, place, period)')
    ru_add.add_argument("--does-not-include", dest="does_not_include", help="rejected: backend cannot apply this")
    ru_add.add_argument("--exclude", help="rejected: backend cannot apply this")
    ru_sub.add_parser("ls")
    ru_rm = ru_sub.add_parser("rm")
    ru_rm.add_argument("name")

    vw = sub.add_parser("view", help="rows|agg|series|graph for current scope")
    vw.add_argument("kind", choices=["rows", "agg", "series", "graph"])
    vw.add_argument("--by", help="month|year_month|importer|exporter|origin|ncm|country|consignee")
    vw.add_argument("--metric", default="fob")
    vw.add_argument("--limit", type=int)
    vw.add_argument("--cursor")
    vw.add_argument("--raw", action="store_true")

    pr = sub.add_parser("profile", help="company ID | product --ncm | shipment TOKEN")
    pr.add_argument("kind", choices=["company", "product", "shipment"])
    pr.add_argument("id", nargs="?", help="company entity_id or shipment token")
    pr.add_argument("--ncm")
    pr.add_argument("--period", choices=["latest", "3m", "6m", "12m", "all"])
    pr.add_argument("--limit", type=int)
    pr.add_argument("--raw", action="store_true")

    sub.add_parser("watch", help="re-run series + agg on current scope (not a daemon)")

    pan = sub.add_parser("panel", help="reusable intel panel: breaks | stacks | lines")
    pan.add_argument("layout", nargs="?", default="breaks",
                     choices=["breaks", "stacks", "lines", "universe-selection-breaks", "universe-selection-stacks", "universe-selection-lines"])
    pan.add_argument("--break", dest="breaks", default="importer,exporter")
    pan.add_argument("--limit", type=int, default=5)
    pan.add_argument("--out", default="", help="PNG path (default: ./intel-panel-<layout>.png in cwd)")

    lk = sub.add_parser("look", help="local named looks in ~/.config/lx/looks/ (this machine only)")
    lk_sub = lk.add_subparsers(dest="look_cmd", required=True)
    lk_save = lk_sub.add_parser("save", help="save a look from current text selection")
    lk_save.add_argument("name")
    lk_save.add_argument("--view", choices=["rows", "agg", "series", "graph"])
    lk_save.add_argument("--layout", choices=["breaks", "stacks", "lines"])
    lk_save.add_argument("--by", help="month|year_month|importer|exporter|origin|ncm|country|consignee")
    lk_save.add_argument("--metric", default="fob")
    lk_save.add_argument("--limit", type=int)
    lk_save.add_argument("--break", dest="breaks", default="importer,exporter")
    lk_sub.add_parser("ls", help="list saved looks (plus built-in breaks|stacks|lines)")
    lk_show = lk_sub.add_parser("show", help="print look JSON")
    lk_show.add_argument("name")
    lk_rm = lk_sub.add_parser("rm", help="delete a saved look file")
    lk_rm.add_argument("name")

    db = sub.add_parser("dashboard", help="local named dashboards in ~/.config/lx/dashboards/ (this machine only)")
    db_sub = db.add_subparsers(dest="dashboard_cmd", required=True)
    db_save = db_sub.add_parser("save", help="save current explore + looks")
    db_save.add_argument("name")
    db_save.add_argument("--looks", default="breaks,stacks,lines", help="comma names: built-in panels or saved looks")
    db_sub.add_parser("ls", help="list local dashboards")
    db_show = db_sub.add_parser("show", help="rebuild each look; --period/--ncm are room overrides")
    db_show.add_argument("name")
    db_show.add_argument("--period", choices=["latest", "3m", "6m", "12m", "all"])
    db_show.add_argument("--ncm")
    db_show.add_argument("--text", help="override look selection query (quadro)")
    db_show.add_argument("--out", default="", help="directory for PNG files (default: current working directory)")
    db_rm = db_sub.add_parser("rm", help="delete a local dashboard file")
    db_rm.add_argument("name")

    ncm = sub.add_parser("ncm", help="alias: raw NCM Intel BR products")
    ncm.add_argument("--code", help="NCM code, e.g. 22042100")
    ncm.add_argument("--ncm")
    ncm.add_argument("--query")
    ncm.add_argument("--importer")
    ncm.add_argument("--exporter")
    ncm.add_argument("--origin-country", dest="origin_country")
    add_period_flags(ncm)

    company = sub.add_parser("company", help="alias: raw company search/get")
    csub = company.add_subparsers(dest="company_cmd")
    csearch = csub.add_parser("search")
    csearch.add_argument("q", nargs="?", default="")
    csearch.add_argument("--query")
    csearch.add_argument("--limit", type=int, default=25)
    csearch.add_argument("--sort", choices=["updated", "name", "fob", "shipments"])
    cget = csub.add_parser("get")
    cget.add_argument("id")
    company.set_defaults(company_cmd="search")

    sh = sub.add_parser("shipments", help="alias: raw shipments BR/LATAM/MX")
    sh.add_argument("region", nargs="?", default="br", choices=["br", "latam", "mx"])
    sh.add_argument("--country", choices=["AR", "CL", "CO", "PE"])
    sh.add_argument("--ncm")
    sh.add_argument("--hs-code", dest="hs_code")
    sh.add_argument("--query")
    sh.add_argument("--container")
    sh.add_argument("--vessel")
    sh.add_argument("--port")
    sh.add_argument("--flow", choices=["import", "export"])
    sh.add_argument("--consignee")
    sh.add_argument("--shipper")
    sh.add_argument("--carrier")
    sh.add_argument("--importer")
    sh.add_argument("--exporter")
    sh.add_argument("--dataset")
    add_period_flags(sh)

    cs = sub.add_parser("comexstat", help="saved Comex Stat market scopes")
    cs.add_argument("q", nargs="?", default="")
    cs.add_argument("--id")
    cs.add_argument("--query")
    cs.add_argument("--limit", type=int, default=25)
    cs.add_argument("--cursor")

    tr = sub.add_parser("tracking", help="operations tracking tower/list")
    tr.add_argument("tracking_cmd", nargs="?", default="tower", choices=["tower", "list"])
    tr.add_argument("--modal")
    tr.add_argument("--limit", type=int, default=25)
    tr.add_argument("--offset", type=int, default=0)
    tr.add_argument("--reference")

    sub.add_parser("ocr", help="document conference dashboard")

    an = sub.add_parser("analyses", help="alias: raw analyses endpoints")
    an.add_argument("kind", choices=["ncm", "company", "shipments", "latam", "mx"])
    an.add_argument("--ncm")
    an.add_argument("--query")
    add_period_flags(an)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "health": cmd_health,
        "login": cmd_login,
        "logout": cmd_logout,
        "whoami": cmd_whoami,
        "ws": cmd_ws,
        "find": cmd_find,
        "scope": cmd_scope,
        "rule": cmd_rule,
        "view": cmd_view,
        "profile": cmd_profile,
        "watch": cmd_watch,
        "panel": cmd_panel,
        "look": cmd_look,
        "dashboard": cmd_dashboard,
        "ncm": cmd_ncm,
        "company": cmd_company,
        "shipments": cmd_shipments,
        "comexstat": cmd_comexstat,
        "tracking": cmd_tracking,
        "ocr": cmd_ocr,
        "analyses": cmd_analyses,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
