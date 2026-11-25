#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then
    echo "Run this script as the unprivileged Airflow account." >&2
    exit 1
fi
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
airflow_version=3.4.0
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ "$python_version" != 3.12 ]]; then
    echo "This installation uses the Ubuntu 24.04 Python 3.12 runtime." >&2
    exit 1
fi

python3 -m venv "$airflow_runtime/airflow-venv"
python3 -m venv "$airflow_runtime/pipeline-venv"
"$airflow_runtime/airflow-venv/bin/python" -m pip install --disable-pip-version-check \
    "apache-airflow[postgres]==$airflow_version" \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-$airflow_version/constraints-$python_version.txt"
"$DATABUILD_PIPELINE_PYTHON" -m pip install --disable-pip-version-check -r "$project_root/requirements-silver.txt"
if [[ ! -s "$DATABUILD_JDBC_JAR" ]]; then
    jar=mssql-jdbc-13.6.0.jre11.jar
    expected_jar="$project_root/.runtime/$jar"
    if [[ -s "$expected_jar" ]]; then
        cp -- "$expected_jar" "$DATABUILD_JDBC_JAR"
    else
        curl -fsSL "https://repo.maven.apache.org/maven2/com/microsoft/sqlserver/mssql-jdbc/13.6.0.jre11/$jar" \
            -o "$DATABUILD_JDBC_JAR.download"
        mv -- "$DATABUILD_JDBC_JAR.download" "$DATABUILD_JDBC_JAR"
    fi
fi
"$airflow_runtime/airflow-venv/bin/python" -m pip check
"$DATABUILD_PIPELINE_PYTHON" -m pip check
airflow db migrate
