"""Regras de consistência entre medições consecutivas (operam só sobre `Medicao`).

Identificam o serviço pelo código hierárquico do item. Mudanças de escopo (serviço
removido/incluído, cadastro alterado) dependem de validação contratual: o motor não
afirma erro, apenas aponta e preserva a evidência.
"""
from __future__ import annotations

import catalog
import reader as C
from catalog import PARAMETROS
from model import Linha, Medicao, Ocorrencia, ocorrencia_id

TOL = PARAMETROS["tolerancia_item"]
CAMPOS_CADASTRO = (("qtd_contrato", "quantidade"), ("preco", "preço"), ("valor_contrato", "valor de contrato"))


def _oc(med: Medicao, regra: str, l: Linha | None, campo: str, desc: str, evid: str, codigo: str,
        original=None, esperado=None, dif=None) -> Ocorrencia:
    r = catalog.get(regra)
    col = med.colunas.get(campo, "")
    cel = f"{col}{l.row}" if l is not None and col else ""
    return Ocorrencia(
        medicao=med.id, obra_id=med.obra_id, aba=C.MAIN_SHEET, celula=cel, codigo_item=codigo, regra=regra,
        classe=r.classe, severidade=r.severidade, descricao=desc, evidencia=evid, campo=campo,
        valor_original=original, valor_esperado=esperado, diferenca=dif,
        sugestao=r.sugestao.format(celula=cel, esperado="" if esperado is None else f"{esperado:.2f}"),
        id=ocorrencia_id(med.id, regra, C.MAIN_SHEET, cel or codigo, campo + ":" + codigo), versao_regra=r.versao)


def _st(l: Linha, campo: str) -> float:
    return l.armazenado.get(campo) or 0.0


def comparar(ant: Medicao, atu: Medicao) -> list[Ocorrencia]:
    """Ocorrências atribuídas à medição ATUAL, comparada à anterior."""
    fa = {l.codigo: l for l in ant.folhas()}
    fb = {l.codigo: l for l in atu.folhas()}
    out: list[Ocorrencia] = []
    for cod in sorted(set(fa) - set(fb), key=lambda c: fa[c].row):
        x = fa[cod]
        pai = atu.linhas.get(x.pai) if x.pai else None
        mant = None
        if pai is not None and pai.filhos is not None:
            mant = _st(pai, "val_ant") - sum(_st(atu.linhas[c], "val_ant") for c in pai.filhos if c in atu.linhas)
        ev = f"{ant.id}: {x.descricao[:40]} | contrato={_st(x, 'valor_contrato'):.2f} acumulado={_st(x, 'val_ac'):.2f}"
        if mant is not None and abs(mant) > TOL:
            ev += f" | AC ANTERIOR do grupo {pai.codigo} excede em {mant:.2f} a soma dos serviços restantes"
        out.append(_oc(atu, "item_removido_entre_medicoes", pai, "val_ant", f"Serviço {cod} existia em {ant.id} e não está em {atu.id}",
                       ev, cod, original=_st(x, "val_ac"), dif=mant))
    for cod in sorted(set(fb) - set(fa), key=lambda c: fb[c].row):
        y = fb[cod]
        out.append(_oc(atu, "item_incluido_entre_medicoes", y, "valor_contrato", f"Serviço {cod} não existia em {ant.id}",
                       f"{y.descricao[:40]} | contrato={_st(y, 'valor_contrato'):.2f}", cod,
                       original=_st(y, "valor_contrato")))
    for cod in sorted(set(fa) & set(fb), key=lambda c: fb[c].row):
        x, y = fa[cod], fb[cod]
        mud = []
        for campo, nome in CAMPOS_CADASTRO:
            a, b = x.armazenado.get(campo), y.armazenado.get(campo)
            if a is not None and b is not None and abs(a - b) > 1e-6:
                mud.append((campo, f"{nome}: {a:g} → {b:g}"))
        if x.unidade != y.unidade:
            mud.append(("qtd_contrato", f"unidade: {x.unidade!r} → {y.unidade!r}"))
        if x.servico != y.servico:
            mud.append(("qtd_contrato", f"código de serviço: {x.servico!r} → {y.servico!r}"))
        if mud:
            out.append(_oc(atu, "cadastro_alterado_entre_medicoes", y, mud[0][0],
                           f"Cadastro do serviço {cod} mudou desde {ant.id}", "; ".join(t for _, t in mud), cod))
        a_l, b_m = _st(x, "val_ac"), _st(y, "val_ant")
        if abs(a_l - b_m) > TOL:
            out.append(_oc(atu, "acumulado_anterior_descontinuo", y, "val_ant",
                           f"AC ANTERIOR de {cod} difere do AC ATUAL de {ant.id}",
                           f"{ant.id} acumulado={a_l:.2f} | {atu.id} anterior={b_m:.2f}", cod,
                           original=b_m, esperado=a_l, dif=b_m - a_l))
    return out


def comparar_todas(meds: list[Medicao]) -> list[Ocorrencia]:
    meds = sorted(meds, key=lambda m: m.numero)
    out: list[Ocorrencia] = []
    for a, b in zip(meds, meds[1:]):
        out.extend(comparar(a, b))
    return out
