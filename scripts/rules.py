"""Detectores estruturais, de fórmula, de referência e de controle (Etapa 02).

Cada detector devolve `model.Ocorrencia`; classe, versão e texto de sugestão vêm do
`catalog`. A aritmética (recálculo independente, primárias x cascatas) fica em
`recalc.py` e as regras entre medições em `compare.py`.

Princípios:
* sem regra por célula: tudo é derivado da estrutura (grupo x folha),
  das fórmulas dominantes por coluna e de identidades aritméticas;
* as marcações em vermelho NÃO são usadas para detectar, só para avaliar cobertura.
"""
from __future__ import annotations

import collections
import re

import catalog
import reader as C
from catalog import PARAMETROS
from model import Ocorrencia, ocorrencia_id

TOL = PARAMETROS["tolerancia_item"]          # resíduo de centavos (TRUNC)
TOL_GROUP = PARAMETROS["tolerancia_grupo"]   # somatórios de vários itens
DOM = PARAMETROS["limiar_dominante"]

AGG_RANGE = re.compile(r"^=(SUM|SUBTOTAL)\((?:9,)?\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?\)$")
REF_SUM = re.compile(r"^=\$?[A-Z]+\$?\d+(?:\+\$?[A-Z]+\$?\d+)*$")
REF_ONE = re.compile(r"\$?([A-Z]+)\$?(\d+)")


class _Safe(dict):
    def __missing__(self, k):
        return "(ver evidência)"


def Finding(arquivo, aba, celula, codigo_item, regra, classe, severidade, descricao,
            evidencia="", impacto=None, causa="", campo="", esperado=None):
    """Cria uma Ocorrencia a partir do catálogo (classe, versão, sugestão)."""
    r = catalog.get(regra)
    cls = classe if regra == "fórmula_ausente_ou_divergente" else r.classe  # única regra com duas classes
    sug = r.sugestao.format_map(_Safe(celula=celula, esperado=("{:.2f}".format(esperado) if esperado is not None else "(ver evidência)")))
    return Ocorrencia(medicao=arquivo, aba=aba, celula=celula, codigo_item=codigo_item, regra=regra, classe=cls,
                      severidade=severidade or r.severidade, descricao=descricao, evidencia=evidencia, campo=campo,
                      valor_esperado=esperado, diferenca=impacto, sugestao=sug,
                      causa="cascata" if causa == "cascata" else "primaria",
                      id=ocorrencia_id(arquivo, regra, aba, celula, campo), versao_regra=r.versao)



# ---------------------------------------------------------------- geometria
def classify_rows(m: C.Med):
    """folha = linha de item com unidade; seção = grupo seguido de folha;
    lote = grupo seguido de outro grupo."""
    ucol = m.cols.get("unid", "E")
    ordered = sorted(m.items.values(), key=lambda i: i.row)
    kind = {}
    for it in ordered:
        unit = m.f(f"{ucol}{it.row}")
        kind[it.row] = "folha" if unit not in (None, "") else "grupo"
    groups = [it for it in ordered if kind[it.row] == "grupo"]
    for idx, it in enumerate(ordered):
        if kind[it.row] != "grupo":
            continue
        nxt = ordered[idx + 1] if idx + 1 < len(ordered) else None
        kind[it.row] = "secao" if (nxt and kind[nxt.row] == "folha") else "lote"
    leaves_of: dict[int, list[int]] = {}
    sections_of: dict[int, list[int]] = {}
    cur_lote = None
    cur_sec = None
    for it in ordered:
        k = kind[it.row]
        if k == "lote":
            cur_lote, cur_sec = it.row, None
            sections_of[cur_lote] = []
            leaves_of[cur_lote] = []
        elif k == "secao":
            cur_sec = it.row
            leaves_of[cur_sec] = []
            if cur_lote is not None:
                sections_of[cur_lote].append(cur_sec)
        else:
            if cur_sec is not None:
                leaves_of[cur_sec].append(it.row)
            if cur_lote is not None:
                leaves_of[cur_lote].append(it.row)
    lotes = [r for r, k in kind.items() if k == "lote"]
    return kind, leaves_of, sections_of, lotes


def code_at(m: C.Med, row: int) -> str:
    v = m.f(f"{m.cols.get('item', 'A')}{row}")
    return str(v) if v is not None else ""


