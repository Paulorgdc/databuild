"""Ingestão das medições Excel na camada Bronze do SQL Server (Etapa 04).

    python scripts/ingest.py file <arquivo.xlsx>      # ingere um arquivo
    python scripts/ingest.py batch [--pasta data]     # ingere todos os `MED *.xlsx` da pasta
    python scripts/ingest.py status                   # lotes, arquivos e totais armazenados
    python scripts/ingest.py log [--carga N]          # log de processamento
    python scripts/ingest.py verify [--pasta data]    # confere o banco contra os arquivos Excel

Opções comuns: `--json` (saída legível por máquina, p.ex. para o Airflow), `--dry-run` (extrai e conta, sem gravar).
Códigos de saída: 0 sucesso, 1 falha em algum arquivo ou divergência na conferência, 2 uso incorreto.

O que entra na Bronze, sem corrigir nada:
  - `bronze.arquivo`/`aba`/`celula_raw`: toda célula preenchida de todas as abas (fórmula, valor armazenado,
    erro do Excel, posição A1), identificada pelo SHA-256 do arquivo e pelo lote de carga.
  - tabelas estruturadas `*_raw` (obras, medições, categorias, serviços, valores), extraídas pelos módulos
    existentes (`reader`, `build`, `export`), com a hierarquia de serviços e a posição de origem.
Idempotência: o SHA-256 identifica o arquivo; um conteúdo já carregado vira `duplicado` e não grava nada.
Cada arquivo é uma transação: qualquer falha desfaz o arquivo inteiro e o lote segue com os demais.
As planilhas originais só são abertas para leitura.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pyodbc

import build
import db
import export
import reader as C
from engine import Resultado

warnings.filterwarnings("ignore", message="Cannot parse header or footer")

NOME_OBRA = "Construtora Horizontes"
OBRA_ID = "OBRA-1"
LOTE_CELULAS = 5000
MODELO_VERSAO = "ingest-1"


# ========================================================================= extração (sem banco)
@dataclass
class AbaExtraida:
    indice: int
    nome: str
    estado: str
    linhas_dim: int
    colunas_dim: int
    celulas: list[tuple] = field(default_factory=list)   # (linha, coluna, celula, formula, ref, valor, tipo, eh_formula, eh_erro)
    mescladas: str | None = None
    n_vazias: int = 0

    @property
    def n_formulas(self) -> int:
        return sum(1 for c in self.celulas if c[7])

    @property
    def n_erros(self) -> int:
        return sum(1 for c in self.celulas if c[8])

    @property
    def hash_conteudo(self) -> str:
        return hash_celulas(self.celulas)


@dataclass
class Extracao:
    caminho: Path
    sha256: str
    nome: str
    tamanho: int
    modificado: dt.datetime
    rotulo: str
    abas: list[AbaExtraida]
    estruturado: dict[str, list[dict]]

    @property
    def n_celulas(self) -> int:
        return sum(len(a.celulas) for a in self.abas)

    @property
    def n_formulas(self) -> int:
        return sum(a.n_formulas for a in self.abas)

    @property
    def n_erros(self) -> int:
        return sum(a.n_erros for a in self.abas)

    @property
    def n_estruturados(self) -> int:
        return sum(len(v) for v in self.estruturado.values())


def texto_valor(v) -> tuple[str | None, str]:
    """(texto fiel ao valor armazenado, tipo). Números usam `repr` (ida e volta exata); datas, ISO 8601."""
    if v is None:
        return None, "sem_valor"
    if isinstance(v, bool):
        return ("TRUE" if v else "FALSE"), "booleano"
    if isinstance(v, (int, float)):
        return repr(v), "numero"
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat(), "data"
    if isinstance(v, dt.timedelta):
        return str(v), "data"
    return str(v), "texto"


def hash_celulas(celulas) -> str:
    """SHA-256 da serialização canônica (linha, coluna, tipo, fórmula, referência, valor), em ordem estável."""
    h = hashlib.sha256()
    for c in sorted(celulas, key=lambda x: (x[0], x[1])):
        h.update(json.dumps([c[0], c[1], c[6], c[3], c[4], c[5]], ensure_ascii=False).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _celula(cel, cel_valor) -> tuple | None:
    v = cel.value
    if v is None:
        return None
    formula = ref = None
    eh_formula = False
    if isinstance(v, str) and v.startswith("=") and cel.data_type == "f":
        formula, eh_formula = v, True
    elif cel.data_type == "f" or type(v).__name__ in ("ArrayFormula", "DataTableFormula"):
        eh_formula = True
        formula = getattr(v, "text", None) or str(v)
        ref = getattr(v, "ref", None)
    if eh_formula:
        armazenado = cel_valor.value
        erro = cel_valor.data_type == "e"
    else:
        armazenado = v
        erro = cel.data_type == "e"
    texto, tipo = texto_valor(armazenado)
    if erro:
        tipo = "erro"
    return (cel.row, cel.column, cel.coordinate, formula, ref, texto, tipo, eh_formula, erro)


def extrair_celulas(caminho: Path) -> list[AbaExtraida]:
    """Lê todas as abas (fórmulas e valores armazenados) somente para leitura."""
    wb = openpyxl.load_workbook(caminho)
    wv = openpyxl.load_workbook(caminho, data_only=True)
    abas: list[AbaExtraida] = []
    for i, ws in enumerate(wb.worksheets):
        wsv = wv[ws.title]
        a = AbaExtraida(i, ws.title, ws.sheet_state, ws.max_row or 0, ws.max_column or 0)
        for linha in ws.iter_rows():
            for cel in linha:
                if cel.value is None:
                    continue
                reg = _celula(cel, wsv[cel.coordinate])
                if reg:
                    a.celulas.append(reg)
        a.n_vazias = max(a.linhas_dim * a.colunas_dim - len(a.celulas), 0)
        a.mescladas = ",".join(sorted(str(r) for r in ws.merged_cells.ranges)) or None
        abas.append(a)
    wb.close()
    wv.close()
    return abas


def _txt(v) -> str | None:
    """Texto de um campo estruturado; vazio continua NULL (nada é preenchido nem corrigido)."""
    return None if v is None else texto_valor(v)[0]


def extrair_estruturado(caminho: Path) -> dict[str, list[dict]]:
    """Tabelas estruturadas pelos módulos existentes, com a posição de origem (aba, linha, célula)."""
    m = C.load(caminho)
    med = build.construir(m, OBRA_ID)
    t = export.tabelas(Resultado([med], []), NOME_OBRA)
    t.pop("ocorrencias", None)          # achados da validação pertencem à Etapa 02/05, não à ingestão
    aba = C.MAIN_SHEET
    for r in t["categorias"] + t["servicos"]:
        r["aba_origem"], r["linha_origem"] = aba, med.linhas[r["codigo"]].row
    for r in t["valores"]:
        r["valor_recalculado"] = None   # recálculo é validação, não dado de origem
        r["aba_origem"] = aba
        r["celula_origem"] = f"{med.colunas[r['campo']]}{med.linhas[r['codigo']].row}"
    for nome, linhas in t.items():
        t[nome] = [{k: _txt(v) if k not in ("linha_origem",) else v for k, v in r.items()} for r in linhas]
    return t


def extrair(caminho: Path | str, estruturado: bool = True) -> Extracao:
    p = Path(caminho)
    st = p.stat()
    return Extracao(p, build.sha256(p), p.name, st.st_size, dt.datetime.fromtimestamp(st.st_mtime),
                    C.label_of(p), extrair_celulas(p), extrair_estruturado(p) if estruturado else {})


# ========================================================================= gravação
_COLS = {
    "obras": ("obra_id", "nome"),
    "medicoes": ("medicao_id", "obra_id", "numero", "periodo_inicio", "periodo_fim", "contrato_total",
                 "contrato_controle", "arquivo_sha256", "n_servicos", "n_categorias"),
    "categorias": ("medicao_id", "codigo", "tipo", "pai", "nivel", "descricao", "aba_origem", "linha_origem"),
    "servicos": ("medicao_id", "codigo", "pai", "descricao", "unidade", "codigo_servico", "fonte", "aba_origem",
                 "linha_origem"),
    "valores": ("medicao_id", "codigo", "tipo", "campo", "valor_armazenado", "valor_recalculado", "formula",
                "aba_origem", "celula_origem"),
}
_TABELA = {k: f"bronze.{k}_raw" for k in _COLS}


class Ingestor:
    """Executa a ingestão numa conexão do papel `pipeline`. `log` recebe mensagens legíveis."""

    def __init__(self, conn, log=print):
        self.conn = conn
        self.log = log
        self.carga_id: int | None = None

    # --- lote ---------------------------------------------------------------------------------
    def abrir_carga(self, origem: str) -> int:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO bronze.carga (origem, versao_motor) OUTPUT INSERTED.carga_id VALUES (?, ?)",
                    origem[:200], MODELO_VERSAO)
        self.carga_id = int(cur.fetchone()[0])
        self.conn.commit()
        self._log("info", f"carga {self.carga_id} iniciada: {origem}")
        return self.carga_id

    def fechar_carga(self, status: str, observacao: str) -> None:
        self.conn.execute("UPDATE bronze.carga SET status = ?, finalizada_em = SYSUTCDATETIME(), observacao = ? "
                          "WHERE carga_id = ?", status, observacao[:500], self.carga_id)
        self.conn.commit()
        self._log("info", f"carga {self.carga_id} {status}: {observacao}")

    def _log(self, nivel: str, msg: str, sha: str | None = None) -> None:
        self.log(f"[{nivel}] {msg}")
        if self.carga_id is not None:
            self.conn.execute("INSERT INTO bronze.carga_log (carga_id, nivel, sha256, mensagem) VALUES (?, ?, ?, ?)",
                              self.carga_id, nivel, sha, msg[:2000])
            self.conn.commit()

    # --- arquivo ------------------------------------------------------------------------------
    def _arquivo_existente(self, sha: str) -> int | None:
        r = self.conn.execute("SELECT arquivo_id FROM bronze.arquivo WHERE sha256 = ?", sha).fetchone()
        return int(r[0]) if r else None

    def _registrar(self, resultado: str, ex: Extracao | None, nome: str, arquivo_id=None, msg=None, ini=None,
                   sha=None) -> None:
        self.conn.execute(
            "INSERT INTO bronze.carga_arquivo (carga_id, arquivo_id, sha256, nome_arquivo, resultado, n_abas, "
            "n_celulas, n_formulas, n_erros, n_registros_estruturados, iniciado_em, finalizado_em, mensagem) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,SYSUTCDATETIME(),?)",
            self.carga_id, arquivo_id, sha or (ex.sha256 if ex else None), nome[:260], resultado,
            len(ex.abas) if ex else None, ex.n_celulas if ex else None, ex.n_formulas if ex else None,
            ex.n_erros if ex else None, ex.n_estruturados if ex else None, ini, (msg or "")[:2000] or None)
        self.conn.commit()

    def ingerir(self, caminho: Path | str) -> dict:
        """Ingere um arquivo. Devolve {resultado, ...}; nunca propaga erro de arquivo (registra e segue)."""
        p = Path(caminho)
        ini = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        rotulo = C.label_of(p)
        try:
            ex = extrair(p)
        except Exception as e:  # arquivo corrompido, layout não reconhecido, sem permissão ...
            msg = f"{type(e).__name__}: {e}"
            self._log("erro", f"{rotulo}: extração falhou ({msg})")
            self._registrar("falhou", None, p.name, msg=msg, ini=ini)
            return {"arquivo": p.name, "rotulo": rotulo, "resultado": "falhou", "erro": msg}
        try:
            return self._gravar(ex, ini)
        except Exception as e:
            self.conn.rollback()
            msg = f"{type(e).__name__}: {e}"
            self._log("erro", f"{rotulo}: gravação desfeita ({msg})", ex.sha256)
            self._registrar("falhou", ex, p.name, msg=msg, ini=ini)
            return {"arquivo": p.name, "rotulo": rotulo, "resultado": "falhou", "erro": msg,
                    "sha256": ex.sha256}

    def _gravar(self, ex: Extracao, ini) -> dict:
        cur = self.conn.cursor()
        # Serializa ingestões concorrentes do mesmo conteúdo e confere duplicidade dentro da transação.
        cur.execute("SET NOCOUNT ON; DECLARE @r INT; EXEC @r = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
                    "@LockOwner = 'Transaction', @LockTimeout = 60000; SELECT @r;", f"databuild_ingest_{ex.sha256}")
        if cur.fetchone()[0] < 0:
            raise RuntimeError("não obteve o bloqueio de ingestão (outra execução em andamento)")
        existente = self._arquivo_existente(ex.sha256)
        if existente is not None:
            self.conn.commit()
            self._log("info", f"{ex.rotulo}: conteúdo idêntico já carregado (arquivo_id {existente}); nada gravado",
                      ex.sha256)
            self._registrar("duplicado", ex, ex.nome, existente, "conteúdo idêntico já existente (SHA-256)", ini)
            return {"arquivo": ex.nome, "rotulo": ex.rotulo, "resultado": "duplicado", "sha256": ex.sha256,
                    "arquivo_id": existente}

        homonimo = cur.execute("SELECT COUNT(*) FROM bronze.arquivo WHERE nome_arquivo = ?", ex.nome).fetchone()[0]
        cur.execute("INSERT INTO bronze.arquivo (sha256, nome_arquivo, tamanho_bytes, modificado_origem, medicao_id, "
                    "n_abas, n_celulas, n_formulas, n_erros, primeira_carga_id) OUTPUT INSERTED.arquivo_id "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", ex.sha256, ex.nome[:260], ex.tamanho,
                    ex.modificado.replace(microsecond=0), ex.rotulo, len(ex.abas), ex.n_celulas, ex.n_formulas,
                    ex.n_erros, self.carga_id)
        arquivo_id = int(cur.fetchone()[0])
        wv = pyodbc.SQL_WVARCHAR
        for a in ex.abas:
            # Tamanhos explícitos: com `mescladas` NULL na primeira execução o pyodbc fixaria um tipo legado.
            cur.setinputsizes([None, None, (wv, 200, 0), (wv, 20, 0), None, None, None, None, None, None,
                               (wv, 0, 0), (wv, 64, 0)])
            cur.execute("INSERT INTO bronze.aba (arquivo_id, indice, nome, estado, linhas_dim, colunas_dim, "
                        "n_preenchidas, n_vazias, n_formulas, n_erros, mescladas, hash_conteudo) "
                        "OUTPUT INSERTED.aba_id VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", arquivo_id, a.indice, a.nome,
                        a.estado, a.linhas_dim, a.colunas_dim, len(a.celulas), a.n_vazias, a.n_formulas, a.n_erros,
                        a.mescladas, a.hash_conteudo)
            aba_id = int(cur.fetchone()[0])
            self._celulas(cur, arquivo_id, aba_id, a)
        n_est = self._estruturado(cur, arquivo_id, ex)
        self.conn.commit()   # único ponto de confirmação do arquivo: tudo ou nada
        aviso = ""
        if homonimo:
            aviso = " (mesmo nome de um arquivo anterior, conteúdo diferente: nova versão preservada)"
            self._log("aviso", f"{ex.rotulo}: {aviso.strip()}", ex.sha256)
        self._log("info", f"{ex.rotulo}: carregado (arquivo_id {arquivo_id}): {len(ex.abas)} abas, {ex.n_celulas} "
                          f"células, {ex.n_formulas} fórmulas, {ex.n_erros} erros, {n_est} registros estruturados",
                  ex.sha256)
        self._registrar("carregado", ex, ex.nome, arquivo_id, aviso.strip() or None, ini)
        return {"arquivo": ex.nome, "rotulo": ex.rotulo, "resultado": "carregado", "sha256": ex.sha256,
                "arquivo_id": arquivo_id, "abas": len(ex.abas), "celulas": ex.n_celulas, "formulas": ex.n_formulas,
                "erros": ex.n_erros, "estruturados": n_est}

    def _celulas(self, cur, arquivo_id: int, aba_id: int, a: AbaExtraida) -> None:
        sql = ("INSERT INTO bronze.celula_raw (carga_id, arquivo_id, aba_id, linha, coluna, celula, eh_formula, "
               "eh_erro, tipo_valor, formula, formula_ref, valor_armazenado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
        # Tamanhos explícitos: sem eles, um NULL na primeira linha do lote faz o pyodbc inferir tipo legado
        # (text/ntext) e a carga falha. Textos acima de 4000 caracteres usam o caminho lento (sem fast_executemany).
        wv = pyodbc.SQL_WVARCHAR
        tamanhos = [None, None, None, None, None, (wv, 20, 0), None, None, (wv, 12, 0), (wv, 4000, 0),
                    (wv, 50, 0), (wv, 4000, 0)]
        for i in range(0, len(a.celulas), LOTE_CELULAS):
            parte = a.celulas[i:i + LOTE_CELULAS]
            dados = [(self.carga_id, arquivo_id, aba_id, c[0], c[1], c[2], int(c[7]), int(c[8]), c[6], c[3], c[4],
                      c[5]) for c in parte]
            longo = any(len(c[3] or "") > 4000 or len(c[5] or "") > 4000 for c in parte)
            cur.fast_executemany = not longo
            if not longo:
                cur.setinputsizes(tamanhos)      # o pyodbc descarta os tamanhos após cada execução
            cur.executemany(sql, dados)
        cur.fast_executemany = False

    def _estruturado(self, cur, arquivo_id: int, ex: Extracao) -> int:
        total = 0
        cur.fast_executemany = True
        for nome, colunas in _COLS.items():
            linhas = ex.estruturado.get(nome, [])
            if not linhas:
                continue
            cols = ", ".join(("carga_id", "arquivo_id") + colunas)
            marcas = ", ".join("?" * (2 + len(colunas)))
            # Texto com tamanho explícito (NULL na primeira linha faria o pyodbc inferir tipo legado).
            cur.setinputsizes([None, None] + [None if c == "linha_origem" else (pyodbc.SQL_WVARCHAR, 4000, 0)
                                              for c in colunas])
            cur.executemany(f"INSERT INTO {_TABELA[nome]} ({cols}) VALUES ({marcas})",
                            [(self.carga_id, arquivo_id) + tuple(r.get(c) for c in colunas) for r in linhas])
            total += len(linhas)
        cur.fast_executemany = False
        return total


def ingerir_lote(arquivos: list[Path], cfg: db.Config | None = None, log=print, origem: str = "batch") -> dict:
    """Uma carga (lote) para todos os arquivos. Retorna o resumo; `ok` é falso se algum arquivo falhou."""
    cfg = cfg or db.Config.do_ambiente()
    resultados: list[dict] = []
    with db.sessao("pipeline", cfg) as conn:
        ing = Ingestor(conn, log)
        carga = ing.abrir_carga(f"{origem}: {len(arquivos)} arquivo(s)")
        for p in arquivos:
            resultados.append(ing.ingerir(p))
        cont = {r: sum(1 for x in resultados if x["resultado"] == r) for r in ("carregado", "duplicado", "falhou")}
        status = "falhou" if cont["falhou"] else "concluida"
        ing.fechar_carga(status, f"{cont['carregado']} carregado(s), {cont['duplicado']} duplicado(s), "
                                 f"{cont['falhou']} falha(s)")
    return {"carga_id": carga, "status": status, "ok": not cont["falhou"], **cont, "arquivos": resultados}


def simular(arquivos: list[Path]) -> dict:
    """`--dry-run`: extrai e conta, sem tocar no banco."""
    saida = []
    for p in arquivos:
        try:
            ex = extrair(p)
            saida.append({"arquivo": p.name, "rotulo": ex.rotulo, "resultado": "simulado", "sha256": ex.sha256,
                          "abas": len(ex.abas), "celulas": ex.n_celulas, "formulas": ex.n_formulas,
                          "erros": ex.n_erros, "estruturados": ex.n_estruturados})
        except Exception as e:
            saida.append({"arquivo": p.name, "rotulo": C.label_of(p), "resultado": "falhou",
                          "erro": f"{type(e).__name__}: {e}"})
    return {"carga_id": None, "status": "simulado", "ok": all(x["resultado"] != "falhou" for x in saida),
            "arquivos": saida}


# ========================================================================= conferência de integridade
def _celulas_do_banco(conn, aba_id: int) -> list[tuple]:
    rows = conn.execute("SELECT linha, coluna, celula, formula, formula_ref, valor_armazenado, tipo_valor, "
                        "eh_formula, eh_erro FROM bronze.celula_raw WHERE aba_id = ?", aba_id).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], bool(r[7]), bool(r[8])) for r in rows]


def verificar(cfg: db.Config | None = None, pasta: Path | None = None, log=print) -> dict:
    """Confere cada arquivo carregado: (1) consistência interna do banco; (2) se o original está na pasta,
    comparação célula a célula e dos registros estruturados. Devolve {ok, divergencias, arquivos}."""
    cfg = cfg or db.Config.do_ambiente()
    pasta = pasta or C.DATA
    locais = {}
    for p in C.med_files(pasta):
        locais.setdefault(build.sha256(p), p)
    div: list[str] = []
    relatorio: list[dict] = []
    with db.sessao("pipeline", cfg) as conn:
        arqs = conn.execute("SELECT arquivo_id, sha256, nome_arquivo, medicao_id, n_abas, n_celulas, n_formulas, "
                            "n_erros FROM bronze.arquivo ORDER BY arquivo_id").fetchall()
        for a in arqs:
            aid, sha, nome, rotulo = a[0], a[1], a[2], a[3]
            ruins: list[str] = []
            abas = conn.execute("SELECT aba_id, indice, nome, n_preenchidas, n_formulas, n_erros, hash_conteudo "
                                "FROM bronze.aba WHERE arquivo_id = ? ORDER BY indice", aid).fetchall()
            if len(abas) != a[4]:
                ruins.append(f"abas: {len(abas)} no banco, {a[4]} registradas no arquivo")
            n_cel = conn.execute("SELECT COUNT(*) FROM bronze.celula_raw WHERE arquivo_id = ?", aid).fetchone()[0]
            if n_cel != a[5] or sum(x[3] for x in abas) != a[5]:
                ruins.append(f"células: {n_cel} no banco, {a[5]} registradas")
            if sum(x[4] for x in abas) != a[6] or sum(x[5] for x in abas) != a[7]:
                ruins.append("fórmulas/erros das abas não somam o total do arquivo")
            dados_banco = {}
            for ab in abas:
                cel = _celulas_do_banco(conn, ab[0])
                dados_banco[ab[2]] = cel
                if len(cel) != ab[3]:
                    ruins.append(f"aba {ab[2]!r}: {len(cel)} células, esperadas {ab[3]}")
                if hash_celulas(cel) != ab[6]:
                    ruins.append(f"aba {ab[2]!r}: hash de conteúdo não confere")
            confrontado = False
            origem = locais.get(sha)
            if origem is not None:
                confrontado = True
                if build.sha256(origem) != sha:
                    ruins.append("SHA-256 do arquivo mudou")
                orig = extrair_celulas(origem)
                if [x.nome for x in orig] != [x[2] for x in abas]:
                    ruins.append("abas do arquivo diferem das do banco")
                for x in orig:
                    mapa = {(c[0], c[1]): c for c in dados_banco.get(x.nome, [])}
                    if len(mapa) != len(x.celulas):
                        ruins.append(f"aba {x.nome!r}: {len(x.celulas)} células no Excel, {len(mapa)} no banco")
                    errados = [c[2] for c in x.celulas if mapa.get((c[0], c[1])) != c]
                    if errados:
                        ruins.append(f"aba {x.nome!r}: {len(errados)} célula(s) divergem (ex.: {errados[:3]})")
                est = extrair_estruturado(origem)
                for nome_t, colunas in _COLS.items():
                    linhas = est.get(nome_t, [])
                    n_db = conn.execute(f"SELECT COUNT(*) FROM {_TABELA[nome_t]} WHERE arquivo_id = ?", aid).fetchone()[0]
                    if n_db != len(linhas):
                        ruins.append(f"{_TABELA[nome_t]}: {n_db} registros no banco, {len(linhas)} esperados")
                        continue
                    if linhas:
                        esperado = sorted(tuple(r.get(c) for c in colunas) for r in linhas)
                        real = sorted(tuple(r) for r in conn.execute(
                            f"SELECT {', '.join(colunas)} FROM {_TABELA[nome_t]} WHERE arquivo_id = ?", aid).fetchall())
                        if esperado != real:
                            ruins.append(f"{_TABELA[nome_t]}: conteúdo difere do extraído do Excel")
            orfas = conn.execute("SELECT COUNT(*) FROM bronze.celula_raw c LEFT JOIN bronze.aba b ON b.aba_id = c.aba_id "
                                 "WHERE c.arquivo_id = ? AND (b.aba_id IS NULL OR b.arquivo_id <> c.arquivo_id)",
                                 aid).fetchone()[0]
            if orfas:
                ruins.append(f"{orfas} célula(s) órfã(s)")
            relatorio.append({"arquivo_id": aid, "rotulo": rotulo, "sha256": sha, "celulas": n_cel,
                              "confrontado_com_excel": confrontado, "divergencias": ruins})
            div += [f"{rotulo}: {r}" for r in ruins]
            log(f"  {rotulo}: {'OK' if not ruins else 'DIVERGE'} ({n_cel} células"
                f"{', conferido contra o Excel' if confrontado else ', original ausente: só consistência interna'})")
    return {"ok": not div and bool(arqs), "divergencias": div, "arquivos": relatorio}


# ========================================================================= consultas
def status(cfg: db.Config | None = None) -> dict:
    cfg = cfg or db.Config.do_ambiente()
    with db.sessao("pipeline", cfg) as conn:
        q = lambda s, *a: conn.execute(s, *a).fetchone()[0]
        tot = {t: q(f"SELECT COUNT(*) FROM bronze.{t}") for t in
               ("carga", "arquivo", "aba", "celula_raw", "obras_raw", "medicoes_raw", "categorias_raw",
                "servicos_raw", "valores_raw")}
        tot["formulas"] = q("SELECT COUNT(*) FROM bronze.celula_raw WHERE eh_formula = 1")
        tot["erros_excel"] = q("SELECT COUNT(*) FROM bronze.celula_raw WHERE eh_erro = 1")
        cargas = [dict(carga_id=r[0], status=r[1], iniciada_em=str(r[2]), observacao=r[3]) for r in conn.execute(
            "SELECT TOP 10 carga_id, status, iniciada_em, observacao FROM bronze.carga ORDER BY carga_id DESC")]
        arqs = [dict(arquivo_id=r[0], rotulo=r[1], sha256=r[2][:12], abas=r[3], celulas=r[4], formulas=r[5],
                     erros=r[6]) for r in conn.execute(
            "SELECT arquivo_id, medicao_id, sha256, n_abas, n_celulas, n_formulas, n_erros FROM bronze.arquivo "
            "ORDER BY arquivo_id")]
        res = dict(conn.execute("SELECT resultado, COUNT(*) FROM bronze.carga_arquivo GROUP BY resultado").fetchall())
    return {"totais": tot, "arquivos": arqs, "ultimas_cargas": cargas, "execucoes_por_resultado": res}


def ler_log(cfg: db.Config | None = None, carga: int | None = None, limite: int = 200) -> list[dict]:
    cfg = cfg or db.Config.do_ambiente()
    with db.sessao("pipeline", cfg) as conn:
        if carga is None:
            carga = conn.execute("SELECT MAX(carga_id) FROM bronze.carga").fetchone()[0]
        rows = conn.execute("SELECT TOP (?) log_id, carga_id, ocorrido_em, nivel, mensagem FROM bronze.carga_log "
                            "WHERE carga_id = ? ORDER BY log_id", limite, carga).fetchall()
    return [dict(log_id=r[0], carga_id=r[1], em=str(r[2]), nivel=r[3], mensagem=r[4]) for r in rows]


# ========================================================================= CLI
def _saida(obj, como_json: bool, formatador) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str) if como_json else formatador(obj))


def _fmt_lote(r: dict) -> str:
    linhas = [f"carga {r['carga_id']}: {r['status']}"]
    for x in r["arquivos"]:
        extra = (f" — {x['celulas']} células, {x['formulas']} fórmulas, {x['erros']} erros, "
                 f"{x['estruturados']} estruturados" if "celulas" in x else f" — {x.get('erro', '')}")
        linhas.append(f"  {x['rotulo']}: {x['resultado']}{extra}")
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nome in ("file", "batch", "status", "log", "verify"):
        s = sub.add_parser(nome)
        s.add_argument("--json", action="store_true", help="saída em JSON")
        if nome == "file":
            s.add_argument("arquivo", type=Path)
        if nome in ("file", "batch"):
            s.add_argument("--dry-run", action="store_true", help="extrai e conta, sem gravar")
        if nome in ("batch", "verify"):
            s.add_argument("--pasta", type=Path, default=C.DATA)
        if nome == "log":
            s.add_argument("--carga", type=int)
    a = ap.parse_args(argv)
    silencioso = (lambda *_: None) if a.json else print
    db.carregar_env()
    cfg = db.Config.do_ambiente()
    try:
        if a.cmd in ("file", "batch"):
            arquivos = [a.arquivo] if a.cmd == "file" else C.med_files(a.pasta)
            if a.cmd == "batch" and not arquivos:
                print(f"nenhum arquivo 'MED *.xlsx' em {a.pasta}", file=sys.stderr)
                return 2
            if a.cmd == "file" and not a.arquivo.is_file():
                print(f"arquivo não encontrado: {a.arquivo}", file=sys.stderr)
                return 2
            r = simular(arquivos) if a.dry_run else ingerir_lote(arquivos, cfg, silencioso, a.cmd)
            _saida(r, a.json, _fmt_lote)
            return 0 if r["ok"] else 1
        if a.cmd == "verify":
            r = verificar(cfg, a.pasta, silencioso)
            _saida(r, a.json, lambda r: "\n".join(["integridade: " + ("OK" if r["ok"] else "DIVERGENTE")] + r["divergencias"]))
            return 0 if r["ok"] else 1
        if a.cmd == "status":
            _saida(status(cfg), a.json, lambda r: json.dumps(r, ensure_ascii=False, indent=1, default=str))
            return 0
        _saida(ler_log(cfg, a.carga), a.json, lambda r: "\n".join(f"{x['em']} [{x['nivel']}] {x['mensagem']}" for x in r))
        return 0
    except RuntimeError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
