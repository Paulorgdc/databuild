"""Testes da Etapa 03 (SQL Server): estáticos (sem servidor) e de banco (pulados se não houver servidor).

Os testes de banco criam um banco descartável `databuild_test_<aleatório>` e logins de teste próprios,
usam somente dados sintéticos e removem tudo ao final. O banco real `databuild` nunca é tocado.
Rodar: python -m unittest tests.test_database -v   (a partir da raiz) ou via `unittest discover -s tests`.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import dbadmin  # noqa: E402

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None

SQL = "\n".join(m.sql for m in dbadmin.listar_migrations())

TABELAS_ESPERADAS = {
    "bronze": {"carga", "obras_raw", "medicoes_raw", "categorias_raw", "servicos_raw", "valores_raw",
               "ocorrencias_raw", "catalogo_regras_raw", "arquivo", "aba", "celula_raw", "carga_arquivo",
               "carga_log"},
    "silver": {"obra", "medicao", "item_medicao", "valor_item", "formula_item", "regra", "ocorrencia",
               "ocorrencia_causa_raiz", "log_processamento", "execucao", "medicao_origem",
               "ocorrencia_origem", "stg_valor_item"},
    "gold": {"dim_data", "dim_obra", "dim_medicao", "dim_item", "dim_regra", "dim_classe", "dim_severidade",
             "dim_causa", "fact_valor_item", "fact_medicao", "fact_ocorrencia", "bridge_cascata_primaria"},
}


# ============================================================================================ estáticos
class MigrationsEstaticas(unittest.TestCase):
    def test_numeracao_sem_lacunas_e_checksum_estavel(self):
        migs = dbadmin.listar_migrations()
        self.assertEqual([m.versao for m in migs], list(range(len(migs))))
        self.assertEqual(migs[0].nome, "create_database")
        self.assertEqual([m.checksum for m in migs], [m.checksum for m in dbadmin.listar_migrations()])
        self.assertEqual(len({m.checksum for m in migs}), len(migs))

    def test_separador_go(self):
        self.assertEqual(dbadmin.dividir_lotes("A;\nGO\nB;\n  go  -- fim\n\nGO\nC;"), ["A;", "B;", "C;"])

    def test_scripts_sao_idempotentes_e_nao_destrutivos(self):
        self.assertIsNone(re.search(r"\bDROP\s+(TABLE|SCHEMA|DATABASE|VIEW|PROCEDURE)\b", SQL, re.I))
        self.assertIsNone(re.search(r"\bTRUNCATE\b", SQL, re.I))
        for m in dbadmin.listar_migrations():
            for lote in dbadmin.dividir_lotes(m.sql):
                if re.match(r"\s*CREATE\s+TABLE", re.sub(r"/\*.*?\*/", "", lote, flags=re.S), re.I):
                    self.fail(f"{m.nome}: CREATE TABLE sem guarda IF OBJECT_ID")
        criacoes = len(re.findall(r"^\s*CREATE TABLE", SQL, re.M))
        guardas = len(re.findall(r"IF OBJECT_ID\(N'[a-z_.]+', N'U'\) IS NULL\s*\nCREATE TABLE", SQL))
        self.assertEqual(criacoes, guardas)
        self.assertEqual(criacoes, sum(len(v) for v in TABELAS_ESPERADAS.values()))

    def test_sem_tipos_imprecisos_para_dinheiro(self):
        sem_comentarios = re.sub(r"/\*.*?\*/|--[^\n]*", "", SQL, flags=re.S)
        self.assertIsNone(re.search(r"\b(FLOAT|REAL|MONEY|SMALLMONEY)\b", sem_comentarios, re.I))
        self.assertIn("DECIMAL(18,2)", SQL)
        self.assertIn("DECIMAL(18,4)", SQL)

    def test_toda_tabela_tem_chave_primaria(self):
        for lote in re.split(r"\bCREATE TABLE\b", SQL)[1:]:
            corpo = lote.split("\nGO", 1)[0]
            self.assertRegex(corpo, r"PRIMARY KEY", corpo[:60])

    def test_sintaxe_tsql_com_sqlglot(self):
        try:
            import sqlglot
        except ImportError:
            self.skipTest("sqlglot não instalado")
        for m in dbadmin.listar_migrations():
            for lote in dbadmin.dividir_lotes(dbadmin.preparar(m.sql, "databuild")):
                if re.match(r"\s*(/\*.*?\*/\s*)?(GRANT|DENY)\b", lote, re.S | re.I):
                    continue  # o sqlglot não implementa GRANT/DENY sobre SCHEMA::; validados no teste de banco
                try:
                    sqlglot.parse(lote, read="tsql")
                except sqlglot.errors.ParseError as e:  # pragma: no cover
                    self.fail(f"{m.nome}: {str(e)[:200]}\n{lote[:120]}")

    def test_sem_identificadores_sensiveis_nos_scripts(self):
        arquivos = list((ROOT / "database").rglob("*")) + [ROOT / ".env.example", ROOT / "scripts" / "db.py",
                                                            ROOT / "scripts" / "dbadmin.py"]
        for p in (a for a in arquivos if a.is_file() and a.suffix in {".sql", ".ps1", ".py", ".example"}):
            t = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", t), f"CNPJ em {p.name}")
            self.assertIsNone(re.search(r"[A-Za-z]:\\Users\\", t.replace("%ProgramFiles%", "")), f"caminho local em {p.name}")
            self.assertNotIn("R$", t, p.name)


class ConfiguracaoEstatica(unittest.TestCase):
    ENV = {"DATABUILD_DB_SERVER": "srv.local,14330", "DATABUILD_DB_NAME": "databuild",
           "DATABUILD_PIPELINE_PASSWORD": "Pipe-Senha_123.abc!", "DATABUILD_POWERBI_PASSWORD": "Bi-Senha_456.xyz}"}

    def cfg(self, **extra):
        return db.Config.do_ambiente({**self.ENV, **extra})

    def test_variaveis_documentadas_no_env_example(self):
        t = (ROOT / ".env.example").read_text(encoding="utf-8")
        for v in db.VARIAVEIS:
            self.assertIn(v + "=", t, v)
        for v in db.SEGREDOS:  # nenhum segredo com valor no exemplo
            for linha in t.splitlines():
                if linha.startswith(v + "="):
                    self.assertEqual(linha.split("=", 1)[1].strip(), "", linha)

    def test_env_local_ignorado_pelo_git(self):
        self.assertIn(".env.local", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_admin_windows_nao_leva_senha(self):
        s = self.cfg().odbc("admin")
        self.assertIn("Trusted_Connection=yes", s)
        self.assertNotIn("PWD=", s)
        self.assertIn("SERVER=srv.local,14330", s)

    def test_papeis_usam_usuario_sql_e_escapam_chaves(self):
        s = self.cfg().odbc("powerbi")
        self.assertIn("UID=databuild_powerbi", s)
        self.assertIn("PWD={Bi-Senha_456.xyz}}}", s)  # '}' duplicado dentro de {...}

    def test_url_sqlalchemy_pandas(self):
        from sqlalchemy import create_engine
        eng = create_engine(self.cfg().sqlalchemy_url("pipeline"))  # não conecta
        self.assertEqual(eng.dialect.name, "mssql")
        self.assertEqual(eng.dialect.driver, "pyodbc")

    def test_jdbc_spark(self):
        j = self.cfg().jdbc("pipeline")
        self.assertEqual(j["url"], "jdbc:sqlserver://srv.local:14330;databaseName=databuild;encrypt=true;trustServerCertificate=true")
        self.assertNotIn("Senha", j["url"])
        self.assertEqual(j["driver"], "com.microsoft.sqlserver.jdbc.SQLServerDriver")
        with self.assertRaises(ValueError):
            self.cfg().jdbc("admin")

    def test_uri_airflow(self):
        u = self.cfg().airflow_conn_uri("pipeline")
        self.assertTrue(u.startswith("odbc://databuild_pipeline:"))
        self.assertIn("@srv.local:14330/databuild?driver=ODBC+Driver+18+for+SQL+Server", u)

    def test_instancia_nomeada(self):
        c = db.Config.do_ambiente({"DATABUILD_DB_SERVER": r"PC\SQLEXPRESS", "DATABUILD_PIPELINE_PASSWORD": "x"})
        self.assertEqual((c.host, c.porta, c.instancia), ("PC", None, "SQLEXPRESS"))
        self.assertTrue(c.jdbc("pipeline")["url"].endswith(";instanceName=SQLEXPRESS"))

    def test_segredos_nao_aparecem_em_repr_nem_em_redigido(self):
        c = self.cfg()
        texto = repr(c) + str(c.redigido())
        for senha in ("Pipe-Senha_123.abc!", "Bi-Senha_456.xyz}"):
            self.assertNotIn(senha, texto)

    def test_identificadores_perigosos_recusados(self):
        for ruim in ("db; DROP DATABASE x", "a b", "1abc", "", "x" * 70, "a]b"):
            with self.assertRaises(ValueError):
                db.identificador_valido(ruim)
        with self.assertRaises(ValueError):
            db.Config.do_ambiente({"DATABUILD_DB_NAME": "x'; --"})

    def test_carregar_env_nao_sobrescreve_ambiente(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e"
            p.write_text("# c\nDB_T_A=1\nDB_T_B='dois'\nDB_T_C=\n", encoding="utf-8")
            os.environ["DB_T_A"] = "ja"
            try:
                lidos = db.carregar_env(p)
                self.assertEqual((lidos["DB_T_B"], os.environ["DB_T_A"], os.environ["DB_T_B"]), ("dois", "ja", "dois"))
            finally:
                for k in ("DB_T_A", "DB_T_B", "DB_T_C"):
                    os.environ.pop(k, None)

    def test_senha_gerada_cumpre_politica(self):
        for _ in range(25):
            s = dbadmin.gerar_senha()
            self.assertTrue(dbadmin.senha_valida(s))
            self.assertTrue(any(c.isupper() for c in s) and any(c.islower() for c in s) and any(c.isdigit() for c in s))
            self.assertFalse(set(s) & set(";{}='\""))

    def test_init_env_nao_sobrescreve(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env.local"
            self.assertTrue(dbadmin.iniciar_env(p))
            conteudo = p.read_text(encoding="utf-8")
            self.assertFalse(dbadmin.iniciar_env(p))
            self.assertEqual(p.read_text(encoding="utf-8"), conteudo)
            antes = dict(os.environ)
            try:
                lidos = db.carregar_env(p, sobrescrever=False)
                self.assertTrue(dbadmin.senha_valida(lidos["DATABUILD_PIPELINE_PASSWORD"]))
                self.assertNotEqual(lidos["DATABUILD_PIPELINE_PASSWORD"], lidos["DATABUILD_POWERBI_PASSWORD"])
            finally:
                os.environ.clear()
                os.environ.update(antes)

    def test_descarte_so_de_banco_de_teste(self):
        cfg = self.cfg()
        for nome in ("databuild", "master", "outro"):
            with self.assertRaises(ValueError):
                dbadmin.descartar_banco(cfg, nome)

    def test_modulos_futuros_importaveis(self):
        import pandas  # noqa: F401
        import sqlalchemy  # noqa: F401
        self.assertTrue(hasattr(pyodbc, "connect"))


# ============================================================================================ banco real
def _config_de_teste():
    db.carregar_env()
    base = db.Config.do_ambiente()
    token = secrets.token_hex(4)
    from dataclasses import replace
    return replace(base, banco=f"{dbadmin.PREFIXO_TESTE}{token}", pipeline_user=f"dbt_pipe_{token}",
                   powerbi_user=f"dbt_bi_{token}", pipeline_password=dbadmin.gerar_senha(),
                   powerbi_password=dbadmin.gerar_senha())


class BancoSQLServer(unittest.TestCase):
    cfg: db.Config
    conn = None

    @classmethod
    def setUpClass(cls):
        if pyodbc is None:
            raise unittest.SkipTest("pyodbc não instalado")
        cls.cfg = _config_de_teste()
        try:
            with db.sessao("admin", cls.cfg, banco="master") as c:
                cls.misto = c.execute("SELECT CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS INT)").fetchone()[0] == 0
        except Exception as e:  # sem servidor/driver
            raise unittest.SkipTest(f"SQL Server indisponível ({str(e)[:90]})")
        cls.criado = False
        dbadmin.migrar(cls.cfg, log=lambda *_: None)
        cls.criado = True
        cls.conn = db.conectar("admin", cls.cfg)

    @classmethod
    def tearDownClass(cls):
        if cls.conn is not None:
            cls.conn.close()
        if getattr(cls, "criado", False):
            dbadmin.descartar_banco(cls.cfg, cls.cfg.banco)
            for u in (cls.cfg.pipeline_user, cls.cfg.powerbi_user):
                try:
                    with db.sessao("admin", cls.cfg, banco="master", autocommit=True) as c:
                        c.execute(f"IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{u}') DROP LOGIN [{u}]")
                except Exception:
                    pass

    def q(self, sql, *params):
        return self.conn.execute(sql, *params).fetchall()

    def um(self, sql, *params):
        return self.conn.execute(sql, *params).fetchone()[0]

    # ---- estrutura -------------------------------------------------------------------------------
    def test_banco_e_schemas(self):
        self.assertEqual(self.um("SELECT DB_NAME()"), self.cfg.banco)
        self.assertEqual({r[0] for r in self.q("SELECT name FROM sys.schemas WHERE name IN ('bronze','silver','gold')")},
                         {"bronze", "silver", "gold"})

    def test_tabelas_por_camada(self):
        for schema, esperadas in TABELAS_ESPERADAS.items():
            reais = {r[0] for r in self.q("SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID(?)", schema)}
            self.assertEqual(reais, esperadas, schema)

    def test_valores_financeiros_sao_decimal(self):
        cols = self.q("""SELECT TABLE_SCHEMA + '.' + TABLE_NAME + '.' + COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
                         FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA IN ('silver','gold')
                           AND (COLUMN_NAME NOT LIKE '%[_]id' AND COLUMN_NAME NOT LIKE '%[_]key' AND (COLUMN_NAME LIKE 'valor%' OR COLUMN_NAME LIKE 'val[_]%' OR COLUMN_NAME IN ('saldo','diferenca')
                                OR COLUMN_NAME LIKE 'contrato[_]%' OR COLUMN_NAME LIKE 'impacto%'))""")
        self.assertGreater(len(cols), 20)
        for nome, tipo, prec, esc in cols:
            self.assertEqual((tipo, prec), ("decimal", 18), nome)
            self.assertIn(esc, (2, 4), nome)

    def test_chaves_e_indices(self):
        sem_pk = self.q("""SELECT s.name + '.' + t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
                           WHERE s.name IN ('bronze','silver','gold') AND NOT EXISTS
                                 (SELECT 1 FROM sys.key_constraints k WHERE k.parent_object_id = t.object_id AND k.type = 'PK')""")
        self.assertEqual(sem_pk, [])
        self.assertGreaterEqual(self.um("SELECT COUNT(*) FROM sys.foreign_keys"), 39)
        fk_sem_indice = self.q("""
            SELECT OBJECT_NAME(fk.parent_object_id), fk.name FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fc ON fc.constraint_object_id = fk.object_id AND fc.constraint_column_id = 1
            WHERE fk.parent_object_id IN (SELECT object_id FROM sys.tables WHERE SCHEMA_NAME(schema_id) IN ('silver','gold'))
              AND NOT EXISTS (SELECT 1 FROM sys.index_columns ic WHERE ic.object_id = fc.parent_object_id
                              AND ic.column_id = fc.parent_column_id AND ic.key_ordinal = 1)""")
        # toda FK de Silver/Gold tem índice cujo 1º campo é a coluna inicial da FK
        self.assertEqual(fk_sem_indice, [], fk_sem_indice)

    def test_migrations_registradas_e_reexecucao(self):
        migrations = dbadmin.listar_migrations()
        n = len(migrations)
        self.assertEqual(self.um("SELECT COUNT(*) FROM dbo.schema_migration"), n)
        self.assertEqual(dbadmin.migrar(self.cfg, log=lambda *_: None), [])      # nada pendente
        antes = (self.um("SELECT COUNT(*) FROM gold.dim_data"), self.um("SELECT COUNT(*) FROM gold.dim_classe"))
        refeitas = dbadmin.migrar(self.cfg, reexecutar=True, log=lambda *_: None)
        self.assertEqual(refeitas, [m.caminho.stem for m in migrations if m.reexecutavel])
        self.assertEqual(self.um("SELECT COUNT(*) FROM dbo.schema_migration WHERE versao = 10"), 1)
        self.assertEqual((self.um("SELECT COUNT(*) FROM gold.dim_data"), self.um("SELECT COUNT(*) FROM gold.dim_classe")), antes)
        self.assertEqual(self.um("SELECT COUNT(*) FROM dbo.schema_migration"), n)

    def test_migration_alterada_e_recusada(self):
        self.conn.execute("UPDATE dbo.schema_migration SET checksum_sha256 = REPLICATE('0', 64) WHERE versao = 3")
        self.conn.commit()
        try:
            with self.assertRaises(RuntimeError):
                dbadmin.migrar(self.cfg, log=lambda *_: None)
        finally:
            dbadmin.migrar(self.cfg, reexecutar=True, log=lambda *_: None)

    def test_calendario_e_dimensoes_fixas(self):
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_data WHERE data_key > 0"), 5844)  # 2020-01-01..2035-12-31
        self.assertEqual(self.um("SELECT dia_semana FROM gold.dim_data WHERE data_key = 20261124"), 2)   # terça-feira
        self.assertEqual(self.um("SELECT nome_mes FROM gold.dim_data WHERE data_key = 20260305"), "março")
        self.assertEqual(self.um("SELECT ano_mes FROM gold.dim_data WHERE data_key = 20261124"), "2026-11")
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_classe"), 4)
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_severidade"), 4)
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_causa"), 2)
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_item WHERE item_key = -1"), 1)

    # ---- integridade (casos negativos) --------------------------------------------------------------
    def _recusa(self, sql, *params):
        try:
            with self.assertRaises(pyodbc.Error):
                self.conn.execute(sql, *params)
        finally:
            self.conn.rollback()

    def test_restricoes_de_integridade(self):
        self._recusa("INSERT INTO silver.medicao (obra_id, numero, rotulo, contrato_total, carga_id) VALUES (999, 1, 'M', 1, 999)")
        self._recusa("INSERT INTO bronze.carga (origem, status) VALUES ('x', 'invalido')")
        self._recusa("INSERT INTO bronze.carga (origem, iniciada_em, finalizada_em) VALUES ('x', '2026-02-01', '2026-01-01')")
        self._recusa("INSERT INTO silver.obra (obra_codigo, nome) VALUES ('X', '   ')")
        self._recusa("INSERT INTO gold.dim_classe (classe_key, classe, rotulo, ordem) VALUES (1, 'dup', 'x', 9)")
        self._recusa("INSERT INTO silver.regra (regra_id, versao, versao_catalogo, categoria, classe_padrao, severidade_padrao, titulo, descricao) "
                     "VALUES ('r', '1', '1', 'inexistente', 'comprovado', 'alta', 't', 'd')")

    def test_decimal_arredonda_e_nao_estoura(self):
        self.conn.execute("INSERT INTO bronze.carga (origem) VALUES (N'teste decimal')")
        carga = self.um("SELECT MAX(carga_id) FROM bronze.carga")
        self.conn.execute("INSERT INTO silver.obra (obra_codigo, nome) VALUES (N'DEC', N'Decimal')")
        obra = self.um("SELECT obra_id FROM silver.obra WHERE obra_codigo = N'DEC'")
        with self.assertRaises(pyodbc.Error):  # DECIMAL(18,2) não aceita 10^17
            self.conn.execute("INSERT INTO silver.medicao (obra_id, numero, rotulo, contrato_total, carga_id) VALUES (?, 1, N'D', ?, ?)",
                              obra, Decimal("100000000000000000.00"), carga)
        self.conn.rollback()
        self.assertEqual(self.um("SELECT CAST(0.12345 AS DECIMAL(18,4))"), Decimal("0.1235"))   # sem erro de ponto flutuante
        self.assertEqual(self.um("SELECT CAST(0.1 AS DECIMAL(18,4)) + CAST(0.2 AS DECIMAL(18,4))"), Decimal("0.3000"))
        self.conn.rollback()

    # ---- dados sintéticos: Silver -> Gold -------------------------------------------------------------
    def _carregar_sinteticos(self):
        c = self.conn
        c.execute("DELETE FROM gold.bridge_cascata_primaria; DELETE FROM gold.fact_ocorrencia; DELETE FROM gold.fact_valor_item; "
                  "DELETE FROM gold.fact_medicao")
        for t in ("silver.log_processamento", "silver.ocorrencia_causa_raiz", "silver.ocorrencia", "silver.formula_item",
                  "silver.valor_item", "silver.item_medicao", "silver.medicao", "silver.regra", "silver.obra"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM gold.dim_item WHERE item_key > 0; DELETE FROM gold.dim_medicao; DELETE FROM gold.dim_regra; "
                  "DELETE FROM gold.dim_obra WHERE obra_key > 0")
        carga = c.execute("INSERT INTO bronze.carga (origem, versao_motor, versao_catalogo) OUTPUT INSERTED.carga_id "
                          "VALUES (N'sintetico', N'test', N'2.0.0')").fetchone()[0]
        obra = c.execute("INSERT INTO silver.obra (obra_codigo, nome) OUTPUT INSERTED.obra_id VALUES (N'OBRA-TESTE', N'Obra sintética')").fetchone()[0]
        for rid, cat, cl, sev in (("erro_de_fórmula", "referencia", "comprovado", "alta"),
                                  ("valor_armazenado_diverge_do_recalculo", "aritmetica", "comprovado", "media")):
            c.execute("INSERT INTO silver.regra (regra_id, versao, versao_catalogo, categoria, classe_padrao, severidade_padrao, titulo, descricao, usa_recalculo) "
                      "VALUES (?, N'1.0.0', N'2.0.0', ?, ?, ?, ?, N'sintética', 1)", rid, cat, cl, sev, rid)
        itens: dict[tuple[int, str], int] = {}
        meds = {}
        for num, (ini, fim) in ((1, ("2026-01-01", "2026-01-31")), (2, ("2026-02-01", "2026-02-28"))):
            med = c.execute("INSERT INTO silver.medicao (obra_id, numero, rotulo, periodo_inicio, periodo_fim, contrato_total, contrato_controle, "
                            "arquivo_sha256, n_servicos, n_categorias, carga_id) OUTPUT INSERTED.medicao_id VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 2, ?)",
                            obra, num, f"MED {num:02d}", ini, fim, Decimal("1000.00"), Decimal("1000.00"), "ab" * 32, carga).fetchone()[0]
            meds[num] = med
            pai = None
            for cod, tipo, nivel, desc in (("TOTAL", "total", 0, "Total"), ("A", "lote", 1, "Lote A"),
                                           ("A.1", "secao", 2, f"Seção A.1 v{num}"), ("A.1.1", "folha", 3, "Serviço sintético")):
                itens[(num, cod)] = c.execute(
                    "INSERT INTO silver.item_medicao (medicao_id, codigo, tipo, pai_item_id, nivel, descricao, unidade) OUTPUT INSERTED.item_id "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", med, cod, tipo, pai, nivel, desc, "m2" if tipo == "folha" else None).fetchone()[0]
                pai = itens[(num, cod)]
            for cod in ("TOTAL", "A.1.1"):
                for campo, arm, rec in (("val_med", "100.0000", "100.0000"), ("val_ac", "100.0000", "90.0000"), ("saldo", "900.0000", "910.0000")):
                    c.execute("INSERT INTO silver.valor_item (item_id, campo, valor_armazenado, valor_recalculado) VALUES (?, ?, ?, ?)",
                              itens[(num, cod)], campo, Decimal(arm), Decimal(rec))
            c.execute("INSERT INTO silver.formula_item (item_id, campo, formula_texto) VALUES (?, N'val_med', N'=ROUND(F10*G10,2)')", itens[(num, "A.1.1")])
        m1, m2 = meds[1], meds[2]
        p1 = "OC-0000000001"
        ins = ("INSERT INTO silver.ocorrencia (ocorrencia_id, obra_id, medicao_id, regra_id, versao_regra, classe, severidade, causa, aba, celula, "
               "item_id, campo, descricao, valor_original, valor_esperado, diferenca, contabiliza_impacto, carga_id) "
               "VALUES (?, ?, ?, ?, N'1.0.0', ?, ?, ?, N'Resumo', ?, ?, ?, N'sintética', ?, ?, ?, ?, ?)")
        c.execute(ins, p1, obra, m1, "valor_armazenado_diverge_do_recalculo", "comprovado", "alta", "primaria", "H10",
                  itens[(1, "A.1.1")], "val_ac", Decimal("100.00"), Decimal("90.00"), Decimal("10.00"), 1, carga)
        c.execute(ins, "OC-0000000002", obra, m1, "valor_armazenado_diverge_do_recalculo", "comprovado", "media", "cascata", "H8",
                  itens[(1, "A.1")], "val_ac", Decimal("100.00"), Decimal("90.00"), Decimal("10.00"), 0, carga)
        c.execute(ins, "OC-0000000003", obra, m1, "valor_armazenado_diverge_do_recalculo", "comprovado", "media", "cascata", "H5",
                  itens[(1, "TOTAL")], "val_ac", Decimal("100.00"), Decimal("90.00"), Decimal("10.00"), 0, carga)
        c.execute(ins, "OC-0000000004", obra, m2, "erro_de_fórmula", "suspeita", "baixa", "primaria", "CFF!B2",
                  None, None, None, None, None, 0, carga)
        for casc in ("OC-0000000002", "OC-0000000003"):
            c.execute("INSERT INTO silver.ocorrencia_causa_raiz (cascata_id, primaria_id) VALUES (?, ?)", casc, p1)
        c.commit()

    def test_carga_gold_com_dados_sinteticos_e_idempotente(self):
        self._carregar_sinteticos()
        for _ in range(2):   # a segunda execução precisa dar o mesmo resultado
            self.conn.execute("EXEC gold.usp_carregar_gold")
            self.conn.commit()
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_obra WHERE obra_key > 0"), 1)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_medicao"), 2)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_item WHERE item_key > 0"), 4)      # conforme entre medições
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.dim_regra"), 2)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.fact_medicao"), 2)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.fact_valor_item"), 4)                  # 2 medições x (TOTAL, A.1.1)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.fact_ocorrencia"), 4)
            self.assertEqual(self.um("SELECT COUNT(*) FROM gold.bridge_cascata_primaria"), 2)
        self.assertEqual(self.um("SELECT SUM(impacto_contabilizado) FROM gold.fact_ocorrencia"), Decimal("10.00"))   # só a primária
        self.assertEqual(self.um("SELECT COUNT(*) FROM gold.fact_ocorrencia WHERE contabiliza_impacto = 0 AND impacto_contabilizado <> 0"), 0)
        self.assertEqual(self.um("SELECT item_key FROM gold.fact_ocorrencia WHERE ocorrencia_id = 'OC-0000000004'"), -1)   # sem item
        self.assertEqual(self.um("SELECT descricao FROM gold.dim_item WHERE codigo = 'A.1'"), "Seção A.1 v2")            # Tipo 1: mais recente
        self.assertEqual(self.um("SELECT data_key FROM gold.fact_medicao fm JOIN gold.dim_medicao dm ON dm.medicao_key = fm.medicao_key WHERE dm.numero = 2"), 20260228)
        self.assertEqual(self.um("SELECT valor_medido_mes FROM gold.fact_medicao fm JOIN gold.dim_medicao dm ON dm.medicao_key = fm.medicao_key WHERE dm.numero = 1"), Decimal("100.0000"))
        # estrela: toda chave de fato resolve numa dimensão (a FK garante; conferimos pela junção completa)
        self.assertEqual(self.um("""SELECT COUNT(*) FROM gold.fact_ocorrencia f
            JOIN gold.dim_obra o ON o.obra_key = f.obra_key JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key
            JOIN gold.dim_item i ON i.item_key = f.item_key JOIN gold.dim_regra r ON r.regra_key = f.regra_key
            JOIN gold.dim_classe c ON c.classe_key = f.classe_key JOIN gold.dim_severidade s ON s.severidade_key = f.severidade_key
            JOIN gold.dim_causa ca ON ca.causa_key = f.causa_key JOIN gold.dim_data d ON d.data_key = f.data_key"""), 4)
        self.assertEqual(self.um("SELECT regra_id FROM gold.dim_regra WHERE regra_id = N'erro_de_fórmula'"), "erro_de_fórmula")   # acentos

    def test_visoes_da_silver_e_qualidade(self):
        self._carregar_sinteticos()
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_erro_primario"), 2)
        self.assertEqual(self.um("SELECT n_cascatas FROM silver.vw_erro_primario WHERE ocorrencia_id = 'OC-0000000001'"), 2)
        self.assertEqual(self.um("SELECT causas_raiz FROM silver.vw_cascata WHERE ocorrencia_id = 'OC-0000000002'"), "OC-0000000001")
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_servico"), 2)
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_categoria"), 6)
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_qualidade_cascata_sem_causa"), 0)
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_qualidade_impacto_duplicado"), 0)
        self.conn.execute("DELETE FROM silver.ocorrencia_causa_raiz WHERE cascata_id = 'OC-0000000003'")   # injeta defeito
        self.assertEqual(self.um("SELECT COUNT(*) FROM silver.vw_qualidade_cascata_sem_causa"), 1)
        self.conn.rollback()

    def test_regras_de_causa_raiz_e_impacto_no_esquema(self):
        self._carregar_sinteticos()
        self._recusa("INSERT INTO silver.ocorrencia_causa_raiz (cascata_id, primaria_id) VALUES ('OC-0000000001', 'OC-0000000004')")  # origem não é cascata
        self._recusa("INSERT INTO silver.ocorrencia_causa_raiz (cascata_id, primaria_id) VALUES ('OC-0000000002', 'OC-0000000003')")  # causa não é primária
        self._recusa("UPDATE silver.ocorrencia SET contabiliza_impacto = 1 WHERE ocorrencia_id = 'OC-0000000002'")                   # cascata não carrega impacto
        self._recusa("UPDATE silver.ocorrencia SET item_id = (SELECT TOP 1 item_id FROM silver.item_medicao WHERE medicao_id <> "
                     "(SELECT medicao_id FROM silver.ocorrencia WHERE ocorrencia_id = 'OC-0000000001')) WHERE ocorrencia_id = 'OC-0000000001'")  # item de outra medição
        self._recusa("INSERT INTO silver.medicao (obra_id, numero, rotulo, contrato_total, carga_id) "
                     "SELECT obra_id, numero, N'MED 99', 1, carga_id FROM silver.medicao WHERE numero = 1")                          # número repetido na obra

    # ---- permissões ----------------------------------------------------------------------------------
    def _como(self, usuario, sql):
        self.conn.execute(f"EXECUTE AS USER = N'{usuario}'")
        try:
            return _tentar(self.conn, sql)
        finally:
            self.conn.execute("REVERT")
            self.conn.rollback()

    def test_papeis_pipeline_e_powerbi(self):
        self._carregar_sinteticos()
        for u, role in (("u_teste_pipe", dbadmin.PAPEIS_BANCO["pipeline"]), ("u_teste_bi", dbadmin.PAPEIS_BANCO["powerbi"])):
            self.conn.execute(f"IF DATABASE_PRINCIPAL_ID(N'{u}') IS NULL CREATE USER [{u}] WITHOUT LOGIN")
            self.conn.execute(f"ALTER ROLE [{role}] ADD MEMBER [{u}]")
        self.conn.commit()
        P, B = "u_teste_pipe", "u_teste_bi"
        # Power BI: lê a Gold; sem acesso a bronze/silver; sem escrita nem execução
        self.assertEqual(self._como(B, "SELECT TOP 1 * FROM gold.fact_ocorrencia"), "permitido")
        self.assertEqual(self._como(B, "SELECT TOP 1 * FROM gold.dim_data"), "permitido")
        self.assertEqual(self._como(B, "SELECT TOP 1 * FROM silver.ocorrencia"), "negado")
        self.assertEqual(self._como(B, "SELECT TOP 1 * FROM bronze.carga"), "negado")
        self.assertEqual(self._como(B, "SELECT TOP 1 * FROM dbo.schema_migration"), "negado")
        self.assertEqual(self._como(B, "DELETE FROM gold.dim_causa WHERE 1 = 0"), "negado")
        self.assertEqual(self._como(B, "EXEC gold.usp_carregar_gold"), "negado")
        self.assertEqual(self._como(B, "CREATE TABLE gold.x_teste (a INT)"), "negado")
        # Pipeline: lê e grava nas três camadas e executa a carga; não altera esquema
        self.assertEqual(self._como(P, "SELECT TOP 1 * FROM silver.ocorrencia"), "permitido")
        self.assertEqual(self._como(P, "INSERT INTO bronze.carga (origem) VALUES (N'perm')"), "permitido")
        self.assertEqual(self._como(P, "EXEC gold.usp_carregar_gold"), "permitido")
        self.assertEqual(self._como(P, "CREATE TABLE silver.x_teste (a INT)"), "negado")
        self.assertEqual(self._como(P, "SELECT TOP 1 * FROM dbo.schema_migration"), "negado")

    def test_logins_reais_provisionados_e_verificacao(self):
        if not self.misto:
            self.skipTest("autenticação mista desabilitada no servidor (rode install_local_sqlserver.ps1)")
        dbadmin.provisionar(self.cfg, log=lambda *_: None)
        dbadmin.provisionar(self.cfg, log=lambda *_: None)    # reexecução (ALTER LOGIN) sem erro
        self.assertTrue(dbadmin.verificar(self.cfg, log=lambda *_: None))
        with db.sessao("powerbi", self.cfg) as c:
            self.assertEqual(c.execute("SELECT USER_NAME()").fetchone()[0], self.cfg.powerbi_user)
            with self.assertRaises(pyodbc.Error):
                c.execute("SELECT TOP 1 * FROM silver.obra").fetchall()


def _tentar(conn, sql):
    return dbadmin._tentar(conn, sql)


if __name__ == "__main__":
    unittest.main()