def val(m: C.Med, col: str, row: int) -> float:
    return C.num(m.v(f"{col}{row}")) or 0.0


def agg_rows(formula, col):
    """Linhas somadas por uma fórmula agregadora; None se não reconhecida."""
    if not C.is_formula(formula):
        return None
    f = formula.replace(" ", "")
    mm = AGG_RANGE.match(f)
    if mm:
        c1, r1, c2, r2 = mm.group(2), int(mm.group(3)), mm.group(4), mm.group(5)
        if c2 is None:
            return {r1}, c1
        return set(range(r1, int(r2) + 1)), c1
    if REF_SUM.match(f):
        refs = REF_ONE.findall(f)
        cols = {c for c, _ in refs}
        if len(cols) == 1:
            return {int(r) for _, r in refs}, cols.pop()
    return None


# ---------------------------------------------------------------- detectores
def d_error_tokens(m: C.Med):
    for ws in m.wb.worksheets:
        wsv = m.wv[ws.title]
        hidden = ws.sheet_state != "visible"
        n_f = n_v = 0
        first = None
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                bad_f = C.is_formula(v) and any(t in v for t in C.ERROR_TOKENS)
                cv = wsv[c.coordinate].value
                bad_v = isinstance(cv, str) and cv.strip() in C.ERROR_TOKENS
                if not (bad_f or bad_v):
                    continue
                if hidden:
                    n_f += bad_f
                    n_v += bad_v
                    first = first or c.coordinate
                    continue
                yield Finding(m.label, ws.title, c.coordinate, "", "erro_de_fórmula",
                              "comprovado", "alta",
                              "Fórmula ou valor armazenado com erro do Excel",
                              f"fórmula={str(v)[:60]} | valor={str(cv)[:20]}")
        if hidden and (n_f or n_v):
            yield Finding(m.label, ws.title, first or "", "", "erro_em_aba_oculta", "suspeita", "baixa",
                          "Aba oculta herdada com erros do Excel (não alimenta aba visível se não houver referência)",
                          f"{n_f} fórmulas e {n_v} valores com erro")


def d_hidden_dependencies(m: C.Med):
    hidden = [ws.title for ws in m.wb.worksheets if ws.sheet_state != "visible"]
    for ws in m.wb.worksheets:
        if ws.title in hidden:
            continue
        for row in ws.iter_rows():
            for c in row:
                if C.is_formula(c.value):
                    if any(f"'{h}'!" in c.value or f"{h}!" in c.value for h in hidden):
                        yield Finding(m.label, ws.title, c.coordinate, "", "dependência_de_aba_oculta",
                                      "suspeita", "media", "Fórmula visível depende de aba oculta",
                                      c.value[:70])
                    if re.search(r"\[\d+\]", c.value) and ws.title != C.FIN_SHEET:
                        yield Finding(m.label, ws.title, c.coordinate, "", "vínculo_externo",
                                      "suspeita", "media", "Fórmula aponta para outro arquivo", c.value[:70])


def _is_subtotal(f) -> bool:
    return isinstance(f, str) and f.replace(" ", "").upper().startswith("=SUBTOTAL(")


