"""Reconstrói a fonte do motor de regras exclusivamente a partir da Bronze."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd

from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

import ingest
import reader


@dataclass(frozen=True)
class Arquivo:
    id: int
    rotulo: str
    sha256: str
    carga_id: int
    abas: int
    celulas: int


@dataclass
class Fonte:
    arquivo: Arquivo
    med: reader.Med
    abas: dict[str, int]
    numeros: dict[str, dict[str, str]]
    formulas: dict[str, str]

    def decimal(self, aba, celula):
        numero = self.numeros.get(aba, {}).get(celula)
        return Decimal(numero) if numero is not None else None


def manifesto(conn):
    return [Arquivo(*r) for r in conn.execute(
        "SELECT arquivo_id, medicao_id, sha256, primeira_carga_id, n_abas, n_celulas "
        "FROM bronze.arquivo ORDER BY medicao_id, sha256"
    )]


def _valor(tipo, texto):
    if tipo == "numero":
        signo = texto.removeprefix("-")
        return int(texto) if signo.isdigit() else float(texto)
    if tipo == "booleano":
        return texto == "TRUE"
    if tipo == "data":
        if "T" in texto:
            return datetime.fromisoformat(texto)
        if "day" in texto or texto.count(":") == 2 and len(texto.split(":")[0]) != 2:
            # timedelta é serializado pelo coletor como "[D days, ]H:MM:SS[.ffffff]".
            dias, sep, horas = texto.partition(", ")
            if not sep:
                dias, horas = "0", dias
            h, m, s = horas.split(":")
            return timedelta(days=int(dias.split()[0]), hours=int(h), minutes=int(m), seconds=float(s))
        return time.fromisoformat(texto) if ":" in texto else date.fromisoformat(texto)
    return texto


def _reconstruir(arq, dados):
    orig, cache = Workbook(), Workbook()
    orig.remove(orig.active)
    cache.remove(cache.active)
    abas, numeros, formulas = {}, {}, {}
    for _, grupo in dados.groupby("indice", sort=True):
        meta = grupo.iloc[0]
        nome = meta.nome
        ws, wsv = orig.create_sheet(nome), cache.create_sheet(nome)
        ws.sheet_state = wsv.sheet_state = meta.estado
        abas[nome] = int(meta.aba_id)
        if meta.linhas_dim and meta.colunas_dim:
            ws.cell(int(meta.linhas_dim), int(meta.colunas_dim))
            wsv.cell(int(meta.linhas_dim), int(meta.colunas_dim))
        preenchidas = grupo[grupo.linha.notna()]
        num = preenchidas[preenchidas.tipo_valor.eq("numero")]
        numeros[nome] = num.set_index("celula").valor_armazenado.to_dict()
        tuplas = []
        for c in preenchidas.itertuples(index=False):
            linha, coluna = int(c.linha), int(c.coluna)
            valor = _valor(c.tipo_valor, c.valor_armazenado)
            wsv.cell(linha, coluna).value = valor
            if c.eh_formula and c.formula_ref:
                bruto = (ArrayFormula(c.formula_ref, c.formula) if reader.is_formula(c.formula)
                         else DataTableFormula(c.formula_ref))
            else:
                bruto = c.formula if c.eh_formula else valor
            ws.cell(linha, coluna).value = bruto
            if nome == reader.MAIN_SHEET and c.eh_formula:
                formulas[c.celula] = c.formula
            tuplas.append((linha, coluna, c.celula, c.formula, c.formula_ref,
                           c.valor_armazenado, c.tipo_valor, c.eh_formula, c.eh_erro))
        if len(tuplas) != meta.n_preenchidas or ingest.hash_celulas(tuplas) != meta.hash_conteudo:
            raise ValueError("A contagem ou o hash da aba Bronze diverge")
    if len(abas) != arq.abas or dados.linha.count() != arq.celulas:
        raise ValueError("A contagem do arquivo Bronze diverge")
    med = reader.das_abas(arq.rotulo, orig, cache, arquivo_sha256=arq.sha256)
    return Fonte(arq, med, abas, numeros, formulas)


def ler(spark, cfg, arquivos):
    from pyspark.sql import functions as F

    ids = ",".join(str(a.id) for a in arquivos)
    query = f"""SELECT a.arquivo_id, a.aba_id, a.indice, a.nome, a.estado, a.linhas_dim, a.colunas_dim,
                       a.n_preenchidas, a.hash_conteudo, c.linha, c.coluna, c.celula, c.formula, c.formula_ref,
                       c.valor_armazenado, c.tipo_valor, c.eh_formula, c.eh_erro
                FROM bronze.aba AS a LEFT JOIN bronze.celula_raw AS c
                    ON a.aba_id = c.aba_id AND a.arquivo_id = c.arquivo_id
                WHERE a.arquivo_id IN ({ids})"""
    # Spark particiona e normaliza a projeção; Pandas organiza uma visão por arquivo e aba.
    dados = (spark.read.format("jdbc").options(**cfg.jdbc(), query=query).load()
             .withColumn("hash_conteudo", F.rtrim("hash_conteudo"))
             .withColumn("celula", F.upper("celula")).toPandas())
    dados = dados.astype(object).where(pd.notna(dados), None)
    grupos = dict(tuple(dados.groupby("arquivo_id", sort=False)))
    if set(grupos) != {a.id for a in arquivos}:
        raise ValueError("Arquivos do manifesto ausentes na leitura JDBC")
    return [_reconstruir(a, grupos[a.id]) for a in arquivos]
