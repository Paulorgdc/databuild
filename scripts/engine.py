"""Motor de validação: junta detectores, recálculo e comparação entre medições.

audit(path)          -> Resultado de uma planilha
audit_batch(paths)   -> medições + ocorrências (inclui regras entre medições)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import build
import catalog
import compare
import recalc
import reader as C
import rules
from catalog import PARAMETROS
from model import Medicao, Ocorrencia

PRIORIDADE_IMPACTO = {"aritmetica": 0, "estrutura": 1, "controle": 2, "formula": 3, "referencia": 4}
SEV_ORDEM = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}


@dataclass
class Resultado:
    medicoes: list[Medicao] = field(default_factory=list)
    ocorrencias: list[Ocorrencia] = field(default_factory=list)


def severidade_por_impacto(dif: float, contrato: float, padrao: str) -> str:
    if not contrato:
        return padrao
    rel = abs(dif) / contrato
    for nome, limiar in PARAMETROS["severidade_por_impacto"].items():
        if rel >= limiar:
            return nome
    return "baixa"


def finalizar(occs: list[Ocorrencia], contratos: dict[str, float]) -> list[Ocorrencia]:
    """Severidade por impacto, impacto contabilizado uma única vez por célula, ids únicos, ordem estável."""
    for o in occs:
        r = catalog.get(o.regra)
        if (o.causa == "primaria" and o.classe == "comprovado" and r.categoria == "aritmetica"
                and o.diferenca is not None):
            o.severidade = severidade_por_impacto(o.diferenca, contratos.get(o.medicao, 0.0), r.severidade)
    # impacto financeiro: no máximo uma ocorrência por (medição, aba, célula), a mais "aritmética"
    grupos: dict[tuple, list[Ocorrencia]] = {}
    for o in occs:
        o.contabiliza_impacto = False
        if (o.causa == "primaria" and o.classe == "comprovado" and o.diferenca not in (None, 0)
                and abs(o.diferenca) > 0.005):
            grupos.setdefault((o.medicao, o.aba, o.celula), []).append(o)
    for g in grupos.values():
        g.sort(key=lambda o: PRIORIDADE_IMPACTO.get(catalog.get(o.regra).categoria, 9))
        g[0].contabiliza_impacto = True
    occs.sort(key=lambda o: (o.medicao, SEV_ORDEM.get(o.severidade, 9), o.regra, o.aba, _chave_celula(o.celula), o.id))
    vistos: dict[str, int] = {}
    for o in occs:
        n = vistos.get(o.id, 0)
        vistos[o.id] = n + 1
        if n:
            o.id = f"{o.id}-{n + 1}"
    return occs


def _chave_celula(c: str):
    import re
    m = re.match(r"([A-Z]+)(\d+)", c or "")
    return (int(m.group(2)), m.group(1)) if m else (0, c or "")


def _uma(path: Path, obra_id: str):
    m = C.load(Path(path))
    med = build.construir(m, obra_id)
    occs = [o for o in rules.run_all(m)[0]]
    occs += recalc.detectar(med)
    for o in occs:
        o.obra_id = obra_id
    return med, occs


def audit(path, obra_id: str = "OBRA-1") -> Resultado:
    med, occs = _uma(Path(path), obra_id)
    return Resultado([med], finalizar(occs, {med.id: med.contrato_total()}))


def audit_batch(paths, obra_id: str = "OBRA-1") -> Resultado:
    meds, occs = [], []
    for p in sorted(Path(x) for x in paths):
        med, o = _uma(p, obra_id)
        meds.append(med)
        occs += o
    occs += compare.comparar_todas(meds)
    # regras de período entre medições do controle ficam em rules (por arquivo); aqui só consolidamos
    return Resultado(meds, finalizar(occs, {m.id: m.contrato_total() for m in meds}))
