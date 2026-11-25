"""Recálculo independente e separação entre erros primários e efeitos em cascata.

Para cada linha e campo financeiro (H contrato, M anterior, N medição, L acumulado, O saldo):

* `local`  = valor esperado a partir dos OPERANDOS ARMAZENADOS da própria linha
             (folha: H=trunc(F·G), N=trunc(K·G), L=M+N, O=H−L;
              grupo/total: todo campo = soma armazenada dos filhos diretos).
* `recalc` = valor esperado a partir das ENTRADAS PRIMÁRIAS (F, G, K, M das folhas),
             propagado de baixo para cima sem confiar em nenhum valor armazenado calculado.

Armazenado ≠ local            -> erro PRIMÁRIO (a linha não obedece à própria identidade);
armazenado = local ≠ recalc   -> CASCATA (herdou erro de outra célula; `causa_raiz_id` aponta a origem).

Só as primárias contabilizam impacto financeiro; cascatas repetem o mesmo dinheiro.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN
from operator import add, sub

import catalog
import model as M
import reader as C
from catalog import PARAMETROS
from model import Linha, Medicao, Ocorrencia, ocorrencia_id

CAMPOS_CALC = ("valor_contrato", "val_ant", "val_med", "val_ac", "saldo")
TOL_ITEM = PARAMETROS["tolerancia_item"]
TOL_GRUPO = PARAMETROS["tolerancia_grupo"]


def trunc2(x: float) -> float:
    return math.trunc(x * 100 + 1e-9) / 100


def _st(l: Linha, campo: str) -> float:
    return l.armazenado.get(campo) or 0.0


def _tol(l: Linha) -> float:
    return TOL_ITEM if l.tipo == "folha" else TOL_GRUPO


def _ordem(med: Medicao) -> list[Linha]:
    """Pós-ordem: filhos antes dos pais."""
    out, seen = [], set()

    def visita(cod):
        if cod in seen or cod not in med.linhas:
            return
        seen.add(cod)
        for f in med.linhas[cod].filhos:
            visita(f)
        out.append(med.linhas[cod])
    raiz = M.TOTAL if M.TOTAL in med.linhas else None
    if raiz:
        visita(raiz)
    for cod in med.linhas:
        visita(cod)
    return out


def calcular_valores(med: Medicao, entradas_exatas=None) -> None:
    """Reusa as identidades com Decimal e propaga ausências quando fornecidas entradas exatas."""
    exato = entradas_exatas is not None
    zero = Decimal(0) if exato else 0.0

    def combinar(a, b, operacao):
        if exato and (a is None or b is None):
            return None
        return operacao(a or zero, b or zero)

    def soma(valores):
        if exato and any(v is None for v in valores):
            return None
        return sum((v or zero for v in valores), Decimal(0) if exato else 0)

    def truncar(v):
        return v.quantize(Decimal("0.01"), rounding=ROUND_DOWN) if exato else trunc2(v)

    for l in _ordem(med):
        loc, rec = {}, {}
        origem = entradas_exatas[l.codigo] if exato else l.armazenado
        if l.tipo == "folha":
            F, G, K = (origem.get(k) for k in ("qtd_contrato", "preco", "qtd_med"))
            H = truncar(F * G) if F is not None and G is not None else None
            N = truncar(K * G) if K is not None and G is not None else None
            Mi = origem.get("val_ant")
            loc["valor_contrato"], loc["val_med"] = H, N
            loc["val_ant"] = rec["val_ant"] = Mi
            rec["valor_contrato"] = H if exato or H is not None else origem.get("valor_contrato")
            rec["val_med"] = N if exato or N is not None else origem.get("val_med")
            loc["val_ac"] = combinar(Mi, origem.get("val_med"), add)
            loc["saldo"] = combinar(origem.get("valor_contrato"), origem.get("val_ac"), sub)
            rec["val_ac"] = combinar(Mi, rec["val_med"], add)
            rec["saldo"] = combinar(rec["valor_contrato"], rec["val_ac"], sub)
        else:
            ch = [med.linhas[c] for c in l.filhos if c in med.linhas]
            for campo in CAMPOS_CALC:      # grupo/total: todo campo é a soma dos filhos diretos
                armazenados = [(entradas_exatas[c.codigo] if exato else c.armazenado).get(campo) for c in ch]
                loc[campo] = soma(armazenados)
                rec[campo] = soma([c.recalc.get(campo) for c in ch])
        l.local, l.recalc = loc, rec


def _dependencias(med: Medicao, l: Linha, campo: str) -> list[tuple[str, str]]:
    if l.tipo == "folha":
        if campo == "val_ac":
            return [(l.codigo, "val_ant"), (l.codigo, "val_med")]
        if campo == "saldo":
            return [(l.codigo, "valor_contrato"), (l.codigo, "val_ac")]
        return []
    return [(c, campo) for c in l.filhos if c in med.linhas]


def _regra_primaria(l: Linha, campo: str) -> str:
    if l.tipo == "folha":
        return "valor_contrato_incoerente" if campo == "valor_contrato" else "identidade_quebrada"
    return "divergência_financeira_agregada"


_NOMES = {"valor_contrato": "contrato", "val_ant": "acumulado anterior", "val_med": "medição atual",
          "val_ac": "acumulado atual", "saldo": "saldo"}


def _celula(med: Medicao, l: Linha, campo: str) -> str:
    return f"{med.colunas[campo]}{l.row}"


def _descricao(l: Linha, campo: str, regra: str) -> str:
    n = _NOMES[campo]
    if regra == "valor_contrato_incoerente":
        return "Valor de contrato difere de quantidade × preço"
    if l.tipo == "folha":
        return {"val_med": "Medição atual ≠ quantidade medida × preço",
                "val_ac": "Acumulado atual ≠ anterior + medição",
                "saldo": "Saldo ≠ contrato − acumulado atual"}.get(campo, f"{n} fora da identidade")
    if regra == "divergência_financeira_agregada":
        quem = "total da obra" if l.tipo == "total" else "grupo"
        return f"Valor de {n} do {quem} difere da soma dos filhos diretos"
    return f"Valor de {n} do grupo difere da soma dos filhos diretos"


def detectar(med: Medicao) -> list[Ocorrencia]:
    """Ocorrências aritméticas: primárias, cascatas e regras de contrato/exceção legítima."""
    calcular_valores(med)
    aba = C.MAIN_SHEET
    out: list[Ocorrencia] = []
    primarias: dict[tuple[str, str], Ocorrencia] = {}
    cascatas: dict[tuple[str, str], Ocorrencia] = {}
    ordem = _ordem(med)
    contrato = med.contrato_total()

    for l in ordem:                                   # 1) primárias
        for campo in CAMPOS_CALC:
            if campo not in med.colunas:
                continue
            esperado = l.local.get(campo)
            if esperado is None:
                continue
            dif = _st(l, campo) - esperado
            if abs(dif) <= _tol(l):
                continue
            regra = _regra_primaria(l, campo)
            r = catalog.get(regra)
            cel = _celula(med, l, campo)
            o = Ocorrencia(
                medicao=med.id, obra_id=med.obra_id, aba=aba, celula=cel, codigo_item=l.codigo, regra=regra,
                classe=r.classe, severidade=r.severidade, descricao=_descricao(l, campo, regra),
                evidencia=f"armazenado={_st(l, campo):.2f} esperado(local)={esperado:.2f}", campo=campo,
                valor_original=_st(l, campo), valor_esperado=esperado, diferenca=dif,
                sugestao=r.sugestao.format(celula=cel, esperado=f"{esperado:.2f}"), causa="primaria",
                id=ocorrencia_id(med.id, regra, aba, cel, campo), versao_regra=r.versao)
            primarias[(l.codigo, campo)] = o
            out.append(o)

    memo: dict[tuple[str, str], list[str]] = {}

    def raizes(cod: str, campo: str) -> list[str]:
        k = (cod, campo)
        if k in primarias:
            return [primarias[k].id]
        if k in memo:
            return memo[k]
        memo[k] = []
        ids: list[str] = []
        for d in _dependencias(med, med.linhas[cod], campo):
            ids += raizes(*d)
        memo[k] = sorted(set(ids))
        return memo[k]

    r_cas = catalog.get("valor_armazenado_diverge_do_recalculo")
    for l in ordem:                                   # 2) cascatas
        for campo in CAMPOS_CALC:
            if campo not in med.colunas or (l.codigo, campo) in primarias:
                continue
            rec = l.recalc.get(campo)
            if rec is None:
                continue
            dif = _st(l, campo) - rec
            if abs(dif) <= _tol(l):
                continue
            raiz = raizes(l.codigo, campo)
            if not raiz:
                continue                              # sem origem identificável: não afirmar
            cel = _celula(med, l, campo)
            o = Ocorrencia(
                medicao=med.id, obra_id=med.obra_id, aba=aba, celula=cel, codigo_item=l.codigo,
                regra=r_cas.id, classe=r_cas.classe, severidade=r_cas.severidade,
                descricao=f"Valor de {_NOMES[campo]} difere do recálculo a partir das entradas (efeito de erro a montante)",
                evidencia=f"armazenado={_st(l, campo):.2f} recálculo={rec:.2f}", campo=campo,
                valor_original=_st(l, campo), valor_esperado=rec, diferenca=dif,
                sugestao=r_cas.sugestao.format(celula=cel), causa="cascata", causa_raiz_id=",".join(raiz),
                id=ocorrencia_id(med.id, r_cas.id, aba, cel, campo), versao_regra=r_cas.versao)
            cascatas[(l.codigo, campo)] = o
            out.append(o)

    tot = med.total                                   # 3) conservação: total x soma das folhas
    if tot:
        r_t = catalog.get("total_vs_soma_das_folhas")
        for campo in ("valor_contrato", "val_ant", "val_med"):
            if campo not in med.colunas:
                continue
            soma = sum(_st(f, campo) for f in med.folhas())
            dif = _st(tot, campo) - soma
            if abs(dif) <= TOL_GRUPO:
                continue
            cel = _celula(med, tot, campo)
            raiz = sorted({o.id for (cod, cp), o in primarias.items() if cp == campo})
            out.append(Ocorrencia(
                medicao=med.id, obra_id=med.obra_id, aba=aba, celula=cel, codigo_item=M.TOTAL, regra=r_t.id,
                classe=r_t.classe, severidade=r_t.severidade,
                descricao=f"Total de {_NOMES[campo]} da obra difere da soma de todos os serviços",
                evidencia=f"total={_st(tot, campo):.2f} soma das folhas={soma:.2f}", campo=campo,
                valor_original=_st(tot, campo), valor_esperado=soma, diferenca=dif,
                sugestao=r_t.sugestao.format(celula=cel, esperado=f"{soma:.2f}"), causa="cascata",
                causa_raiz_id=",".join(raiz), id=ocorrencia_id(med.id, r_t.id, aba, cel, campo),
                versao_regra=r_t.versao))

    out.extend(_contrato_e_excecoes(med, aba))        # 4) contrato e exceções legítimas
    return out


def _contrato_e_excecoes(med: Medicao, aba: str) -> list[Ocorrencia]:
    out: list[Ocorrencia] = []
    H, L, O = (med.colunas.get(k) for k in ("valor_contrato", "val_ac", "saldo"))
    if not (H and L and O):
        return out

    def mk(regra, l, campo, desc, evid, dif, esperado=None):
        r = catalog.get(regra)
        cel = f"{med.colunas[campo]}{l.row}"
        return Ocorrencia(
            medicao=med.id, obra_id=med.obra_id, aba=aba, celula=cel, codigo_item=l.codigo, regra=regra,
            classe=r.classe, severidade=r.severidade, descricao=desc, evidencia=evid, campo=campo,
            valor_original=_st(l, campo), valor_esperado=esperado, diferenca=dif,
            sugestao=r.sugestao.format(celula=cel, esperado=("" if esperado is None else f"{esperado:.2f}")),
            id=ocorrencia_id(med.id, regra, aba, cel, campo), versao_regra=r.versao)

    for l in med.linhas.values():
        if l.tipo not in ("folha", "total"):
            continue
        h, ac, sd = _st(l, "valor_contrato"), _st(l, "val_ac"), _st(l, "saldo")
        if ac - h > TOL_ITEM:
            quem = "da obra" if l.tipo == "total" else "do item"
            out.append(mk("medido_acima_do_contrato", l, "val_ac", f"Acumulado medido {quem} maior que o contratado",
                          f"{ac:.2f} > {h:.2f}", ac - h, h))
        elif l.tipo == "folha" and h and ac and 0.005 <= abs(sd) <= TOL_ITEM:
            out.append(mk("resíduo_de_truncamento", l, "saldo", "Saldo de centavos por TRUNC em item 100% medido",
                          f"saldo {sd:.2f}", None))
    return out
