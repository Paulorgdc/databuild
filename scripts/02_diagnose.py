"""Etapa 01 - diagnóstico genérico por arquivo.

Saída (ignorada pelo Git): outputs/findings.csv, outputs/red_coverage.csv
Uso: python scripts/02_diagnose.py
"""
from __future__ import annotations

import collections
import csv

import reader as C
import rules as D


def main():
    C.OUT.mkdir(exist_ok=True)
    findings = []
    meta = {}
    for p in C.med_files():
        m = C.load(p)
        fs, geo = D.run_all(m)
        findings += [f.row() for f in fs]
        meta[m.label] = {"kinds": collections.Counter(geo[0].values()), "cols": m.cols}
    for i, f in enumerate(findings, 1):
        f["id"] = f"F{i:04d}"
    cols = ["id", "arquivo", "aba", "celula", "codigo_item", "regra", "classe", "severidade", "causa",
            "descricao", "evidencia", "impacto"]
    with open(C.OUT / "findings.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows({k: f.get(k) for k in cols} for f in findings)

    # cobertura das marcações vermelhas (avaliação, não detecção)
    marks = list(csv.DictReader(open(C.OUT / "red_marks.csv", encoding="utf-8-sig")))
    index = collections.defaultdict(list)
    for f in findings:
        index[(f["arquivo"], f["aba"], f["celula"])].append(f)
    cov = []
    for mk in marks:
        if mk["tipo"].startswith("preenchimento amarelo"):
            continue
        hit = index.get((mk["arquivo"], mk["aba"], mk["celula"]), [])
        cov.append({**mk, "detectado": bool(hit), "regras": ";".join(sorted({h["regra"] for h in hit}))})
    with open(C.OUT / "red_coverage.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cov[0].keys()))
        w.writeheader()
        w.writerows(cov)

    print(f"{len(findings)} achados")
    tab = collections.Counter((f["classe"], f["regra"]) for f in findings)
    for (cl, rg), n in sorted(tab.items()):
        print(f"  {cl:17s} {rg:42s} {n}")
    hit = sum(c["detectado"] for c in cov)
    print(f"marcações vermelhas detectadas pelos métodos genéricos: {hit}/{len(cov)}")
    for c in cov:
        if not c["detectado"]:
            print("  NÃO detectada:", c["arquivo"], c["aba"], c["celula"], c["conteudo"])


if __name__ == "__main__":
    main()
