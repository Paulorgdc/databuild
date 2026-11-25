"""Reconcile Gold and a refreshed Power BI Desktop model. Print no case values."""
import argparse
import json
import subprocess
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from dashboard.build import ROOT
from dashboard.model import MEASURES
from scripts.db import carregar_env, conectar

REPORT_BASELINE = (6, 306, 111, 91, 47, 40, 17, 62, 2570, 2568, 2)
COUNT_COLUMNS = (
    "Medições", "Total de ocorrências", "Erros primários", "Cascatas", "Suspeitas",
    "Exceções legítimas", "Validações contratuais", "Impactos contabilizados",
    "Campos pendentes", "Aguardando dados", "Aguardando contrato",
)


def sql_expected(connection, med=None, date=None, parent=None, service=None, issue_class=None):
    args = (med, date, parent, service, issue_class)
    metrics = {
        "Medições": "SELECT COUNT(*) FROM Summary",
        "Medido oficial": "SELECT CASE WHEN COUNT(valor_medido_mes) = COUNT(*) THEN SUM(valor_medido_mes) END FROM Summary",
        "Itens de serviço": "SELECT COUNT(*) FROM Services",
        "Serviços sem par medido": "SELECT COUNT(*) FROM Services WHERE val_med IS NULL OR val_med_recalc IS NULL",
        "Total de ocorrências": "SELECT COUNT(*) FROM Issues",
        "Erros primários": "SELECT COUNT(*) FROM Issues WHERE classe = 'comprovado' AND causa = 'primaria'",
        "Cascatas": "SELECT COUNT(*) FROM Issues WHERE causa = 'cascata'",
        "Suspeitas": "SELECT COUNT(*) FROM Issues WHERE classe = 'suspeita'",
        "Exceções legítimas": "SELECT COUNT(*) FROM Issues WHERE classe = 'excecao_legitima'",
        "Validações contratuais": "SELECT COUNT(*) FROM Issues WHERE classe = 'depende_validacao_contratual'",
        "Impactos contabilizados": "SELECT COUNT(*) FROM Booked",
        "Impacto financeiro comprovado": "SELECT SUM(impacto_contabilizado) FROM Booked",
        "Primários críticos ou altos": """SELECT COUNT(*) FROM Issues WHERE classe = 'comprovado'
            AND causa = 'primaria' AND severidade IN ('critica', 'alta')""",
        "Campos pendentes": "SELECT COUNT(*) FROM Pending",
        "Aguardando dados": "SELECT COUNT(*) FROM Pending WHERE situacao = 'pendente_dados'",
        "Aguardando contrato": "SELECT COUNT(*) FROM Pending WHERE situacao = 'pendente_contratual'",
    }
    for label, column in (("Contrato", "contrato_total"), ("Acumulado", "valor_acumulado"), ("Saldo", "saldo")):
        metrics[f"{label} oficial no corte"] = f"""SELECT CASE WHEN COUNT({column}) = COUNT(*)
            THEN SUM({column}) END FROM Summary WHERE numero = (SELECT MAX(numero) FROM Summary)"""
    for label, column in (("Contrato", "valor_contrato"), ("Medido", "val_med"),
                          ("Acumulado", "val_ac"), ("Saldo", "saldo")):
        scope = "Services" if column == "val_med" else "ServicesAtCut"
        for kind, field in (("original", column), ("recalculado", column + "_recalc")):
            metrics[f"{label} {kind}"] = f"SELECT CASE WHEN COUNT({field}) = COUNT(*) THEN SUM({field}) END FROM {scope}"
        metrics[f"Δ {label.lower()}"] = f"""SELECT CASE WHEN COUNT({column}) = COUNT(*) AND
            COUNT({column}_recalc) = COUNT(*) THEN SUM({column} - {column}_recalc) END FROM {scope}"""
    temps = ",\n".join(f"({query}) AS [{name}]" for name, query in metrics.items())
    row = connection.execute(f"""
        WITH Filters AS (
            SELECT CAST(? AS int) med, CAST(? AS int) date_key, CAST(? AS nvarchar(40)) parent,
                   CAST(? AS int) service, CAST(? AS nvarchar(40)) issue_class
        ), Measurements AS (
            SELECT m.medicao_key, m.numero FROM gold.dim_medicao m CROSS JOIN Filters f
            WHERE (f.med IS NULL OR m.numero = f.med)
              AND (f.date_key IS NULL OR m.periodo_fim_key = f.date_key)
        ), Items AS (
            SELECT i.item_key, i.tipo FROM gold.dim_item i CROSS JOIN Filters f
            WHERE (f.service IS NULL OR i.item_key = f.service)
              AND (f.parent IS NULL OR (i.tipo = 'folha' AND i.codigo_pai = f.parent)
                   OR (i.tipo IN ('secao','lote') AND i.codigo = f.parent))
        ), Summary AS (
            SELECT m.numero, r.* FROM gold.fact_medicao r JOIN Measurements m ON m.medicao_key = r.medicao_key
        ), Services AS (
            SELECT m.numero, v.* FROM gold.fact_valor_item v
            JOIN Measurements m ON m.medicao_key = v.medicao_key
            JOIN Items i ON i.item_key = v.item_key WHERE i.tipo = 'folha'
        ), ServicesAtCut AS (
            SELECT * FROM Services WHERE numero = (
                SELECT MAX(m.numero) FROM gold.fact_valor_item v
                JOIN Measurements m ON m.medicao_key = v.medicao_key)
        ), Issues AS (
            SELECT o.*, c.classe, a.causa, s.severidade FROM gold.fact_ocorrencia o
            JOIN Measurements m ON m.medicao_key = o.medicao_key JOIN Items i ON i.item_key = o.item_key
            JOIN gold.dim_classe c ON c.classe_key = o.classe_key
            JOIN gold.dim_causa a ON a.causa_key = o.causa_key
            JOIN gold.dim_severidade s ON s.severidade_key = o.severidade_key
            CROSS JOIN Filters f WHERE f.issue_class IS NULL OR c.classe = f.issue_class
        ), Booked AS (
            SELECT * FROM Issues WHERE contabiliza_impacto = 1 AND classe = 'comprovado' AND causa = 'primaria'
        ), Pending AS (
            SELECT p.* FROM gold.fact_pendencia_valor p
            JOIN Measurements m ON m.medicao_key = p.medicao_key JOIN Items i ON i.item_key = p.item_key
        )
        SELECT {temps}
    """, args).fetchone()
    result = dict(zip(metrics, row, strict=True))
    if result["Medições"] == 0:
        priority = None
    elif result["Primários críticos ou altos"]:
        priority = "Alta · erro comprovado"
    elif result["Validações contratuais"] or result["Aguardando contrato"]:
        priority = "Revisar contrato"
    elif result["Campos pendentes"]:
        priority = "Completar dados"
    elif result["Total de ocorrências"] > result["Exceções legítimas"]:
        priority = "Revisar ocorrências"
    else:
        priority = "Sem ação prioritária"
    result["Prioridade de revisão"] = priority
    return result