def d_aggregation(m: C.Med, kind, leaves_of, sections_of, lotes):
    """Cobertura direta dos somatórios (SUM / lista de referências / SUBTOTAL)."""
    cols = m.cols
    money_cols = [cols[k] for k in ("valor_contrato", "val_ant", "val_med", "saldo") if k in cols]
    ratio_cols = [cols[k] for k in ("qtd_ac", "qtd_ant") if k in cols]
    exp_direct, exp_leaves = {}, {}
    for r, k in kind.items():
        if k == "secao":
            exp_direct[r] = set(leaves_of[r])
            exp_leaves[r] = set(leaves_of[r])
        elif k == "lote":
            exp_direct[r] = set(sections_of[r])
            exp_leaves[r] = set(leaves_of[r])
    if m.total_row:
        exp_direct[m.total_row] = set(lotes)
        exp_leaves[m.total_row] = {r for r, k in kind.items() if k == "folha"}
    ratio_hits = collections.defaultdict(list)
    for row in exp_direct:
        for col in money_cols + ratio_cols:
            f = m.f(f"{col}{row}")
            parsed = agg_rows(f, col)
            if parsed is None:
                continue
            got, src_col = parsed
            if col in ratio_cols:
                ratio_hits[col].append((row, f))
                continue
            if src_col != col:
                yield Finding(m.label, m.ws.title, f"{col}{row}", code_at(m, row), "agregação_coluna_trocada",
                              "comprovado", "alta", "Somatório lê outra coluna", f)
            if _is_subtotal(f):
                # SUBTOTAL ignora subtotais aninhados: vale a cobertura de folhas
                leaf_cov = {r for r in got if kind.get(r) == "folha"}
                dup = {r for r in got if kind.get(r) in ("secao", "lote") and not _is_subtotal(m.f(f"{col}{r}"))}
                miss = sorted(exp_leaves[row] - leaf_cov)
                extra = sorted(leaf_cov - exp_leaves[row])
                outside = sorted(r for r in got if r in kind and kind[r] != "folha"
                                 and r not in sections_of.get(row, []) and r != row and r not in dup)
                dup = sorted(dup)
            else:
                miss = sorted(exp_direct[row] - got)
                extra = sorted(r for r in got - exp_direct[row] if r in kind)
                outside, dup = [], []
            if miss or extra or dup:
                imp = sum(val(m, col, r) for r in miss) - sum(val(m, col, r) for r in extra)
                yield Finding(m.label, m.ws.title, f"{col}{row}", code_at(m, row), "agregação_incompleta",
                              "comprovado", "alta",
                              "Somatório não cobre exatamente os itens esperados "
                              f"(omitidos: {[code_at(m, r) or r for r in miss]}; "
                              f"estranhos: {extra}; dupla contagem: {dup})",
                              f"{f} ; esperado {len(exp_leaves[row] if _is_subtotal(f) else exp_direct[row])} filhos",
                              imp, "raiz")
            elif _is_subtotal(f):
                far = [r for r in got if r in kind and kind[r] != "folha" and r not in sections_of.get(row, [])
                       and r != row and _is_subtotal(m.f(f"{col}{r}")) and r not in exp_leaves[row]
                       and r > max(exp_leaves[row] | {row})]
                if far:
                    yield Finding(m.label, m.ws.title, f"{col}{row}", code_at(m, row), "faixa_invade_grupo_vizinho",
                                  "suspeita", "baixa",
                                  "Faixa do SUBTOTAL inclui o cabeçalho do grupo seguinte (inócuo enquanto este "
                                  "também for SUBTOTAL, mas frágil)", f, 0.0, "raiz")
    for col, hits in ratio_hits.items():
        yield Finding(m.label, m.ws.title, f"{col}{hits[0][0]}", "", "soma_de_razões", "suspeita", "baixa",
                      "Somatório de coluna de proporções (%) em linhas de grupo (valores >100% sem sentido)",
                      f"{len(hits)} linhas de grupo; ex.: {hits[0][1]} -> {val(m, col, hits[0][0]):.2f}")


def column_templates(m: C.Med, kind):
    """Template dominante por (coluna, tipo de linha)."""
    dom = {}
    for k in ("folha", "secao", "lote"):
        rows = [r for r, kk in kind.items() if kk == k]
        for col in "ABCDEFGHIJKLMNOPQ":
            ci = C.column_index_from_string(col)
            tpl = collections.Counter()
            for r in rows:
                v = m.f(f"{col}{r}")
                if C.is_formula(v):
                    tpl[C.normalize_formula(v, r, ci)] += 1
                elif v is None:
                    tpl["<vazio>"] += 1
                else:
                    tpl["<constante>"] += 1
            if rows:
                top, n = tpl.most_common(1)[0]
                dom[(k, col)] = (top, n / len(rows), tpl)
    return dom


