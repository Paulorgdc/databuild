"""Author the four PBIR pages from presentation metadata and field bindings."""
import hashlib

from dashboard.model import MEASURES

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report"
NAVY = "#122640"
INK = "#162C47"
TEAL = "#087F78"
BLUE = "#225E9C"
MUTED = "#4B647B"
PAPER = "#EDF3F7"
WHITE = "#FFFFFF"
EDGE = "#D5E0E9"
W, H = 1360, 820

PAGES = (
    ("overview", "Visão geral", "Medições publicadas e qualidade dos dados"),
    ("finance", "Análise financeira", "Comparação no grão de serviço • Δ = original menos recalculado"),
    ("findings", "Inconsistências", "Erros comprovados, efeitos em cascata e casos que exigem revisão"),
    ("review", "Auditoria e decisão", "Priorize erros de alta severidade e acompanhe o que ainda depende de validação"),
)


def ident(value):
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def expr(value):
    if isinstance(value, bool):
        value = str(value).lower()
    elif isinstance(value, int):
        value = f"{value}D"
    else:
        value = "'" + value.replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": value}}}


def color(value):
    return {"solid": {"color": expr(value)}}


def style(**properties):
    return [{"properties": properties}]


def schema(kind, version="1.0.0"):
    return f"{SCHEMA}/definition/{kind}/{version}/schema.json"


def field(table, name):
    return (table, name)


def projection(binding, source=None):
    table, name = binding
    kind = "Measure" if name in MEASURES.get(table, {}) else "Column"
    ref = {"Entity": table} if source is None else {"Source": source}
    return {
        "field": {kind: {"Expression": {"SourceRef": ref}, "Property": name}},
        "queryRef": f"{table}.{name}", "nativeQueryRef": name,
    }


