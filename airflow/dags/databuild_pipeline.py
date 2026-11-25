"""DataBuild: Bronze incremental, Silver auditada e publicação Gold reconciliada."""

import json
import logging
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.sdk import dag, task

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def executar(script, *args):
    python = os.environ.get("DATABUILD_PIPELINE_PYTHON")
    if not python:
        raise AirflowFailException("Configure o Python da pipeline com airflow/env.sh.")
    r = subprocess.run(
        [python, "-B", str(ROOT / "scripts" / f"{script}.py"), *args],
        cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if r.returncode == 3 and script == "orquestrar":
        raise AirflowFailException(r.stderr.strip())
    if r.returncode:
        if script == "orquestrar":
            LOG.warning("%s", r.stderr.strip())
        raise RuntimeError(f"{script} {args[0]} falhou (código {r.returncode}). Consulte o status local da camada.")
    return json.loads(r.stdout)


@dag(
    dag_id="databuild_pipeline",
    start_date=pendulum.datetime(2026, 11, 25, tz="America/Cuiaba"),
    schedule=os.environ.get("DATABUILD_SCHEDULE") or None,
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    max_active_tasks=1,
    dagrun_timeout=timedelta(hours=2),
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
        "retry_exponential_backoff": True,
        "execution_timeout": timedelta(minutes=25),
    },
    tags=["databuild"],
)
def databuild_pipeline():
    @task(do_xcom_push=False)
    def identificar():
        resumo = executar("orquestrar", "discover")
        LOG.info("Entrada: %s", resumo)
        if not (resumo["novas"] or resumo["registradas"]):
            raise AirflowSkipException("Nenhuma medição disponível.")

    @task(do_xcom_push=False)
    def ingerir_bronze():
        resumo = executar("orquestrar", "bronze")
        LOG.info("Bronze: %s", resumo)

    @task
    def promover_silver():
        resumo = executar("silver", "run", "--json")
        LOG.info("Silver: %s", resumo)
        return resumo["execucao_id"]

    @task(do_xcom_push=False)
    def auditar_silver():
        LOG.info("Auditoria Silver: %s", executar("silver", "verify", "--json"))

    @task(do_xcom_push=False)
    def publicar_gold(execucao_id):
        LOG.info("Gold: %s", executar("gold", "run", "--execucao", execucao_id, "--json"))

    @task(do_xcom_push=False)
    def reconciliar_gold(execucao_id):
        resumo = executar("gold", "verify", "--json")
        if resumo["execucao_id"] != execucao_id:
            raise AirflowFailException("A reconciliação encontrou outra execução Silver.")
        LOG.info("Reconciliação Gold: %s", resumo)

    descoberta = identificar()
    bronze = ingerir_bronze()
    silver = promover_silver()
    auditoria = auditar_silver()
    gold = publicar_gold(silver)
    reconciliacao = reconciliar_gold(silver)
    descoberta >> bronze >> silver >> auditoria >> gold >> reconciliacao


pipeline = databuild_pipeline()
