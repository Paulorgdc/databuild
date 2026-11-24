"""Etapa 01 - experimento de viabilidade de IA/ML como recurso complementar.

Pergunta: um detector estatístico não supervisionado (IsolationForest) sobre
características numéricas das linhas encontra os erros que os métodos
determinísticos comprovaram?  Compara-se o ranking de anomalias com os achados
comprovados por item (folha). Não usa nenhuma API externa nem envia dados.

Saída: outputs/ai_viability.md (ignorada pelo Git).
Uso: python scripts/04_ai_viability.py
"""
from __future__ import annotations

import csv

import numpy as np
from sklearn.ensemble import IsolationForest

import common as C
import detectors as D


def features(m: C.Med):
    kind, *_ = D.classify_rows(m)
    c = m.cols
    rows, keys = [], []
    for r, k in kind.items():
        if k != "folha":
            continue
        H = D.val(m, c["valor_contrato"], r) or 1.0
        F = D.val(m, c["qtd_contrato"], r) or 1.0
        rows.append([
            D.val(m, c["val_ac"], r) / H, D.val(m, c["val_ant"], r) / H, D.val(m, c["val_med"], r) / H,
            D.val(m, c["qtd_med"], r) / F, D.val(m, c["saldo"], r) / H,
            float(m.f(f"{c['val_ac']}{r}") is None), float(m.f(f"{c['val_ant']}{r}") is None),
            float(not C.is_formula(m.f(f"{c['val_med']}{r}"))),
            float(np.log10(H + 1)),
        ])
        keys.append((m.label, D.code_at(m, r), f"{c['val_ac']}{r}"))
    return np.array(rows), keys


def main():
    meds = [C.load(p) for p in C.med_files()]
    X, K = [], []
    for m in meds:
        x, k = features(m)
        X.append(x)
        K += k
    X = np.vstack(X)
    # verdade-terreno: achados comprovados que apontam para uma folha (arquivo, código)
    pos = set()
    with open(C.OUT / "findings.csv", encoding="utf-8-sig") as fh:
        for f in csv.DictReader(fh):
            if f["classe"] == "comprovado" and f["codigo_item"] and f["codigo_item"] != "TOTAL" \
               and f["regra"] in ("fórmula_ausente_ou_divergente", "folha_com_somatório", "identidade_quebrada",
                                  "valor_contrato_incoerente"):
                pos.add((f["arquivo"], f["codigo_item"]))
    pos |= {(m, c) for (m, c, _) in K if False}
    # entradas ausentes (suspeita forte) também entram como positivas
    with open(C.OUT / "findings.csv", encoding="utf-8-sig") as fh:
        for f in csv.DictReader(fh):
            if f["regra"] == "entrada_ausente":
                pos.add((f["arquivo"], f["codigo_item"]))
    y = np.array([(m, c) in pos for (m, c, _) in K])
    n, npos = len(y), int(y.sum())
    lines = [f"# Viabilidade de IA/ML (experimento reproduzível)\n",
             f"- linhas de itens analisadas: {n} (6 arquivos); positivas (erro comprovado/forte em folha): {npos}"]
    res = {}
    for contam in (0.02, 0.05, 0.10):
        iso = IsolationForest(n_estimators=300, contamination=contam, random_state=0).fit(X)
        score = -iso.score_samples(X)
        order = np.argsort(-score)
        for topk in (npos, 2 * npos, 5 * npos):
            hit = int(y[order[:topk]].sum())
            res[(contam, topk)] = hit
            lines.append(f"- IsolationForest contam={contam:.2f} top-{topk}: recall={hit}/{npos} "
                         f"({hit / max(npos, 1):.0%}), precisão={hit / topk:.0%}")
    # ablação: sem flags estruturais (branco/sem fórmula), só razões numéricas -> sem vazamento do rótulo
    Xn = X[:, [0, 1, 2, 3, 4, 8]]
    iso = IsolationForest(n_estimators=300, contamination=0.05, random_state=0).fit(Xn)
    order = np.argsort(-(-iso.score_samples(Xn)))
    for topk in (npos, 2 * npos, 5 * npos):
        hit = int(y[order[:topk]].sum())
        lines.append(f"- ABLAÇÃO (só razões numéricas, sem flags de célula) top-{topk}: recall={hit}/{npos} "
                     f"({hit / max(npos, 1):.0%}), precisão={hit / topk:.0%}")
    rng = np.random.default_rng(0)
    base = np.mean([y[rng.permutation(n)[:2 * npos]].sum() / npos for _ in range(500)])
    lines.append(f"- linha de base aleatória top-{2 * npos}: recall≈{base:.0%}")
    # cobertura estrutural: quantos achados comprovados são sequer representáveis como característica de linha?
    tot = leaf = 0
    with open(C.OUT / "findings.csv", encoding="utf-8-sig") as fh:
        for f in csv.DictReader(fh):
            if f["classe"] == "comprovado":
                tot += 1
                leaf += (f["arquivo"], f["codigo_item"]) in pos
    lines.append(f"- achados comprovados representáveis por características de item: {leaf}/{tot} "
                 f"({leaf / tot:.0%}); o restante é estrutural (faixas de SUM, vínculos, #REF!, totais, períodos)")
    out = "\n".join(lines)
    (C.OUT / "ai_viability.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
