"""Utilitários compartilhados do diagnóstico (Etapa 01).

Nada aqui conhece células específicas: a estrutura é descoberta pelos
cabeçalhos e pelos códigos hierárquicos de item (A, A.1, A.1.1.1 ...).
As medições originais em data/ são abertos somente para leitura.
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

MAIN_SHEET = "Resumo Medição"
FIN_SHEET = "03-CONTROLE FINANCEIRO"
CODE_RE = re.compile(r"^[A-Z](\.\d+)*$")
ERROR_TOKENS = ("#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!")
CELL_RE = re.compile(r"(?<![A-Za-z_!'])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(A-Za-z_])")


def label_of(path: Path) -> str:
    m = re.match(r"(MED \d+)", path.name)
    return m.group(1) if m else path.stem


def med_files() -> list[Path]:
    return sorted(DATA.glob("MED *.xlsx"))


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
    path: Path
    label: str
    wb: openpyxl.Workbook
    wv: openpyxl.Workbook
    ws: object
    wsv: object
    cols: dict
    items: dict[str, Item]
    first_row: int
    total_row: int | None

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
    ws, wsv = wb[MAIN_SHEET], wv[MAIN_SHEET]
    cols = find_columns(ws)
    first_row = 4
    total_row = None
    for r in range(1, 400):
        for c in range(1, 8):
            v = ws.cell(r, c).value
            if isinstance(v, str) and "VALOR TOTAL DA OBRA" in v.upper():
                total_row = r
                break
        if total_row:
            break
    last = total_row - 1 if total_row else 400
    items = build_items(ws, cols.get("item", "A"), first_row, last)
    return Med(path, label_of(path), wb, wv, ws, wsv, cols, items, first_row, total_row)


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def money(x):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
