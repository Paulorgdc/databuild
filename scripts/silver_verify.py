"""Conferências da Silver e comparação opcional com artefatos locais da Etapa 02."""

import csv
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

import db
import reader

CHECAGENS = {
    "medicoes_sem_origem": """
        SELECT COUNT(*) FROM silver.medicao m LEFT JOIN bronze.arquivo a ON a.arquivo_id=m.arquivo_origem_id
        WHERE a.arquivo_id IS NULL OR m.arquivo_sha256<>a.sha256 OR m.carga_id<>a.primeira_carga_id
    """,
    "medicoes_sem_metadados": """
        SELECT COUNT(*) FROM silver.medicao m
        WHERE NOT EXISTS (SELECT 1 FROM silver.medicao_origem o WHERE m.medicao_id=o.medicao_id AND o.campo=N'contrato_total')
        OR EXISTS (SELECT 1 FROM silver.medicao_origem o JOIN bronze.aba a ON a.aba_id=o.aba_origem_id
                   WHERE o.medicao_id=m.medicao_id AND a.arquivo_id<>m.arquivo_origem_id)
    """,
    "itens_sem_origem": """
        SELECT COUNT(*) FROM silver.item_medicao i JOIN silver.medicao m ON m.medicao_id=i.medicao_id
        LEFT JOIN bronze.aba a ON a.aba_id=i.aba_origem_id
        LEFT JOIN bronze.celula_raw c ON c.aba_id=a.aba_id AND c.linha=i.linha_origem
                  AND c.celula=i.celula_origem AND c.arquivo_id=m.arquivo_origem_id
        WHERE a.aba_id IS NULL OR m.arquivo_origem_id<>a.arquivo_id OR c.celula_id IS NULL
    """,
    "contagens_medicao_divergentes": """
        SELECT COUNT(*) FROM silver.medicao m
        WHERE m.n_categorias<>(SELECT COUNT(*) FROM silver.item_medicao i WHERE i.medicao_id=m.medicao_id
                               AND i.tipo IN (N'lote', N'secao'))
           OR m.n_servicos<>(SELECT COUNT(*) FROM silver.item_medicao i WHERE i.medicao_id=m.medicao_id
                             AND i.tipo=N'folha')
    """,
    "ocorrencias_sem_origem": """
        SELECT COUNT(*) FROM silver.ocorrencia o JOIN silver.medicao m ON m.medicao_id=o.medicao_id
        WHERE o.carga_id<>m.carga_id
           OR NOT EXISTS (SELECT 1 FROM silver.ocorrencia_origem p WHERE p.ocorrencia_id=o.ocorrencia_id)
           OR EXISTS (SELECT 1 FROM silver.ocorrencia_origem p JOIN bronze.aba a ON a.aba_id=p.aba_origem_id
                      WHERE p.ocorrencia_id=o.ocorrencia_id AND p.papel<>N'anterior'
                        AND a.arquivo_id<>m.arquivo_origem_id)
    """,
    "cascatas_sem_causa": "SELECT COUNT(*) FROM silver.vw_qualidade_cascata_sem_causa",
    "impactos_duplicados": "SELECT COUNT(*) FROM silver.vw_qualidade_impacto_duplicado",
    "impactos_indevidos": """
        SELECT COUNT(*) FROM silver.ocorrencia
        WHERE contabiliza_impacto=1 AND (causa<>N'primaria' OR classe<>N'comprovado' OR diferenca IS NULL)
    """,
    "escala_decimal_incorreta": """
        SELECT COUNT(*) FROM (VALUES
           (OBJECT_ID(N'silver.valor_item'), N'valor_armazenado', 4),
           (OBJECT_ID(N'silver.valor_item'), N'valor_recalculado', 4),
           (OBJECT_ID(N'silver.medicao'), N'contrato_total', 2),
           (OBJECT_ID(N'silver.medicao'), N'contrato_controle', 2),
           (OBJECT_ID(N'silver.ocorrencia'), N'diferenca', 2)
        ) v(objeto, coluna, escala)
        LEFT JOIN sys.columns c ON c.object_id=v.objeto AND c.name=v.coluna
        WHERE c.object_id IS NULL OR TYPE_NAME(c.user_type_id)<>N'decimal' OR c.precision<>18 OR c.scale<>v.escala
    """,
}


