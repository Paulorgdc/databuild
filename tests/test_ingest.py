"""Testes da Etapa 04 (ingestão na Bronze): estáticos (sem servidor) e de banco (pulados sem SQL Server).

Usam somente planilhas sintéticas geradas em diretório temporário e um banco descartável
`databuild_test_<aleatório>`; as medições reais e o banco real `databuild` nunca são tocados.
Há um teste adicional (pulado se `data/` não existir) que confere que a leitura não altera os originais.
Rodar: python -m unittest tests.test_ingest -v   (a partir da raiz) ou via `unittest discover -s tests`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import db  # noqa: E402
import dbadmin  # noqa: E402
import ingest  # noqa: E402
import reader  # noqa: E402

try:
    import pyodbc  # noqa: F401
except ImportError:  # pragma: no cover
    pyodbc = None


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def criar_planilha(destino: Path, qtd: float = 10, extra: bool = False) -> Path:
    """Medição sintética mínima, com a estrutura que o `reader` reconhece (lote > seção > serviços > total)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumo Medição"
    cab = {1: {"A": "ITEM", "B": "DISCRIMINAÇÃO", "E": "UNID.", "F": "QUANTIDADE", "G": "PREÇO UNIT", "H": "CONTRATO"},
           2: {"I": "AC ATUAL", "J": "AC ANTERIOR", "K": "MED ATUAL", "L": "SALDO"},
           3: {"H": "Contrato", "I": "AC ATUAL", "J": "AC ANTERIOR", "K": "MEDIÇÃO ATUAL", "M": "%"}}
    for r, d in cab.items():
        for c, v in d.items():
            ws[f"{c}{r}"] = v
    ws["A4"], ws["B4"] = "A", "LOTE"
    ws["A5"], ws["B5"] = "A.1", "SEÇÃO"
    for i, (cod, desc, un, q, p) in enumerate([("A.1.1", "Contrapiso", "m2", qtd, 5), ("A.1.2", "Piso", "m2", 20, 7)]):
        r = 6 + i
        ws[f"A{r}"], ws[f"B{r}"], ws[f"E{r}"], ws[f"F{r}"], ws[f"G{r}"] = cod, desc, un, q, p
        ws[f"H{r}"] = f"=F{r}*G{r}"
        ws[f"K{r}"] = f"=F{r}*0.5"
    ws["A8"], ws["H8"] = "VALOR TOTAL DA OBRA", "=SUM(H6:H7)"
    ws["N9"] = "#REF!"                                      # erro literal do Excel
    ws["O9"] = dt.datetime(2026, 1, 31, 8, 30)
    ws["P9"] = True
    ws["Q9"] = "   espaços e acentuação: ç ã é   "
    ws["R9"] = 1234.5678901234567                           # 17 dígitos significativos: ida e volta exata
    ws["S9"] = "=SUM(F6:F7)"                                 # fórmula de total
    ws.merge_cells("U1:V2")
    ws.append([])                                           # linha vazia no fim
    if extra:
        wb.create_sheet("Oculta").sheet_state = "hidden"
        wb["Oculta"]["A1"] = "=1/0"
    wb.save(destino)
    return destino


