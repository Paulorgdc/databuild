"""Publica e reconcilia o modelo dimensional Gold a partir da Silver."""

import argparse
import json
import re
import sys

import db

TABELAS = ("fact_medicao", "fact_valor_item", "fact_ocorrencia", "bridge_cascata_primaria", "fact_pendencia_valor")
CAMPOS = ("valor_contrato", "val_ant", "val_med", "val_ac", "saldo")
PENDENTES = "(N'pendente_dados', N'pendente_contratual')"

ERROS = {
    "51100": "Já existe uma publicação Gold ou Silver em execução.",
    "51101": "A execução Silver selecionada precisa estar concluída.",
    "51102": "As medições Silver precisam pertencer integralmente à execução selecionada.",
    "51103": "A Gold já contém outra publicação.",
    "51104": "A Gold contém registros sem publicação registrada.",
    "51105": "A Silver falhou nas verificações de impactos ou de cascatas.",
    "51106": "A reconciliação Gold/Silver falhou durante a publicação.",
}


class ErroGold(RuntimeError):
    """Erro de operação com mensagem apropriada ao operador."""


def publicar(cfg, execucao_id=None):
    with db.sessao("pipeline", cfg, autocommit=True) as c:
        if execucao_id is None:
            concluida = c.execute(
                "SELECT execucao_id FROM silver.execucao WHERE status = N'concluida' ORDER BY execucao_id"
            ).fetchall()
            if len(concluida) != 1:
                raise ErroGold("Informe a execução Silver concluída com --execucao.")
            execucao_id = concluida[0][0]
        if not re.fullmatch(r"[a-f0-9]{32}", execucao_id):
            raise ErroGold("O identificador da execução deve ter 32 caracteres hexadecimais.")
        try:
            r = c.execute("EXEC gold.usp_publicar_execucao @execucao_id = ?", execucao_id).fetchone()
        except Exception as exc:
            cod = re.search(r"\((5110[0-6])\)", str(exc))
            if cod:
                raise ErroGold(ERROS[cod[1]]) from exc
            raise
    return {"status": r[0], "execucao_id": r[1]}


def status(cfg):
    with db.sessao("pipeline", cfg) as c:
        r = c.execute("SELECT execucao_id, publicado_em FROM gold.publicacao WHERE publicacao_id = 1").fetchone()
        totais = {t: c.execute(f"SELECT COUNT(*) FROM gold.{t}").fetchone()[0] for t in TABELAS}
        classes = dict(c.execute("""
            SELECT d.classe, COUNT(*) FROM gold.fact_ocorrencia f
            JOIN gold.dim_classe d ON d.classe_key = f.classe_key GROUP BY d.classe
        """).fetchall())
        pendencias = dict(c.execute("""
            SELECT situacao, COUNT(*) FROM gold.fact_pendencia_valor GROUP BY situacao
        """).fetchall())
        cascatas, impactos = c.execute("""
            SELECT COALESCE(SUM(cascatas), 0), COALESCE(SUM(impactos_contabilizados), 0)
            FROM gold.vw_qualidade_medicao
        """).fetchone()
    return {"execucao_id": r[0] if r else None, "publicado_em": r[1] if r else None,
            "totais": totais, "classes": classes, "pendencias": pendencias,
            "cascatas": cascatas, "impactos_contabilizados": impactos}


def _diferencas(c, esperado, publicado):
    # EXCEPT trata NULL como igual e compara o valor DECIMAL sem conversão para float.
    return c.execute(f"""
        SELECT SUM(n) FROM (
            SELECT COUNT(*) AS n FROM ({esperado} EXCEPT {publicado}) a
            UNION ALL
            SELECT COUNT(*) AS n FROM ({publicado} EXCEPT {esperado}) b
        ) d
    """).fetchone()[0]


