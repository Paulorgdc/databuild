"""Install the project-local Windows runtime for the Silver pipeline."""

import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"
JAVA_HOME = RUNTIME / "jdk17"
JDBC_NAME = "mssql-jdbc-13.6.0.jre11"
JDBC_JAR = RUNTIME / f"{JDBC_NAME}.jar"
JDK_NAME = "amazon-corretto-17-x64-windows-jdk.zip"
CORRETTO = "https://corretto.aws/downloads/"
MAVEN = (
    "https://repo.maven.apache.org/maven2/com/microsoft/sqlserver/"
    f"mssql-jdbc/13.6.0.jre11/{JDBC_NAME}.jar"
)


def retrieve(url, destination, expected, algorithm):
    with urllib.request.urlopen(url, timeout=90) as response, destination.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    with destination.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != expected.lower():
        raise RuntimeError("Downloaded artifact failed checksum verification")


def main():
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("This installer targets Windows x64")
    RUNTIME.mkdir(exist_ok=True)
    if not (JAVA_HOME / "bin" / "java.exe").is_file():
        with urllib.request.urlopen(CORRETTO + "latest_sha256/" + JDK_NAME, timeout=90) as response:
            checksum = response.read().decode("ascii").split()[0]
        with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
            tmp = Path(tmp)
            download = tmp / "jdk.zip"
            retrieve(CORRETTO + "latest/" + JDK_NAME, download, checksum, "sha256")
            with zipfile.ZipFile(download) as archive:
                archive.extractall(tmp / "java")
            shutil.copytree(next((tmp / "java").iterdir()), JAVA_HOME, dirs_exist_ok=True)
    if not JDBC_JAR.is_file():
        with urllib.request.urlopen(MAVEN + ".sha1", timeout=90) as response:
            checksum = response.read().decode("ascii").split()[0]
        with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
            download = Path(tmp) / JDBC_JAR.name
            retrieve(MAVEN, download, checksum, "sha1")
            shutil.copyfile(download, JDBC_JAR)
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-silver.txt")], check=True)
    subprocess.run([str(JAVA_HOME / "bin" / "java.exe"), "-version"], check=True)
    subprocess.run([str(python), "-c", "import pyspark, pandas; print(pyspark.__version__, pandas.__version__)"],
                   check=True)


if __name__ == "__main__":
    main()
