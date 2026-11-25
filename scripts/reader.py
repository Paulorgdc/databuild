"""Leitura de medições Excel (Etapa 01, evoluída na Etapa 02).

Nada aqui conhece células específicas: a estrutura é descoberta pelos
cabeçalhos, pelo conteúdo das colunas e pelos códigos hierárquicos de item
(A, A.1, A.1.1.1 ...). As medições originais são abertas somente para leitura.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

warnings.filterwarnings("ignore", message="Cannot parse header or footer")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "outputs"
MED_GLOB = "MED *.xlsx"

MAIN_SHEET = "Resumo Medição"
FIN_SHEET = "03-CONTROLE FINANCEIRO"
CODE_RE = re.compile(r"^[A-Z](\.\d+)*$")
ERROR_TOKENS = ("#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!")
CELL_RE = re.compile(r"(?<![A-Za-z_!'])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(A-Za-z_])")


def label_of(path: Path) -> str:
    m = re.match(r"(MED \d+)", path.name)
    return m.group(1) if m else path.stem


def med_files(folder: Path | None = None) -> list[Path]:
    return sorted((folder or DATA).glob(MED_GLOB))


def is_formula(v) -> bool:
    return isinstance(v, str) and v.startswith("=")


def rgb_of(color):
    return color.rgb if color is not None and color.type == "rgb" and isinstance(color.rgb, str) else None


def is_red(hexrgb: str | None) -> bool:
    if not hexrgb or len(hexrgb) != 8:
        return False
    r, g, b = (int(hexrgb[i:i + 2], 16) for i in (2, 4, 6))
    return r >= 180 and g <= 110 and b <= 110


def normalize_formula(formula: str, row: int, col: int) -> str:
    """Converte referências A1 em deslocamentos relativos (estilo R1C1)."""
    def repl(m):
        c_abs, c_let, r_abs, r_num = m.groups()
        c = column_index_from_string(c_let)
        r = int(r_num)
        cs = f"C{c}" if c_abs else f"C[{c - col}]"
        rs = f"R{r}" if r_abs else f"R[{r - row}]"
        return rs + cs
    return CELL_RE.sub(repl, formula)


@dataclass
class Item:
    row: int
    code: str
    level: int
    parent: str | None = None
    children: list[str] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class Med:
    path: Path | None
    label: str
    wb: openpyxl.Workbook
    wv: openpyxl.Workbook
    ws: object
    wsv: object
    cols: dict
    items: dict[str, Item]
    first_row: int
    total_row: int | None
    arquivo_sha256: str | None = None
    total_celula: str | None = None

    def f(self, ref):  # fórmula/valor bruto
        return self.ws[ref].value

    def v(self, ref):  # valor armazenado (cache do Excel)
        return self.wsv[ref].value


def find_columns(ws) -> dict[str, str]:
    """Mapeia colunas lógicas pelos textos de cabeçalho (linhas 1-3)."""
    seen = []  # (linha, coluna, texto normalizado)
    for r in range(1, 5):
        for c in range(1, 30):
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.strip():
                seen.append((r, c, re.sub(r"\s+", " ", v.strip().upper())))
    L = get_column_letter
    cols: dict[str, str] = {}
    ac_atual, ac_ant = [], []
    for r, c, t in seen:
        if t == "ITEM":
            cols["item"] = L(c)
        elif t.startswith("DISCRIMINA"):
            cols["desc_hdr"] = L(c)
        elif t.startswith("UNID"):
            cols["unid"] = L(c)
        elif t == "QUANTIDADE":
            cols["qtd_contrato"] = L(c)
        elif t.startswith("PREÇO") or t.startswith("PRECO"):
            cols["preco"] = L(c)
        elif t.startswith("UNIT"):
            cols["preco"] = cols.get("preco", L(c))
        elif t == "CONTRATO" and r == 3:
            cols["valor_contrato"] = L(c)
        elif t == "AC ATUAL":
            ac_atual.append(L(c))
        elif t == "AC ANTERIOR":
            ac_ant.append(L(c))
        elif t == "MED ATUAL":
            cols["qtd_med"] = L(c)
        elif t.startswith("MEDIÇÃO ATUAL") or t.startswith("MEDICAO ATUAL"):
            cols["val_med"] = L(c)
        elif t == "SALDO" and r == 2:
            cols["saldo"] = L(c)
        elif t == "%" and r == 3:
            cols["saldo_pct"] = L(c)
    # headers de contrato: 'CONTRATO' está na linha 1 (F1) e 'Contrato' em H3
    for r, c, t in seen:
        if t == "CONTRATO" and r == 3:
            cols["valor_contrato"] = L(c)
    if ac_atual:
        cols["qtd_ac"] = ac_atual[0]
        cols["val_ac"] = ac_atual[-1]
    if ac_ant:
        cols["qtd_ant"] = ac_ant[0]
        cols["val_ant"] = ac_ant[-1]
    # coluna auxiliar de percentual (Q) usada em fórmulas de K
    return cols


def build_items(ws, item_col: str, first_row: int, last_row: int) -> dict[str, Item]:
    items: dict[str, Item] = {}
    for r in range(first_row, last_row + 1):
        v = ws[f"{item_col}{r}"].value
        if isinstance(v, str) and CODE_RE.match(v.strip()):
            code = v.strip()
            items[code] = Item(row=r, code=code, level=code.count(".") + 1)
    for code, it in items.items():
        parts = code.split(".")
        for k in range(len(parts) - 1, 0, -1):
            cand = ".".join(parts[:k])
            if cand in items:
                it.parent = cand
                items[cand].children.append(code)
                break
    return items


def load(path: Path) -> Med:
    wb = openpyxl.load_workbook(path)  # fórmulas
    wv = openpyxl.load_workbook(path, data_only=True)  # valores armazenados
    return das_abas(label_of(path), wb, wv, path)


def das_abas(label, wb, wv, path=None, arquivo_sha256=None) -> Med:
    """Aplica a mesma descoberta estrutural às abas de qualquer origem."""
    ws, wsv = wb[MAIN_SHEET], wv[MAIN_SHEET]
    cols = find_columns(ws)
    first_row = 4
    total_row = None
    total_celula = None
    for r in range(1, 400):
        for c in range(1, 8):
            v = ws.cell(r, c).value
            if isinstance(v, str) and "VALOR TOTAL DA OBRA" in v.upper():
                total_row = r
                total_celula = f"{get_column_letter(c)}{r}"
                break
        if total_row:
            break
    last = total_row - 1 if total_row else 400
    items = build_items(ws, cols.get("item", "A"), first_row, last)
    cols.update(find_text_columns(ws, cols, items))
    return Med(path, label, wb, wv, ws, wsv, cols, items, first_row, total_row, arquivo_sha256, total_celula)


def find_text_columns(ws, cols: dict, items: dict[str, "Item"]) -> dict[str, str]:
    """Descobre pelo conteúdo as colunas de código de serviço, fonte e descrição
    (folhas: texto entre o código do item e a unidade)."""
    item_ci = column_index_from_string(cols.get("item", "A"))
    unid_ci = column_index_from_string(cols.get("unid", "E"))
    leaves = [it.row for it in items.values() if not it.children]
    stats = {}
    for ci in range(item_ci + 1, unid_ci):
        vals = [ws.cell(r, ci).value for r in leaves]
        vals = [str(v).strip() for v in vals if v not in (None, "")]
        if not vals:
            continue
        stats[ci] = (sum(len(v) for v in vals) / len(vals), sum(v.isdigit() for v in vals) / len(vals))
    out = {}
    if stats:
        desc = max(stats, key=lambda c: stats[c][0])
        out["desc"] = get_column_letter(desc)
        digits = [c for c in stats if c != desc and stats[c][1] >= 0.6]
        if digits:
            out["servico"] = get_column_letter(digits[0])
        rest = [c for c in stats if c != desc and get_column_letter(c) != out.get("servico")]
        if rest:
            out["fonte"] = get_column_letter(rest[0])
    return out


_MED_ROW = re.compile(r"^\s*\d+\s*\S*\s*medi", re.I)
_LINK = re.compile(r"^='?(\[\d+\])?([^'!]+)'?!\$?([A-Z]+)\$?(\d+)$")


def find_fin_layout(wb) -> dict | None:
    """Localiza no controle financeiro as linhas de medição e suas colunas
    (rótulo, período, valor ligado ao resumo, acumulado, prazo, contrato)."""
    if FIN_SHEET not in wb.sheetnames:
        return None
    ws = wb[FIN_SHEET]
    label_hits = collections_counter()
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and _MED_ROW.match(c.value):
                label_hits[c.column] += 1
    if not label_hits:
        return None
    lc = label_hits.most_common(1)[0][0]
    rows = [r for r in range(1, ws.max_row + 1)
            if isinstance(ws.cell(r, lc).value, str) and _MED_ROW.match(ws.cell(r, lc).value)]
    date_cols = [c for c in range(1, ws.max_column + 1)
                 if sum(hasattr(ws.cell(r, c).value, "year") for r in rows) >= max(1, len(rows) // 2)]
    link_hits = collections_counter()
    for r in rows:
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str) and _LINK.match(v) or (isinstance(v, str) and "Resumo" in v):
                link_hits[c] += 1
    vc = link_hits.most_common(1)[0][0] if link_hits else None
    acc = None
    if vc:
        for c in (vc + 1, vc + 2):
            for r in range(max(1, rows[0] - 4), rows[0]):
                if isinstance(ws.cell(r, c).value, str) and "ACUMUL" in ws.cell(r, c).value.upper():
                    acc = c
        acc = acc or vc + 1
    prazo = None
    for r in range(max(1, rows[0] - 4), rows[0]):
        for c in range(1, ws.max_column + 1):
            if isinstance(ws.cell(r, c).value, str) and "PRAZO" in ws.cell(r, c).value.upper():
                prazo = c
    contrato = None
    for r in range(1, rows[0]):
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if isinstance(v, str) and "VALOR CONTR" in v.upper():
                for rr in range(r + 1, min(r + 3, rows[0])):
                    if isinstance(ws.cell(rr, c).value, (int, float)):
                        contrato = (rr, c)
    L = get_column_letter
    return {"rows": rows, "label": L(lc), "ini": L(date_cols[0]) if date_cols else None,
            "fim": L(date_cols[1]) if len(date_cols) > 1 else None,
            "valor": L(vc) if vc else None, "acum": L(acc) if acc else None,
            "prazo": L(prazo) if prazo else None,
            "contrato": f"{L(contrato[1])}{contrato[0]}" if contrato else None}


def collections_counter():
    import collections
    return collections.Counter()


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def money(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
