"""Testes dos detectores genéricos.

* testes unitários (sem dados): normalização de fórmulas e leitura de somatórios;
* testes de mutação (exigem as planilhas em data/, que não é versionado): injeta erros em
  células ESCOLHIDAS AO ACASO (semente fixa) numa cópia temporária e verifica
  que o detector acusa a célula alterada, sem regra por célula.

Uso: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reader as C  # noqa: E402
import rules as D  # noqa: E402


class UnitTests(unittest.TestCase):
    def test_normalize_formula_is_position_independent(self):
        a = C.normalize_formula("=TRUNC(K6*G6,2)", 6, 14)
        b = C.normalize_formula("=TRUNC(K70*G70,2)", 70, 14)
        self.assertEqual(a, b)

    def test_agg_rows_range_and_refs(self):
        self.assertEqual(D.agg_rows("=SUM(N6:N9)", "N"), ({6, 7, 8, 9}, "N"))
        self.assertEqual(D.agg_rows("=SUBTOTAL(9,N116)", "N"), ({116}, "N"))
        self.assertEqual(D.agg_rows("=N5+N11", "N"), ({5, 11}, "N"))
        self.assertIsNone(D.agg_rows("=TRUNC(K6*G6,2)", "N"))
        self.assertIsNone(D.agg_rows("=N5+O11", "N"))

    def test_is_red(self):
        self.assertTrue(C.is_red("FFFF0000"))
        self.assertFalse(C.is_red("FFFFFF00"))
        self.assertFalse(C.is_red("FF6AA84F"))


MEDS = C.med_files()


@unittest.skipUnless(MEDS, "planilhas MED *.xlsx ausentes em data/ (dados confidenciais, não versionados)")
class MutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="databuild_"))
        cls.src = [p for p in MEDS if "MED 05" in p.name][0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _mutate(self, name, fn):
        """Salva cópia (sem tocar no original) e aplica fn(ws, kind, leaves_of, sections_of, rng)."""
        m0 = C.load(self.src)
        kind, leaves_of, sections_of, lotes = D.classify_rows(m0)
        wb = C.openpyxl.load_workbook(self.src)
        ws = wb[C.MAIN_SHEET]
        rng = random.Random(20261124)
        target = fn(ws, m0, kind, leaves_of, sections_of, rng)
        out = self.tmp / f"{name}.xlsx"
        wb.save(out)
        return out, target

    def _baseline_keys(self):
        wb = C.openpyxl.load_workbook(self.src)
        base = self.tmp / "baseline.xlsx"
        wb.save(base)  # mesmo salvamento (sem cache de valores) para comparar maçã com maçã
        m = C.load(base)
        return {(f.regra, f.celula) for f in D.run_all(m)[0]}

    def _new(self, path, regra, celula, base):
        m = C.load(path)
        keys = {(f.regra, f.celula) for f in D.run_all(m)[0]}
        return (regra, celula) in keys - base

    def test_random_shrunk_sum_ranges_are_detected(self):
        base = self._baseline_keys()
        rng = random.Random(7)
        m0 = C.load(self.src)
        kind, leaves_of, sections_of, _ = D.classify_rows(m0)
        secs = [r for r, k in kind.items() if k == "secao" and len(leaves_of[r]) >= 3]
        for trial, row in enumerate(rng.sample(secs, 5)):
            col = m0.cols["valor_contrato"]
            leaves = leaves_of[row]

            def fn(ws, *_a, row=row, col=col, leaves=leaves):
                ws[f"{col}{row}"] = f"=SUBTOTAL(9,{col}{leaves[0]}:{col}{leaves[-2]})"
                return f"{col}{row}"
            path, cell = self._mutate(f"range{trial}", fn)
            self.assertTrue(self._new(path, "agregação_incompleta", cell, base), f"não detectou {cell}")

    def test_random_deleted_formulas_are_detected(self):
        base = self._baseline_keys()
        rng = random.Random(11)
        m0 = C.load(self.src)
        kind, *_ = D.classify_rows(m0)
        leaves = [r for r, k in kind.items() if k == "folha"]
        col = m0.cols["val_ac"]
        for trial, row in enumerate(rng.sample(leaves, 5)):
            def fn(ws, *_a, row=row, col=col):
                ws[f"{col}{row}"] = None
                return f"{col}{row}"
            path, cell = self._mutate(f"blank{trial}", fn)
            self.assertTrue(self._new(path, "fórmula_ausente_ou_divergente", cell, base), f"não detectou {cell}")

    def test_random_hardcoded_values_are_detected(self):
        base = self._baseline_keys()
        rng = random.Random(13)
        m0 = C.load(self.src)
        kind, *_ = D.classify_rows(m0)
        leaves = [r for r, k in kind.items() if k == "folha"]
        col = m0.cols["saldo"]
        for trial, row in enumerate(rng.sample(leaves, 5)):
            def fn(ws, *_a, row=row, col=col):
                ws[f"{col}{row}"] = 123.45
                return f"{col}{row}"
            path, cell = self._mutate(f"const{trial}", fn)
            self.assertTrue(self._new(path, "fórmula_ausente_ou_divergente", cell, base), f"não detectou {cell}")

    def test_injected_ref_error_and_leaf_sum_are_detected(self):
        base = self._baseline_keys()
        m0 = C.load(self.src)
        kind, *_ = D.classify_rows(m0)
        leaves = [r for r, k in kind.items() if k == "folha"]
        row = random.Random(17).choice(leaves[:-3])
        col = m0.cols["val_med"]

        def fn_ref(ws, *_a):
            ws[f"{col}{row}"] = "=#REF!*2"
            return f"{col}{row}"
        path, cell = self._mutate("ref", fn_ref)
        self.assertTrue(self._new(path, "erro_de_fórmula", cell, base))

        def fn_sum(ws, *_a):
            ws[f"{col}{row}"] = f"=SUM({col}{row + 1}:{col}{row + 3})"
            return f"{col}{row}"
        path, cell = self._mutate("leafsum", fn_sum)
        self.assertTrue(self._new(path, "folha_com_somatório", cell, base))

    def test_original_files_untouched_by_tests(self):
        self.assertTrue(all(p.exists() for p in MEDS))


if __name__ == "__main__":
    unittest.main()