def d_templates(m: C.Med, kind):
    """Célula vazia/constante/diferente onde ≥80% dos pares têm a mesma fórmula."""
    dom = column_templates(m, kind)
    cols = m.cols
    chk = [cols[k] for k in ("valor_contrato", "qtd_ac", "qtd_ant", "val_ac", "val_med", "saldo", "saldo_pct")
           if k in cols]
    for (k, col), (top, share, tpl) in dom.items():
        if col not in chk or not top.startswith("=") and not top.startswith("R"):
            continue
        if top in ("<vazio>", "<constante>") or share < 0.8:
            continue
        if "SUM(" in top or "SUBTOTAL(" in top or top.count("+") >= 2:
            continue  # formas de agregação variam por natureza em grupos
        for r in [r for r, kk in kind.items() if kk == k]:
            v = m.f(f"{col}{r}")
            ci = C.column_index_from_string(col)
            cur = "<vazio>" if v is None else ("<constante>" if not C.is_formula(v) else C.normalize_formula(v, r, ci))
            if cur == top:
                continue
            if k == "folha" and isinstance(v, str) and v.upper().startswith(("=SUM(", "=SUBTOTAL(")):
                yield Finding(m.label, m.ws.title, f"{col}{r}", code_at(m, r), "folha_com_somatório",
                              "comprovado", "alta",
                              "Item (folha) calculado por somatório de outras linhas em vez de quantidade × preço "
                              "(dupla contagem potencial)",
                              f"padrão={top} | atual={v} | valor armazenado={val(m, col, r):.2f}",
                              val(m, col, r), "raiz")
                continue
            if cur == "<vazio>":
                cls, why = "comprovado", "Célula sem fórmula onde os pares têm"
            elif cur == "<constante>":
                cls, why = "comprovado", "Valor digitado onde os pares têm fórmula"
            else:
                if k != "folha":
                    continue  # em grupos, fórmulas equivalentes (O=H-L ou soma dos filhos) são válidas
                cls, why = "suspeita", "Fórmula diferente do padrão da coluna"
            # evidência de que a linha tem atividade (medição ou saldo)
            act = (val(m, cols.get("val_ant", "M"), r) != 0) or (val(m, cols.get("val_med", "N"), r) != 0) \
                  or (C.num(m.v(f"{cols.get('qtd_med', 'K')}{r}")) not in (None, 0))
            sev = "alta" if act else "media"
            yield Finding(m.label, m.ws.title, f"{col}{r}", code_at(m, r), "fórmula_ausente_ou_divergente",
                          cls, sev, why,
                          f"padrão={top} ({share:.0%} das {k}s) | atual={v if v is not None else '(vazio)'}"
                          f" | linha com atividade={act}", None, "raiz")


def d_missing_inputs(m: C.Med, kind):
    """Coluna de entrada (constante dominante) em branco numa folha."""
    c = m.cols
    leaves = [r for r, k in kind.items() if k == "folha"]
    for key in ("qtd_contrato", "preco", "val_ant"):
        col = c.get(key)
        if not col:
            continue
        const = [r for r in leaves if m.f(f"{col}{r}") is not None and not C.is_formula(m.f(f"{col}{r}"))]
        if len(const) / len(leaves) < 0.8:
            continue
        for r in leaves:
            if m.f(f"{col}{r}") is None:
                yield Finding(m.label, m.ws.title, f"{col}{r}", code_at(m, r), "entrada_ausente", "suspeita", "alta",
                              "Campo de entrada em branco onde os demais itens têm valor",
                              f"{len(const)}/{len(leaves)} folhas preenchidas na coluna {col}", None, "raiz")


LITERAL = re.compile(r"(?<![A-Z$\d.])(\d+(?:\.\d+)?%?)(?![\d(A-Z])")


def d_embedded_constants(m: C.Med, kind):
    seen = collections.defaultdict(list)
    for row in m.ws.iter_rows(min_row=m.first_row, max_row=(m.total_row or 400)):
        for c in row:
            v = c.value
            if not C.is_formula(v) or c.row not in kind:
                continue
            f = re.sub(r"SUBTOTAL\(9,", "SUBTOTAL(", v)
            f = re.sub(r",\s*2\)", ")", f)  # TRUNC(x,2)
            f = re.sub(r"\$?[A-Z]{1,3}\$?\d+", "R", f)
            lit = [x for x in LITERAL.findall(f) if x not in ("0", "1")]
            if lit:
                seen[tuple(lit)].append(c.coordinate)
    for lit, cells in seen.items():
        yield Finding(m.label, m.ws.title, cells[0], "", "constante_embutida_em_fórmula", "suspeita", "media",
                      "Divisor/percentual digitado dentro da fórmula de medição (pode ser legítimo, mas não é rastreável nem auditável)",
                      f"literal={list(lit)} em {len(cells)} células (ex.: {cells[:4]})", None, "")


