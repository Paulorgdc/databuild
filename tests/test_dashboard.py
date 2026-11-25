import json
import tempfile
import unittest
from pathlib import Path

from dashboard.build import build
from dashboard.model import MEASURES, MONEY, RELATIONSHIPS, TABLES
from dashboard.reconcile import COUNT_COLUMNS, dax_query
from dashboard.report import PAGES, ident


class DashboardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "project"
        build(self.root, "example.invalid", "synthetic_gold")
        self.report = self.root / "DataBuild.Report" / "definition"
        self.model = self.root / "DataBuild.SemanticModel" / "definition"

    @staticmethod
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_reproducible_and_non_overwriting(self):
        pair = self.root.with_name("fresh")
        build(pair, "example.invalid", "synthetic_gold")
        files = lambda root: {p.relative_to(root): p.read_bytes()
                              for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(self.root), files(pair))
        with self.assertRaises(FileExistsError):
            build(self.root, "example.invalid", "synthetic_gold")
        self.assertFalse(list(self.root.rglob("*.abf")))

    def test_pages_navigation_and_bindings(self):
        pages = self.report / "pages"
        order = self.read(pages / "pages.json")["pageOrder"]
        self.assertEqual(order, [ident(key) for key, _, _ in PAGES])
        for key, name, _ in PAGES:
            folder = pages / ident(key)
            page = self.read(folder / "page.json")
            self.assertEqual(page["displayName"], name)
            visuals = [self.read(v) for v in folder.glob("visuals/*/visual.json")]
            types = {v["visual"]["visualType"] for v in visuals}
            self.assertTrue({"actionButton", "tableEx", "slicer"}.issubset(types))
            destinations = {
                v["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]
                ["navigationSection"]["expr"]["Literal"]["Value"].strip("'")
                for v in visuals if v["visual"]["visualType"] == "actionButton"
            }
            self.assertEqual(destinations, set(order))
            for visual in visuals:
                pos = visual["position"]
                self.assertLessEqual(pos["x"] + pos["width"], page["width"])
                self.assertLessEqual(pos["y"] + pos["height"], page["height"])
                for fixed in visual.get("filterConfig", {}).get("filters", []):
                    comparison = fixed["filter"]["Where"][0]["Condition"]["Comparison"]
                    self.assertEqual(comparison["Right"], {"Literal": {"Value": "0D"}})
                    self.assertEqual(comparison["ComparisonKind"], 1)
                for role in visual["visual"].get("query", {}).get("queryState", {}).values():
                    for binding in role["projections"]:
                        (kind, expr), = binding["field"].items()
                        table = expr["Expression"]["SourceRef"]["Entity"]
                        available = MEASURES[table] if kind == "Measure" else TABLES[table][1]
                        self.assertIn(expr["Property"], available)

    def test_financial_display_and_distinct_chart_series(self):
        money = [(table, name) for table, measures in MEASURES.items()
                 for name, (_, fmt) in measures.items() if fmt == MONEY]
        self.assertEqual(len(money), 17)
        self.assertEqual(MONEY.count("0.00"), 3)
        self.assertNotIn("0.000", MONEY)
        for table, name in money:
            definition = (self.model / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
            self.assertIn(f"measure '{name}'", definition)
            self.assertIn(f"formatString: {MONEY}", definition)
        chart = self.read(self.report / "pages" / ident("overview") / "visuals" /
                          ident("overview/volume") / "visual.json")["visual"]
        series = chart["query"]["queryState"]["Y"]["projections"]
        fills = chart["objects"]["dataPoint"]
        self.assertEqual([p["queryRef"] for p in series], [p["selector"]["metadata"] for p in fills])
        self.assertNotEqual(fills[0]["properties"]["fill"], fills[1]["properties"]["fill"])

    def test_gold_only_and_single_direction_dimensions(self):
        for table, (source, _) in TABLES.items():
            text = (self.model / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
            self.assertIn(f'Schema = "gold", Item = "{source}"', text)
            self.assertIn('Sql.Database("example.invalid", "synthetic_gold"', text)
            self.assertNotIn("Excel.Workbook", text)
            self.assertNotIn("Query =", text)
        for fact, key, dimension, unique in RELATIONSHIPS:
            self.assertIn(key, TABLES[fact][1])
            self.assertIn(unique, TABLES[dimension][1])
            self.assertFalse(TABLES[dimension][0].startswith("fact"))
        self.assertNotIn("bothDirections", (self.model / "relationships.tmdl").read_text())

    def test_dax_probe_escapes_strings_and_filters_each_measure(self):
        dax = dax_query([[], ["TREATAS({1}, 'Medição'[numero])"]], COUNT_COLUMNS)
        for measure in COUNT_COLUMNS:
            self.assertIn(f'"{measure}", [{measure}]', dax)
            self.assertIn(f'CALCULATE([{measure}], TREATAS', dax)
        self.assertEqual(dax.count('ROW("Context"'), 2)


if __name__ == "__main__":
    unittest.main()
