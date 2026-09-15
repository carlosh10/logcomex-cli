#!/usr/bin/env python3
"""Parser/envelope tests for company intelligence. No live credentials."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import company_intel
import lx


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "company_intel_page.json"


class ParseIncludeTests(unittest.TestCase):
    def test_colon_style(self) -> None:
        self.assertEqual(
            company_intel.parse_include_rule("country: CL"),
            {"field": "pais", "value": "CL"},
        )

    def test_gt_token(self) -> None:
        self.assertEqual(
            company_intel.parse_include_rule("fob_12m>1000000"),
            {"field": "fob_12m", "op": "maior", "value": "1000000"},
        )

    def test_colon_then_op(self) -> None:
        self.assertEqual(
            company_intel.parse_include_rule("fob_12m: >1000000"),
            {"field": "fob_12m", "op": "maior", "value": "1000000"},
        )

    def test_words(self) -> None:
        self.assertEqual(
            company_intel.parse_include_rule("fob_12m gt 1000000"),
            {"field": "fob_12m", "op": "maior", "value": "1000000"},
        )

    def test_empty(self) -> None:
        with self.assertRaises(ValueError):
            company_intel.parse_include_rule("")


class PeriodAndArgsTests(unittest.TestCase):
    def test_period_12m_only(self) -> None:
        self.assertEqual(company_intel.period_since("12m", on=date(2026, 8, 24)), "2025-08-24")
        self.assertIsNone(company_intel.period_since("3m", on=date(2026, 8, 24)))
        self.assertIsNone(company_intel.period_since("latest", on=date(2026, 8, 24)))
        self.assertIsNone(company_intel.period_since("all", on=date(2026, 8, 24)))

    def test_find_filters_text_is_descricao(self) -> None:
        args = lx.build_parser().parse_args([
            "find", "company",
            "--pais", "BRASIL",
            "--categoria", "importadores",
            "--period", "12m",
            "--text", "vinho",
            "--fob-12m-min", "1",
        ])
        filters = company_intel.company_find_filters(args)
        self.assertEqual(filters["descricao_produto"], "vinho")
        self.assertEqual(filters["pais"], "BRASIL")
        self.assertEqual(filters["categoria"], "importadores")
        self.assertEqual(filters["period"], "12m")
        self.assertEqual(filters["fob_12m_min"], 1)
        self.assertNotIn("query", filters)
        self.assertNotIn("description", filters)

    def test_tool_args_period_and_fob(self) -> None:
        scope = {
            "entity": "company",
            "filters": {
                "pais": "BRASIL",
                "categoria": "importadores",
                "descricao_produto": "vinho",
                "period": "12m",
            },
            "rules": [
                {"name": "fob", "verb": "include", "field": "fob_12m", "op": "maior", "value": "1000000"},
            ],
        }
        with patch.object(company_intel, "today", return_value=date(2026, 8, 24)):
            args, warnings = company_intel.apply_company_rules(scope)
        self.assertEqual(args["pais"], "BRASIL")
        self.assertEqual(args["categoria"], "importadores")
        self.assertEqual(args["descricao_produto"], "vinho")
        self.assertEqual(args["fob_12m_min"], 1000000)
        self.assertNotIn("query", args)
        self.assertNotIn("description", args)
        ops = {(x["campo"], x["operador"], str(x["valor"])) for x in args["filtros_avancados"]}
        self.assertIn(("ultima_operacao", "desde", "2025-08-24"), ops)
        self.assertIn(("fob_12m", "maior", "1000000"), ops)
        self.assertEqual(warnings, [])

    def test_unmapped_period_warns(self) -> None:
        scope = {"entity": "company", "filters": {"period": "3m"}, "rules": []}
        args, warnings = company_intel.apply_company_rules(scope)
        self.assertTrue(any("not mapped" in w for w in warnings))
        self.assertNotIn("filtros_avancados", args)


class EnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(FIXTURE.read_text())
        self.scope = {
            "entity": "company",
            "filters": {"pais": "BRASIL", "categoria": "importadores", "descricao_produto": "vinho", "period": "12m"},
            "rules": [],
        }

    def test_totals_from_linhas_not_page_sum(self) -> None:
        totals = company_intel.intel_totals(self.payload)
        assert totals is not None
        self.assertEqual(totals["linhas"], 70957)
        self.assertTrue(totals["exato"])
        self.assertEqual(totals["query_id"], "q-fixture-vinho-12m")
        self.assertEqual(totals["linhas_retornadas"], 2)
        page_fob = sum(
            (r.get("inteligencia_12m") or {}).get("fob_12m") or 0
            for r in self.payload["empresas"]
        )
        self.assertNotEqual(totals["linhas"], page_fob)
        self.assertNotIn("fob", totals)

    def test_view_count_envelope(self) -> None:
        env = company_intel.result_envelope(self.scope, self.payload, kind="count")
        self.assertTrue(env["ok"])
        self.assertEqual(env["contract"], company_intel.CONTRACT)
        self.assertEqual(env["totals"]["linhas"], 70957)
        self.assertIsNone(env["data"])
        self.assertTrue(env["coverage"]["partial"])
        self.assertFalse(env["coverage"]["somavel_entre_estabelecimentos"])

    def test_view_rows_projects_intel_fields(self) -> None:
        env = company_intel.result_envelope(self.scope, self.payload, kind="rows", limit=10)
        self.assertEqual(len(env["data"]), 2)
        self.assertEqual(env["data"][0]["codigo"], "12345678000190")
        self.assertEqual(env["data"][0]["faixa_fob"], "1M|-10M")
        self.assertEqual(env["data"][0]["fob_12m"], 2500000)

    def test_find_envelope_does_not_claim_rows(self) -> None:
        env = company_intel.find_envelope(self.scope)
        self.assertTrue(env["ok"])
        self.assertEqual(env["data"], [])
        self.assertIsNone(env["totals"])
        self.assertEqual(env["next"]["action"], "lx intel query")
        self.assertEqual(env["next"]["tool"]["arguments"]["descricao_produto"], "vinho")


class CliHelpAndRejectTests(unittest.TestCase):
    def test_find_company_help(self) -> None:
        buf = io.StringIO()
        parser = lx.build_parser()
        find = None
        for action in parser._subparsers._group_actions:  # noqa: SLF001
            if action.dest == "cmd":
                find = action.choices["find"]
        assert find is not None
        find.print_help(buf)
        text = buf.getvalue()
        for needle in ("--pais", "--categoria", "--period", "--text", "--fob-12m-min", "--fob-12m-max", "--fob-total-min"):
            self.assertIn(needle, text)

    def test_view_help_has_count(self) -> None:
        buf = io.StringIO()
        parser = lx.build_parser()
        view = None
        for action in parser._subparsers._group_actions:  # noqa: SLF001
            if action.dest == "cmd":
                view = action.choices["view"]
        assert view is not None
        view.print_help(buf)
        self.assertIn("count", buf.getvalue())

    def test_rule_rejects_does_not_include(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LX_HOME"] = tmp
            lx.CFG = Path(tmp)
            lx.CURRENT_SCOPE_PATH = lx.CFG / "current-scope.json"
            lx.SCOPES_DIR = lx.CFG / "scopes"
            lx.save_current_scope({"entity": "company", "filters": {}, "rules": []})
            args = lx.build_parser().parse_args(["rule", "include", "--does-not-include", "x"])
            buf = io.StringIO()
            with patch("sys.stdout", buf), self.assertRaises(SystemExit) as ctx:
                lx.cmd_rule(args)
            self.assertEqual(ctx.exception.code, 1)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["error"], "unsupported_verb")

    def test_find_company_writes_intel_scope_not_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LX_HOME"] = tmp
            lx.CFG = Path(tmp)
            lx.CURRENT_SCOPE_PATH = lx.CFG / "current-scope.json"
            lx.SCOPES_DIR = lx.CFG / "scopes"
            lx.LOOKS_DIR = lx.CFG / "looks"
            lx.DASH_DIR = lx.CFG / "dashboards"
            lx.INTEL_JOBS_DIR = lx.CFG / "intel-jobs"
            args = lx.build_parser().parse_args([
                "find", "company",
                "--pais", "BRASIL",
                "--categoria", "importadores",
                "--period", "12m",
                "--text", "vinho",
            ])
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                lx.cmd_find(args)
            env = json.loads(buf.getvalue())
            self.assertEqual(env["contract"], company_intel.CONTRACT)
            self.assertEqual(env["data"], [])
            scope = json.loads(lx.CURRENT_SCOPE_PATH.read_text())
            self.assertEqual(scope["backend"], "company_rds")
            self.assertEqual(scope["filters"]["descricao_produto"], "vinho")
            self.assertNotIn("query", scope["filters"])

    def test_view_count_from_fixture_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LX_HOME"] = tmp
            os.environ["LX_COMPANY_INTEL_FIXTURE"] = str(FIXTURE)
            lx.CFG = Path(tmp)
            lx.CURRENT_SCOPE_PATH = lx.CFG / "current-scope.json"
            lx.SCOPES_DIR = lx.CFG / "scopes"
            lx.save_current_scope({
                "entity": "company",
                "backend": "company_rds",
                "filters": {"pais": "BRASIL", "descricao_produto": "vinho", "period": "12m"},
                "rules": [],
            })
            args = lx.build_parser().parse_args(["view", "count"])
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                lx.cmd_view(args)
            env = json.loads(buf.getvalue())
            self.assertEqual(env["totals"]["linhas"], 70957)
            self.assertTrue(env["totals"]["exato"])
            self.assertIsNone(env["data"])
            os.environ.pop("LX_COMPANY_INTEL_FIXTURE", None)

    def test_view_agg_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LX_HOME"] = tmp
            lx.CFG = Path(tmp)
            lx.CURRENT_SCOPE_PATH = lx.CFG / "current-scope.json"
            lx.save_current_scope({"entity": "company", "filters": {}, "rules": []})
            args = lx.build_parser().parse_args(["view", "agg", "--by", "faixa"])
            buf = io.StringIO()
            with patch("sys.stdout", buf), self.assertRaises(SystemExit):
                lx.cmd_view(args)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["error"], "unsupported")

    def test_product_find_parser_unchanged_ncm(self) -> None:
        args = lx.build_parser().parse_args(["find", "product", "--ncm", "22042100", "--period", "12m", "--text", "cabernet"])
        scope = lx.build_find_scope(args)
        self.assertEqual(scope["entity"], "product")
        self.assertEqual(scope["filters"]["ncm"], "22042100")
        self.assertEqual(scope["filters"]["query"], "cabernet")
        self.assertNotIn("descricao_produto", scope["filters"])


if __name__ == "__main__":
    unittest.main()
