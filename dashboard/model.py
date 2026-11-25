"""Build the Gold semantic model. All list entries describe schema, never case data."""
from pathlib import Path

# Semantic column name -> (source column, TMDL type). Identifiers stay hidden.
K = "int64"
T = "string"
M = "decimal"
D = "dateTime"
B = "boolean"
TABLES = {
    "Obra": ("dim_obra", {"obra_key": ("obra_key", K)}),
    "Calendário": ("dim_data", {"data_key": ("data_key", K),
                                "Data": ("data_relatorio", D)}),
    "Medição": ("dim_medicao", {
        "medicao_key": ("medicao_key", K), "obra_key": ("obra_key", K),
        "periodo_fim_key": ("periodo_fim_key", K), "numero": ("numero", K),
        "Medição": ("rotulo", T),
    }),
    "Item": ("dim_item", {
        "item_key": ("item_key", K), "tipo": ("tipo", T), "Código": ("codigo", T),
        "Categoria": ("categoria_relatorio", T),
        "Serviço": ("servico_relatorio", T),
    }),
    "Regra": ("dim_regra", {"regra_key": ("regra_key", K), "Regra": ("regra_id", T),
                            "Título": ("titulo", T)}),
    "Classe": ("dim_classe", {"classe_key": ("classe_key", K), "classe_codigo": ("classe", T),
                              "Classe": ("rotulo", T)}),
    "Severidade": ("dim_severidade", {"severidade_key": ("severidade_key", K),
                                      "severidade_codigo": ("severidade", T), "Severidade": ("rotulo", T)}),
    "Causa": ("dim_causa", {"causa_key": ("causa_key", K), "causa_codigo": ("causa", T),
                            "Causa": ("rotulo", T)}),
    "Resumo": ("fact_medicao", {
        "medicao_key": ("medicao_key", K), "valor_medido_mes": ("valor_medido_mes", M),
        "valor_acumulado": ("valor_acumulado", M), "saldo": ("saldo", M),
        "contrato_total": ("contrato_total", M),
    }),
    "Valores": ("fact_valor_item", {
        "medicao_key": ("medicao_key", K), "item_key": ("item_key", K),
        **{c: (c, M) for c in ("valor_contrato", "val_med", "val_ac", "saldo",
                               "valor_contrato_recalc", "val_med_recalc", "val_ac_recalc", "saldo_recalc")},
    }),
    "Ocorrências": ("fact_ocorrencia", {
        **{f"{k}_key": (f"{k}_key", K) for k in
           ("medicao", "item", "regra", "classe", "severidade", "causa")},
        "Ocorrência": ("ocorrencia_id", T), "Campo": ("campo", T), "Célula": ("celula", T),
        "contabiliza_impacto": ("contabiliza_impacto", B),
        "impacto_contabilizado": ("impacto_contabilizado", M),
    }),
    "Pendências": ("fact_pendencia_valor", {
        "medicao_key": ("medicao_key", K), "item_key": ("item_key", K),
        "Campo": ("campo", T), "situacao": ("situacao", T),
        "Situação": ("situacao_relatorio", T),
    }),
}

REPORT_COLUMNS = {
    "Calendário": (("data_relatorio", "if [data_key] = 0 then null else [data]", "nullable date"),),
    "Item": (
        ("categoria_relatorio", '''if [tipo] = "folha" and [codigo_pai] <> null then
            [codigo_pai] & " · " & ([descricao_pai] ?? "")
            else if List.Contains({"secao", "lote"}, [tipo]) then
            [codigo] & " · " & ([descricao] ?? "") else "Sem categoria"''', "text"),
        ("servico_relatorio", '''if [tipo] = "folha" then
            [codigo] & " · " & ([descricao] ?? "") else null''', "nullable text"),
    ),
    "Pendências": (("situacao_relatorio", '''if [situacao] = "pendente_dados" then "Aguardando dados"
        else if [situacao] = "pendente_contratual" then "Aguardando contrato" else null''', "nullable text"),),
}

RELATIONSHIPS = (
    ("Medição", "obra_key", "Obra", "obra_key"),
    ("Medição", "periodo_fim_key", "Calendário", "data_key"),
    *((t, "medicao_key", "Medição", "medicao_key")
      for t in ("Resumo", "Valores", "Ocorrências", "Pendências")),
    *((t, "item_key", "Item", "item_key") for t in ("Valores", "Ocorrências", "Pendências")),
    *(("Ocorrências", c, t, c) for t, c in (
        ("Regra", "regra_key"), ("Classe", "classe_key"),
        ("Severidade", "severidade_key"), ("Causa", "causa_key"))),
)

