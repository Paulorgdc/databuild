"""Constrói o modelo padronizado (`model.Medicao`) a partir de uma planilha lida por `reader`.

Só aqui existe conhecimento de planilha (colunas, linhas, aba). Daqui em diante o
motor trabalha apenas com `Medicao`/`Linha`.
"""
from __future__ import annotations

import hashlib
import re

import model as M
import reader as C
import rules as D


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _txt(m: C.Med, col: str | None, row: int) -> str:
    if not col:
        return ""
    v = m.f(f"{col}{row}")
    return "" if v is None else str(v).strip()


def _iso(v):
    return v.strftime("%Y-%m-%d") if hasattr(v, "year") else None


def origens_controle(m: C.Med, numero: int) -> dict[str, str]:
    """Localiza as células que fornecem as datas e o contrato do controle."""
    lay = C.find_fin_layout(m.wb)
    if not lay:
        return {}
    ws = m.wb[C.FIN_SHEET]
    origens = {}
    for r in lay["rows"]:
        mt = re.match(r"\s*(\d+)", str(ws[f"{lay['label']}{r}"].value))
        if mt and int(mt.group(1)) == numero:
            for campo, chave in (("periodo_inicio", "ini"), ("periodo_fim", "fim")):
                if lay[chave]:
                    origens[campo] = f"{lay[chave]}{r}"
    if lay["contrato"]:
        origens["contrato_controle"] = lay["contrato"]
    return origens


def periodo_e_contrato(m: C.Med, numero: int):
    origens = origens_controle(m, numero)
    datas = [_iso(m.wb[C.FIN_SHEET][origens[k]].value) if k in origens else None
             for k in ("periodo_inicio", "periodo_fim")]
    contrato = origens.get("contrato_controle")
    return *datas, C.num(m.wv[C.FIN_SHEET][contrato].value) if contrato else None


def construir(m: C.Med, obra_id: str = "OBRA-1") -> M.Medicao:
    kind, leaves_of, sections_of, lotes = D.classify_rows(m)
    cols = dict(m.cols)
    campos = {k: cols[k] for k in M.CAMPOS if k in cols}
    num_m = re.search(r"(\d+)", m.label)
    numero = int(num_m.group(1)) if num_m else 0
    linhas: dict[str, M.Linha] = {}

    def nova(row: int, tipo: str, codigo: str, pai: str | None) -> M.Linha:
        ln = M.Linha(codigo=codigo, tipo=tipo, row=row, pai=pai,
                     descricao=_txt(m, cols.get("desc"), row)[:120], unidade=_txt(m, cols.get("unid"), row),
                     servico=_txt(m, cols.get("servico"), row), fonte=_txt(m, cols.get("fonte"), row))
        for campo, col in campos.items():
            raw = m.f(f"{col}{row}")
            ln.armazenado[campo] = C.num(m.v(f"{col}{row}"))
            ln.formula[campo] = raw if C.is_formula(raw) else None
        linhas[codigo] = ln
        return ln

    cur_lote = cur_sec = None
    ordered = sorted(kind)
    for row in ordered:
        k = kind[row]
        codigo = D.code_at(m, row)
        if k == "lote":
            pai = M.TOTAL
            cur_lote, cur_sec = codigo, None
        elif k == "secao":
            pai = cur_lote or M.TOTAL
            cur_sec = codigo
        else:
            pai = cur_sec or cur_lote or M.TOTAL
        nova(row, {"folha": "folha", "secao": "secao", "lote": "lote"}[k], codigo, pai)
    if m.total_row:
        tot = nova(m.total_row, "total", M.TOTAL, None)
    for ln in linhas.values():
        if ln.pai and ln.pai in linhas:
            linhas[ln.pai].filhos.append(ln.codigo)
    ini, fim, contrato = periodo_e_contrato(m, numero)
    return M.Medicao(id=m.label, obra_id=obra_id, numero=numero, linhas=linhas, colunas=campos,
                     periodo_inicio=ini, periodo_fim=fim, contrato_controle=contrato,
                     arquivo_sha256=m.arquivo_sha256 or sha256(m.path))
