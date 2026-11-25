#!/usr/bin/env bash
# Source this file from the Linux account running Airflow.
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
airflow_runtime="${DATABUILD_AIRFLOW_RUNTIME:-$HOME/.local/share/databuild-airflow}"
metadata_db="${DATABUILD_AIRFLOW_METADATA_DB:-databuild_airflow}"
if [[ ! "$metadata_db" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "Invalid PostgreSQL database name." >&2
    return 1
fi

umask 077
export AIRFLOW_HOME="${AIRFLOW_HOME:-$airflow_runtime/state}"
export AIRFLOW__CORE__DAGS_FOLDER="$project_root/airflow/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=2
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2:///$metadata_db?host=/var/run/postgresql"
export AIRFLOW__API__HOST=127.0.0.1
export AIRFLOW__API__PORT="${DATABUILD_AIRFLOW_PORT:-8080}"
export AIRFLOW__CORE__EXECUTION_API_SERVER_URL="http://127.0.0.1:${AIRFLOW__API__PORT}/execution/"
export PYTHONNOUSERSITE=1
export DATABUILD_PIPELINE_PYTHON="$airflow_runtime/pipeline-venv/bin/python"
export DATABUILD_JDBC_JAR="${DATABUILD_JDBC_JAR:-$airflow_runtime/mssql-jdbc-13.6.0.jre11.jar}"
if [[ -z "${JAVA_HOME:-}" ]]; then
    JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")
    export JAVA_HOME
fi
export PATH="$airflow_runtime/airflow-venv/bin:$PATH"
# A host override is useful only when a Linux route differs from the Windows .env.local.
if [[ -n "${DATABUILD_SQL_HOST:-}" ]]; then
    export DATABUILD_DB_SERVER="$DATABUILD_SQL_HOST,1433"
fi
mkdir -p "$AIRFLOW_HOME"
