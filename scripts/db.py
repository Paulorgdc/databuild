"""Configuração e conexão com o SQL Server (Etapa 03).

Credenciais vêm só do ambiente (`.env.local`, ignorado pelo Git; modelo em `.env.example`). Nada aqui
grava ou imprime senha: `Config` oculta segredos no `repr` e `redigido()` devolve a configuração sem eles.

Um único conjunto de parâmetros alimenta os consumidores previstos:
  - pyodbc (esta etapa)                 -> `connect()`
  - Pandas (`read_sql`/`to_sql`)        -> `sqlalchemy_url()` (SQLAlchemy 2 + pyodbc)
  - PySpark (JDBC, `mssql-jdbc`)        -> `jdbc()`
  - Apache Airflow (conexão ODBC)       -> `airflow_conn_uri()`

Três papéis de acesso: `admin` (DDL; autenticação do Windows por padrão), `pipeline` (lê e grava
bronze/silver/gold) e `powerbi` (somente leitura da Gold).
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, quote_plus

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.local"
PAPEIS = ("admin", "pipeline", "powerbi")
DRIVER_PADRAO = "ODBC Driver 18 for SQL Server"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# Variáveis lidas do ambiente (o `.env.example` documenta todas, sem valores sensíveis).
VARIAVEIS = (
    "DATABUILD_DB_SERVER", "DATABUILD_DB_NAME", "DATABUILD_DB_DRIVER", "DATABUILD_DB_ENCRYPT",
    "DATABUILD_DB_TRUST_SERVER_CERT", "DATABUILD_DB_ADMIN_AUTH", "DATABUILD_DB_ADMIN_USER",
    "DATABUILD_DB_ADMIN_PASSWORD", "DATABUILD_PIPELINE_USER", "DATABUILD_PIPELINE_PASSWORD",
    "DATABUILD_POWERBI_USER", "DATABUILD_POWERBI_PASSWORD",
)
SEGREDOS = ("DATABUILD_DB_ADMIN_PASSWORD", "DATABUILD_PIPELINE_PASSWORD", "DATABUILD_POWERBI_PASSWORD")


def identificador_valido(nome: str) -> str:
    """Nome de banco/login seguro para interpolar em T-SQL (apenas letras, dígitos e `_`)."""
    if not _IDENT.match(nome or ""):
        raise ValueError(f"identificador inválido: {nome!r}")
    return nome


def carregar_env(caminho: Path = ENV_FILE, sobrescrever: bool = False) -> dict[str, str]:
    """Lê `CHAVE=valor` de um arquivo .env e coloca em `os.environ` (sem sobrescrever o que já existe)."""
    lidos: dict[str, str] = {}
    if not Path(caminho).is_file():
        return lidos
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave, valor = chave.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        lidos[chave] = valor
        if sobrescrever or chave not in os.environ:
            os.environ[chave] = valor
    return lidos


def _bool(valor: str) -> bool:
    return str(valor).strip().lower() in ("1", "true", "yes", "sim", "y")


@dataclass(frozen=True)
class Config:
    servidor: str = "localhost,1433"          # host, host,porta ou host\instância
    banco: str = "databuild"
    driver: str = DRIVER_PADRAO
    encrypt: bool = True
    confiar_certificado: bool = True          # certificado autoassinado do SQL Server local
    admin_auth: str = "windows"               # windows | sql
    admin_user: str = field(default="", repr=False)
    admin_password: str = field(default="", repr=False)
    pipeline_user: str = "databuild_pipeline"
    pipeline_password: str = field(default="", repr=False)
    powerbi_user: str = "databuild_powerbi"
    powerbi_password: str = field(default="", repr=False)

    @classmethod
    def do_ambiente(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        g = lambda k, d="": e.get(k, d) or d
        cfg = cls(
            servidor=g("DATABUILD_DB_SERVER", cls.servidor),
            banco=g("DATABUILD_DB_NAME", cls.banco),
            driver=g("DATABUILD_DB_DRIVER", DRIVER_PADRAO),
            encrypt=_bool(g("DATABUILD_DB_ENCRYPT", "yes")),
            confiar_certificado=_bool(g("DATABUILD_DB_TRUST_SERVER_CERT", "yes")),
            admin_auth=g("DATABUILD_DB_ADMIN_AUTH", "windows").lower(),
            admin_user=g("DATABUILD_DB_ADMIN_USER"), admin_password=g("DATABUILD_DB_ADMIN_PASSWORD"),
            pipeline_user=g("DATABUILD_PIPELINE_USER", cls.pipeline_user),
            pipeline_password=g("DATABUILD_PIPELINE_PASSWORD"),
            powerbi_user=g("DATABUILD_POWERBI_USER", cls.powerbi_user),
            powerbi_password=g("DATABUILD_POWERBI_PASSWORD"))
        identificador_valido(cfg.banco)
        if cfg.admin_auth not in ("windows", "sql"):
            raise ValueError("DATABUILD_DB_ADMIN_AUTH deve ser 'windows' ou 'sql'")
        return cfg

    # --- servidor ---------------------------------------------------------------------------------
    @property
    def host(self) -> str:
        return re.split(r"[,\\]", self.servidor, maxsplit=1)[0]

    @property
    def porta(self) -> int | None:
        m = re.search(r",\s*(\d+)$", self.servidor)
        return int(m.group(1)) if m else None

    @property
    def instancia(self) -> str | None:
        return self.servidor.split("\\", 1)[1] if "\\" in self.servidor else None

    # --- credenciais por papel ---------------------------------------------------------------------
    def credenciais(self, papel: str) -> tuple[str, str] | None:
        """(usuário, senha) do papel; `None` para autenticação do Windows (somente `admin`)."""
        if papel == "admin":
            return None if self.admin_auth == "windows" else (self.admin_user, self.admin_password)
        if papel == "pipeline":
            return self.pipeline_user, self.pipeline_password
        if papel == "powerbi":
            return self.powerbi_user, self.powerbi_password
        raise ValueError(f"papel desconhecido: {papel!r} (use {PAPEIS})")

    def odbc(self, papel: str = "pipeline", banco: str | None = None) -> str:
        """String de conexão ODBC. CONTÉM SENHA: nunca registrar em log."""
        partes = [f"DRIVER={{{self.driver}}}", f"SERVER={self.servidor}", f"DATABASE={banco or self.banco}",
                  f"Encrypt={'yes' if self.encrypt else 'no'}",
                  f"TrustServerCertificate={'yes' if self.confiar_certificado else 'no'}"]
        cred = self.credenciais(papel)
        if cred is None:
            partes.append("Trusted_Connection=yes")
        else:
            partes += [f"UID={cred[0]}", f"PWD={{{cred[1].replace('}', '}}')}}}"]
        return ";".join(partes) + ";"

    # --- consumidores futuros -----------------------------------------------------------------------
    def sqlalchemy_url(self, papel: str = "pipeline") -> str:
        """URL SQLAlchemy 2 (`pandas.read_sql`, `DataFrame.to_sql`). Contém senha."""
        return "mssql+pyodbc:///?odbc_connect=" + quote_plus(self.odbc(papel))

    def jdbc(self, papel: str = "pipeline") -> dict[str, str]:
        """Parâmetros para `spark.read.format('jdbc')` (driver `com.microsoft.sqlserver.jdbc.SQLServerDriver`).
        A URL não leva credenciais; `user`/`password` vão em opções separadas. Contém senha."""
        cred = self.credenciais(papel)
        if cred is None:
            raise ValueError("JDBC sem usuário SQL não é suportado para o papel admin com autenticação do Windows")
        url = f"jdbc:sqlserver://{self.host}"
        if self.porta:
            url += f":{self.porta}"
        url += f";databaseName={self.banco};encrypt={'true' if self.encrypt else 'false'};"
        url += f"trustServerCertificate={'true' if self.confiar_certificado else 'false'}"
        if self.instancia:
            url += f";instanceName={self.instancia}"
        return {"url": url, "user": cred[0], "password": cred[1],
                "driver": "com.microsoft.sqlserver.jdbc.SQLServerDriver"}

    def airflow_conn_uri(self, papel: str = "pipeline") -> str:
        """URI de conexão do tipo `odbc` do Airflow. Contém senha."""
        cred = self.credenciais(papel)
        if cred is None:
            raise ValueError("Airflow exige usuário SQL; use o papel pipeline")
        host = self.host + (f":{self.porta}" if self.porta else "")
        extras = (f"driver={quote_plus(self.driver)}&Encrypt={'yes' if self.encrypt else 'no'}"
                  f"&TrustServerCertificate={'yes' if self.confiar_certificado else 'no'}")
        return f"odbc://{quote(cred[0], safe='')}:{quote(cred[1], safe='')}@{host}/{self.banco}?{extras}"

    def redigido(self) -> dict[str, str]:
        """Configuração sem segredos, própria para exibir."""
        return {"servidor": self.servidor, "banco": self.banco, "driver": self.driver,
                "encrypt": str(self.encrypt), "confiar_certificado": str(self.confiar_certificado),
                "admin_auth": self.admin_auth, "pipeline_user": self.pipeline_user,
                "powerbi_user": self.powerbi_user,
                "senhas_definidas": ",".join(k for k, v in (("admin", self.admin_password),
                                                          ("pipeline", self.pipeline_password),
                                                          ("powerbi", self.powerbi_password)) if v) or "nenhuma"}


def drivers_odbc() -> list[str]:
    import pyodbc
    return [d for d in pyodbc.drivers() if "SQL Server" in d]


def conectar(papel: str = "pipeline", config: Config | None = None, banco: str | None = None,
             autocommit: bool = False, timeout: int = 10):
    """Abre uma conexão pyodbc. Falha com mensagem clara se o driver ODBC configurado não está instalado."""
    import pyodbc
    cfg = config or Config.do_ambiente()
    if cfg.driver not in pyodbc.drivers():
        raise RuntimeError(f"driver ODBC {cfg.driver!r} não instalado (instalados: {drivers_odbc() or 'nenhum'}). "
                           "Use database/setup/install_local_sqlserver.ps1 no Windows ou airflow/install-ubuntu.sh no Ubuntu.")
    return pyodbc.connect(cfg.odbc(papel, banco), autocommit=autocommit, timeout=timeout)


def travar_sequencia(conn, modo: str, dono: str, timeout_ms: int) -> bool:
    """Coordena mudanças no manifesto Bronze com promoção Silver e publicação Gold."""
    return conn.execute("""
        DECLARE @rc INT;
        EXEC @rc = sp_getapplock @Resource = N'DataBuild.Silver', @LockMode = ?,
            @LockOwner = ?, @LockTimeout = ?;
        SELECT @rc;
    """, modo, dono, timeout_ms).fetchone()[0] >= 0


@contextmanager
def sessao(papel: str = "pipeline", config: Config | None = None, banco: str | None = None,
           autocommit: bool = False, timeout: int = 10):
    """Conexão que sempre fecha ao sair (o `with` do pyodbc só faz commit). Com `autocommit=False`, confirma
    ao sair sem erro e desfaz se houver exceção."""
    conn = conectar(papel, config, banco, autocommit, timeout)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