def d_group_constants(m: C.Med, kind):
    """Colunas de entrada (M) em grupos digitadas à mão: info para o relatório."""
    M_ = m.cols.get("val_ant")
    n = sum(1 for r, k in kind.items() if k != "folha" and M_ and m.f(f"{M_}{r}") is not None
            and not C.is_formula(m.f(f"{M_}{r}")))
    if n:
        yield Finding(m.label, m.ws.title, f"{M_}", "", "acumulado_anterior_digitado_em_grupos",
                      "excecao_legitima", "baixa",
                      "AC ANTERIOR é entrada manual (inclusive em grupos); só é erro se divergir da soma dos itens "
                      "(testado em divergência_financeira_agregada)", f"{n} linhas de grupo com constante")


def d_repeated_codes(m: C.Med, kind):
    """Mesmo código de serviço (coluna B) com preço/unidade/descrição diferentes."""
    c = m.cols
    seen = collections.defaultdict(list)
    for r, k in kind.items():
        if k != "folha":
            continue
        code = m.f(f"B{r}")
        if code in (None, ""):
            continue
        seen[(str(code), str(m.f(f"C{r}")))].append((r, C.num(m.v(f"{c['preco']}{r}")), m.f(f"{c['unid']}{r}")))
    for (code, src), rows in seen.items():
        prices = {round(p, 4) for _, p, _ in rows if p is not None}
        units = {u for _, _, u in rows}
        if len(prices) > 1 or len(units) > 1:
            yield Finding(m.label, m.ws.title, f"{c['preco']}{rows[0][0]}", code, "mesmo_serviço_preço_ou_unidade_diferente",
                          "suspeita", "media", "Mesmo código de serviço com preço ou unidade distintos",
                          f"preços={sorted(prices)} unidades={sorted(map(str, units))} linhas={[r for r, _, _ in rows]}")


def d_total_row(m: C.Med, kind):
    if not m.total_row:
        return
    c = m.cols
    for key in ("valor_contrato", "val_ac", "val_ant", "val_med", "saldo", "saldo_pct"):
        col = c.get(key)
        if col and m.f(f"{col}{m.total_row}") is None:
            yield Finding(m.label, m.ws.title, f"{col}{m.total_row}", "TOTAL", "total_sem_fórmula", "comprovado",
                          "alta", "Linha de total sem fórmula em coluna que os demais totais calculam",
                          f"coluna {key}", None, "raiz")


MED_ROW = re.compile(r"^\s*\d+\s*\S*\s*medi", re.I)
LINK = re.compile(r"^='?(\[\d+\])?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)$")


def fin_rows(m: C.Med):
    ws = m.wb[C.FIN_SHEET]
    return ws, [r for r in range(1, ws.max_row + 1) if isinstance(ws[f"C{r}"].value, str)
                and MED_ROW.match(ws[f"C{r}"].value)]