def _csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _comparar(reais):
    base = _csv(reader.OUT / "etapa02" / "ocorrencias.csv")
    marcas = [a for a in _csv(reader.OUT / "red_marks.csv")
              if not a["tipo"].startswith("preenchimento amarelo")]
    orig = {o["ocorrencia_id"]: o for o in base}
    atuais = {o[0]: o[1:] for o in reais}
    ambos = orig.keys() & atuais.keys()
    campos = ("medicao_id", "regra", "versao_regra", "aba", "celula", "classe", "severidade", "causa",
              "contabiliza_impacto", "valor_original", "valor_esperado", "diferenca")
    celulas = {(o[0], o[3], o[4]) for o in atuais.values()}

    def valor(o, k):
        if k == "contabiliza_impacto":
            return o[k] == "True"
        if k in ("valor_original", "valor_esperado", "diferenca"):
            return Decimal(o[k]) if o[k] else None
        return o[k]

    diferencas = Counter(k for i in ambos for k, x in zip(campos, atuais[i]) if x != valor(orig[i], k))
    return {"referencia": len(base), "pareadas_por_id": len(ambos), "somente_etapa02": len(orig.keys() - ambos),
            "somente_silver": len(atuais.keys() - ambos), "campos_divergentes": dict(diferencas),
            "marcas_vermelhas": len(marcas), "marcas_cobertas": sum(
                (m["arquivo"], m["aba"], m["celula"]) in celulas for m in marcas)}


def verificar(cfg, comparar=False):
    with db.sessao("pipeline", cfg) as c:
        erros = {k: c.execute(sql).fetchone()[0] for k, sql in CHECAGENS.items()}
        valores = c.execute("""
            SELECT v.valor_armazenado, b.tipo_valor, b.valor_armazenado, f.formula_texto, b.formula,
                   v.celula_origem, v.situacao
            FROM silver.valor_item v JOIN silver.item_medicao i ON i.item_id=v.item_id
            LEFT JOIN bronze.celula_raw b ON b.aba_id=i.aba_origem_id AND b.linha=i.linha_origem
                AND b.celula=v.celula_origem
            LEFT JOIN silver.formula_item f ON f.item_id=v.item_id AND f.campo=v.campo
        """).fetchall()
        originais = 0
        for armazenado, tipo, raw, formula, raw_formula, celula, situacao in valores:
            original = Decimal(raw).quantize(Decimal(".0001"), rounding=ROUND_HALF_UP) if tipo == "numero" else None
            originais += (armazenado == original and formula == raw_formula and bool(celula and situacao))
        erros["valores_ou_formulas_divergentes"] = len(valores) - originais
        ocorrencias = c.execute("""
            SELECT o.ocorrencia_id, m.rotulo, o.regra_id, o.versao_regra, o.aba, o.celula,
                   o.classe, o.severidade, o.causa, o.contabiliza_impacto, o.valor_original,
                   o.valor_esperado, o.diferenca
            FROM silver.ocorrencia o JOIN silver.medicao m ON m.medicao_id=o.medicao_id
        """).fetchall()
        arquivos, medicoes = c.execute("""
            SELECT (SELECT COUNT(*) FROM bronze.arquivo), (SELECT COUNT(*) FROM silver.medicao)
        """).fetchone()
    r = {"ok": bool(valores) and arquivos == medicoes and not any(erros.values()), "arquivos_bronze": arquivos,
         "medicoes_silver": medicoes, "valores_conferidos": originais, "ocorrencias": len(ocorrencias),
         "primarias_com_impacto": sum(o[9] for o in ocorrencias), "divergencias": erros}
    if comparar:
        r["etapa02"] = _comparar(ocorrencias)
        r["ok"] = r["ok"] and r["etapa02"]["marcas_cobertas"] == r["etapa02"]["marcas_vermelhas"]
    return r