class Page:
    def __init__(self, root, key, title, description, write_json):
        self.root = root
        self.key = key
        self.write_json = write_json
        self.index = 0
        write_json(root / "page.json", {
            "$schema": schema("page"), "name": ident(key), "displayName": title,
            "displayOption": "FitToPage", "width": W, "height": H,
            "objects": {"background": style(color=color(PAPER), transparency=expr(0))},
        })
        self.banner("header", (0, 0, W, 120), NAVY, [
            ("DataBuild  /  Construtora Horizontes", 15, "#A5DCD5"),
            (title, 31, WHITE), (description, 15, "#D7E5EF"),
        ])
        self.visual("navigation", "pageNavigator", (24, 128, W - 48, 41), objects={
            "layout": style(orientation=expr("horizontal")),
        }, chrome=False)

    def visual(self, key, kind, bounds, *, title=None, query=None, objects=None,
               chrome=True, background=WHITE, alt=None, descending=None, require_rows=None):
        name = ident(f"{self.key}/{key}")
        self.index += 1
        x, y, w, h = bounds
        visual = {"visualType": kind}
        if query:
            visual["query"] = {"queryState": {
                role: {"projections": [projection(b) for b in bindings]} for role, bindings in query.items()
            }}
            if descending:
                visual["query"]["sortDefinition"] = {
                    "sort": [{"field": projection(descending)["field"], "direction": "Descending"}],
                    "isDefaultSort": False,
                }
        if objects:
            visual["objects"] = objects
        container = {
            "background": style(show=expr(chrome), color=color(background), transparency=expr(0)),
            "border": style(show=expr(chrome and background == WHITE), color=color(EDGE), radius=expr(6)),
            "padding": style(**{side: expr(8 if kind == "slicer" else 12 if chrome else 4)
                               for side in ("top", "bottom", "left", "right")}),
        }
        if title:
            container["title"] = style(show=expr(True), text=expr(title), fontColor=color(INK),
                                        fontFamily=expr("Segoe UI Semibold"), fontSize=expr(12))
        if alt or title:
            container["general"] = style(altText=expr(alt or title))
        visual["visualContainerObjects"] = container
        payload = {
            "$schema": schema("visualContainer", "2.9.0"), "name": name,
            "position": {"x": x, "y": y, "z": self.index * 1000, "width": w, "height": h,
                         "tabOrder": self.index * 1000},
            "visual": visual,
        }
        if require_rows:
            payload["filterConfig"] = {"filters": [{
                "name": "Filter" + name, "field": projection(require_rows)["field"],
                "type": "Advanced", "howCreated": "User", "isHiddenInViewMode": True,
                "filter": {"Version": 2, "From": [{"Name": "f", "Entity": require_rows[0], "Type": 0}],
                           "Where": [{"Condition": {"Comparison": {
                               "ComparisonKind": 1,
                               "Left": projection(require_rows, source="f")["field"],
                               "Right": {"Literal": {"Value": "0D"}},
                           }}}]},
            }]}
        self.write_json(self.root / "visuals" / name / "visual.json", payload)

    def banner(self, key, bounds, background, lines):
        self.visual(key, "textbox", bounds, background=background, objects={
            "general": style(paragraphs=[{"textRuns": [{"value": text, "textStyle": {
                "fontFamily": "Segoe UI Semibold", "fontSize": f"{size}px", "color": fg,
            }}]} for text, size, fg in lines]),
        })

    def filters(self, *columns):
        width = (W - 48 - 12 * (len(columns) - 1)) // len(columns)
        for index, binding in enumerate(columns):
            self.visual(f"filter-{index}", "slicer", (24 + index * (width + 12), 180, width, 80),
                        query={"Values": [binding]}, alt=f"Filtrar por {binding[1]}",
                        objects={"data": style(mode=expr("Dropdown")),
                                 "header": style(show=expr(True), fontColor=color(INK))})

    def cards(self, *measures):
        width = (W - 48 - 12 * (len(measures) - 1)) // len(measures)
        for index, (binding, label) in enumerate(measures):
            self.visual(f"card-{index}", "card", (24 + index * (width + 12), 274, width, 104),
                        title=label, query={"Values": [binding]}, objects={
                            "labels": style(color=color(TEAL), fontSize=expr(24), labelDisplayUnits=expr(0)),
                            "categoryLabels": style(show=expr(False)),
                        })

    def table(self, key, bounds, title, *columns, descending=None, require_rows):
        self.visual(key, "tableEx", bounds, title=title, query={"Values": columns},
                    descending=descending, require_rows=require_rows, objects={
                        "columnHeaders": style(autoSizeColumnWidth=expr(True), columnAdjustment=expr("growToFit"),
                                               backColor=color(NAVY), fontColor=color(WHITE), fontSize=expr(10)),
                        "values": style(backColorPrimary=color(WHITE), backColorSecondary=color("#F3F7FA"),
                                        fontColorPrimary=color(INK), fontColorSecondary=color(INK), fontSize=expr(10)),
                    })

    def chart(self, key, kind, bounds, title, category, *series, descending=None, require_rows):
        self.visual(key, kind, bounds, title=title, query={"Category": [category], "Y": series},
                    descending=descending, require_rows=require_rows, objects={
                        "categoryAxis": style(showAxisTitle=expr(False)),
                        "valueAxis": style(showAxisTitle=expr(False), labelDisplayUnits=expr(0)),
                        "legend": style(show=expr(len(series) > 1)),
                        "dataPoint": style(defaultColor=color(BLUE)),
                    })