def d_financial_control(m: C.Med, kind):
    """Controle financeiro: períodos, vínculos com o resumo e totais."""
    if C.FIN_SHEET not in m.wb.sheetnames:
        return
    ws, rows = fin_rows(m)
    wsv = m.wv[C.FIN_SHEET]
    cols = m.cols
    tot = m.total_row
    # períodos
    prev = None
    for r in rows:
        d0, d1 = ws[f"D{r}"].value, ws[f"F{r}"].value
        if hasattr(d0, "year") and hasattr(d1, "year"):
            if prev and (d0, d1) == prev[1]:
                yield Finding(m.label, C.FIN_SHEET, f"D{r}", ws[f"C{r}"].value, "período_repetido", "comprovado",
                              "alta", "Período idêntico ao da medição anterior (datas não avançaram)",
                              f"{d0:%d/%m/%Y}–{d1:%d/%m/%Y} repetido em {prev[0]} e {r}")
            elif prev and d0 <= prev[1][1]:
                yield Finding(m.label, C.FIN_SHEET, f"D{r}", ws[f"C{r}"].value, "período_sobreposto", "comprovado",
                              "alta", "Período começa antes do fim do anterior", f"{d0:%d/%m/%Y} ≤ {prev[1][1]:%d/%m/%Y}")
            elif prev and (d0 - prev[1][1]).days > 1:
                yield Finding(m.label, C.FIN_SHEET, f"D{r}", ws[f"C{r}"].value, "lacuna_entre_períodos", "suspeita",
                              "media", "Intervalo sem cobertura entre medições",
                              f"{(d0 - prev[1][1]).days - 1} dias")
            prev = (r, (d0, d1))
    # vínculos da coluna I com o resumo
    for r in rows:
        f = ws[f"I{r}"].value
        if isinstance(f, str) and f.startswith("="):
            mm = LINK.match(f)
            if mm:
                ext, sheet, col, rr = mm.groups()
                rr = int(rr)
                if ext:
                    yield Finding(m.label, C.FIN_SHEET, f"I{r}", ws[f"C{r}"].value, "vínculo_externo",
                                  "comprovado", "alta", "Valor da medição lê outro arquivo (não este)", f)
                elif sheet == C.MAIN_SHEET and tot and (rr != tot or col != cols.get("val_med")):
                    yield Finding(m.label, C.FIN_SHEET, f"I{r}", ws[f"C{r}"].value, "vínculo_para_linha_errada",
                                  "comprovado", "alta",
                                  f"Vínculo aponta {col}{rr}, mas o total da medição está em {cols.get('val_med')}{tot}",
                                  f, (C.num(m.v(f"{col}{rr}")) or 0) - val(m, cols["val_med"], tot))
        elif f is not None:
            pass
    # última medição: I deve ser igual ao total medido; J acumulado = L total
    if rows and tot:
        last = rows[-1]
        i_last = C.num(wsv[f"I{last}"].value)
        n_tot = val(m, cols["val_med"], tot)
        if i_last is not None and abs(i_last - n_tot) > TOL_GROUP:
            yield Finding(m.label, C.FIN_SHEET, f"I{last}", ws[f"C{last}"].value, "controle_vs_resumo",
                          "comprovado", "alta", "Valor da última medição difere do total medido no resumo",
                          f"controle={i_last:.2f} resumo={n_tot:.2f}", i_last - n_tot)
        j_last = C.num(wsv[f"J{last}"].value)
        l_tot = val(m, cols["val_ac"], tot)
        if j_last is not None and abs(j_last - l_tot) > TOL_GROUP:
            yield Finding(m.label, C.FIN_SHEET, f"J{last}", ws[f"C{last}"].value, "acumulado_controle_vs_resumo",
                          "comprovado", "alta", "Acumulado do controle financeiro difere do acumulado do resumo",
                          f"controle={j_last:.2f} resumo={l_tot:.2f}", j_last - l_tot)
    # contrato do controle x contrato do resumo
    cv = None
    for r in range(1, 12):
        v = ws[f"J{r}"].value
        if isinstance(v, (int, float)):
            cv = float(v)
            break
    if cv is not None and tot:
        h_tot = val(m, cols["valor_contrato"], tot)
        if abs(cv - h_tot) > TOL_GROUP:
            yield Finding(m.label, C.FIN_SHEET, f"J{r}", "", "contrato_controle_vs_resumo", "comprovado", "alta",
                          "Valor contratual do controle financeiro difere do total contratado no resumo",
                          f"controle={cv:.2f} resumo={h_tot:.2f}", cv - h_tot)
    # templates por coluna nas linhas de medição
    for col in "GHJMNO":
        ci = C.column_index_from_string(col)
        tpl = {}
        for r in rows:
            v = ws[f"{col}{r}"].value
            tpl[r] = ("<vazio>" if v is None else "<const>" if not C.is_formula(v) else C.normalize_formula(v, r, ci))
        cnt = collections.Counter(tpl.values())
        if not cnt:
            continue
        top, n = cnt.most_common(1)[0]
        if len(rows) >= 4 and n / len(rows) >= 0.6 and top.startswith("R"):
            for r, t in tpl.items():
                if t != top and not (col in "HJ" and r == rows[0]):
                    yield Finding(m.label, C.FIN_SHEET, f"{col}{r}", ws[f"C{r}"].value,
                                  "fórmula_ausente_ou_divergente", "suspeita", "media",
                                  "Linha de medição com fórmula diferente do padrão da coluna",
                                  f"padrão={top} atual={ws[f'{col}{r}'].value}")


def run_all(m: C.Med):
    kind, leaves_of, sections_of, lotes = classify_rows(m)
    out = []
    for gen in (d_error_tokens(m), d_hidden_dependencies(m),
                d_aggregation(m, kind, leaves_of, sections_of, lotes),
                d_templates(m, kind), d_missing_inputs(m, kind), d_embedded_constants(m, kind),
                d_group_constants(m, kind), d_repeated_codes(m, kind),
                d_financial_control(m, kind), d_total_row(m, kind)):
        out.extend(gen)
    return out, (kind, leaves_of, sections_of, lotes)
