"""Política da DAG e recuperação incremental no SQL Server descartável."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import db
import dbadmin
import ingest
import orquestrar
from test_database import _config_de_teste
from test_ingest import criar_planilha


class PoliticaSequencia(unittest.TestCase):
    def test_inclui_so_hashes_novos_e_normaliza_o_numero(self):
        adicional = Path("MED 02.xlsx")
        resumo, novos = orquestrar.planejar(
            [(Path("MED 1 revisao.xlsx"), "a"), (adicional, "b"), (Path("MED 2 copia.xlsx"), "b")],
            [("MED 01", "a")], False,
        )
        self.assertEqual(resumo, {"novas": 1, "duplicadas": 2, "registradas": 1})
        self.assertEqual(novos, [(adicional, "b")])

    def test_rejeita_revisoes_da_entrada_ou_ja_registradas(self):
        with self.assertRaisesRegex(orquestrar.RevisaoPendente, "detectada uma revisão"):
            orquestrar.planejar([(Path("MED 01.xlsx"), "b")], [("MED 1", "a")], False)
        with self.assertRaisesRegex(orquestrar.RevisaoPendente, "revisões concorrentes"):
            orquestrar.planejar([], [("MED 01", "a"), ("MED 1", "b")], False)

    def test_rejeita_mesmo_conteudo_com_numero_distinto(self):
        with self.assertRaisesRegex(orquestrar.RevisaoPendente, "números de medição diferentes"):
            orquestrar.planejar([(Path("MED 02.xlsx"), "a")], [("MED 01", "a")], False)

    def test_sequencia_selada_aceita_repeticao_e_bloqueia_acrescimo(self):
        registros = [("MED 01", "a")]
        resumo, novos = orquestrar.planejar([(Path("MED 01.xlsx"), "a")], registros, True)
        self.assertEqual(resumo["duplicadas"], 1)
        self.assertFalse(novos)
        with self.assertRaisesRegex(orquestrar.RevisaoPendente, "banco candidato"):
            orquestrar.planejar([(Path("MED 02.xlsx"), "b")], registros, True)

    def test_rejeita_numero_invalido(self):
        for nome in ("MED 00.xlsx", "MED 32768.xlsx", "MED errado.xlsx"):
            with self.subTest(nome=nome), self.assertRaises(orquestrar.RevisaoPendente):
                orquestrar.planejar([(Path(nome), "a")], [], False)


class OrquestracaoSQLServer(unittest.TestCase):
    def test_recupera_lote_interrompido_sem_repetir_arquivo_confirmado(self):
        cfg = _config_de_teste()
        try:
            with db.sessao("admin", cfg, banco="master"):
                pass
        except Exception as exc:
            self.skipTest(f"SQL Server indisponível ({type(exc).__name__})")
        self.addCleanup(self._descartar, cfg)
        dbadmin.migrar(cfg, log=lambda *_: None)
        dbadmin.provisionar(cfg, log=lambda *_: None)
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            primeira = criar_planilha(pasta / "MED 01.xlsx")
            criar_planilha(pasta / "MED 02.xlsx", qtd=20)
            with db.sessao("pipeline", cfg) as conn:
                carga = ingest.Ingestor(conn, log=lambda *_: None)
                interrompida = carga.abrir_carga("airflow: teste de recuperação")
                self.assertEqual(carga.ingerir(primeira)["resultado"], "carregado")
            with db.sessao("pipeline", cfg, autocommit=True) as conn:
                self.assertTrue(db.travar_sequencia(conn, "Exclusive", "Session", 0))
                with self.assertRaisesRegex(orquestrar.ErroOrquestracao, "Outra operação"):
                    orquestrar.carregar_bronze(cfg, pasta)
            resumo = orquestrar.carregar_bronze(cfg, pasta)
            self.assertEqual((resumo["carregadas"], resumo["duplicadas"]), (1, 1))
            with db.sessao("pipeline", cfg) as conn:
                antigo = conn.execute("SELECT status FROM bronze.carga WHERE carga_id=?", interrompida).fetchone()
                recibos = conn.execute("SELECT COUNT(*) FROM bronze.carga_arquivo WHERE resultado=N'carregado'").fetchone()
                self.assertEqual((antigo[0], recibos[0]), ("falhou", 2))
            self.assertIsNone(orquestrar.carregar_bronze(cfg, pasta)["carga_id"])

    @staticmethod
    def _descartar(cfg):
        dbadmin.descartar_banco(cfg, cfg.banco)
        with db.sessao("admin", cfg, banco="master", autocommit=True) as conn:
            for user in (cfg.pipeline_user, cfg.powerbi_user):
                conn.execute(f"IF SUSER_ID(?) IS NOT NULL DROP LOGIN [{db.identificador_valido(user)}]", user)
