"""Comparação dos resultados da Etapa 02 com a linha de base da Etapa 01.

Entradas locais (ignoradas pelo Git): outputs/baseline_etapa01/findings.csv, outputs/red_marks.csv
e as planilhas em data/. Saída: comparacao_etapa01.csv e comparacao_etapa01.json em outputs/etapa02/.
"""
from __future__ import annotations

import collections
import csv
import json
from pathlib import Path

import engine
import reader as C

BASE = C.OUT / "baseline_etapa01" / "findings.csv"
MARCAS = C.OUT / "red_marks.csv"


def _ler(p: Path) -> list[dict]:
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main(out: Path) -> int:
    if not BASE.exists():
        print(f"baseline ausente: {BASE}")
        return 2
    base = _ler(BASE)
    res = engine.audit_batch(C.med_files())
    novo = res.ocorrencias
    out.mkdir(parents=True, exist_ok=True)
    cel_b = collections.defaultdict(list)
    for f in base:
        cel_b[(f["arquivo"], f["aba"], f["celula"])].append(f)
    cel_n = collections.defaultdict(list)
    for o in novo:
        cel_n[(o.medicao, o.aba, o.celula)].append(o)
    linhas = []
    for k in sorted(set(cel_b) | set(cel_n)):
        b, n = cel_b.get(k, []), cel_n.get(k, [])
        estado = "ambos" if b and n else ("so_etapa01" if b else "so_etapa02")
        linhas.append({"medicao": k[0], "aba": k[1], "celula": k[2], "estado": estado,
                       "regras_etapa01": ";".join(sorted({x["regra"] for x in b})),
                       "classes_etapa01": ";".join(sorted({x["classe"] for x in b})),
                       "regras_etapa02": ";".join(sorted({x.regra for x in n})),
                       "classes_etapa02": ";".join(sorted({x.classe for x in n})),
                       "causas_etapa02": ";".join(sorted({x.causa for x in n}))})
    with open(out / "comparacao_etapa01.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)
    comprov_b = sum(1 for f in base if f["classe"] == "comprovado")
    comprov_n = sum(1 for o in novo if o.classe == "comprovado")
    prim_n = sum(1 for o in novo if o.classe == "comprovado" and o.causa == "primaria")
    marcas = [m for m in _ler(MARCAS) if not m["tipo"].startswith("preenchimento amarelo")] if MARCAS.exists() else []
    cob = [m for m in marcas if (m["arquivo"], m["aba"], m["celula"]) in cel_n]
    cob_b = [m for m in marcas if (m["arquivo"], m["aba"], m["celula"]) in cel_b]
    resumo = {
        "etapa01": {"achados": len(base), "comprovados": comprov_b,
                    "por_classe": dict(collections.Counter(f["classe"] for f in base)), "marcas_cobertas": len(cob_b)},
        "etapa02": {"ocorrencias": len(novo), "comprovados": comprov_n, "comprovados_primarios": prim_n,
                    "comprovados_cascata": comprov_n - prim_n,
                    "por_classe": dict(collections.Counter(o.classe for o in novo)),
                    "por_causa": dict(collections.Counter(o.causa for o in novo)), "marcas_cobertas": len(cob)},
        "marcas_vermelhas_total": len(marcas),
        "celulas": dict(collections.Counter(l["estado"] for l in linhas)),
        "regras_so_etapa01": dict(collections.Counter(
            r for l in linhas if l["estado"] == "so_etapa01" for r in l["regras_etapa01"].split(";"))),
        "regras_so_etapa02": dict(collections.Counter(
            r for l in linhas if l["estado"] == "so_etapa02" for r in l["regras_etapa02"].split(";"))),
        "marcas_perdidas": [f'{m["arquivo"]} {m["celula"]}' for m in cob_b if m not in cob],
        "marcas_nao_cobertas": [f'{m["arquivo"]} {m["celula"]}' for m in marcas if m not in cob],
    }
    (out / "comparacao_etapa01.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    return 0