# ============================================================================================ estáticos
class ExtracaoEstatica(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.arq = criar_planilha(self.dir / "MED 01 - sintetica.xlsx", extra=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_texto_valor_preserva_tipos(self):
        self.assertEqual(ingest.texto_valor(None), (None, "sem_valor"))
        self.assertEqual(ingest.texto_valor(True), ("TRUE", "booleano"))
        self.assertEqual(ingest.texto_valor(0), ("0", "numero"))
        self.assertEqual(ingest.texto_valor(0.1 + 0.2), ("0.30000000000000004", "numero"))
        self.assertEqual(float(ingest.texto_valor(1234.5)[0]), 1234.5)
        self.assertEqual(ingest.texto_valor(dt.datetime(2026, 1, 31, 8, 30)), ("2026-01-31T08:30:00", "data"))
        self.assertEqual(ingest.texto_valor("  x "), ("  x ", "texto"))     # espaços preservados
        self.assertEqual(ingest.texto_valor(""), ("", "texto"))              # vazio textual ≠ NULL

    def test_extrai_celulas_formulas_erros_e_vazios(self):
        abas = {a.nome: a for a in ingest.extrair_celulas(self.arq)}
        self.assertEqual(list(abas), ["Resumo Medição", "Oculta"])
        r = abas["Resumo Medição"]
        por = {c[2]: c for c in r.celulas}
        self.assertEqual(por["H6"][3], "=F6*G6")                              # fórmula original
        self.assertTrue(por["H6"][7])
        self.assertIsNone(por["H6"][5])                                       # sem cache (arquivo não calculado)
        self.assertEqual(por["H6"][6], "sem_valor")
        self.assertEqual((por["N9"][5], por["N9"][6], por["N9"][8]), ("#REF!", "erro", True))
        self.assertEqual(por["R9"][5], repr(openpyxl.load_workbook(self.arq)["Resumo Medição"]["R9"].value))
        self.assertEqual(por["Q9"][5], "   espaços e acentuação: ç ã é   ")
        self.assertEqual(por["P9"][5], "TRUE")
        self.assertNotIn("C6", por)                                           # célula vazia não é gravada
        self.assertGreater(r.n_vazias, 0)
        self.assertEqual(len(r.celulas) + r.n_vazias, r.linhas_dim * r.colunas_dim)
        self.assertEqual(r.mescladas, "U1:V2")
        o = abas["Oculta"]
        self.assertEqual(o.estado, "hidden")
        self.assertEqual(o.n_formulas, 1)

    def test_posicao_a1_linha_coluna_coerentes(self):
        from openpyxl.utils import get_column_letter
        for a in ingest.extrair_celulas(self.arq):
            for lin, col, ref, *_ in a.celulas:
                self.assertEqual(ref, f"{get_column_letter(col)}{lin}")

    def test_hash_depende_do_conteudo_e_nao_da_ordem(self):
        a = ingest.extrair_celulas(self.arq)[0]
        self.assertEqual(ingest.hash_celulas(a.celulas), ingest.hash_celulas(list(reversed(a.celulas))))
        alterada = [c[:5] + ("x",) + c[6:] if c[2] == "Q9" else c for c in a.celulas]
        self.assertNotEqual(ingest.hash_celulas(a.celulas), ingest.hash_celulas(alterada))

    def test_extracao_estruturada_hierarquia_e_origem(self):
        est = ingest.extrair_estruturado(self.arq)
        self.assertNotIn("ocorrencias", est)                                  # validação não é ingestão
        servicos = {r["codigo"]: r for r in est["servicos"]}
        self.assertEqual(set(servicos), {"A.1.1", "A.1.2"})
        self.assertEqual(servicos["A.1.1"]["pai"], "A.1")
        self.assertEqual((servicos["A.1.1"]["aba_origem"], servicos["A.1.1"]["linha_origem"]), ("Resumo Medição", 6))
        cats = {r["codigo"]: r for r in est["categorias"]}
        self.assertEqual(cats["A.1"]["pai"], "A")
        self.assertEqual(cats["A"]["tipo"], "lote")
        for r in est["valores"]:
            self.assertIsNone(r["valor_recalculado"])                         # sem recálculo na Bronze
            self.assertRegex(r["celula_origem"], r"^[A-Z]+\d+$")
        formulas = {(r["codigo"], r["campo"]): r["formula"] for r in est["valores"]}
        self.assertEqual(formulas[("A.1.1", "valor_contrato")], "=F6*G6")

    def test_leitura_nao_altera_o_arquivo_original(self):
        antes = sha(self.arq)
        ingest.extrair(self.arq)
        self.assertEqual(sha(self.arq), antes)

    def test_arquivo_corrompido_ou_sem_layout_levanta_erro(self):
        ruim = self.dir / "MED 09 - ruim.xlsx"
        ruim.write_bytes(b"isto nao e um xlsx")
        with self.assertRaises(Exception):
            ingest.extrair(ruim)
        wb = openpyxl.Workbook()
        wb.active.title = "Outra"
        sem = self.dir / "MED 08 - sem layout.xlsx"
        wb.save(sem)
        with self.assertRaises(Exception):
            ingest.extrair(sem)

    def test_dry_run_nao_toca_no_banco(self):
        with mock.patch.object(db, "sessao", side_effect=AssertionError("tocou no banco")):
            r = ingest.simular([self.arq, self.dir / "inexistente.xlsx"])
        self.assertFalse(r["ok"])
        self.assertEqual([x["resultado"] for x in r["arquivos"]], ["simulado", "falhou"])
        self.assertEqual(r["arquivos"][0]["abas"], 2)

    def test_cli_arquivo_inexistente_e_pasta_vazia_retornam_2(self):
        self.assertEqual(ingest.main(["file", str(self.dir / "nao_existe.xlsx")]), 2)
        vazia = self.dir / "vazia"
        vazia.mkdir()
        self.assertEqual(ingest.main(["batch", "--pasta", str(vazia)]), 2)

    def test_cli_dry_run_json(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ingest.main(["batch", "--pasta", str(self.dir), "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        dados = json.loads(buf.getvalue())
        self.assertEqual(dados["status"], "simulado")
        self.assertEqual(dados["arquivos"][0]["sha256"], sha(self.arq))

    def test_modulo_nao_contem_segredos_nem_dados_da_empresa(self):
        texto = (ROOT / "scripts" / "ingest.py").read_text(encoding="utf-8")
        self.assertNotRegex(texto, r"PASSWORD\s*=\s*['\"][^'\"]+['\"]")
        self.assertNotRegex(texto, r"R\$\s*\d")
        self.assertEqual(ingest.NOME_OBRA, "Construtora Horizontes")

    @unittest.skipUnless(list(reader.DATA.glob("MED *.xlsx")) if reader.DATA.exists() else False,
                         "medições reais ausentes")
    def test_originais_reais_intactos_apos_extracao(self):
        arqs = reader.med_files()
        antes = {p: sha(p) for p in arqs}
        mtimes = {p: p.stat().st_mtime_ns for p in arqs}
        ingest.extrair(arqs[0])
        for p in arqs:
            self.assertEqual((sha(p), p.stat().st_mtime_ns), (antes[p], mtimes[p]))


# ============================================================================================ banco real
from test_database import _config_de_teste  # noqa: E402


class IngestaoSQLServer(unittest.TestCase):
    cfg: db.Config

    @classmethod
    def setUpClass(cls):
        if pyodbc is None:
            raise unittest.SkipTest("pyodbc não instalado")
        cls.cfg = _config_de_teste()
        try:
            with db.sessao("admin", cls.cfg, banco="master"):
                pass
        except Exception as e:
            raise unittest.SkipTest(f"SQL Server indisponível ({str(e)[:90]})")
        cls.criado = False
        dbadmin.migrar(cls.cfg, log=lambda *_: None)
        cls.criado = True
        dbadmin.provisionar(cls.cfg, log=lambda *_: None)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()
        if getattr(cls, "criado", False):
            dbadmin.descartar_banco(cls.cfg, cls.cfg.banco)
            for u in (cls.cfg.pipeline_user, cls.cfg.powerbi_user):
                try:
                    with db.sessao("admin", cls.cfg, banco="master", autocommit=True) as c:
                        c.execute(f"IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{u}') DROP LOGIN [{u}]")
                except Exception:
                    pass

    def setUp(self):
        # estado limpo a cada teste, no banco descartável (o admin do teste pode truncar à vontade)
        with db.sessao("admin", self.cfg) as c:
            for t in ("carga_log", "carga_arquivo", "celula_raw", "valores_raw", "servicos_raw", "categorias_raw",
                      "medicoes_raw", "obras_raw", "catalogo_regras_raw", "ocorrencias_raw", "aba", "arquivo", "carga"):
                c.execute(f"DELETE FROM bronze.{t}")
        self.pasta = Path(tempfile.mkdtemp(dir=self.dir))

    def lote(self, arquivos, log=None):
        return ingest.ingerir_lote([Path(a) for a in arquivos], self.cfg, log or (lambda *_: None))

    def n(self, tabela, where=""):
        with db.sessao("pipeline", self.cfg) as c:
            return c.execute(f"SELECT COUNT(*) FROM bronze.{tabela} {where}").fetchone()[0]

    def totais(self):
        return {t: self.n(t) for t in ("arquivo", "aba", "celula_raw", "obras_raw", "medicoes_raw", "categorias_raw",
                                       "servicos_raw", "valores_raw")}

    # ---- carga ------------------------------------------------------------------------------------
    def test_ingestao_grava_tudo_e_a_conferencia_passa(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx", extra=True)
        r = self.lote([a])
        self.assertTrue(r["ok"])
        self.assertEqual((r["carregado"], r["duplicado"], r["falhou"]), (1, 0, 0))
        ex = ingest.extrair(a)
        t = self.totais()
        self.assertEqual((t["arquivo"], t["aba"], t["celula_raw"]), (1, 2, ex.n_celulas))
        self.assertEqual((t["categorias_raw"], t["servicos_raw"]), (3, 2))     # lote, seção e total; 2 serviços
        with db.sessao("pipeline", self.cfg) as c:
            sha_db, med = c.execute("SELECT sha256, medicao_id FROM bronze.arquivo").fetchone()
            f = c.execute("SELECT COUNT(*) FROM bronze.celula_raw WHERE eh_formula = 1").fetchone()[0]
            e = c.execute("SELECT COUNT(*) FROM bronze.celula_raw WHERE eh_erro = 1").fetchone()[0]
        self.assertEqual((sha_db, med, f, e), (sha(a), "MED 01", ex.n_formulas, ex.n_erros))
        ver = ingest.verificar(self.cfg, self.pasta, lambda *_: None)
        self.assertTrue(ver["ok"], ver["divergencias"])
        self.assertTrue(ver["arquivos"][0]["confrontado_com_excel"])

    def test_valores_originais_preservados_no_banco(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        self.lote([a])
        with db.sessao("pipeline", self.cfg) as c:
            lin = {r[0]: r for r in c.execute(
                "SELECT celula, formula, valor_armazenado, tipo_valor, eh_erro FROM bronze.celula_raw")}
        self.assertEqual(lin["H6"][1], "=F6*G6")
        self.assertIsNone(lin["H6"][2])
        self.assertEqual((lin["N9"][2], lin["N9"][3], lin["N9"][4]), ("#REF!", "erro", True))
        self.assertEqual(lin["R9"][2], repr(openpyxl.load_workbook(a)["Resumo Medição"]["R9"].value))
        self.assertEqual(lin["Q9"][2], "   espaços e acentuação: ç ã é   ")
        self.assertEqual(lin["P9"][2], "TRUE")
        self.assertEqual(lin["O9"][2], "2026-01-31T08:30:00")
        with db.sessao("pipeline", self.cfg) as c:
            r = c.execute("SELECT valor_armazenado, valor_recalculado, formula, aba_origem, celula_origem "
                          "FROM bronze.valores_raw WHERE codigo = 'A.1.1' AND campo = 'valor_contrato'").fetchone()
        self.assertEqual((r[1], r[2], r[3], r[4]), (None, "=F6*G6", "Resumo Medição", "H6"))

    def test_hierarquia_de_servicos_e_linha_de_origem(self):
        self.lote([criar_planilha(self.pasta / "MED 01 - x.xlsx")])
        with db.sessao("pipeline", self.cfg) as c:
            s = c.execute("SELECT codigo, pai, linha_origem FROM bronze.servicos_raw ORDER BY linha_origem").fetchall()
            k = c.execute("SELECT codigo, pai, tipo, nivel FROM bronze.categorias_raw ORDER BY nivel").fetchall()
        self.assertEqual([tuple(x) for x in s], [("A.1.1", "A.1", 6), ("A.1.2", "A.1", 7)])
        self.assertEqual([tuple(x) for x in k], [("TOTAL", None, "total", "0"), ("A", "TOTAL", "lote", "1"),
                                                  ("A.1", "A", "secao", "2")])

    # ---- idempotência e duplicidade ---------------------------------------------------------------
    def test_reexecucao_nao_duplica_e_registra_a_execucao(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        b = criar_planilha(self.pasta / "MED 02 - y.xlsx", qtd=11)
        self.lote([a, b])
        antes = self.totais()
        for _ in range(2):
            r = self.lote([a, b])
            self.assertEqual((r["carregado"], r["duplicado"], r["falhou"]), (0, 2, 0))
            self.assertEqual(self.totais(), antes)
        self.assertEqual(self.n("carga"), 3)                                   # execuções preservadas
        self.assertEqual(self.n("carga_arquivo", "WHERE resultado = 'duplicado'"), 4)
        self.assertEqual(self.n("carga_arquivo", "WHERE resultado = 'carregado'"), 2)

    def test_copia_com_outro_nome_e_duplicada_pelo_sha256(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        copia = self.pasta / "MED 07 - copia.xlsx"
        copia.write_bytes(a.read_bytes())
        r = self.lote([a, copia])
        self.assertEqual((r["carregado"], r["duplicado"]), (1, 1))
        self.assertEqual(self.n("arquivo"), 1)

    def test_mesmo_nome_com_conteudo_novo_preserva_as_duas_versoes(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx", qtd=10)
        self.lote([a])
        criar_planilha(a, qtd=99)                                              # mesmo nome, conteúdo diferente
        r = self.lote([a])
        self.assertEqual(r["carregado"], 1)
        self.assertEqual(self.n("arquivo"), 2)
        with db.sessao("pipeline", self.cfg) as c:
            avisos = c.execute("SELECT COUNT(*) FROM bronze.carga_log WHERE nivel = 'aviso'").fetchone()[0]
        self.assertEqual(avisos, 1)
        ver = ingest.verificar(self.cfg, self.pasta, lambda *_: None)
        self.assertTrue(ver["ok"], ver["divergencias"])                       # a versão antiga: só consistência interna
        self.assertEqual(sorted(x["confrontado_com_excel"] for x in ver["arquivos"]), [False, True])

    # ---- falhas, rollback, logs -------------------------------------------------------------------
    def test_falha_no_meio_desfaz_o_arquivo_inteiro(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        with mock.patch.object(ingest.Ingestor, "_estruturado", side_effect=RuntimeError("falha injetada")):
            r = self.lote([a])
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "falhou")
        self.assertEqual(self.totais(), {k: 0 for k in self.totais()})        # nada ficou (nem arquivo, nem células)
        with db.sessao("pipeline", self.cfg) as c:
            res = c.execute("SELECT resultado, mensagem FROM bronze.carga_arquivo").fetchall()
            st = c.execute("SELECT status FROM bronze.carga").fetchone()[0]
        self.assertEqual([x[0] for x in res], ["falhou"])
        self.assertIn("falha injetada", res[0][1])
        self.assertEqual(st, "falhou")
        # depois da falha, a mesma planilha carrega normalmente (nada ficou preso)
        self.assertEqual(self.lote([a])["carregado"], 1)

    def test_falha_em_um_arquivo_nao_impede_os_demais(self):
        bom1 = criar_planilha(self.pasta / "MED 01 - a.xlsx")
        ruim = self.pasta / "MED 02 - corrompida.xlsx"
        ruim.write_bytes(b"lixo")
        bom2 = criar_planilha(self.pasta / "MED 03 - c.xlsx", qtd=33)
        r = self.lote([bom1, ruim, bom2])
        self.assertEqual((r["carregado"], r["falhou"]), (2, 1))
        self.assertFalse(r["ok"])
        self.assertEqual(self.n("arquivo"), 2)
        with db.sessao("pipeline", self.cfg) as c:
            f = c.execute("SELECT nome_arquivo, sha256 FROM bronze.carga_arquivo WHERE resultado = 'falhou'").fetchone()
            erros = c.execute("SELECT COUNT(*) FROM bronze.carga_log WHERE nivel = 'erro'").fetchone()[0]
        self.assertEqual(f[0], ruim.name)
        self.assertIsNone(f[1])
        self.assertEqual(erros, 1)

    def test_log_de_processamento_consultavel(self):
        self.lote([criar_planilha(self.pasta / "MED 01 - x.xlsx")])
        msgs = [x["mensagem"] for x in ingest.ler_log(self.cfg)]
        self.assertTrue(any("iniciada" in m for m in msgs))
        self.assertTrue(any("carregado" in m for m in msgs))
        self.assertTrue(any("concluida" in m for m in msgs))

    def test_ingestoes_concorrentes_do_mesmo_arquivo_gravam_uma_vez(self):
        import threading
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        resultados = []
        ts = [threading.Thread(target=lambda: resultados.append(self.lote([a]))) for _ in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertTrue(all(r["ok"] for r in resultados), resultados)
        self.assertEqual(sum(r["carregado"] for r in resultados), 1)
        self.assertEqual(sum(r["duplicado"] for r in resultados), 2)
        self.assertEqual(self.n("arquivo"), 1)

    # ---- integridade ------------------------------------------------------------------------------
    def test_conferencia_detecta_adulteracao_no_banco(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        self.lote([a])
        with db.sessao("admin", self.cfg) as c:
            c.execute("UPDATE bronze.celula_raw SET valor_armazenado = '999' WHERE celula = 'Q9'")
        ver = ingest.verificar(self.cfg, self.pasta, lambda *_: None)
        self.assertFalse(ver["ok"])
        self.assertTrue(any("hash" in d or "divergem" in d for d in ver["divergencias"]), ver["divergencias"])

    def test_conferencia_detecta_celula_removida(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        self.lote([a])
        with db.sessao("admin", self.cfg) as c:
            c.execute("DELETE TOP (1) FROM bronze.celula_raw WHERE celula = 'H6'")
        ver = ingest.verificar(self.cfg, self.pasta, lambda *_: None)
        self.assertFalse(ver["ok"])

    def test_conferencia_sem_nada_carregado_nao_aprova(self):
        self.assertFalse(ingest.verificar(self.cfg, self.pasta, lambda *_: None)["ok"])

    def test_status_e_cli_json(self):
        import contextlib
        import io
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        self.lote([a])
        s = ingest.status(self.cfg)
        self.assertEqual(s["totais"]["arquivo"], 1)
        self.assertEqual(s["execucoes_por_resultado"], {"carregado": 1})
        self.assertEqual(s["arquivos"][0]["sha256"], sha(a)[:12])

    def test_papel_powerbi_nao_le_nem_grava_bronze(self):
        self.lote([criar_planilha(self.pasta / "MED 01 - x.xlsx")])
        with db.sessao("powerbi", self.cfg) as c:
            for sql in ("SELECT COUNT(*) FROM bronze.celula_raw", "DELETE FROM bronze.celula_raw"):
                with self.assertRaises(pyodbc.Error):
                    c.execute(sql)

    def test_restricoes_de_integridade_do_esquema(self):
        a = criar_planilha(self.pasta / "MED 01 - x.xlsx")
        self.lote([a])
        with db.sessao("pipeline", self.cfg) as c:
            with self.assertRaises(pyodbc.IntegrityError):                     # SHA-256 único
                c.execute("INSERT INTO bronze.arquivo (sha256, nome_arquivo, tamanho_bytes, n_abas, n_celulas, "
                          "n_formulas, n_erros, primeira_carga_id) SELECT sha256, 'x', 1, 0, 0, 0, 0, primeira_carga_id "
                          "FROM bronze.arquivo")
            c.rollback()
            with self.assertRaises(pyodbc.IntegrityError):                     # célula única por posição
                c.execute("INSERT INTO bronze.celula_raw (carga_id, arquivo_id, aba_id, linha, coluna, celula, "
                          "eh_formula, eh_erro, tipo_valor) SELECT TOP 1 carga_id, arquivo_id, aba_id, linha, coluna, "
                          "celula, 0, 0, 'texto' FROM bronze.celula_raw")
            c.rollback()
            with self.assertRaises(pyodbc.IntegrityError):                     # tipo_valor controlado
                c.execute("UPDATE bronze.celula_raw SET tipo_valor = 'qualquer'")


if __name__ == "__main__":
    unittest.main()