# Gold and DAX retain four decimal places. Only the display rounds to cents.
MONEY = '"R$ "#,0.00;-"R$ "#,0.00;"R$ "0.00'
COUNT = "#,0"
MEASURES = {
    "Resumo": {
        "Medições": ("COALESCE(COUNTROWS('Resumo'), 0)", COUNT),
        "Medido oficial": ("""IF(COUNT('Resumo'[valor_medido_mes]) = COUNTROWS('Resumo'),
            SUM('Resumo'[valor_medido_mes]))""", MONEY),
    },
    "Valores": {
        "Itens de serviço": ("COALESCE(CALCULATE(COUNTROWS('Valores'), KEEPFILTERS('Item'[tipo] = \"folha\")), 0)", COUNT),
    },
    "Ocorrências": {
        "Total de ocorrências": ("COALESCE(COUNTROWS('Ocorrências'), 0)", COUNT),
        "Erros primários": ("""CALCULATE([Total de ocorrências],
            KEEPFILTERS('Classe'[classe_codigo] = "comprovado"), KEEPFILTERS('Causa'[causa_codigo] = "primaria"))""", COUNT),
        "Cascatas": ('CALCULATE([Total de ocorrências], KEEPFILTERS(\'Causa\'[causa_codigo] = "cascata"))', COUNT),
        **{display: (f'CALCULATE([Total de ocorrências], KEEPFILTERS(\'Classe\'[classe_codigo] = "{value}"))', COUNT)
           for display, value in (("Suspeitas", "suspeita"), ("Exceções legítimas", "excecao_legitima"),
                                   ("Validações contratuais", "depende_validacao_contratual"))},
        "Impactos contabilizados": ("""CALCULATE([Total de ocorrências],
            KEEPFILTERS('Ocorrências'[contabiliza_impacto] = TRUE()),
            KEEPFILTERS('Classe'[classe_codigo] = "comprovado"), KEEPFILTERS('Causa'[causa_codigo] = "primaria"))""", COUNT),
        "Impacto financeiro comprovado": ("""CALCULATE(SUM('Ocorrências'[impacto_contabilizado]),
            KEEPFILTERS('Ocorrências'[contabiliza_impacto] = TRUE()),
            KEEPFILTERS('Classe'[classe_codigo] = "comprovado"), KEEPFILTERS('Causa'[causa_codigo] = "primaria"))""", MONEY),
        "Primários críticos ou altos": ("""CALCULATE([Erros primários],
            KEEPFILTERS('Severidade'[severidade_codigo] IN {"critica", "alta"}))""", COUNT),
    },
    "Pendências": {
        "Campos pendentes": ("COALESCE(COUNTROWS('Pendências'), 0)", COUNT),
        "Aguardando dados": ('CALCULATE([Campos pendentes], KEEPFILTERS(\'Pendências\'[situacao] = "pendente_dados"))', COUNT),
        "Aguardando contrato": ('CALCULATE([Campos pendentes], KEEPFILTERS(\'Pendências\'[situacao] = "pendente_contratual"))', COUNT),
        "Prioridade de revisão": ("""IF([Medições] = 0, BLANK(),
            SWITCH(TRUE(), [Primários críticos ou altos] > 0, "Alta · erro comprovado",
                [Validações contratuais] + [Aguardando contrato] > 0, "Revisar contrato",
                [Campos pendentes] > 0, "Completar dados",
                [Total de ocorrências] > [Exceções legítimas], "Revisar ocorrências", "Sem ação prioritária"))""", None),
    },
}

for name, field in (("Contrato oficial no corte", "contrato_total"),
                    ("Acumulado oficial no corte", "valor_acumulado"), ("Saldo oficial no corte", "saldo")):
    MEASURES["Resumo"][name] = (f"""VAR Corte = MAXX(FILTER('Resumo', TRUE()), RELATED('Medição'[numero]))
        VAR NoCorte = FILTER('Resumo', RELATED('Medição'[numero]) = Corte)
        RETURN IF(COUNTROWS(FILTER(NoCorte, NOT ISBLANK('Resumo'[{field}]))) = COUNTROWS(NoCorte),
            SUMX(NoCorte, 'Resumo'[{field}]))""", MONEY)

