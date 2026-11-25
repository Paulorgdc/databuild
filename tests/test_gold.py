"""Integração da publicação dimensional Gold em banco descartável."""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import db
import dbadmin
import gold
import test_database

EXECUCAO = "0" * 32


class GoldSQLServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = test_database._config_de_teste()
        try:
            with db.sessao("admin", cls.cfg, banco="master"):
                pass
        except Exception as exc:
            raise unittest.SkipTest(f"SQL Server indisponível ({type(exc).__name__})") from exc
        cls.addClassCleanup(cls._descartar)
        dbadmin.migrar(cls.cfg, log=lambda *_: None)
        dbadmin.provisionar(cls.cfg, log=lambda *_: None)

    @classmethod
    def _descartar(cls):
        dbadmin.descartar_banco(cls.cfg, cls.cfg.banco)
        with db.sessao("admin", cls.cfg, banco="master", autocommit=True) as c:
            for user in (cls.cfg.pipeline_user, cls.cfg.powerbi_user):
                c.execute(f"IF SUSER_ID(?) IS NOT NULL DROP LOGIN [{db.identificador_valido(user)}]", user)

    def setUp(self):
        self.conn = db.conectar("admin", self.cfg)
        self.addCleanup(self.conn.close)
        self.conn.execute("DELETE FROM gold.fact_pendencia_valor; DELETE FROM gold.publicacao")
        test_database.BancoSQLServer._carregar_sinteticos(self)
        self.conn.execute("DELETE FROM silver.execucao")
        self.conn.execute("""
            INSERT INTO silver.execucao (execucao_id, manifesto, versao, status, n_arquivos, etapa)
            VALUES (?, REPLICATE('0', 64), N'teste', N'concluida', 2, N'publicacao')
        """, EXECUCAO)
        self.conn.execute("UPDATE silver.medicao SET execucao_id = ?", EXECUCAO)
        self.conn.execute("UPDATE silver.medicao SET contrato_total = NULL WHERE numero = 1")
        self.conn.execute("""
            UPDATE v SET valor_armazenado = NULL, valor_recalculado = NULL, situacao = N'pendente_dados'
            FROM silver.valor_item v JOIN silver.item_medicao i ON i.item_id = v.item_id
            JOIN silver.medicao m ON m.medicao_id = i.medicao_id
            WHERE m.numero = 1 AND i.tipo = N'folha' AND v.campo = N'val_ac'
        """)
        self.conn.commit()

    def test_publicacao_reconcilia_nulos_vinculos_e_reexecucao(self):
        self.assertFalse(gold.verificar(self.cfg)["ok"])
        self.assertEqual(gold.publicar(self.cfg), {"status": "concluida", "execucao_id": EXECUCAO})
        primeiro = gold.status(self.cfg)
        self.assertEqual(gold.publicar(self.cfg, EXECUCAO)["status"], "inalterada")
        self.assertEqual(primeiro, gold.status(self.cfg))
        self.assertTrue(gold.verificar(self.cfg)["ok"])
        self.assertEqual(primeiro["totais"], dict(zip(gold.TABELAS, (2, 4, 4, 2, 1))))
        self.assertEqual(primeiro["impactos_contabilizados"], 1)
        self.assertEqual(primeiro["pendencias"], {"pendente_dados": 1})
        with db.sessao("powerbi", self.cfg) as reader:
            n, impacto = reader.execute("""
                SELECT COUNT(*), SUM(impacto_contabilizado) FROM gold.vw_qualidade_medicao
            """).fetchone()
            self.assertEqual((n, impacto), (2, Decimal("10.00")))
            self.assertIsNone(reader.execute("""
                SELECT f.contrato_total FROM gold.fact_medicao f INNER JOIN gold.dim_medicao d
                    ON d.medicao_key = f.medicao_key WHERE d.numero = 1
            """).fetchone()[0])

    def test_recusa_silver_incompleta(self):
        self.conn.execute("UPDATE silver.execucao SET status = N'iniciada'")
        self.conn.commit()
        with self.assertRaisesRegex(gold.ErroGold, "concluída"):
            gold.publicar(self.cfg, EXECUCAO)
        self.assertFalse(any(gold.status(self.cfg)["totais"].values()))

    def test_preserva_fatos_preexistentes_e_veta_execucao_legada_ao_pipeline(self):
        self.conn.execute("EXEC gold.usp_carregar_gold")
        self.conn.commit()
        antes = gold.status(self.cfg)
        with self.assertRaisesRegex(gold.ErroGold, "sem publicação registrada"):
            gold.publicar(self.cfg, EXECUCAO)
        self.assertEqual(gold.status(self.cfg), antes)
        import pyodbc
        with self.assertRaises(pyodbc.Error):
            with db.sessao("pipeline", self.cfg) as c:
                c.execute("EXEC gold.usp_carregar_gold")

    def test_falha_no_calendario_reverte_publicacao(self):
        self.conn.execute("UPDATE silver.medicao SET periodo_fim = '20400101' WHERE numero = 1")
        self.conn.commit()
        import pyodbc
        with self.assertRaises(pyodbc.Error):
            gold.publicar(self.cfg, EXECUCAO)
        self.assertFalse(any(gold.status(self.cfg)["totais"].values()))
        self.assertIsNone(gold.status(self.cfg)["execucao_id"])
