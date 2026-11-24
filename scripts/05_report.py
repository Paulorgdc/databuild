"""Etapa 01 - gera outputs/diagnostico-etapa-01.md a partir de outputs/*.csv.

O relatório fica em outputs/ (ignorado pelo Git, como todo resultado derivado dos
dados reais). Só contém contagens, percentuais, regras e referências de célula:
nenhum valor em R$, descrição de serviço ou nome de arquivo da empresa.
Uso: python scripts/05_report.py   (depois de 01 a 04)
"""
from __future__ import annotations

import pandas as pd

import reader as C

def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def main():
    f = pd.read_csv(C.OUT / "findings.csv", encoding="utf-8-sig")
    f["med"] = f["arquivo"].str[:6]
    red = pd.read_csv(C.OUT / "red_coverage.csv", encoding="utf-8-sig")
    ser = pd.read_csv(C.OUT / "compare_series.csv")
    struct = pd.read_csv(C.OUT / "compare_structure.csv", encoding="utf-8-sig")
    cont = pd.read_csv(C.OUT / "compare_continuity.csv", encoding="utf-8-sig")
    ai = (C.OUT / "ai_viability.md").read_text(encoding="utf-8")

    by_cls = f.pivot_table(index="regra", columns="classe", values="id", aggfunc="count", fill_value=0)
    by_cls["total"] = by_cls.sum(axis=1)
    by_cls = by_cls.sort_values("total", ascending=False).reset_index()
    per_file = f.pivot_table(index="med", columns="classe", values="id", aggfunc="count", fill_value=0).reset_index()

    # fatos relativos (sem valores absolutos)
    h0, h1 = ser.contrato_H.iloc[0], ser.contrato_H.iloc[2]
    removed_h = struct["H"].sum()
    removed_l = struct["L_anterior"].sum()
    over = (ser.acumulado_L.iloc[-1] - ser.contrato_H.iloc[-1]) / ser.contrato_H.iloc[-1]
    n5 = f[(f.regra == "agregação_incompleta") & (f.celula == "N5")].copy()
    n5 = n5.merge(ser[["arquivo", "medido_N"]].assign(med=lambda d: d.arquivo.str[:6])[["med", "medido_N"]], on="med")
    n5["pct_da_medição"] = (n5["impacto"] / n5["medido_N"] * 100).round(3)

    L = []
    L.append("# Diagnóstico — Etapa 01 (gerado por `scripts/05_report.py`)\n")
    L.append("Relatório local: contém apenas contagens, percentuais, regras e referências de célula. "
             "Valores em R$, descrições de serviços e as planilhas ficam fora do repositório "
             "(`data/` e `outputs/` são ignorados pelo Git).\n")
    L.append("## 1. Escopo\n")
    L.append(f"- {len(ser)} medições (MED 01 a MED 06) de uma obra de assentamento de piso, abas lidas com "
             "openpyxl (fórmulas e valores armazenados) e pandas (consolidação).")
    L.append("- Aba principal `Resumo Medição`: itens em hierarquia por código (lote › grupo › item), "
             "colunas de contrato, acumulado anterior/atual, medição do mês e saldo.\n")
    L.append("## 2. Achados por regra e classe\n")
    L.append("`comprovado`: contradição aritmética ou estrutural demonstrável no próprio arquivo. "
             "`suspeita`: padrão anômalo que depende de contexto ou intenção. "
             "`excecao_legitima`: parece anomalia, mas é explicável (ex.: resíduo de centavos por `TRUNC`).\n")
    L.append(md_table(by_cls) + "\n")
    L.append("Por arquivo:\n")
    L.append(md_table(per_file) + "\n")
    L.append("## 3. Cobertura das marcações em vermelho\n")
    L.append(f"Os detectores **não leem cores**. Depois de rodar, {int(red.detectado.sum())} de {len(red)} "
             "células marcadas em vermelho/fonte vermelha foram encontradas por métodos genéricos "
             "(fórmula agregadora incompleta, fórmula ausente onde os pares têm, identidade aritmética quebrada).\n")
    L.append("Achados comprovados **sem** marcação vermelha (não dependem dela): "
             f"{int((~f[f.classe == 'comprovado'].set_index(['arquivo', 'aba', 'celula']).index.isin(red[red.detectado].set_index(['arquivo', 'aba', 'celula']).index)).sum())} "
             f"de {int((f.classe == 'comprovado').sum())}.\n")
    L.append("## 4. Comparação entre as seis medições\n")
    L.append(f"- {len(struct)} itens removidos entre MED 02 e MED 03, equivalentes a "
             f"{removed_h / h0:.2%} do contrato original; o acumulado anterior desses itens "
             f"({removed_l / h0:.2%} do contrato original) permaneceu digitado no grupo, "
             "produzindo divergência entre grupo e soma dos itens e, na MED 06, medido acima do contrato do resumo "
             f"({over:.2%}).")
    L.append(f"- Descontinuidades acumulado atual × anterior entre medições consecutivas: {len(cont)} itens "
             "(todos entre MED 01 e MED 02; redução uniforme em itens de administração e perda de um item).")
    L.append("- Efeito em cadeia: `N5` (soma incompleta) faz o acumulado anterior do grupo `A.1` ficar abaixo da soma "
             "dos itens em todas as medições seguintes.\n")
    L.append("Impacto da soma incompleta em `N5` sobre o medido do mês:\n")
    L.append(md_table(n5[["med", "pct_da_medição"]].rename(columns={"med": "arquivo"})) + "\n")
    L.append("## 5. Viabilidade de IA\n")
    L.append(ai.split("\n", 1)[1] + "\n")
    L.append("**Conclusão:** regras genéricas e identidades aritméticas cobrem os erros comprovados; "
             "IA estatística sozinha recupera pouco e só enxerga erros ao nível de item. "
             "IA é opcional e complementar (explicação em linguagem natural, triagem de exceções legítimas, "
             "inconsistências de descrição), nunca como detector principal.\n")
    (C.OUT / "diagnostico-etapa-01.md").write_text("\n".join(L), encoding="utf-8")
    print("outputs/diagnostico-etapa-01.md gerado")


if __name__ == "__main__":
    main()
