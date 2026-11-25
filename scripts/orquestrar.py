"""Descoberta e ingestão incremental do Airflow, com proteção do manifesto publicado."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory

import build
import db
import ingest
import reader


class ErroOrquestracao(RuntimeError):
    """Falha com mensagem segura para o log do Airflow."""


class RevisaoPendente(ErroOrquestracao):
    """O operador deve selecionar a sequência em um banco candidato."""


def numero(rotulo):
    match = re.fullmatch(r"MED (\d{1,12})", rotulo or "")
    n = int(match[1]) if match else 0
    if not 0 < n <= 32767:
        raise RevisaoPendente("Há um número de medição fora do padrão aceito.")
    return n


def planejar(arquivos, registrados, selada):
    """Recebe (caminho, SHA-256) e (rótulo, SHA-256), devolve caminhos inéditos e contagens."""
    por_numero = defaultdict(set)
    por_hash = {}
    for rotulo, sha in registrados:
        n = numero(rotulo)
        por_numero[n].add(sha)
        por_hash[sha] = n
    if any(len(hashes) > 1 for hashes in por_numero.values()):
        raise RevisaoPendente("A Bronze contém revisões concorrentes para a mesma medição.")

    novas = []
    duplicadas = 0
    for caminho, sha in arquivos:
        n = numero(reader.label_of(caminho))
        if sha in por_hash:
            if por_hash[sha] != n:
                raise RevisaoPendente("O mesmo conteúdo foi identificado com números de medição diferentes.")
            duplicadas += 1
        elif n in por_numero:
            raise RevisaoPendente("Foi detectada uma revisão. Selecione a versão aprovada em um banco candidato.")
        else:
            novas.append((caminho, sha))
            por_numero[n].add(sha)
            por_hash[sha] = n
    if selada and novas:
        raise RevisaoPendente("Há novas medições. Prepare a sequência completa em um banco candidato.")
    return {
        "novas": len(novas),
        "duplicadas": duplicadas,
        "registradas": len(registrados),
    }, novas


def descobrir(conn, entrada):
    if not entrada.is_dir():
        raise ErroOrquestracao("A pasta de entrada não está disponível.")
    arquivos = reader.med_files(entrada)
    if any(p.is_symlink() or not p.is_file() for p in arquivos):
        raise RevisaoPendente("A entrada contém um arquivo de medição inválido.")
    registros = conn.execute("SELECT medicao_id, sha256 FROM bronze.arquivo").fetchall()
    selada = conn.execute("""
        SELECT CASE WHEN EXISTS (SELECT 1 FROM silver.medicao)
                      OR EXISTS (SELECT 1 FROM gold.publicacao) THEN 1 ELSE 0 END
    """).fetchone()[0]
    return planejar([(p, build.sha256(p)) for p in arquivos], registros, selada)


def carregar_bronze(cfg, entrada):
    with db.sessao("pipeline", cfg) as conn:
        if not db.travar_sequencia(conn, "Exclusive", "Session", 0):
            raise ErroOrquestracao("Outra operação está processando a sequência de medições.")
        # A conexão que mantinha a trava encerrou sem finalizar este lote.
        # Outros canais de ingestão e os recibos já confirmados ficam intactos.
        conn.execute("""
            UPDATE bronze.carga SET status = N'falhou', finalizada_em = SYSUTCDATETIME(),
                observacao = N'Processo interrompido antes de finalizar o lote'
            WHERE status = N'iniciada' AND origem LIKE N'airflow:%'
        """)
        conn.commit()
        contagens, novas = descobrir(conn, entrada)
        if novas:
            # Processa cópias privadas validadas pelo hash, mesmo se a entrada mudar durante a leitura.
            with TemporaryDirectory(prefix="databuild-") as tmp:
                copias = []
                for caminho, sha in novas:
                    copia = Path(tmp) / caminho.name
                    shutil.copy2(caminho, copia)
                    if build.sha256(copia) != sha:
                        raise ErroOrquestracao("A entrada mudou durante a ingestão. Tente novamente.")
                    copias.append(copia)
                destino = ingest.Ingestor(conn, log=lambda *_: None, sequencia_bloqueada=True)
                lote = ingest.gravar_lote(destino, copias, origem="airflow")
                if not lote["ok"]:
                    raise ErroOrquestracao(f"Lote Bronze {lote['carga_id']} falhou. Consulte o log local do SQL Server.")
                return {**contagens, "carga_id": lote["carga_id"], "carregadas": lote["carregado"]}
        return {**contagens, "carga_id": None, "carregadas": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comando", choices=("discover", "bronze"))
    args = parser.parse_args(argv)
    try:
        db.carregar_env()
        cfg = db.Config.do_ambiente()
        entrada = Path(os.environ.get("DATABUILD_INPUT_DIR", str(reader.DATA)))
        if args.comando == "discover":
            with db.sessao("pipeline", cfg) as conn:
                resumo, _ = descobrir(conn, entrada)
        else:
            resumo = carregar_bronze(cfg, entrada)
        print(json.dumps(resumo))
        return 0
    except RevisaoPendente as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ErroOrquestracao as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Falha de orquestração ({type(exc).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
