"""Testes do motor da Etapa 02.

* unitários: recálculo (primária x cascata), regras entre medições, catálogo, exportação,
  em medições sintéticas (sem dados reais);
* integração e mutação (exigem data/MED *.xlsx, que não é versionado): auditoria das seis medições,
  determinismo, preservação dos originais (SHA256), CLI, injeção de erros em cópias temporárias.

Uso: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit  # noqa: E402
import catalog  # noqa: E402
import compare  # noqa: E402
import engine  # noqa: E402
import export  # noqa: E402
import model as M  # noqa: E402
import reader as C  # noqa: E402
import recalc  # noqa: E402

COLS = {"qtd_contrato": "F", "preco": "G", "valor_contrato": "H", "qtd_med": "K", "val_ant": "M",
        "val_med": "N", "val_ac": "L", "saldo": "O"}


def make_med(id="MED 01", numero=1, folhas=None, offset=0):
    """Medição sintética coerente: TOTAL > lote A > seção A.1 > folhas. `folhas`: (código, F, G, K, M, unid)."""
    folhas = folhas or [("A.1.1", 10, 5.0, 2, 0.0, "m2"), ("A.1.2", 4, 12.5, 1, 10.0, "m2"), ("A.1.3", 3, 100.0, 3, 0.0, "un")]
    L = {}
    row = 4 + offset
    L["A"] = M.Linha("A", "lote", row, M.TOTAL)
    L["A.1"] = M.Linha("A.1", "secao", row + 1, "A")
    L["A"].filhos.append("A.1")
    for i, (cod, F, G, K, Mi, un) in enumerate(folhas):
        l = M.Linha(cod, "folha", row + 2 + i, "A.1", unidade=un, servico=f"S{i}", descricao=f"servico {i}")
        H, N = recalc.trunc2(F * G), recalc.trunc2(K * G)
        l.armazenado = {"qtd_contrato": F, "preco": G, "valor_contrato": H, "qtd_med": K, "val_ant": Mi,
                        "val_med": N, "val_ac": Mi + N, "saldo": H - Mi - N}
        L[cod] = l
        L["A.1"].filhos.append(cod)
    L[M.TOTAL] = M.Linha(M.TOTAL, "total", row + 2 + len(folhas), None, filhos=["A"])
    for cod in ("A.1", "A", M.TOTAL):
        ch = [L[c] for c in L[cod].filhos]
        L[cod].armazenado = {k: round(sum(c.armazenado[k] for c in ch), 2) for k in
                             ("valor_contrato", "val_ant", "val_med", "val_ac", "saldo")}
    return M.Medicao(id, "OBRA-T", numero, L, dict(COLS))


def propagar(med):
    """Refaz os totais dos pais a partir dos filhos armazenados (o erro da folha entra na soma, como na planilha)."""
    for cod in ("A.1", "A", M.TOTAL):
        ch = [med.linhas[c] for c in med.linhas[cod].filhos]
        med.linhas[cod].armazenado = {k: round(sum(c.armazenado[k] for c in ch), 2) for k in
                                      ("valor_contrato", "val_ant", "val_med", "val_ac", "saldo")}


def achados(med):
    return recalc.detectar(med)


class RecalcTests(unittest.TestCase):
    def test_medicao_coerente_nao_gera_ocorrencias(self):
        self.assertEqual([o for o in achados(make_med()) if o.classe == "comprovado"], [])

    def test_erro_em_folha_e_primario_e_pais_sao_cascata(self):
        med = make_med()
        med.linhas["A.1.1"].armazenado["val_med"] += 100.0          # medição da folha digitada errada
        propagar(med)
        occ = achados(med)
        prim = [o for o in occ if o.causa == "primaria" and o.classe == "comprovado"]
        self.assertTrue(any(o.codigo_item == "A.1.1" and o.celula == "N6" for o in prim))
        cas = [o for o in occ if o.causa == "cascata"]
        self.assertTrue({"A.1", "A", M.TOTAL} <= {o.codigo_item for o in cas if o.campo == "val_med"})
        raiz = next(o for o in prim if o.celula == "N6" and o.regra == "identidade_quebrada").id if False else \
            next(o for o in prim if o.celula == "N6").id
        self.assertTrue(all(raiz in o.causa_raiz_id.split(",") for o in cas if o.campo == "val_med"))

    def test_impacto_financeiro_so_nas_primarias(self):
        med = make_med()
        med.linhas["A.1.1"].armazenado["val_med"] += 100.0
        propagar(med)
        res = engine.finalizar(achados(med), {med.id: med.contrato_total()})
        contab = [o for o in res if o.contabiliza_impacto]
        self.assertTrue(all(o.causa == "primaria" for o in contab))
        self.assertTrue(any(o.causa == "cascata" for o in res))          # as cascatas existem, mas não somam
        self.assertAlmostEqual(sum(o.diferenca for o in contab if o.campo == "val_med"), 100.0, places=2)

    def test_erro_no_grupo_nao_atribui_causa_as_folhas(self):
        med = make_med()
        med.linhas["A.1"].armazenado["valor_contrato"] -= 50.0      # somatório incompleto no grupo
        for cod in ("A", M.TOTAL):                                  # os pais somam o valor errado do grupo
            med.linhas[cod].armazenado["valor_contrato"] -= 50.0
        occ = achados(med)
        p = [o for o in occ if o.causa == "primaria" and o.campo == "valor_contrato"]
        self.assertEqual([o.codigo_item for o in p], ["A.1"])
        self.assertEqual(p[0].regra, "divergência_financeira_agregada")
        self.assertAlmostEqual(p[0].diferenca, -50.0, places=2)

    def test_contrato_incoerente_em_folha(self):
        med = make_med()
        med.linhas["A.1.2"].armazenado["valor_contrato"] += 7.0
        occ = [o for o in achados(med) if o.regra == "valor_contrato_incoerente"]
        self.assertEqual([o.celula for o in occ], ["H7"])

    def test_residuo_de_centavos_nao_e_erro(self):
        med = make_med(folhas=[("A.1.1", 3, 3.333, 3, 0.0, "m2")])  # 9.999 -> TRUNC 9.99
        self.assertEqual([o for o in achados(med) if o.classe == "comprovado"], [])

    def test_medido_acima_do_contrato_depende_de_validacao(self):
        med = make_med()
        l = med.linhas["A.1.3"]
        l.armazenado["val_ac"] = l.armazenado["valor_contrato"] + 30.0
        l.armazenado["saldo"] = -30.0
        occ = [o for o in achados(med) if o.regra == "medido_acima_do_contrato"]
        self.assertEqual(len(occ), 1)
        self.assertEqual(occ[0].classe, "depende_validacao_contratual")

    def test_deslocar_linhas_nao_muda_os_achados(self):
        def chave(o): return (o.regra, o.codigo_item, o.campo, o.causa, round(o.diferenca or 0, 2))
        a, b = make_med(), make_med(offset=37)
        for med in (a, b):
            med.linhas["A.1.1"].armazenado["val_med"] += 100.0
        self.assertEqual(sorted(map(chave, achados(a))), sorted(map(chave, achados(b))))
        self.assertNotEqual({o.celula for o in achados(a)}, {o.celula for o in achados(b)})

    def test_ids_deterministicos(self):
        def ids():
            med = make_med(); med.linhas["A.1.1"].armazenado["val_med"] += 1
            return sorted(o.id for o in achados(med))
        self.assertEqual(ids(), ids())


class CompareTests(unittest.TestCase):
    def regras(self, ant, atu):
        return {(o.regra, o.codigo_item) for o in compare.comparar(ant, atu)}

    def test_item_removido_e_incluido(self):
        ant = make_med("MED 01", 1)
        atu = make_med("MED 02", 2, folhas=[("A.1.1", 10, 5.0, 2, 0.0, "m2"), ("A.1.3", 3, 100.0, 3, 0.0, "un"),
                                            ("A.1.4", 1, 9.0, 0, 0.0, "un")])
        r = self.regras(ant, atu)
        self.assertIn(("item_removido_entre_medicoes", "A.1.2"), r)
        self.assertIn(("item_incluido_entre_medicoes", "A.1.4"), r)

    def test_item_removido_nao_e_erro_comprovado(self):
        atu = make_med("MED 02", 2, folhas=[("A.1.1", 10, 5.0, 2, 10.0, "m2")])
        occ = compare.comparar(make_med(), atu)
        self.assertTrue(occ and all(o.classe == "depende_validacao_contratual" for o in occ))

    def test_cadastro_alterado_e_descontinuidade(self):
        ant = make_med("MED 01", 1)
        atu = make_med("MED 02", 2, folhas=[("A.1.1", 10, 6.0, 2, 0.0, "m2"), ("A.1.2", 4, 12.5, 1, 10.0, "m2"),
                                            ("A.1.3", 3, 100.0, 3, 0.0, "un")])
        atu.linhas["A.1.3"].armazenado["val_ant"] = 5.0           # != acumulado atual da medição anterior (300)
        r = self.regras(ant, atu)
        self.assertIn(("cadastro_alterado_entre_medicoes", "A.1.1"), r)
        self.assertIn(("acumulado_anterior_descontinuo", "A.1.3"), r)

    def test_medicoes_iguais_nao_geram_mudanca_de_escopo(self):
        a, b = make_med("MED 01", 1), make_med("MED 02", 2)
        self.assertFalse({o.regra for o in compare.comparar(a, b)} & {
            "item_removido_entre_medicoes", "item_incluido_entre_medicoes", "cadastro_alterado_entre_medicoes"})


class CatalogTests(unittest.TestCase):
    def test_catalogo_consistente(self):
        self.assertEqual(len(catalog.CATALOGO), 34)
        for r in catalog.CATALOGO.values():
            self.assertIn(r.classe, M.CLASSES)
            self.assertIn(r.severidade, M.SEVERIDADES)
        json.dumps(catalog.as_json(), ensure_ascii=False)

    def test_regras_emitidas_existem_no_catalogo(self):
        for o in achados(make_med()) + compare.comparar(make_med(), make_med("MED 02", 2, [("A.1.1", 1, 1.0, 0, 0.0, "un")])):
            self.assertIn(o.regra, catalog.CATALOGO)

    def test_reclassificacoes_da_etapa02(self):
        for rid in ("medido_acima_do_contrato", "contrato_controle_vs_resumo", "item_removido_entre_medicoes"):
            self.assertEqual(catalog.get(rid).classe, "depende_validacao_contratual")


class ExportTests(unittest.TestCase):
    def test_tabelas_seguem_o_esquema(self):
        med = make_med()
        med.linhas["A.1.1"].armazenado["val_med"] += 10
        res = engine.Resultado([med], engine.finalizar(achados(med), {med.id: med.contrato_total()}))
        t = export.tabelas(res)
        for nome, cols in M.TABELAS.items():
            self.assertTrue(t[nome], nome)
            for linha in t[nome]:
                self.assertEqual(list(linha), list(cols), nome)

    def test_exportar_csv_json(self):
        med = make_med()
        res = engine.Resultado([med], engine.finalizar(achados(med), {med.id: med.contrato_total()}))
        tmp = Path(tempfile.mkdtemp(prefix="databuild_"))
        try:
            export.exportar(res, tmp)
            for nome in M.TABELAS:
                with open(tmp / f"{nome}.csv", encoding="utf-8-sig") as fh:
                    self.assertEqual(next(csv.reader(fh)), list(M.TABELAS[nome]))
            dados = json.loads((tmp / "auditoria.json").read_text(encoding="utf-8"))
            self.assertEqual(set(dados["tabelas"]), set(M.TABELAS))
            self.assertEqual(json.loads((tmp / "schema.json").read_text(encoding="utf-8")), M.TABELAS)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


MEDS = C.med_files()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@unittest.skipUnless(len(MEDS) >= 2, "planilhas MED *.xlsx ausentes em data/ (confidenciais, não versionadas)")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.antes = {p: sha(p) for p in MEDS}
        cls.res = engine.audit_batch(MEDS)

    def test_originais_intactos(self):
        self.assertEqual(self.antes, {p: sha(p) for p in MEDS})
        self.assertEqual({m.arquivo_sha256 for m in self.res.medicoes}, set(self.antes.values()))

    def test_causa_raiz_aponta_para_primaria_existente(self):
        prim = {o.id for o in self.res.ocorrencias if o.causa == "primaria"}
        cas = [o for o in self.res.ocorrencias if o.causa == "cascata"]
        self.assertTrue(cas)
        for o in cas:
            self.assertTrue(o.causa_raiz_id and set(o.causa_raiz_id.split(",")) <= prim, o.id)
            self.assertFalse(o.contabiliza_impacto)

    def test_ids_unicos_e_impacto_uma_vez_por_celula(self):
        ids = [o.id for o in self.res.ocorrencias]
        self.assertEqual(len(ids), len(set(ids)))
        cel = [(o.medicao, o.aba, o.celula) for o in self.res.ocorrencias if o.contabiliza_impacto]
        self.assertEqual(len(cel), len(set(cel)))

    def test_deterministico(self):
        outra = engine.audit_batch(MEDS)
        self.assertEqual([(o.id, o.regra, o.celula) for o in self.res.ocorrencias],
                         [(o.id, o.regra, o.celula) for o in outra.ocorrencias])

    def test_marcacoes_vermelhas_cobertas(self):
        marcas = C.OUT / "red_marks.csv"
        if not marcas.exists():
            self.skipTest("outputs/red_marks.csv ausente (rode scripts/01_inventory.py)")
        with open(marcas, encoding="utf-8-sig") as fh:
            ms = [m for m in csv.DictReader(fh) if not m["tipo"].startswith("preenchimento amarelo")]
        cel = {(o.medicao, o.aba, o.celula) for o in self.res.ocorrencias}
        faltam = [m for m in ms if (m["arquivo"], m["aba"], m["celula"]) not in cel]
        self.assertEqual(faltam, [])

    def test_reclassificacoes_vistas_nos_dados(self):
        for o in self.res.ocorrencias:
            if o.regra in ("medido_acima_do_contrato", "contrato_controle_vs_resumo", "item_removido_entre_medicoes"):
                self.assertEqual(o.classe, "depende_validacao_contratual")

    def test_cli_audit_e_catalog(self):
        tmp = Path(tempfile.mkdtemp(prefix="databuild_"))
        try:
            self.assertEqual(audit.main(["audit", str(MEDS[0]), str(MEDS[1]), "--out", str(tmp / "o")]), 0)
            self.assertTrue((tmp / "o" / "ocorrencias.csv").exists())
            self.assertEqual(audit.main(["catalog", "--out", str(tmp / "c.json")]), 0)
            self.assertEqual(len(json.loads((tmp / "c.json").read_text(encoding="utf-8"))["regras"]), 34)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(len(MEDS) >= 2, "planilhas MED *.xlsx ausentes em data/")
class MutationEngineTests(unittest.TestCase):
    """Mutação no modelo (valores armazenados) de medições reais: cópias em memória, originais intactos.
    Planilhas regravadas por openpyxl perdem o cache de valores, por isso a mutação de valores é feita no `Medicao`."""

    @classmethod
    def setUpClass(cls):
        import build
        cls.meds = [build.construir(C.load(p)) for p in MEDS]
        cls.med = next(m for m in cls.meds if m.id == "MED 05")
        cls.rng = random.Random(20261124)

    def _copia(self):
        return copy.deepcopy(self.med)

    def _ativas(self, med):
        return [l for l in med.folhas() if l.armazenado.get("val_med")]

    def _novas(self, med, alvo):
        base = {(o.regra, o.celula) for o in recalc.detectar(copy.deepcopy(self.med))}
        return {(o.regra, o.celula) for o in recalc.detectar(med)} - base

    def test_valor_digitado_em_folhas_aleatorias(self):
        for l0 in self.rng.sample(self._ativas(self.med), 6):
            med = self._copia()
            l = med.linhas[l0.codigo]
            l.armazenado["val_med"] += 321.0
            novas = self._novas(med, l)
            self.assertIn(("identidade_quebrada", f"{med.colunas['val_med']}{l.row}"), novas, l.codigo)

    def test_grupo_com_soma_errada(self):
        grupos = [l for l in self.med.linhas.values() if l.tipo == "secao" and len(l.filhos) >= 2]
        for g0 in self.rng.sample(grupos, 5):
            med = self._copia()
            med.linhas[g0.codigo].armazenado["valor_contrato"] += 999.0
            novas = self._novas(med, g0)
            self.assertIn(("divergência_financeira_agregada", f"{med.colunas['valor_contrato']}{g0.row}"), novas)

    def test_cascata_sobe_ate_o_total(self):
        l0 = self.rng.choice(self._ativas(self.med))
        med = self._copia()
        med.linhas[l0.codigo].armazenado["val_med"] += 77.0
        occ = recalc.detectar(med)
        raiz = next(o for o in occ if o.causa == "primaria" and o.codigo_item == l0.codigo and o.campo == "val_med")
        casc = [o for o in occ if o.causa == "cascata" and raiz.id in o.causa_raiz_id.split(",")]
        self.assertIn(M.TOTAL, {o.codigo_item for o in casc})

    def test_deslocar_linhas_preserva_achados(self):
        a, b = self._copia(), self._copia()
        for l in b.linhas.values():
            l.row += 41
        ka = sorted((o.regra, o.codigo_item, o.campo, o.causa) for o in recalc.detectar(a))
        kb = sorted((o.regra, o.codigo_item, o.campo, o.causa) for o in recalc.detectar(b))
        self.assertEqual(ka, kb)

    def test_remover_e_inserir_servico_entre_medicoes(self):
        ant = self.meds[self.meds.index(self.med) - 1]
        atu = self._copia()
        cod = self.rng.choice(sorted(l.codigo for l in atu.folhas() if l.codigo in {x.codigo for x in ant.folhas()}))
        pai = atu.linhas[atu.linhas[cod].pai]
        pai.filhos.remove(cod)
        del atu.linhas[cod]
        r = {(o.regra, o.codigo_item) for o in compare.comparar(ant, atu)}
        self.assertIn(("item_removido_entre_medicoes", cod), r)
        novo = copy.deepcopy(self.med.linhas[cod]) if cod in self.med.linhas else copy.deepcopy(ant.linhas[cod])
        novo.codigo = cod + ".9"
        atu.linhas[novo.codigo] = novo
        pai.filhos.append(novo.codigo)
        r = {(o.regra, o.codigo_item) for o in compare.comparar(ant, atu)}
        self.assertIn(("item_incluido_entre_medicoes", novo.codigo), r)

    def test_mutacoes_nao_alteram_originais(self):
        self.assertEqual(sha(MEDS[0]), self.meds[0].arquivo_sha256)


if __name__ == "__main__":
    unittest.main()
