"""Promove Bronze para Silver no SQL Server e monitora a execução."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from uuid import uuid4

import pandas as pd

import build
import catalog
import db
import engine
import ingest
import reader
import recalc
import silver_source

VERSAO = "silver-1"
JAR = db.ROOT / ".runtime" / "mssql-jdbc-13.6.0.jre11.jar"
NIVEIS = {"total": 0, "lote": 1, "secao": 2, "folha": 3}
CELULA = re.compile(r"[A-Z]{1,3}([1-9]\d*)\Z")
STAGING = "silver.stg_valor_item"


class ErroSilver(RuntimeError):
    """Falha com mensagem apropriada para o operador."""


@contextmanager
def sessao_spark():
    from pyspark.sql import SparkSession

    java = db.ROOT / ".runtime" / "jdk17"
    if (java / "bin" / "java.exe").is_file():
        os.environ.setdefault("JAVA_HOME", str(java))
    jar = Path(os.environ.get("DATABUILD_JDBC_JAR", str(JAR))).resolve()
    if not jar.is_file():
        raise ErroSilver("Driver JDBC indisponível. Execute database/setup/install_silver.py.")
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    spark = (SparkSession.builder.master("local[2]").appName("DataBuild Silver")
             .config("spark.driver.extraClassPath", str(jar))
             .config("spark.executor.extraClassPath", str(jar))
             .config("spark.driver.host", "127.0.0.1")
             .config("spark.driver.bindAddress", "127.0.0.1")
             .config("spark.ui.enabled", "false")
             .config("spark.eventLog.enabled", "false")
             .config("spark.sql.shuffle.partitions", "2")
             .getOrCreate())
    try:
        spark.sparkContext.setLogLevel("ERROR")
        yield spark
    finally:
        spark.stop()


def _id_manifesto(arquivos):
    dados = [VERSAO, catalog.CATALOG_VERSION, [[a.id, a.sha256] for a in arquivos]]
    return hashlib.sha256(json.dumps(dados).encode("utf-8")).hexdigest()


def _validar_arquivos(arquivos):
    if not arquivos:
        raise ErroSilver("A Bronze não possui arquivos disponíveis para promover.")
    rotulos = [a.rotulo for a in arquivos]
    if any(not isinstance(r, str) or not re.fullmatch(r"MED \d+", r) or len(r) > 30 for r in rotulos):
        raise ErroSilver("Há um rótulo de medição fora do padrão MED seguido do número.")
    numeros = [int(r.split()[1]) for r in rotulos]
    if len(set(numeros)) != len(numeros) or any(not 0 < n <= 32767 for n in numeros):
        raise ErroSilver("Há números de medição inválidos ou ambíguos na Bronze.")


def _linha(celula):
    match = CELULA.fullmatch(celula or "")
    return int(match[1]) if match else None


def _contabil(valor):
    return None if valor is None else Decimal(str(valor)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def _data(texto):
    return date.fromisoformat(texto) if texto else None


def _itens(med, fonte):
    col_item = fonte.med.cols.get("item", "A")
    linhas = [(l.codigo, l.tipo, l.pai, NIVEIS[l.tipo], l.descricao,
               l.unidade if l.tipo == "folha" else None,
               l.servico if l.tipo == "folha" else None,
               l.fonte if l.tipo == "folha" else None, l.row,
               fonte.med.total_celula if l.tipo == "total" else f"{col_item}{l.row}")
              for l in med.linhas.values()]
    df = pd.DataFrame(linhas, columns=["codigo", "tipo", "pai", "nivel", "descricao", "unidade",
                                       "servico", "fonte", "linha", "celula"])
    for campo in ("descricao", "unidade", "servico", "fonte"):
        df[campo] = df[campo].str.strip()
    df = df.astype(object).where(df.notna() & df.ne(""), None)
    return df.sort_values(["nivel", "linha"]).itertuples(index=False)


def _origens_medicao(med, fonte):
    origens = [(campo, reader.FIN_SHEET, celula)
               for campo, celula in build.origens_controle(fonte.med, med.numero).items()]
    if med.total and "valor_contrato" in med.colunas:
        origens.append(("contrato_total", reader.MAIN_SHEET, f"{med.colunas['valor_contrato']}{med.total.row}"))
    return origens


def _valores(res, fontes):
    ocorrencias = [(o.medicao, o.celula, o.classe) for o in res.ocorrencias if o.aba == reader.MAIN_SHEET]
    achados = pd.DataFrame(ocorrencias, columns=["medicao", "celula", "classe"])
    achados["contratual"] = achados.classe.eq("depende_validacao_contratual")
    por_celula = achados.groupby(["medicao", "celula"]).contratual.any().to_dict()
    linhas = []
    for med in res.medicoes:
        fonte = fontes[med.id]
        externos = {l.codigo: {campo: fonte.decimal(reader.MAIN_SHEET, f"{col}{l.row}")
                               for campo, col in med.colunas.items()} for l in med.linhas.values()}
        with localcontext() as ctx:
            ctx.prec = 50
            recalc.calcular_valores(med, externos)
        for l in med.linhas.values():
            for campo, col in med.colunas.items():
                celula = f"{col}{l.row}"
                original = externos[l.codigo][campo]
                esperado = l.recalc.get(campo)
                chave = (med.id, celula)
                if por_celula.get(chave, False):
                    situacao = "pendente_contratual"
                elif original is None or campo in recalc.CAMPOS_CALC and esperado is None:
                    situacao = "pendente_dados"
                elif chave in por_celula:
                    situacao = "com_ocorrencia"
                else:
                    situacao = "sem_ocorrencia"
                linhas.append((fonte.arquivo.id, l.codigo, campo,
                               None if original is None else str(original),
                               None if esperado is None else str(esperado),
                               fonte.formulas.get(celula), celula, situacao))
    return linhas


def _gravar_jdbc(spark, cfg, run_id, linhas):
    from pyspark.sql import functions as F

    schema = ("arquivo_id int, codigo string, campo string, original string, recalculado string, "
              "formula_texto string, celula_origem string, situacao string")
    dados = spark.createDataFrame(linhas, schema=schema)
    (dados.select(F.lit(run_id).alias("execucao_id"), "arquivo_id", "codigo", "campo",
                  F.col("original").cast("decimal(18,4)").alias("valor_armazenado"),
                  F.col("recalculado").cast("decimal(18,4)").alias("valor_recalculado"),
                  "formula_texto", "celula_origem", "situacao")
     .coalesce(1).write.format("jdbc").options(**cfg.jdbc(), dbtable=STAGING, batchsize=1000)
     .mode("append").save())


def _regra(cur, r):
    cur.execute("""
        INSERT INTO silver.regra (regra_id, versao, versao_catalogo, categoria, classe_padrao,
            severidade_padrao, titulo, descricao, sugestao, usa_recalculo)
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM silver.regra WHERE regra_id = ? AND versao = ?)
    """, r.id, r.versao, catalog.CATALOG_VERSION, r.categoria, r.classe, r.severidade, r.titulo,
        r.descricao, r.sugestao, r.recalculo, r.id, r.versao)


def _gravar_medicao(cur, obra, med, fonte, run_id):
    origens = _origens_medicao(med, fonte)
    contratos = {campo: _contabil(fonte.decimal(aba, celula)) for campo, aba, celula in origens
                 if campo.startswith("contrato")}
    arq = fonte.arquivo
    mid = cur.execute("""
        INSERT INTO silver.medicao (obra_id, numero, rotulo, periodo_inicio, periodo_fim, contrato_total,
            contrato_controle, arquivo_sha256, n_servicos, n_categorias, carga_id, arquivo_origem_id, execucao_id)
        OUTPUT INSERTED.medicao_id VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, obra, med.numero, med.id, _data(med.periodo_inicio), _data(med.periodo_fim),
        contratos.get("contrato_total"), contratos.get("contrato_controle"), arq.sha256, len(med.folhas()),
        sum(l.tipo in ("lote", "secao") for l in med.linhas.values()), arq.carga_id, arq.id, run_id).fetchone()[0]
    for campo, aba, celula in origens:
        cur.execute("INSERT INTO silver.medicao_origem VALUES (?, ?, ?, ?, ?)", mid, campo,
                    fonte.abas[aba], _linha(celula), celula)
    ids = {None: None}
    for i in _itens(med, fonte):
        ids[i.codigo] = cur.execute("""
            INSERT INTO silver.item_medicao (medicao_id, codigo, tipo, pai_item_id, nivel, descricao, unidade,
                codigo_servico_ref, fonte, aba_origem_id, linha_origem, celula_origem)
            OUTPUT INSERTED.item_id VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, mid, i.codigo, i.tipo, ids[i.pai], int(i.nivel), i.descricao, i.unidade, i.servico, i.fonte,
            fonte.abas[reader.MAIN_SHEET], int(i.linha), i.celula).fetchone()[0]
    return mid, ids


def _gravar_ocorrencias(cur, obra, res, fontes, ids):
    for o in res.ocorrencias:
        mid, itens = ids[o.medicao]
        cur.execute("""
            INSERT INTO silver.ocorrencia (ocorrencia_id, obra_id, medicao_id, regra_id, versao_regra,
              classe, severidade, causa, aba, celula, item_id, campo, descricao, evidencia, valor_original,
              valor_esperado, diferenca, contabiliza_impacto, sugestao, carga_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, o.id, obra, mid, o.regra, o.versao_regra, o.classe, o.severidade, o.causa, o.aba, o.celula,
            itens.get(o.codigo_item), o.campo or None, o.descricao, o.evidencia or None,
            _contabil(o.valor_original), _contabil(o.valor_esperado), _contabil(o.diferenca),
            o.contabiliza_impacto, o.sugestao or None, fontes[o.medicao].arquivo.carga_id)
        refs = set(o.referencias)
        if _linha(o.celula) is not None:
            refs.add((o.medicao, o.aba, o.celula, "alvo"))
        if not refs:
            raise ErroSilver("Uma ocorrência ficou sem coordenada de origem.")
        for med, aba, celula, papel in sorted(refs):
            cur.execute("INSERT INTO silver.ocorrencia_origem VALUES (?, ?, ?, ?, ?)", o.id,
                        fontes[med].abas[aba], _linha(celula), celula, papel)
    for o in res.ocorrencias:
        if o.causa == "cascata":
            if not o.causa_raiz_id:
                raise ErroSilver("Uma cascata ficou sem causa primária identificada.")
            for raiz in set(o.causa_raiz_id.split(",")):
                cur.execute("INSERT INTO silver.ocorrencia_causa_raiz (cascata_id, primaria_id) VALUES (?, ?)",
                            o.id, raiz)


def _publicar(conn, run_id, res, fontes, n_valores):
    cur = conn.cursor()
    obra = cur.execute("SELECT obra_id FROM silver.obra WHERE obra_codigo = ?", ingest.OBRA_ID).fetchone()
    if obra is None:
        obra = cur.execute("INSERT INTO silver.obra (obra_codigo, nome) OUTPUT INSERTED.obra_id VALUES (?, ?)",
                           ingest.OBRA_ID, ingest.NOME_OBRA).fetchone()
    for regra in catalog.CATALOGO.values():
        _regra(cur, regra)
    ids = {m.id: _gravar_medicao(cur, obra[0], m, fontes[m.id], run_id) for m in res.medicoes}
    juncoes = """
        FROM silver.stg_valor_item s JOIN silver.medicao m ON m.arquivo_origem_id = s.arquivo_id
        JOIN silver.item_medicao i ON i.medicao_id = m.medicao_id AND i.codigo = s.codigo
        WHERE s.execucao_id = ? AND m.execucao_id = s.execucao_id
    """
    if cur.execute("SELECT COUNT(*) " + juncoes, run_id).fetchone()[0] != n_valores:
        raise ErroSilver("A quantidade de valores tipados diverge dos itens de destino.")
    cur.execute("""
        INSERT INTO silver.valor_item (item_id, campo, valor_armazenado, valor_recalculado, celula_origem, situacao)
        SELECT i.item_id, s.campo, s.valor_armazenado, s.valor_recalculado, s.celula_origem, s.situacao
    """ + juncoes, run_id)
    cur.execute("""
        INSERT INTO silver.formula_item (item_id, campo, formula_texto)
        SELECT i.item_id, s.campo, s.formula_texto
    """ + juncoes + " AND s.formula_texto IS NOT NULL", run_id)
    _gravar_ocorrencias(cur, obra[0], res, fontes, ids)
    cur.execute("DELETE FROM silver.stg_valor_item WHERE execucao_id = ?", run_id)
    cur.execute("UPDATE silver.execucao SET status = N'concluida', etapa = N'publicacao', "
                "finalizada_em = SYSUTCDATETIME() WHERE execucao_id = ?", run_id)


@contextmanager
def _bloqueio(cfg):
    with db.sessao("pipeline", cfg, autocommit=True) as controle:
        if not db.travar_sequencia(controle, "Exclusive", "Session", 0):
            raise ErroSilver("Já existe uma promoção Silver em execução.")
        try:
            yield controle
        finally:
            controle.execute("EXEC sp_releaseapplock @Resource=N'DataBuild.Silver', @LockOwner=N'Session';")


def promover(spark, cfg):
    """Publica uma versão completa e imutável da sequência de medições."""
    with _bloqueio(cfg) as controle:
        # Uma tentativa ainda aberta com a trava livre perdeu o processo que a iniciou.
        controle.execute("""
            UPDATE silver.execucao SET status = N'falhou', erro_tipo = N'ExecucaoInterrompida',
                finalizada_em = SYSUTCDATETIME() WHERE status = N'iniciada'
        """)
        arquivos = silver_source.manifesto(controle)
        _validar_arquivos(arquivos)
        manifesto = _id_manifesto(arquivos)
        feito = controle.execute("SELECT execucao_id FROM silver.execucao WHERE manifesto = ? "
                                 "AND status = N'concluida'", manifesto).fetchone()
        if feito:
            return {"status": "inalterada", "execucao_id": feito[0], "arquivos": len(arquivos)}
        if controle.execute("SELECT TOP (1) 1 FROM silver.medicao").fetchone():
            raise ErroSilver("A Silver contém outra sequência de medições. É preciso definir a revisão antes de publicar.")
        run_id = uuid4().hex
        etapa = "leitura"
        controle.execute("INSERT INTO silver.execucao (execucao_id, manifesto, versao, status, etapa, n_arquivos) "
                         "VALUES (?, ?, ?, N'iniciada', ?, ?)", run_id, manifesto, VERSAO, etapa, len(arquivos))
        try:
            fontes = {f.arquivo.rotulo: f for f in silver_source.ler(spark, cfg, arquivos)}
            etapa = "validacao"
            controle.execute("UPDATE silver.execucao SET etapa = ? WHERE execucao_id = ?", etapa, run_id)
            resultado = engine.audit_medicoes((f.med for f in fontes.values()), ingest.OBRA_ID)
            linhas = _valores(resultado, fontes)
            etapa = "jdbc"
            controle.execute("UPDATE silver.execucao SET etapa = ? WHERE execucao_id = ?", etapa, run_id)
            _gravar_jdbc(spark, cfg, run_id, linhas)
            etapa = "publicacao"
            with db.sessao("pipeline", cfg) as destino:
                if silver_source.manifesto(destino) != arquivos:
                    raise ErroSilver("O manifesto Bronze mudou durante a promoção.")
                _publicar(destino, run_id, resultado, fontes, len(linhas))
        except Exception as exc:
            with db.sessao("pipeline", cfg) as destino:
                # Os únicos registros removidos são intermediários desta tentativa.
                destino.execute("DELETE FROM silver.stg_valor_item WHERE execucao_id = ?", run_id)
                destino.execute("UPDATE silver.execucao SET status = N'falhou', etapa = ?, erro_tipo = ?, "
                                "finalizada_em = SYSUTCDATETIME() WHERE execucao_id = ? AND status = N'iniciada'",
                                etapa, type(exc).__name__[:80], run_id)
            if isinstance(exc, ErroSilver):
                raise
            raise ErroSilver(f"Falha em {etapa} ({type(exc).__name__}). Consulte o status da execução.") from exc
    return {"status": "concluida", "execucao_id": run_id, "arquivos": len(arquivos),
            "valores": len(linhas), "ocorrencias": len(resultado.ocorrencias)}


def status(cfg):
    with db.sessao("pipeline", cfg) as conn:
        tabelas = ("obra", "medicao", "medicao_origem", "item_medicao", "valor_item", "formula_item", "regra",
                   "ocorrencia", "ocorrencia_origem", "ocorrencia_causa_raiz", "stg_valor_item")
        totais = {t: conn.execute(f"SELECT COUNT(*) FROM silver.{t}").fetchone()[0] for t in tabelas}
        campos = ("execucao_id", "status", "etapa", "n_arquivos", "iniciada_em", "finalizada_em", "erro_tipo")
        runs = [dict(zip(campos, r)) for r in conn.execute(
            "SELECT TOP (10) " + ", ".join(campos) + " FROM silver.execucao ORDER BY iniciada_em DESC, execucao_id")]
        por_classe = dict(conn.execute("SELECT classe, COUNT(*) FROM silver.ocorrencia GROUP BY classe").fetchall())
        por_causa = dict(conn.execute("SELECT causa, COUNT(*) FROM silver.ocorrencia GROUP BY causa").fetchall())
        situacoes = dict(conn.execute("SELECT situacao, COUNT(*) FROM silver.valor_item GROUP BY situacao").fetchall())
    return {"totais": totais, "classes": por_classe, "causas": por_causa, "valores": situacoes, "execucoes": runs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comando", choices=("run", "status", "verify"))
    parser.add_argument("--json", action="store_true", help="saída compacta para automação")
    parser.add_argument("--comparar-etapa02", action="store_true", help="em verify, compara com a referência local")
    args = parser.parse_args(argv)
    if args.comparar_etapa02 and args.comando != "verify":
        parser.error("--comparar-etapa02 exige verify")
    db.carregar_env()
    try:
        cfg = db.Config.do_ambiente()
        if args.comando == "run":
            with sessao_spark() as spark:
                resultado = promover(spark, cfg)
        elif args.comando == "status":
            resultado = status(cfg)
        else:
            import silver_verify
            resultado = silver_verify.verificar(cfg, args.comparar_etapa02)
        print(json.dumps(resultado, ensure_ascii=False, default=str, indent=None if args.json else 2))
        return 0 if resultado.get("ok", True) else 1
    except ErroSilver as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(f"Falha da Silver ({type(exc).__name__}).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