def dax_string(value):
    return '"' + str(value).replace('"', '""') + '"'


def dax_query(scopes, names):
    rows = []
    for index, filters in enumerate(scopes):
        fields = []
        for name in names:
            value = f"[{name}]"
            if filters:
                value = f"CALCULATE({value}, {', '.join(filters)})"
            fields.extend((dax_string(name), value))
        rows.append(f'ROW("Context", {index}, {", ".join(fields)})')
    return "EVALUATE UNION(\n" + ",\n".join(rows) + "\n)"


def reconcile(connection, port, desktop_bin, baseline):
    measurements = connection.execute("""
        SELECT m.numero, m.rotulo, m.periodo_fim_key FROM gold.fact_medicao f
        JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key ORDER BY m.numero
    """).fetchall()
    args = [{}]
    filters = [[]]
    for number, label, _ in measurements:
        args.append({"med": number})
        filters.append([f"TREATAS({{{dax_string(label)}}}, 'Medição'[Medição])"])
    if measurements:
        date = measurements[-1][2]
        args.append({"date": date})
        filters.append([f"TREATAS({{{date}}}, 'Calendário'[data_key])"])
    item = connection.execute("""
        SELECT TOP (1) i.item_key, i.codigo_pai, CONCAT(i.codigo_pai, N' · ', i.descricao_pai),
               CONCAT(i.codigo, N' · ', i.descricao)
        FROM gold.dim_item i
        WHERE i.tipo = 'folha' AND i.codigo_pai IS NOT NULL AND EXISTS (
            SELECT 1 FROM gold.fact_valor_item v WHERE v.item_key = i.item_key)
        ORDER BY i.item_key
    """).fetchone()
    if item:
        args.extend(({"parent": item[1]}, {"service": item[0]}))
        filters.extend((
            [f"TREATAS({{{dax_string(item[2])}}}, 'Item'[Categoria])"],
            [f"TREATAS({{{dax_string(item[3])}}}, 'Item'[Serviço])"],
        ))
    args.extend(({"issue_class": "suspeita"}, {"issue_class": "comprovado"}, {"med": -32768}))
    filters.extend((
        ['TREATAS({"suspeita"}, \'Classe\'[classe_codigo])'],
        ['TREATAS({"comprovado"}, \'Classe\'[classe_codigo])'],
        ["TREATAS({-32768}, 'Medição'[numero])"],
    ))
    expected = [sql_expected(connection, **arg) for arg in args]
    names = list(expected[0])
    if set(names) != {name for measures in MEASURES.values() for name in measures}:
        raise ValueError("Reconciliation does not cover all measures.")
    if baseline and tuple(expected[0][name] for name in COUNT_COLUMNS) != REPORT_BASELINE:
        raise ValueError("The six-measurement SQL baseline differs from the audited Stage 07 counts.")
    invalid = connection.execute("""
        SELECT COUNT(*) FROM gold.fact_ocorrencia o
        JOIN gold.dim_classe c ON c.classe_key = o.classe_key
        JOIN gold.dim_causa a ON a.causa_key = o.causa_key
        WHERE o.contabiliza_impacto = 1 AND (c.classe <> 'comprovado' OR a.causa <> 'primaria')
    """).fetchone()[0]
    if invalid:
        raise ValueError("Gold contains a booked impact outside proven primary findings.")
    for index, (number, _, _) in enumerate(measurements, 1):
        row = connection.execute("""
            SELECT ocorrencias, comprovadas, cascatas, suspeitas, excecoes, contratuais,
                   impactos_contabilizados, valores_pendentes_dados, valores_pendentes_contrato
            FROM gold.vw_qualidade_medicao WHERE numero = ?
        """, number).fetchone()
        original = expected[index]
        checks = (original["Total de ocorrências"], original["Erros primários"] + original["Cascatas"],
                  *(original[n] for n in COUNT_COLUMNS[3:8]),
                  original["Aguardando dados"], original["Aguardando contrato"])
        if row is None or tuple(row) != checks:
            raise ValueError("A measurement differs from the Gold quality view.")
    print(f"SQL: {len(measurements)} measurements, {len(names)} measures, {len(args)} contexts.")
    if port is None:
        return
    with TemporaryDirectory(prefix="databuild-dax-") as temp:
        dax_path = Path(temp) / "audit.dax"
        dax_path.write_text(dax_query(filters, names), encoding="utf-8")
        command = ["powershell.exe", "-NoProfile", "-File", str(ROOT / "query-desktop.ps1"),
                   "-Port", str(port), "-QueryFile", str(dax_path)]
        if desktop_bin:
            command.extend(("-DesktopBin", desktop_bin))
        result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError("The local Desktop DAX query failed. Verify the port and the refreshed model.")
    actual = json.loads(result.stdout.decode("utf-8", errors="replace"))
    indexed = {int(row[0]): row[1:] for row in actual}
    if set(indexed) != set(range(len(expected))) or len(actual) != len(expected):
        raise ValueError("Unexpected context count in the Desktop response.")
    if expected[0]["Medições"] and indexed[0][names.index("Medições")] == "0":
        raise ValueError("The Desktop model is empty. Refresh the Gold source before reconciling.")
    differences = []
    for index, wanted in enumerate(expected):
        for name, got in zip(names, indexed[index], strict=True):
            value = wanted[name]
            if isinstance(value, (int, Decimal)) and got is not None:
                got = Decimal(got)
            if value != got:
                differences.append(f"{index}/{name}")
    if differences:
        raise ValueError(f"DAX differs from SQL ({len(differences)}): {', '.join(differences)}")
    print(f"Desktop: {len(names) * len(args)} DAX results match SQL, including blanks and zeroes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="Local Analysis Services port of the refreshed Desktop project")
    parser.add_argument("--desktop-bin", help="Power BI Desktop installation bin directory")
    parser.add_argument("--baseline", action="store_true", help="Require the audited six-measurement counts")
    args = parser.parse_args()
    carregar_env()
    conn = None
    try:
        conn = conectar("powerbi", autocommit=True)
        reconcile(conn, args.port, args.desktop_bin, args.baseline)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except Exception as exc:
        parser.exit(1, f"Reconciliation could not finish ({type(exc).__name__}).\n")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
