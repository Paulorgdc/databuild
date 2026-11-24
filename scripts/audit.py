"""CLI do DataBuild (Etapa 02).

  python scripts/audit.py audit [ARQUIVO.xlsx ...] [--data DIR] [--out DIR] [--format csv,json]
  python scripts/audit.py catalog [--out ARQUIVO.json]
  python scripts/audit.py baseline [--out DIR]     # compara com a Etapa 01 (outputs/baseline_etapa01/)

Sem arquivos, `audit` usa data/MED *.xlsx. As planilhas são abertas só para leitura.
Saídas ficam em outputs/etapa02/ (ignorado pelo Git).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import catalog
import reader as C

SAIDA = C.OUT / "etapa02"


def cmd_audit(a) -> int:
    import engine
    import export
    if a.files:
        paths = [Path(f) for f in a.files]
    else:
        paths = C.med_files(Path(a.data) if a.data else None)
    if not paths:
        print("nenhuma planilha encontrada (esperado: data/MED *.xlsx)", file=sys.stderr)
        return 2
    res = engine.audit_batch(paths)
    out = Path(a.out) if a.out else SAIDA
    export.exportar(res, out, tuple(x.strip() for x in a.format.split(",")))
    r = export.resumo(res)
    print(f"{r['ocorrencias']} ocorrências em {len(r['medicoes'])} medições -> {out}")
    print("  classe:", r["por_classe"])
    print("  causa :", r["por_causa"])
    print(f"  primárias com impacto financeiro contabilizado: {r['primarias_com_impacto']} (valores em resumo.json local)")
    return 0


def cmd_catalog(a) -> int:
    txt = json.dumps(catalog.as_json(), ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
        print(f"catálogo {catalog.CATALOG_VERSION}: {len(catalog.CATALOGO)} regras -> {a.out}")
    else:
        print(txt)
    return 0


def cmd_baseline(a) -> int:
    import baseline
    return baseline.main(Path(a.out) if a.out else SAIDA)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="audit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("audit", help="audita medições")
    s.add_argument("files", nargs="*")
    s.add_argument("--data")
    s.add_argument("--out")
    s.add_argument("--format", default="csv,json")
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("catalog", help="exporta o catálogo de regras")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_catalog)
    s = sub.add_parser("baseline", help="compara com a Etapa 01")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_baseline)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
