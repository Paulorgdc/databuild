"""Etapa 01 - inventário estrutural das seis medições.

Saída (ignorada pelo Git, contém dados da empresa):
  outputs/inventory.json   inventário completo por arquivo
  outputs/red_marks.csv    todas as marcações em vermelho/amarelo
  outputs/inventory.md     resumo legível
Uso: python scripts/01_inventory.py
"""
from __future__ import annotations

import collections
import csv
import json

import reader as C


def used_range(ws):
    rows = cols = []
    rows, cols = [], []
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None:
                rows.append(c.row)
                cols.append(c.column)
    if not rows:
        return None
    return f"{C.get_column_letter(min(cols))}{min(rows)}:{C.get_column_letter(max(cols))}{max(rows)}"


def sheet_info(ws, wsv):
    n_formula = n_const = n_err_f = n_err_v = 0
    funcs = collections.Counter()
    for row in ws.iter_rows():
        for c in row:
            v = c.value
            if v is None:
                continue
            if C.is_formula(v):
                n_formula += 1
                for fn in C.re.findall(r"([A-Z][A-Z0-9.]+)\(", v):
                    funcs[fn] += 1
                if any(t in v for t in C.ERROR_TOKENS):
                    n_err_f += 1
            else:
                n_const += 1
            cv = wsv[c.coordinate].value
            if isinstance(cv, str) and cv.strip() in C.ERROR_TOKENS:
                n_err_v += 1
    return {
        "state": ws.sheet_state,
        "declared_dimension": ws.dimensions,
        "used_range": used_range(ws),
        "sheet_protected": bool(ws.protection.sheet),
        "formulas": n_formula,
        "constants": n_const,
        "formula_cells_with_error_token": n_err_f,
        "cached_error_values": n_err_v,
        "merged_ranges": len(ws.merged_cells.ranges),
        "hidden_rows": sum(1 for d in ws.row_dimensions.values() if d.hidden),
        "hidden_cols": sum(1 for d in ws.column_dimensions.values() if d.hidden),
        "conditional_formats": len(ws.conditional_formatting),
        "data_validations": len(ws.data_validations.dataValidation) if ws.data_validations else 0,
        "functions": dict(funcs.most_common(12)),
    }


def red_marks(m: C.Med):
    out = []
    for ws in m.wb.worksheets:
        wsv = m.wv[ws.title]
        for row in ws.iter_rows():
            for c in row:
                fc = C.rgb_of(c.font.color) if c.value is not None else None
                fl = C.rgb_of(c.fill.fgColor) if c.fill.fill_type else None
                kind = None
                if C.is_red(fc):
                    kind = f"fonte {fc}"
                elif C.is_red(fl):
                    kind = f"preenchimento {fl}"
                elif fl == "FFFFFF00":
                    kind = "preenchimento amarelo"
                if kind:
                    out.append({"arquivo": m.label, "aba": ws.title, "celula": c.coordinate,
                                "tipo": kind, "conteudo": str(c.value)[:60] if c.value is not None else "",
                                "valor_armazenado": str(wsv[c.coordinate].value)[:30]})
    return out


def main():
    C.OUT.mkdir(exist_ok=True)
    inv, marks = {}, []
    for p in C.med_files():
        m = C.load(p)
        wbp = m.wb.security
        info = {
            "arquivo": p.name,
            "tamanho_bytes": p.stat().st_size,
            "workbook_structure_locked": bool(wbp and wbp.lockStructure),
            "external_links": len(m.wb._external_links),
            "defined_names": {k: (v.attr_text or "")[:70] for k, v in m.wb.defined_names.items()},
            "sheets": {ws.title: sheet_info(ws, m.wv[ws.title]) for ws in m.wb.worksheets},
            "colunas_detectadas": m.cols,
            "linha_total": m.total_row,
        }
        lv = collections.Counter(i.level for i in m.items.values())
        leaves = [i for i in m.items.values() if i.is_leaf]
        src = collections.Counter(str(m.f(f"C{i.row}") or "(vazio)") for i in leaves)
        info["hierarquia"] = {
            "itens_total": len(m.items), "por_nivel": dict(sorted(lv.items())),
            "folhas": len(leaves), "lotes_raiz": [c for c, i in m.items.items() if i.level == 1],
            "fonte_preco_folhas": dict(src),
        }
        inv[p.name] = info
        marks += red_marks(m)
    (C.OUT / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(C.OUT / "red_marks.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(marks[0].keys()))
        w.writeheader()
        w.writerows(marks)
    lines = ["# Inventário (gerado)\n"]
    for name, i in inv.items():
        lines.append(f"## {name}\n")
        lines.append(f"- protegido(estrutura)={i['workbook_structure_locked']}; links externos={i['external_links']}; "
                     f"nomes definidos={len(i['defined_names'])}; linha total={i['linha_total']}")
        h = i["hierarquia"]
        lines.append(f"- itens={h['itens_total']} níveis={h['por_nivel']} folhas={h['folhas']} lotes={h['lotes_raiz']}")
        for s, d in i["sheets"].items():
            lines.append(f"  - `{s}` estado={d['state']} usado={d['used_range']} fórmulas={d['formulas']} "
                         f"const={d['constants']} erros(fórmula/valor)={d['formula_cells_with_error_token']}/{d['cached_error_values']} "
                         f"protegida={d['sheet_protected']} mescladas={d['merged_ranges']}")
        lines.append("")
    (C.OUT / "inventory.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(inv)} arquivos; {len(marks)} marcações; saídas em {C.OUT}")


if __name__ == "__main__":
    main()
