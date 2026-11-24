"""Exportação estruturada (CSV UTF-8 com BOM e JSON) das tabelas de `model.TABELAS`."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import catalog
import model as M
from engine import Resultado

_NIVEL = {"total": 0, "lote": 1, "secao": 2, "folha": 3}


def tabelas(res: Resultado, nome_obra: str = "Construtora Horizontes") -> dict[str, list[dict]]:
    obra_id = res.medicoes[0].obra_id if res.medicoes else "OBRA-1"
    t: dict[str, list[dict]] = {k: [] for k in M.TABELAS}
    t["obras"].append({"obra_id": obra_id, "nome": nome_obra})
    for med in sorted(res.medicoes, key=lambda x: x.numero):
        t["medicoes"].append({
            "medicao_id": med.id, "obra_id": med.obra_id, "numero": med.numero,
            "periodo_inicio": med.periodo_inicio, "periodo_fim": med.periodo_fim,
            "contrato_total": round(med.contrato_total(), 2),
            "contrato_controle": None if med.contrato_controle is None else round(med.contrato_controle, 2),
            "arquivo_sha256": med.arquivo_sha256, "n_servicos": len(med.folhas()),
            "n_categorias": sum(1 for l in med.linhas.values() if l.tipo in ("secao", "lote"))})
        for l in sorted(med.linhas.values(), key=lambda x: x.row):
            if l.tipo == "folha":
                t["servicos"].append({"medicao_id": med.id, "codigo": l.codigo, "pai": l.pai, "descricao": l.descricao,
                                      "unidade": l.unidade, "codigo_servico": l.servico, "fonte": l.fonte})
            else:
                t["categorias"].append({"medicao_id": med.id, "codigo": l.codigo, "tipo": l.tipo, "pai": l.pai,
                                        "nivel": _NIVEL[l.tipo], "descricao": l.descricao})
            for campo in M.CAMPOS_VALOR:
                if campo in l.armazenado:
                    rc = l.recalc.get(campo)
                    t["valores"].append({"medicao_id": med.id, "codigo": l.codigo, "tipo": l.tipo, "campo": campo,
                                         "valor_armazenado": l.armazenado.get(campo),
                                         "valor_recalculado": None if rc is None else round(rc, 4),
                                         "formula": l.formula.get(campo)})
    for o in res.ocorrencias:
        t["ocorrencias"].append({
            "ocorrencia_id": o.id, "obra_id": o.obra_id, "medicao_id": o.medicao, "regra": o.regra,
            "versao_regra": o.versao_regra, "classe": o.classe, "severidade": o.severidade, "causa": o.causa,
            "causa_raiz_id": o.causa_raiz_id, "aba": o.aba, "celula": o.celula, "codigo_item": o.codigo_item,
            "campo": o.campo, "descricao": o.descricao, "evidencia": o.evidencia,
            "valor_original": None if o.valor_original is None else round(o.valor_original, 2),
            "valor_esperado": None if o.valor_esperado is None else round(o.valor_esperado, 2),
            "diferenca": None if o.diferenca is None else round(o.diferenca, 2),
            "contabiliza_impacto": o.contabiliza_impacto, "sugestao": o.sugestao})
    return t


def resumo(res: Resultado) -> dict:
    import collections
    oc = res.ocorrencias
    por = lambda f: dict(sorted(collections.Counter(f(o) for o in oc).items()))
    return {"catalogo_versao": catalog.CATALOG_VERSION, "medicoes": [m.id for m in res.medicoes],
            "ocorrencias": len(oc), "por_classe": por(lambda o: o.classe), "por_causa": por(lambda o: o.causa),
            "por_severidade": por(lambda o: o.severidade), "por_regra": por(lambda o: o.regra),
            "por_medicao": por(lambda o: o.medicao),
            "impacto_contabilizado_primarias": round(sum(o.diferenca for o in oc if o.contabiliza_impacto), 2),
            "primarias_com_impacto": sum(1 for o in oc if o.contabiliza_impacto)}


def exportar(res: Resultado, pasta: Path, formatos=("csv", "json")) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    t = tabelas(res)
    escritos: list[Path] = []
    if "csv" in formatos:
        for nome, linhas in t.items():
            p = pasta / f"{nome}.csv"
            with open(p, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(M.TABELAS[nome]))
                w.writeheader()
                w.writerows(linhas)
            escritos.append(p)
    if "json" in formatos:
        p = pasta / "auditoria.json"
        p.write_text(json.dumps({"resumo": resumo(res), "tabelas": t}, ensure_ascii=False, indent=1, default=str),
                     encoding="utf-8")
        escritos.append(p)
    for nome, conteudo in (("schema.json", M.TABELAS), ("catalogo.json", catalog.as_json()),
                           ("resumo.json", resumo(res))):
        p = pasta / nome
        p.write_text(json.dumps(conteudo, ensure_ascii=False, indent=1), encoding="utf-8")
        escritos.append(p)
    return escritos
