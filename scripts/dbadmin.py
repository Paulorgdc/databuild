"""Administração do banco `databuild`: migrations versionadas, logins, verificação (Etapa 03).

    python scripts/dbadmin.py init-env     # cria .env.local com senhas geradas (nunca sobrescreve)
    python scripts/dbadmin.py migrate      # aplica database/migrations/*.sql pendentes (idempotente)
    python scripts/dbadmin.py provision    # cria/atualiza os logins pipeline e powerbi (senhas do ambiente)
    python scripts/dbadmin.py check        # testa conexões, schemas, integridade e permissões por papel
    python scripts/dbadmin.py status       # lista migrations aplicadas

As migrations são imutáveis depois de aplicadas (checksum SHA-256 registrado em dbo.schema_migration) e
cada script é idempotente (`IF NOT EXISTS`, `CREATE OR ALTER`), de modo que reexecutá-lo é seguro.
Nada aqui lê, grava ou exibe dados das medições reais.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import secrets
import string
import sys
from dataclasses import dataclass
from pathlib import Path

import db

MIGRATIONS = db.ROOT / "database" / "migrations"
PREFIXO_TESTE = "databuild_test_"
PAPEIS_BANCO = {"pipeline": "rl_databuild_pipeline", "powerbi": "rl_databuild_powerbi_reader"}
_GO = re.compile(r"^\s*GO\s*(?:--.*)?$", re.IGNORECASE | re.MULTILINE)
_NOME_ARQUIVO = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
# A migration 010 contém alterações estruturais de aplicação única e seu checksum já é imutável.
_APLICACAO_UNICA = {"010_silver_processamento"}


@dataclass(frozen=True)
class Migration:
    versao: int
    nome: str
    caminho: Path
    sql: str
    checksum: str

    @property
    def reexecutavel(self) -> bool:
        return self.caminho.stem not in _APLICACAO_UNICA


def listar_migrations(pasta: Path = MIGRATIONS) -> list[Migration]:
    saida: list[Migration] = []
    for p in sorted(pasta.glob("*.sql")):
        m = _NOME_ARQUIVO.match(p.name)
        if not m:
            raise ValueError(f"nome de migration inválido: {p.name} (esperado NNN_nome_em_minusculas.sql)")
        texto = p.read_text(encoding="utf-8")
        saida.append(Migration(int(m.group(1)), m.group(2), p, texto, hashlib.sha256(texto.encode("utf-8")).hexdigest()))
    versoes = [m.versao for m in saida]
    if versoes != list(range(len(versoes))):
        raise ValueError(f"migrations devem ser numeradas sem lacunas a partir de 000: {versoes}")
    return saida


def dividir_lotes(sql: str) -> list[str]:
    """Divide o script nas linhas `GO` (separador do sqlcmd/SSMS) e descarta lotes vazios."""
    return [b.strip() for b in _GO.split(sql) if b.strip()]


def preparar(sql: str, banco: str) -> str:
    return sql.replace("$(DB_NAME)", db.identificador_valido(banco))


def _executar_lotes(conn, sql: str, banco: str) -> int:
    n = 0
    cur = conn.cursor()
    for lote in dividir_lotes(preparar(sql, banco)):
        cur.execute(lote)
        while cur.nextset():  # consome resultados auxiliares
            pass
        n += 1
    cur.close()
    return n


_TABELA_CONTROLE = """
IF OBJECT_ID(N'dbo.schema_migration', N'U') IS NULL
CREATE TABLE dbo.schema_migration (
    versao          INT          NOT NULL CONSTRAINT pk_schema_migration PRIMARY KEY,
    nome            NVARCHAR(200) NOT NULL,
    checksum_sha256 CHAR(64)     NOT NULL,
    aplicado_em     DATETIME2(0) NOT NULL CONSTRAINT df_schema_migration_em DEFAULT SYSUTCDATETIME(),
    aplicado_por    SYSNAME      NOT NULL CONSTRAINT df_schema_migration_por DEFAULT SUSER_SNAME()
)"""


def migrar(cfg: db.Config | None = None, reexecutar: bool = False, log=print) -> list[str]:
    """Aplica as migrations pendentes. `reexecutar=True` também roda os scripts reexecutáveis.
    Devolve os nomes das migrations executadas. Falha se uma migration já aplicada foi alterada."""
    cfg = cfg or db.Config.do_ambiente()
    migs = listar_migrations()
    executadas: list[str] = []

    with db.sessao("admin", cfg, banco="master", autocommit=True) as master:
        n = _executar_lotes(master, migs[0].sql, cfg.banco)
        log(f"  000 {migs[0].nome}: {n} lote(s) no master")

    with db.sessao("admin", cfg) as conn:
        cur = conn.cursor()
        cur.execute(_TABELA_CONTROLE)
        aplicadas = {r[0]: r[1] for r in cur.execute("SELECT versao, checksum_sha256 FROM dbo.schema_migration")}
        for m in migs:
            if m.versao in aplicadas and (not reexecutar or not m.reexecutavel):
                if aplicadas[m.versao] != m.checksum:
                    raise RuntimeError(f"migration {m.versao:03d}_{m.nome} foi alterada depois de aplicada; "
                                       "crie uma nova migration em vez de editar a antiga")
                continue
            if m.versao > 0:
                n = _executar_lotes(conn, m.sql, cfg.banco)
                log(f"  {m.versao:03d} {m.nome}: {n} lote(s)")
            cur.execute("DELETE FROM dbo.schema_migration WHERE versao = ?", m.versao)
            cur.execute("INSERT INTO dbo.schema_migration (versao, nome, checksum_sha256) VALUES (?, ?, ?)",
                        m.versao, m.nome, m.checksum)
            conn.commit()
            executadas.append(f"{m.versao:03d}_{m.nome}")
    return executadas


# --- logins ---------------------------------------------------------------------------------------------
def gerar_senha(tamanho: int = 28) -> str:
    """Senha aleatória que cumpre a política do Windows (maiúscula, minúscula, dígito, símbolo), sem `; { } = '`."""
    alfabeto = string.ascii_letters + string.digits
    while True:
        s = "".join(secrets.choice(alfabeto) for _ in range(tamanho - 2)) + secrets.choice("-_") + secrets.choice(".+")
        if (any(c.islower() for c in s) and any(c.isupper() for c in s) and any(c.isdigit() for c in s)):
            chars = list(s)
            secrets.SystemRandom().shuffle(chars)
            return "".join(chars)


def senha_valida(senha: str) -> bool:
    return len(senha) >= 16 and all(32 <= ord(c) < 127 for c in senha)


def _sql_str(valor: str) -> str:
    return "N'" + valor.replace("'", "''") + "'"


def provisionar(cfg: db.Config | None = None, log=print) -> None:
    """Cria (ou atualiza a senha de) os logins do pipeline e do Power BI e os coloca nos papéis do banco."""
    cfg = cfg or db.Config.do_ambiente()
    banco = db.identificador_valido(cfg.banco)
    for papel, role in PAPEIS_BANCO.items():
        usuario, senha = cfg.credenciais(papel)  # type: ignore[misc]
        db.identificador_valido(usuario)
        if not senha_valida(senha):
            raise ValueError(f"senha do papel {papel} ausente ou fraca (mín. 16 caracteres ASCII); rode init-env")
        with db.sessao("admin", cfg, banco="master", autocommit=True) as conn:
            conn.execute(f"IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = {_sql_str(usuario)}) "
                         f"ALTER LOGIN [{usuario}] WITH PASSWORD = {_sql_str(senha)} "
                         f"ELSE CREATE LOGIN [{usuario}] WITH PASSWORD = {_sql_str(senha)}, "
                         f"DEFAULT_DATABASE = [{banco}], CHECK_POLICY = ON, CHECK_EXPIRATION = OFF")
        with db.sessao("admin", cfg, autocommit=True) as conn:
            conn.execute(f"IF DATABASE_PRINCIPAL_ID({_sql_str(usuario)}) IS NULL "
                         f"CREATE USER [{usuario}] FOR LOGIN [{usuario}] WITH DEFAULT_SCHEMA = [dbo]")
            conn.execute(f"ALTER ROLE [{role}] ADD MEMBER [{usuario}]")
        log(f"  login {usuario} -> papel {role}")


# --- verificação ----------------------------------------------------------------------------------------
def _tentar(conn, sql: str) -> str:
    try:
        cur = conn.execute(sql)
        if cur.description:
            cur.fetchall()
        return "permitido"
    except Exception as e:  # noqa: BLE001 - só interessa se houve negação
        return "negado" if "permission" in str(e).lower() or "permiss" in str(e).lower() else f"erro: {str(e)[:80]}"


def verificar(cfg: db.Config | None = None, log=print) -> bool:
    """Conecta com cada papel e confere schemas, migrations e permissões. Não imprime senhas."""
    cfg = cfg or db.Config.do_ambiente()
    ok = True
    log(f"  configuração: {cfg.redigido()}")
    with db.sessao("admin", cfg) as conn:
        versao = conn.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(30)), "
                              "CAST(SERVERPROPERTY('Edition') AS NVARCHAR(80))").fetchone()
        log(f"  servidor {versao[0]} ({versao[1]}), banco {cfg.banco}")
        schemas = {r[0] for r in conn.execute("SELECT name FROM sys.schemas WHERE name IN ('bronze','silver','gold')")}
        log(f"  schemas: {sorted(schemas)}")
        ok &= schemas == {"bronze", "silver", "gold"}
        n = conn.execute("SELECT COUNT(*) FROM dbo.schema_migration").fetchone()[0]
        log(f"  migrations registradas: {n}")
        ok &= n == len(listar_migrations())
    for papel, esperado in (("pipeline", {"silver": "permitido", "gold": "permitido"}),
                            ("powerbi", {"silver": "negado", "gold": "permitido"})):
        try:
            with db.sessao(papel, cfg) as conn:
                res = {"silver": _tentar(conn, "SELECT TOP 1 * FROM silver.obra"),
                       "gold": _tentar(conn, "SELECT TOP 1 * FROM gold.dim_obra")}
        except Exception as e:  # noqa: BLE001
            log(f"  papel {papel}: falha de conexão ({str(e)[:100]})")
            ok = False
            continue
        log(f"  papel {papel}: {res}")
        ok &= res == esperado
    return bool(ok)


def descartar_banco(cfg: db.Config, nome: str) -> None:
    """Remove um banco de TESTE (nome precisa começar com `databuild_test_`). Nunca toca o banco real."""
    if not nome.startswith(PREFIXO_TESTE):
        raise ValueError(f"recusado: só bancos {PREFIXO_TESTE}* podem ser descartados")
    db.identificador_valido(nome)
    with db.sessao("admin", cfg, banco="master", autocommit=True) as conn:
        conn.execute(f"IF DB_ID(N'{nome}') IS NOT NULL BEGIN "
                     f"ALTER DATABASE [{nome}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{nome}]; END")


# --- .env.local -----------------------------------------------------------------------------------------
_MODELO_ENV = """# Credenciais locais do DataBuild. Arquivo ignorado pelo Git: nunca versionar.
# Guarde estes valores no Bitwarden (mantido pelo dono) e mantenha este arquivo só com atribuições.
DATABUILD_DB_SERVER=localhost,1433
DATABUILD_DB_NAME=databuild
DATABUILD_DB_DRIVER=ODBC Driver 18 for SQL Server
DATABUILD_DB_ENCRYPT=yes
DATABUILD_DB_TRUST_SERVER_CERT=yes
DATABUILD_DB_ADMIN_AUTH=windows
DATABUILD_PIPELINE_USER=databuild_pipeline
DATABUILD_PIPELINE_PASSWORD={pipeline}
DATABUILD_POWERBI_USER=databuild_powerbi
DATABUILD_POWERBI_PASSWORD={powerbi}
"""


def iniciar_env(caminho: Path = db.ENV_FILE) -> bool:
    """Cria o `.env.local` com senhas geradas. Devolve False (sem alterar nada) se o arquivo já existe."""
    if caminho.exists():
        return False
    caminho.write_text(_MODELO_ENV.format(pipeline=gerar_senha(), powerbi=gerar_senha()), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=("init-env", "migrate", "provision", "check", "status"))
    ap.add_argument("--reexecutar", action="store_true", help="migrate: roda de novo as migrations reexecutáveis")
    a = ap.parse_args(argv)
    if a.comando == "init-env":
        print("criado .env.local (senhas geradas; não exibidas)" if iniciar_env() else ".env.local já existe; nada alterado")
        return 0
    db.carregar_env()
    cfg = db.Config.do_ambiente()
    if a.comando == "migrate":
        feitas = migrar(cfg, reexecutar=a.reexecutar)
        print(f"{len(feitas)} migration(s) registradas/executadas")
    elif a.comando == "provision":
        provisionar(cfg)
    elif a.comando == "status":
        with db.sessao("admin", cfg) as conn:
            for r in conn.execute("SELECT versao, nome, aplicado_em FROM dbo.schema_migration ORDER BY versao"):
                print(f"  {r[0]:03d} {r[1]}  ({r[2]})")
    else:
        ok = verificar(cfg)
        print("OK" if ok else "FALHOU")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
