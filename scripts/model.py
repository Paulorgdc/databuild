"""Modelo de dados padronizado (obras, medições, categorias, serviços, valores, ocorrências).

O modelo é independente do Excel: as regras de recálculo e de comparação entre
medições operam sobre `Medicao`; a origem (planilha, banco, API) só precisa
preencher estas estruturas. As tabelas exportadas (`TABELAS`) usam nomes e tipos
estáveis, prontos para carga futura em SQL Server (camada Bronze/Silver).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict

# Campos lógicos de valor de uma linha de medição.
CAMPOS_ENTRADA = ("qtd_contrato", "preco", "qtd_med", "val_ant")
CAMPOS_VALOR = ("valor_contrato", "val_ant", "val_med", "val_ac", "saldo")
CAMPOS = ("qtd_contrato", "preco", "valor_contrato", "qtd_ant", "qtd_med", "qtd_ac",
          "val_ant", "val_med", "val_ac", "saldo", "saldo_pct")

TIPOS = ("folha", "secao", "lote", "total")  # serviço, categoria, categoria-raiz, total da obra
CLASSES = ("comprovado", "suspeita", "excecao_legitima", "depende_validacao_contratual")
SEVERIDADES = ("baixa", "media", "alta", "critica")
TOTAL = "TOTAL"


def ocorrencia_id(medicao: str, regra: str, aba: str, celula: str, campo: str = "") -> str:
    """Id determinístico: o mesmo achado na mesma planilha gera sempre o mesmo id."""
    h = hashlib.sha1("|".join((medicao, regra, aba, celula, campo)).encode("utf-8")).hexdigest()
    return "OC-" + h[:10]


@dataclass
class Linha:
    codigo: str
    tipo: str
    row: int
    pai: str | None = None
    filhos: list[str] = field(default_factory=list)
    descricao: str = ""
    unidade: str = ""
    servico: str = ""            # código do serviço na tabela de preços (ex.: SINAPI), se houver
    fonte: str = ""
    armazenado: dict[str, float | None] = field(default_factory=dict)   # valor guardado pelo Excel
    formula: dict[str, str | None] = field(default_factory=dict)
    local: dict[str, float | None] = field(default_factory=dict)        # esperado a partir dos operandos armazenados
    recalc: dict[str, float | None] = field(default_factory=dict)       # esperado a partir das entradas primárias


@dataclass
class Medicao:
    id: str                      # ex.: "MED 05"
    obra_id: str
    numero: int
    linhas: dict[str, Linha]
    colunas: dict[str, str]      # campo lógico -> coluna (somente localização)
    periodo_inicio: str | None = None
    periodo_fim: str | None = None
    contrato_controle: float | None = None
    arquivo_sha256: str | None = None

    @property
    def total(self) -> Linha | None:
        return self.linhas.get(TOTAL)

    def folhas(self):
        return [l for l in self.linhas.values() if l.tipo == "folha"]

    def contrato_total(self) -> float:
        t = self.total
        return (t.armazenado.get("valor_contrato") or 0.0) if t else 0.0


@dataclass
class Ocorrencia:
    """Achado de auditoria. `row()` devolve um registro plano (uma linha por ocorrência)."""
    medicao: str
    aba: str
    celula: str
    codigo_item: str
    regra: str
    classe: str                  # CLASSES
    severidade: str              # SEVERIDADES (ajustada pelo motor)
    descricao: str
    evidencia: str = ""
    campo: str = ""
    valor_original: float | None = None
    valor_esperado: float | None = None
    diferenca: float | None = None       # original − esperado
    sugestao: str = ""
    causa: str = "primaria"              # primaria | cascata
    causa_raiz_id: str = ""
    id: str = ""
    obra_id: str = ""
    versao_regra: str = ""
    contabiliza_impacto: bool = False    # True só em primárias com diferença financeira

    # compatibilidade com a Etapa 01
    @property
    def arquivo(self): return self.medicao
    @property
    def impacto(self): return self.diferenca

    def row(self):
        return asdict(self)


# Esquema das tabelas exportadas (nome -> colunas). Tipos: texto, inteiro, decimal(18,2), booleano, data.
TABELAS: dict[str, dict[str, str]] = {
    "obras": {"obra_id": "texto", "nome": "texto"},
    "medicoes": {"medicao_id": "texto", "obra_id": "texto", "numero": "inteiro", "periodo_inicio": "data",
                 "periodo_fim": "data", "contrato_total": "decimal(18,2)", "contrato_controle": "decimal(18,2)",
                 "arquivo_sha256": "texto", "n_servicos": "inteiro", "n_categorias": "inteiro"},
    "categorias": {"medicao_id": "texto", "codigo": "texto", "tipo": "texto", "pai": "texto", "nivel": "inteiro",
                   "descricao": "texto"},
    "servicos": {"medicao_id": "texto", "codigo": "texto", "pai": "texto", "descricao": "texto", "unidade": "texto",
                 "codigo_servico": "texto", "fonte": "texto"},
    "valores": {"medicao_id": "texto", "codigo": "texto", "tipo": "texto", "campo": "texto",
                "valor_armazenado": "decimal(18,4)", "valor_recalculado": "decimal(18,4)", "formula": "texto"},
    "ocorrencias": {"ocorrencia_id": "texto", "obra_id": "texto", "medicao_id": "texto", "regra": "texto",
                    "versao_regra": "texto", "classe": "texto", "severidade": "texto", "causa": "texto",
                    "causa_raiz_id": "texto", "aba": "texto", "celula": "texto", "codigo_item": "texto",
                    "campo": "texto", "descricao": "texto", "evidencia": "texto",
                    "valor_original": "decimal(18,2)", "valor_esperado": "decimal(18,2)",
                    "diferenca": "decimal(18,2)", "contabiliza_impacto": "booleano", "sugestao": "texto"},
}