for label, column in (("Contrato", "valor_contrato"), ("Medido", "val_med"),
                      ("Acumulado", "val_ac"), ("Saldo", "saldo")):
    for kind, field in (("original", column), ("recalculado", column + "_recalc")):
        # For snapshots use the last measurement in the selected scope. A missing
        # amount in its scope suppresses the total instead of imputing zero.
        cut = ("VAR Corte = CALCULATE(MAXX('Valores', RELATED('Medição'[numero])), "
               "REMOVEFILTERS('Item'))\nVAR Selecao = FILTER('Valores', RELATED('Medição'[numero]) = Corte)\n"
               if column != "val_med" else "VAR Selecao = 'Valores'\n")
        MEASURES["Valores"][f"{label} {kind}"] = (cut + f"""VAR Servicos = FILTER(Selecao, RELATED('Item'[tipo]) = "folha")
            RETURN IF(COUNTROWS(FILTER(Servicos, NOT ISBLANK('Valores'[{field}]))) = COUNTROWS(Servicos),
                SUMX(Servicos, 'Valores'[{field}]))""", MONEY)
    MEASURES["Valores"][f"Δ {label.lower()}"] = (f"""IF(NOT ISBLANK([{label} original]) &&
        NOT ISBLANK([{label} recalculado]), [{label} original] - [{label} recalculado])""", MONEY)

MEASURES["Valores"]["Serviços sem par medido"] = ("""COALESCE(COUNTROWS(FILTER('Valores',
    RELATED('Item'[tipo]) = "folha" &&
    (ISBLANK('Valores'[val_med]) || ISBLANK('Valores'[val_med_recalc])))), 0)""", COUNT)


def write_model(root: Path, server: str, database: str, write_json):
    root.mkdir()
    definition = root / "definition"
    tables = definition / "tables"
    tables.mkdir(parents=True)
    write_json(root / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {},
    })
    (definition / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1604\n", encoding="utf-8")
    refs = "".join(f"\nref table '{table}'" for table in TABLES)
    (definition / "model.tmdl").write_text(
        "model Model\n\tculture: pt-BR\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: pt-BR\n\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n"
        + refs + "\n", encoding="utf-8")
    relationships = []
    for number, (fact, key, dim, target) in enumerate(RELATIONSHIPS, 1):
        relationships.append(
            f"relationship 19378efe-e3ba-4045-a54c-{number:012d}\n"
            f"\tfromColumn: '{fact}'.{key}\n\ttoColumn: '{dim}'.{target}\n")
    (definition / "relationships.tmdl").write_text("\n".join(relationships), encoding="utf-8")

    def m_text(value):
        return '"' + value.replace('"', '""') + '"'

    for table, (source, columns) in TABLES.items():
        elements = [f"table '{table}'"]
        for name, (dax, fmt) in MEASURES.get(table, {}).items():
            expression = "\n".join("\t\t\t" + line.strip() for line in dax.splitlines())
            elements.append(f"\tmeasure '{name}' =\n{expression}" + (f"\n\t\tformatString: {fmt}" if fmt else ""))
        for name, (_, dtype) in columns.items():
            text = f"\tcolumn '{name}'\n\t\tdataType: {dtype}\n\t\tsummarizeBy: none\n\t\tsourceColumn: {name}"
            if name == "Medição":
                text += "\n\t\tsortByColumn: numero"
            if name == "Data":
                text += "\n\t\tformatString: dd/MM/yyyy"
            if name == name.lower():
                text += "\n\t\tisHidden"
            elements.append(text)
        steps = [f"Source = Sql.Database({m_text(server)}, {m_text(database)}, [CreateNavigationProperties = false])",
                 f'Gold = Source{{[Schema = "gold", Item = {m_text(source)}]}}[Data]']
        previous = "Gold"
        for index, (name, expression, m_type) in enumerate(REPORT_COLUMNS.get(table, ())):
            step = f"Derived{index}"
            steps.append(f"{step} = Table.AddColumn({previous}, {m_text(name)}, "
                         f"each {' '.join(expression.split())}, type {m_type})")
            previous = step
        selected = ", ".join(m_text(original) for original, _ in columns.values())
        steps.append(f"Columns = Table.SelectColumns({previous}, {{{selected}}})")
        previous = "Columns"
        renames = ", ".join(f"{{{m_text(original)}, {m_text(name)}}}"
                            for name, (original, _) in columns.items() if original != name)
        if renames:
            steps.append(f"Aliased = Table.RenameColumns(Columns, {{{renames}}})")
            previous = "Aliased"
        currency = ", ".join(f"{{{m_text(name)}, Currency.Type}}"
                             for name, (_, dtype) in columns.items() if dtype == M)
        if currency:
            steps.append(f"Amounts = Table.TransformColumnTypes({previous}, {{{currency}}})")
            previous = "Amounts"
        m = ",\n\t\t\t    ".join(steps)
        elements.append(f"\tpartition '{table}' = m\n\t\tmode: import\n\t\tsource =\n"
                        f"\t\t\tlet {m}\n\t\t\tin {previous}")
        (tables / f"{table}.tmdl").write_text("\n\n".join(elements) + "\n", encoding="utf-8")
