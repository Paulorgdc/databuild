"""Etapa 01 - comparação entre as seis medições.

Saída (ignorada pelo Git): outputs/compare_*.csv e outputs/compare.md
Uso: python scripts/03_compare.py
"""
from __future__ import annotations

import csv

import common as C
import detectors as D

TOL = 0.011


def leaf_table(m: C.Med):
    kind, *_ = D.classify_rows(m)
    c = m.cols
    t = {}
    for r, k in kind.items():
        if k != "folha":
            continue
        code = D.code_at(m, r)
        t[code] = {
            "row": r, "desc": str(m.f(f"D{r}") or ""), "unid": str(m.f(f"{c['unid']}{r}") or "").strip(),
            "srv": str(m.f(f"B{r}") or ""),
            "qtd": D.val(m, c["qtd_contrato"], r), "preco": D.val(m, c["preco"], r),
            "H": D.val(m, c["valor_contrato"], r), "M": D.val(m, c["val_ant"], r),
            "N": D.val(m, c["val_med"], r), "L": D.val(m, c["val_ac"], r),
            "M_blank": m.f(f"{c['val_ant']}{r}") is None, "L_blank": m.f(f"{c['val_ac']}{r}") is None,
        }
    return t


def write(name, rows):
    if not rows:
        return
    with open(C.OUT / name, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    C.OUT.mkdir(exist_ok=True)
    meds = [C.load(p) for p in C.med_files()]
    tabs = [leaf_table(m) for m in meds]
    series, struct, drift, cont = [], [], [], []
    for m in meds:
        c = m.cols
        tot = m.total_row
        series.append({"arquivo": m.label, "folhas": sum(1 for k in D.classify_rows(m)[0].values() if k == "folha"),
                       "contrato_H": round(D.val(m, c["valor_contrato"], tot), 2),
                       "anterior_M": round(D.val(m, c["val_ant"], tot), 2),
                       "medido_N": round(D.val(m, c["val_med"], tot), 2),
                       "acumulado_L": round(D.val(m, c["val_ac"], tot), 2),
                       "saldo_O": round(D.val(m, c["saldo"], tot), 2),
                       "prazo_dias_controle": next((C.num(m.wb[C.FIN_SHEET][f"G{r}"].value) for r in (12, 13)
                                                    if isinstance(m.wb[C.FIN_SHEET][f"G{r}"].value, (int, float))), None)})
    for i in range(1, len(meds)):
        a, b, ta, tb = meds[i - 1], meds[i], tabs[i - 1], tabs[i]
        for code in sorted(set(ta) - set(tb), key=lambda x: ta[x]["row"]):
            struct.append({"de": a.label, "para": b.label, "mudança": "item removido", "codigo": code,
                           "servico": ta[code]["srv"], "H": ta[code]["H"], "L_anterior": ta[code]["L"],
                           "descricao": ta[code]["desc"][:50]})
        for code in sorted(set(tb) - set(ta), key=lambda x: tb[x]["row"]):
            struct.append({"de": a.label, "para": b.label, "mudança": "item incluído", "codigo": code,
                           "servico": tb[code]["srv"], "H": tb[code]["H"], "L_anterior": "",
                           "descricao": tb[code]["desc"][:50]})
        for code in set(ta) & set(tb):
            x, y = ta[code], tb[code]
            for f in ("desc", "unid", "srv", "qtd", "preco"):
                if x[f] != y[f] and not (isinstance(x[f], float) and abs(x[f] - y[f]) < 1e-6):
                    drift.append({"de": a.label, "para": b.label, "codigo": code, "campo": f,
                                  "antes": str(x[f])[:40], "depois": str(y[f])[:40]})
            if abs(x["L"] - y["M"]) > TOL:
                cont.append({"de": a.label, "para": b.label, "codigo": code, "servico": y["srv"],
                             "L_anterior": round(x["L"], 2), "M_atual": round(y["M"], 2),
                             "diferença": round(y["M"] - x["L"], 2),
                             "M_atual_em_branco": y["M_blank"], "L_anterior_em_branco": x["L_blank"]})
    write("compare_series.csv", series)
    write("compare_structure.csv", struct)
    write("compare_drift.csv", drift)
    write("compare_continuity.csv", cont)
    # controle financeiro: histórico de cada medição nos arquivos seguintes
    hist = []
    for m in meds:
        ws, rows = D.fin_rows(m)
        wsv = m.wv[C.FIN_SHEET]
        for r in rows:
            hist.append({"arquivo": m.label, "medicao": ws[f"C{r}"].value,
                         "inicio": ws[f"D{r}"].value, "fim": ws[f"F{r}"].value,
                         "valor_I": C.num(wsv[f"I{r}"].value), "acumulado_J": C.num(wsv[f"J{r}"].value),
                         "formula_I": str(ws[f"I{r}"].value)[:40]})
    write("compare_control_history.csv", hist)
    # resumo no console
    print("série de totais:")
    for s in series:
        print("  ", s)
    print(f"mudanças estruturais: {len(struct)}; deriva de cadastro: {len(drift)}; descontinuidades M×L: {len(cont)}")
    from collections import Counter
    print("  estrutura:", Counter((s['de'], s['para'], s['mudança']) for s in struct))
    print("  deriva por campo:", Counter(d['campo'] for d in drift))
    print("  continuidade por par:", Counter((c['de'], c['para']) for c in cont))
    # histórico imutável?
    by = {}
    for h in hist:
        by.setdefault(h["medicao"], []).append((h["arquivo"], h["valor_I"]))
    for k, v in by.items():
        vals = {round(x[1], 2) for x in v if x[1] is not None}
        if len(vals) > 1:
            print("  valor da", k, "muda entre arquivos:", v)


if __name__ == "__main__":
    main()