def verificar(cfg):
    with db.sessao("pipeline", cfg) as c:
        origem = c.execute("""
            SELECT p.execucao_id, e.n_arquivos
            FROM gold.publicacao p JOIN silver.execucao e ON e.execucao_id = p.execucao_id
            WHERE e.status = N'concluida'
        """).fetchone()
        if origem is None:
            return {"ok": False, "publicacao": None}

        medicoes = _diferencas(c, """
            SELECT m.medicao_id, m.obra_id, m.numero, m.rotulo, m.arquivo_sha256,
                   COALESCE(CONVERT(INT, CONVERT(CHAR(8), m.periodo_inicio, 112)), 0) AS periodo_inicio_key,
                   COALESCE(CONVERT(INT, CONVERT(CHAR(8), m.periodo_fim, 112)), 0) AS periodo_fim_key,
                   m.contrato_total, m.contrato_controle, m.n_servicos, m.n_categorias,
                   MAX(CASE WHEN v.campo = N'val_med' THEN v.valor_armazenado END) AS valor_medido_mes,
                   MAX(CASE WHEN v.campo = N'val_ac' THEN v.valor_armazenado END) AS valor_acumulado,
                   MAX(CASE WHEN v.campo = N'saldo' THEN v.valor_armazenado END) AS saldo
            FROM silver.medicao m
            LEFT JOIN silver.item_medicao i ON i.medicao_id = m.medicao_id AND i.tipo = N'total'
            LEFT JOIN silver.valor_item v ON v.item_id = i.item_id
            GROUP BY m.medicao_id, m.obra_id, m.numero, m.rotulo, m.arquivo_sha256, m.periodo_inicio,
                     m.periodo_fim, m.contrato_total, m.contrato_controle, m.n_servicos, m.n_categorias
        """, """
            SELECT m.medicao_id_origem, o.obra_id_origem, m.numero, m.rotulo, m.arquivo_sha256,
                   m.periodo_inicio_key, f.data_key, f.contrato_total, f.contrato_controle,
                   f.n_servicos, f.n_categorias, f.valor_medido_mes, f.valor_acumulado, f.saldo
            FROM gold.fact_medicao f JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key
            JOIN gold.dim_obra o ON o.obra_key = f.obra_key
        """)
        valores_gold = ", ".join(f"(N'{campo}', f.{campo}, f.{campo}_recalc)" for campo in CAMPOS)
        campos = ", ".join(f"(N'{campo}')" for campo in CAMPOS)
        itens = _diferencas(c, f"""
            SELECT i.medicao_id, i.codigo, cp.campo, v.valor_armazenado, v.valor_recalculado
            FROM silver.item_medicao i CROSS JOIN (VALUES {campos}) cp(campo)
            LEFT JOIN silver.valor_item v ON v.item_id = i.item_id AND v.campo = cp.campo
            WHERE EXISTS (SELECT 1 FROM silver.valor_item s WHERE s.item_id = i.item_id)
        """, f"""
            SELECT m.medicao_id_origem, d.codigo, cp.campo, cp.armazenado, cp.recalculado
            FROM gold.fact_valor_item f JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key
            JOIN gold.dim_item d ON d.item_key = f.item_key
            CROSS APPLY (VALUES {valores_gold}) cp(campo, armazenado, recalculado)
        """)
        ocorrencias = _diferencas(c, """
            SELECT s.ocorrencia_id, s.medicao_id, i.codigo, s.regra_id, s.versao_regra, s.classe,
                   s.severidade, s.causa, s.aba, s.celula, s.campo, s.valor_original,
                   s.valor_esperado, s.diferenca, s.contabiliza_impacto,
                   CASE WHEN s.contabiliza_impacto = 1 THEN s.diferenca ELSE 0 END AS impacto_contabilizado
            FROM silver.ocorrencia s LEFT JOIN silver.item_medicao i ON i.item_id = s.item_id
        """, """
            SELECT f.ocorrencia_id, m.medicao_id_origem, CASE WHEN f.item_key <> -1 THEN i.codigo END AS codigo,
                   r.regra_id, r.versao, c.classe, sv.severidade, ca.causa, f.aba, f.celula, f.campo,
                   f.valor_original, f.valor_esperado, f.diferenca, f.contabiliza_impacto, f.impacto_contabilizado
            FROM gold.fact_ocorrencia f JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key
            JOIN gold.dim_item i ON i.item_key = f.item_key JOIN gold.dim_regra r ON r.regra_key = f.regra_key
            JOIN gold.dim_classe c ON c.classe_key = f.classe_key
            JOIN gold.dim_severidade sv ON sv.severidade_key = f.severidade_key
            JOIN gold.dim_causa ca ON ca.causa_key = f.causa_key
        """)
        vinculos = _diferencas(c, """
            SELECT cascata_id, primaria_id FROM silver.ocorrencia_causa_raiz
        """, """
            SELECT c.ocorrencia_id AS cascata_id, p.ocorrencia_id AS primaria_id FROM gold.bridge_cascata_primaria v
            JOIN gold.fact_ocorrencia c ON c.ocorrencia_key = v.cascata_key
            JOIN gold.fact_ocorrencia p ON p.ocorrencia_key = v.primaria_key
        """)
        pendencias = _diferencas(c, f"""
            SELECT i.medicao_id, i.codigo, v.campo, v.situacao FROM silver.valor_item v
            JOIN silver.item_medicao i ON i.item_id = v.item_id WHERE v.situacao IN {PENDENTES}
        """, """
            SELECT m.medicao_id_origem, i.codigo, p.campo, p.situacao FROM gold.fact_pendencia_valor p
            JOIN gold.dim_medicao m ON m.medicao_key = p.medicao_key JOIN gold.dim_item i ON i.item_key = p.item_key
        """)
        fora_origem = c.execute("""
            SELECT COUNT(*) FROM silver.medicao WHERE execucao_id IS NULL OR execucao_id <> ?
        """, origem[0]).fetchone()[0]
        n_medicoes = c.execute("SELECT COUNT(*) FROM gold.fact_medicao").fetchone()[0]
    divergencias = {"medicoes": medicoes, "valores": itens, "ocorrencias": ocorrencias,
                    "vinculos": vinculos, "pendencias": pendencias, "medicoes_fora_da_origem": fora_origem}
    return {"ok": n_medicoes == origem[1] and not any(divergencias.values()), "execucao_id": origem[0],
            "medicoes_conferidas": n_medicoes, "divergencias": divergencias}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comando", choices=("run", "status", "verify"))
    parser.add_argument("--execucao", help="identificador da publicação Silver para run")
    parser.add_argument("--json", action="store_true", help="saída compacta para automação")
    a = parser.parse_args(argv)
    if a.execucao and a.comando != "run":
        parser.error("--execucao exige run")
    try:
        db.carregar_env()
        cfg = db.Config.do_ambiente()
        if a.comando == "run":
            r = publicar(cfg, a.execucao)
        elif a.comando == "status":
            r = status(cfg)
        else:
            r = verificar(cfg)
        print(json.dumps(r, ensure_ascii=False, indent=None if a.json else 2, default=str))
        return 0 if r.get("ok", True) else 1
    except ErroGold as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(f"Falha da Gold ({type(exc).__name__}).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
