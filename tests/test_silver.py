"""Testes da Silver: reuso de regras, Decimal, SQL Server e ida e volta JDBC."""

import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import db
import dbadmin
import ingest
import model
import recalc
import silver
import silver_source
import silver_verify
from test_database import _config_de_teste
from test_ingest import criar_planilha


class SilverUnit(unittest.TestCase):
    def test_decimal_trunca_sem_float_e_mantem_entrada_original(self):
        item = model.Linha("A", "folha", 1)
        med = model.Medicao("MED 01", "OBRA-1", 1, {"A": item}, {})
        valores = {"A": {"qtd_contrato": Decimal("0.3"), "preco": Decimal("0.7"),
                         "qtd_med": Decimal("-0.3"), "val_ant": Decimal("1.23")}}
        recalc.calcular_valores(med, valores)
        self.assertEqual(item.recalc["valor_contrato"], Decimal("0.21"))
        self.assertEqual(item.recalc["val_med"], Decimal("-0.21"))
        self.assertEqual(item.recalc["val_ac"], Decimal("1.02"))
        self.assertEqual(valores["A"]["val_ant"], Decimal("1.23"))

    def test_ausencia_no_recalculo_se_propaga_sem_substituir_por_zero(self):
        total = model.Linha(model.TOTAL, "total", 2, filhos=["A"])
        item = model.Linha("A", "folha", 1, pai=model.TOTAL)
        med = model.Medicao("MED 01", "OBRA-1", 1, {"A": item, model.TOTAL: total}, {})
        valores = {"A": {"valor_contrato": Decimal("1.00"), "qtd_contrato": None,
                         "qtd_med": Decimal("0"), "preco": Decimal("1")}, model.TOTAL: {}}
        recalc.calcular_valores(med, valores)
        self.assertIsNone(item.recalc["valor_contrato"])
        self.assertIsNone(total.recalc["saldo"])
        self.assertEqual(total.recalc["val_med"], Decimal(0))

    def test_valores_bronze_booleano_data_numero_e_erro(self):
        self.assertIs(silver_source._valor("booleano", "FALSE"), False)
        self.assertEqual(silver_source._valor("data", "2026-01-31T08:30:00"), datetime(2026, 1, 31, 8, 30))
        self.assertEqual(silver_source._valor("numero", "1220"), 1220)
        self.assertEqual(silver_source._valor("erro", "#REF!"), "#REF!")
        self.assertIsNone(silver_source._valor("sem_valor", None))

    def test_manifesto_rejeita_duas_versoes_da_mesma_medicao(self):
        arquivos = [silver_source.Arquivo(i, rotulo, str(i), i, 1, 1)
                    for i, rotulo in enumerate(("MED 01", "MED 1"), 1)]
        with self.assertRaisesRegex(silver.ErroSilver, "ambíguos"):
            silver._validar_arquivos(arquivos)

    def test_manifesto_muda_com_a_origem(self):
        a = silver_source.Arquivo(1, "MED 01", "a" * 64, 1, 1, 1)
        b = silver_source.Arquivo(1, "MED 01", "b" * 64, 1, 1, 1)
        self.assertNotEqual(silver._id_manifesto([a]), silver._id_manifesto([b]))


class SilverSQLServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pyspark  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("PySpark requer o ambiente da Silver") from exc
        cls.cfg = _config_de_teste()
        try:
            with db.sessao("admin", cls.cfg, banco="master"):
                pass
        except Exception as exc:
            raise unittest.SkipTest(f"SQL Server indisponível ({type(exc).__name__})") from exc
        cls.addClassCleanup(cls._descartar)
        dbadmin.migrar(cls.cfg, log=lambda *_: None)
        dbadmin.provisionar(cls.cfg, log=lambda *_: None)
        with tempfile.TemporaryDirectory() as tmp:
            arquivos = [criar_planilha(Path(tmp) / f"MED {n:02}.xlsx", qtd, extra=True)
                        for n, qtd in ((1, 1.23445), (2, 3.14159))]
            assert ingest.ingerir_lote(arquivos, cls.cfg, log=lambda *_: None)["ok"]
        cls.bronze_antes = cls._bronze()
        sessao = silver.sessao_spark()
        cls.spark = sessao.__enter__()
        cls.addClassCleanup(sessao.__exit__, None, None, None)

        with patch("openpyxl.load_workbook", side_effect=AssertionError("Silver abriu um arquivo Excel")):
            with patch.object(silver, "_gravar_ocorrencias", side_effect=silver.ErroSilver("falha sintética")):
                try:
                    silver.promover(cls.spark, cls.cfg)
                except silver.ErroSilver as exc:
                    cls.falha = str(exc)
                else:
                    raise AssertionError("A falha sintética não foi aplicada")
            cls.apos_falha = silver.status(cls.cfg)
            cls.resultado = silver.promover(cls.spark, cls.cfg)

    @classmethod
    def _descartar(cls):
        dbadmin.descartar_banco(cls.cfg, cls.cfg.banco)
        with db.sessao("admin", cls.cfg, banco="master", autocommit=True) as c:
            for user in (cls.cfg.pipeline_user, cls.cfg.powerbi_user):
                c.execute(f"IF SUSER_ID(?) IS NOT NULL DROP LOGIN [{db.identificador_valido(user)}]", user)

    @classmethod
    def _bronze(cls):
        with db.sessao("pipeline", cls.cfg) as c:
            return [tuple(r) for r in c.execute("""
                SELECT a.arquivo_id, a.sha256, b.aba_id, b.hash_conteudo, COUNT(c.celula_id)
                FROM bronze.arquivo a JOIN bronze.aba b ON b.arquivo_id=a.arquivo_id
                LEFT JOIN bronze.celula_raw c ON c.aba_id=b.aba_id
                GROUP BY a.arquivo_id, a.sha256, b.aba_id, b.hash_conteudo ORDER BY a.arquivo_id,b.aba_id
            """)]

    def test_publicacao_e_reexecucao_preservam_bronze_e_identificadores(self):
        self.assertEqual(self.resultado["status"], "concluida")
        antes = silver.status(self.cfg)
        novo = silver.promover(self.spark, self.cfg)
        self.assertEqual(novo["status"], "inalterada")
        self.assertEqual(novo["execucao_id"], self.resultado["execucao_id"])
        self.assertEqual(antes, silver.status(self.cfg))
        self.assertEqual(self.bronze_antes, self._bronze())

    def test_bloqueio_impede_promocoes_concorrentes(self):
        with silver._bloqueio(self.cfg):
            with self.assertRaisesRegex(silver.ErroSilver, "em execução"):
                silver.promover(self.spark, self.cfg)

    def test_falha_reverte_tabelas_finais_e_limpa_somente_staging_do_lote(self):
        self.assertEqual(self.falha, "falha sintética")
        for tabela in ("obra", "medicao", "item_medicao", "valor_item", "formula_item", "regra", "stg_valor_item"):
            self.assertEqual(self.apos_falha["totais"][tabela], 0, tabela)
        self.assertEqual(self.apos_falha["execucoes"][0]["status"], "falhou")

    def test_jdbc_relê_decimais_gravados_por_spark_com_escala_exata(self):
        qry = """SELECT v.valor_armazenado, v.valor_recalculado, v.situacao
                 FROM silver.valor_item v JOIN silver.item_medicao i ON i.item_id = v.item_id
                 JOIN silver.medicao m ON m.medicao_id = i.medicao_id
                 WHERE m.numero=1 AND i.codigo=N'A.1.1' AND v.campo=N'qtd_contrato'"""
        tabela = self.spark.read.format("jdbc").options(**self.cfg.jdbc(), query=qry).load()
        self.assertEqual(tabela.schema["valor_armazenado"].dataType.simpleString(), "decimal(18,4)")
        item = tabela.first()
        self.assertEqual(item.valor_armazenado, Decimal("1.2345"))
        self.assertIsNone(item.valor_recalculado)

    def test_rastreia_valores_e_ocorrencias_sem_contar_cascata(self):
        resultado = silver_verify.verificar(self.cfg)
        self.assertTrue(resultado["ok"], resultado)
        self.assertEqual(resultado["arquivos_bronze"], 2)
        self.assertGreater(resultado["valores_conferidos"], 0)
        self.assertGreater(resultado["ocorrencias"], 0)