def write_report(root, write_json):
    write_json(root / "definition.pbir", {
        "$schema": f"{SCHEMA}/definitionProperties/2.0.0/schema.json", "version": "4.0",
        "datasetReference": {"byPath": {"path": "../DataBuild.SemanticModel"}},
    })
    definition = root / "definition"
    write_json(definition / "version.json", {"$schema": schema("versionMetadata"), "version": "2.0.0"})
    write_json(definition / "report.json", {
        "$schema": schema("report"), "layoutOptimization": "None", "themeCollection": {
            "baseTheme": {"name": "CY24SU06", "reportVersionAtImport": "5.55", "type": "SharedResources"},
        },
    })
    pages = definition / "pages"
    order = [ident(key) for key, _, _ in PAGES]
    write_json(pages / "pages.json", {"$schema": schema("pagesMetadata"), "pageOrder": order, "activePageName": order[0]})
    overview, finance, findings, review = [
        Page(pages / ident(key), key, title, desc, write_json) for key, title, desc in PAGES
    ]
    med = field("Medição", "Medição")
    date = field("Calendário", "Data")
    category = field("Item", "Categoria")
    service = field("Item", "Serviço")
    code = field("Item", "Código")
    class_ = field("Classe", "Classe")
    sev = field("Severidade", "Severidade")
    rule = field("Regra", "Regra")

    def summary(name): return field("Resumo", name)
    def values(name): return field("Valores", name)
    def issue(name): return field("Ocorrências", name)
    def pending(name): return field("Pendências", name)

    total = issue("Total de ocorrências")
    primary = issue("Erros primários")
    cascades = issue("Cascatas")
    contract = issue("Validações contratuais")
    impacts = issue("Impactos contabilizados")
    financial_impact = issue("Impacto financeiro comprovado")
    measurements = summary("Medições")
    service_count = values("Itens de serviço")
    pending_count = pending("Campos pendentes")

    overview.filters(med, date)
    overview.cards((summary("Medições"), "Medições publicadas"),
                   (summary("Medido oficial"), "Medido oficial no período"),
                   (total, "Ocorrências registradas"), (primary, "Erros primários"),
                   (impacts, "Impactos contabilizados"))
    overview.chart("volume", "clusteredColumnChart", (24, 396, 508, 378),
                   "Primários e cascatas por medição", med, primary, cascades, require_rows=measurements)
    overview.table("rollup", (544, 396, 792, 378), "Resumo por medição",
                   med, summary("Medido oficial"), summary("Acumulado oficial no corte"),
                   summary("Saldo oficial no corte"), total, pending_count, require_rows=measurements)

    finance.filters(med, category, service)
    finance.cards((values("Medido original"), "Medido original"),
                  (values("Medido recalculado"), "Medido recalculado"),
                  (values("Δ medido"), "Diferença pareada"),
                  (values("Serviços sem par medido"), "Serviços sem par"))
    finance.table("flows", (24, 396, 1312, 177), "Fluxo medido e contrato por serviço",
                  med, category, service, values("Medido original"), values("Medido recalculado"),
                  values("Δ medido"), values("Contrato original"), values("Contrato recalculado"),
                  values("Δ contrato"), service_count, require_rows=service_count)
    finance.table("snapshots", (24, 585, 1312, 189), "Acumulado e saldo no último corte do filtro",
                  med, category, service, values("Acumulado original"), values("Acumulado recalculado"),
                  values("Δ acumulado"), values("Saldo original"), values("Saldo recalculado"), values("Δ saldo"),
                  service_count, require_rows=service_count)
    finance.banner("nulls", (24, 780, 1312, 32), PAPER, [
        ("Em branco indica falta de valor comparável. Totais financeiros por serviço exigem todos os valores do recorte.", 13, MUTED),
    ])

    findings.filters(med, category, service, class_, sev, rule)
    findings.cards((primary, "Erros primários"), (cascades, "Cascatas"), (issue("Suspeitas"), "Suspeitas"),
                   (issue("Exceções legítimas"), "Exceções legítimas"), (contract, "Validações contratuais"))
    findings.chart("severity", "clusteredBarChart", (24, 396, 334, 176),
                   "Ocorrências por severidade", sev, total, descending=total, require_rows=total)
    findings.chart("rules", "clusteredBarChart", (24, 584, 334, 190),
                   "Ocorrências por regra", rule, total, descending=total, require_rows=total)
    findings.table("register", (370, 396, 966, 378), "Registro de ocorrências",
                   med, code, issue("Ocorrência"), rule, class_, field("Causa", "Causa"), sev,
                   issue("Célula"), total, financial_impact, require_rows=total)

    review.filters(med, category, service, pending("Situação"))
    review.cards((pending("Aguardando dados"), "Campos aguardando dados"),
                 (pending("Aguardando contrato"), "Campos aguardando contrato"),
                 (contract, "Ocorrências contratuais"), (issue("Primários críticos ou altos"), "Primários críticos ou altos"))
    review.table("priorities", (24, 396, 1312, 151), "Fila de revisão por medição",
                 med, pending("Prioridade de revisão"), issue("Primários críticos ou altos"),
                 contract, pending("Aguardando dados"), pending("Aguardando contrato"),
                 impacts, financial_impact, descending=issue("Primários críticos ou altos"), require_rows=measurements)
    review.table("open-fields", (24, 559, 1312, 215), "Campos a revisar por medição e item",
                 med, code, category, service, pending("Campo"), pending("Situação"), pending_count,
                 require_rows=pending_count)
