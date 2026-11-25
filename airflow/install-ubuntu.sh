#!/usr/bin/env bash
set -euo pipefail

# Run as root in Ubuntu 24.04. Airflow itself runs under an unprivileged Linux account.
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 || $EUID != 0 ]]; then
    echo "Run this script as root in Ubuntu 24.04." >&2
    exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl iproute2 \
    openjdk-17-jdk-headless postgresql-16 python3-venv unixodbc
if ! dpkg-query -W -f='${db:Status-Status}' msodbcsql18 2>/dev/null | grep -qx installed; then
    repo_package=$(mktemp --suffix=.deb)
    trap 'unlink "$repo_package"' EXIT
    curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb -o "$repo_package"
    dpkg -i "$repo_package"
    apt-get update
    ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y msodbcsql18
fi

if ! id databuild_airflow >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash databuild_airflow
fi
service postgresql start
if ! runuser -u postgres -- psql -XAtqc "SELECT 1 FROM pg_roles WHERE rolname='databuild_airflow'" | grep -qx 1; then
    runuser -u postgres -- createuser databuild_airflow
fi
if ! runuser -u postgres -- psql -XAtqc "SELECT 1 FROM pg_database WHERE datname='databuild_airflow'" | grep -qx 1; then
    runuser -u postgres -- createdb --owner=databuild_airflow databuild_airflow
fi
