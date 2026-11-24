"""Catálogo versionado de regras de negócio.

Cada regra tem id estável, versão, classe e severidade padrão, descrição e modelo
de sugestão. As regras não conhecem endereços de célula: a localização é apenas o
resultado. A implementação fica em `rules.py`/`engine.py` (funções ligadas por id);
o catálogo é a fonte da verdade para classe, severidade e texto, e pode ser
exportado (`python scripts/audit.py catalog`) para consumo por SQL Server ou pela aplicação web.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

CATALOG_VERSION = "2.0.0"

# Parâmetros numéricos do catálogo (alterar aqui, não nas regras).
PARAMETROS = {
    "tolerancia_item": 0.011,       # R$; resíduo de centavos por TRUNC
    "tolerancia_grupo": 0.05,       # R$; somatórios de vários itens
    "limiar_dominante": 0.80,       # fração mínima para uma fórmula ser o padrão da coluna
    "limiar_preenchimento": 0.80,   # fração mínima de folhas preenchidas para exigir entrada
    "severidade_por_impacto": {"critica": 0.01, "alta": 0.001, "media": 0.0001},  # fração do contrato total
}


@dataclass(frozen=True)
class Regra:
    id: str
    versao: str
    categoria: str          # estrutura | formula | aritmetica | referencia | controle | entre_medicoes | contrato
    classe: str             # comprovado | suspeita | excecao_legitima | depende_validacao_contratual
    severidade: str         # baixa | media | alta | critica
    titulo: str
    descricao: str
    sugestao: str
    recalculo: bool = False  # depende do recálculo independente


def _r(id, categoria, classe, sev, titulo, descricao, sugestao, recalculo=False, versao="1.0.0"):
    return Regra(id, versao, categoria, classe, sev, titulo, descricao, sugestao, recalculo)


_REGRAS = [
    # --- referências e erros do Excel
    _r("erro_de_fórmula", "referencia", "comprovado", "alta", "Erro do Excel",
       "Fórmula ou valor armazenado contém #REF!, #DIV/0!, #N/A etc. em aba visível.",
       "Restaurar a referência quebrada em {celula} e recalcular."),
    _r("erro_em_aba_oculta", "referencia", "suspeita", "baixa", "Erros em aba oculta",
       "Aba oculta com erros do Excel; só afeta a medição se alguma aba visível a referenciar.",
       "Remover a aba oculta ou corrigir seus erros; confirmar que nenhuma fórmula visível depende dela."),
    _r("dependência_de_aba_oculta", "referencia", "suspeita", "media", "Dependência de aba oculta",
       "Fórmula de aba visível lê uma aba oculta.", "Trazer o dado para uma aba visível e auditável."),
    _r("vínculo_externo", "referencia", "suspeita", "media", "Vínculo externo",
       "Fórmula aponta para outro arquivo.", "Substituir o vínculo por referência interna ou valor documentado."),
    _r("vínculo_externo_controle", "controle", "comprovado", "alta", "Valor da medição lê outro arquivo",
       "O controle financeiro lê o valor da medição de outro arquivo, não do resumo desta medição.",
       "Apontar {celula} para o total medido do resumo desta medição."),
    _r("vínculo_para_linha_errada", "controle", "comprovado", "alta", "Vínculo para a linha errada",
       "O controle financeiro aponta para uma linha/coluna do resumo que não é o total medido.",
       "Apontar {celula} para o total medido do resumo (valor esperado: {esperado})."),
    # --- estrutura
    _r("agregação_incompleta", "estrutura", "comprovado", "alta", "Somatório incompleto",
       "O somatório de um grupo não cobre exatamente os itens que a hierarquia indica (omitidos, estranhos ou duplicados).",
       "Ajustar a fórmula de {celula} para cobrir todos os itens do grupo (valor esperado: {esperado})."),
    _r("agregação_coluna_trocada", "estrutura", "comprovado", "alta", "Somatório lê outra coluna",
       "A fórmula agregadora de uma coluna soma outra coluna.", "Corrigir a coluna da fórmula em {celula}."),
    _r("faixa_invade_grupo_vizinho", "estrutura", "suspeita", "baixa", "Faixa invade grupo vizinho",
       "A faixa do SUBTOTAL inclui o cabeçalho do grupo seguinte; inócuo enquanto também for SUBTOTAL, mas frágil.",
       "Encurtar a faixa de {celula} até o último item do grupo."),
    _r("soma_de_razões", "estrutura", "suspeita", "baixa", "Soma de proporções",
       "Coluna de proporções (%) somada em linhas de grupo; resultado sem sentido (>100%).",
       "Calcular o percentual do grupo como acumulado ÷ contrato em {celula}."),
    _r("folha_com_somatório", "formula", "comprovado", "alta", "Serviço calculado por somatório",
       "Item (folha) calculado por SUM/SUBTOTAL de outras linhas em vez de quantidade × preço (dupla contagem).",
       "Restaurar a fórmula padrão da coluna em {celula}."),
    # --- fórmulas
    _r("fórmula_ausente_ou_divergente", "formula", "comprovado", "alta", "Fórmula ausente ou divergente",
       "Célula sem fórmula, com valor digitado ou com fórmula distinta onde ao menos 80% das linhas do mesmo tipo têm a mesma.",
       "Restaurar a fórmula padrão da coluna em {celula}."),
    _r("entrada_ausente", "formula", "suspeita", "alta", "Entrada em branco",
       "Campo de entrada (quantidade, preço, acumulado anterior) em branco onde os demais serviços têm valor.",
       "Preencher {celula} ou registrar a justificativa do vazio."),
    _r("constante_embutida_em_fórmula", "formula", "suspeita", "media", "Constante embutida em fórmula",
       "Divisor ou percentual digitado dentro da fórmula; pode ser legítimo, mas não é rastreável.",
       "Mover o parâmetro para uma célula de entrada identificada."),
    _r("total_sem_fórmula", "formula", "comprovado", "alta", "Total sem fórmula",
       "Linha de total sem fórmula em coluna que os demais totais calculam.",
       "Restaurar a fórmula de total em {celula}."),
    # --- aritmética (recálculo independente)
    _r("valor_contrato_incoerente", "aritmetica", "comprovado", "alta", "Contrato ≠ quantidade × preço",
       "Valor de contrato do serviço difere de quantidade × preço (truncado em centavos).",
       "Corrigir {celula} para o valor esperado {esperado}.", True),
    _r("identidade_quebrada", "aritmetica", "comprovado", "alta", "Identidade aritmética quebrada",
       "Acumulado ≠ anterior + medição, ou saldo ≠ contrato − acumulado, ou medido ≠ quantidade × preço.",
       "Corrigir {celula} para o valor esperado {esperado}.", True),
    _r("divergência_financeira_agregada", "aritmetica", "comprovado", "alta", "Grupo ≠ soma dos itens",
       "O valor de um grupo difere da soma dos valores dos seus itens.",
       "Corrigir {celula} para a soma dos itens ({esperado}).", True),
    _r("total_vs_soma_das_folhas", "aritmetica", "comprovado", "alta", "Total ≠ soma dos serviços",
       "O total da obra difere da soma de todos os serviços.", "Corrigir {celula} para {esperado}.", True),
    _r("valor_armazenado_diverge_do_recalculo", "aritmetica", "comprovado", "media", "Valor armazenado ≠ recálculo",
       "O valor guardado pelo Excel difere do recálculo independente a partir das entradas primárias, "
       "sem que a fórmula local esteja errada (efeito de entrada incorreta a montante).",
       "Corrigir a causa-raiz indicada; {celula} se ajusta ao recalcular.", True),
    # --- exceções legítimas
    _r("resíduo_de_truncamento", "aritmetica", "excecao_legitima", "baixa", "Resíduo de centavos por TRUNC",
       "Saldo de centavos em serviço 100% medido, explicado pelo truncamento de contrato e medido.",
       "Nenhuma ação; documentar a convenção de arredondamento."),
    _r("acumulado_anterior_digitado_em_grupos", "aritmetica", "excecao_legitima", "baixa",
       "Acumulado anterior digitado em grupos",
       "AC ANTERIOR é entrada manual (inclusive em grupos); só é erro se divergir da soma dos serviços "
       "(testado em divergência_financeira_agregada).", "Nenhuma ação, salvo divergência."),
    # --- contrato (dependem de validação documental)
    _r("medido_acima_do_contrato", "contrato", "depende_validacao_contratual", "media", "Medido acima do contratado",
       "Acumulado medido maior que o valor contratado. Pode ser aditivo ou supressão contratual não refletida; "
       "sem o documento contratual não é possível afirmar erro.",
       "Confirmar aditivos/supressões; se não houver, limitar a medição ao contratado ({esperado})."),
    _r("contrato_controle_vs_resumo", "contrato", "depende_validacao_contratual", "alta",
       "Contrato do controle ≠ contrato do resumo",
       "O valor contratual do controle financeiro difere do total contratado no resumo; pode refletir "
       "supressão/aditivo aprovado só em um dos lugares.",
       "Confirmar o contrato vigente e alinhar controle e resumo ({esperado})."),
    _r("mesmo_serviço_preço_ou_unidade_diferente", "contrato", "suspeita", "media", "Mesmo serviço, preço ou unidade distintos",
       "Mesmo código de serviço com preço ou unidade diferentes na mesma medição.",
       "Confirmar na proposta se os preços distintos são intencionais."),
    # --- controle financeiro
    _r("período_repetido", "controle", "comprovado", "alta", "Período repetido",
       "Período idêntico ao da medição anterior (datas não avançaram).", "Corrigir as datas de {celula}."),
    _r("período_sobreposto", "controle", "comprovado", "alta", "Período sobreposto",
       "O período começa antes do fim do anterior.", "Corrigir as datas de {celula}."),
    _r("lacuna_entre_períodos", "controle", "suspeita", "media", "Lacuna entre períodos",
       "Intervalo sem cobertura entre medições consecutivas.", "Confirmar se o intervalo é legítimo."),
    _r("controle_vs_resumo", "controle", "comprovado", "alta", "Valor da última medição ≠ total do resumo",
       "O valor da última medição no controle difere do total medido no resumo.",
       "Corrigir {celula} para {esperado}."),
    _r("acumulado_controle_vs_resumo", "controle", "comprovado", "alta", "Acumulado do controle ≠ resumo",
       "O acumulado do controle difere do acumulado do resumo.", "Corrigir {celula} para {esperado}."),
    # --- entre medições
    _r("item_removido_entre_medicoes", "entre_medicoes", "depende_validacao_contratual", "alta", "Serviço removido",
       "Serviço presente na medição anterior e ausente na atual, possivelmente com acumulado anterior mantido no grupo.",
       "Confirmar a supressão contratual e, se válida, remover também o acumulado do grupo."),
    _r("item_incluido_entre_medicoes", "entre_medicoes", "depende_validacao_contratual", "media", "Serviço incluído",
       "Serviço ausente na medição anterior e presente na atual (possível aditivo).",
       "Confirmar o aditivo contratual correspondente."),
    _r("cadastro_alterado_entre_medicoes", "entre_medicoes", "depende_validacao_contratual", "media",
       "Cadastro alterado (quantidade, preço, unidade)",
       "Quantidade, preço, unidade ou valor de contrato do mesmo serviço mudou entre medições.",
       "Confirmar o termo aditivo ou reajuste que justifica a alteração."),
    _r("acumulado_anterior_descontinuo", "entre_medicoes", "suspeita", "media", "AC ANTERIOR ≠ AC ATUAL anterior",
       "O acumulado anterior desta medição difere do acumulado atual da medição anterior para o mesmo serviço.",
       "Reconciliar com o acumulado atual da medição anterior ({esperado})."),
]

CATALOGO: dict[str, Regra] = {r.id: r for r in _REGRAS}


def get(regra_id: str) -> Regra:
    return CATALOGO[regra_id]


def as_json() -> dict:
    return {"versao": CATALOG_VERSION, "parametros": PARAMETROS,
            "regras": [asdict(r) for r in CATALOGO.values()]}
