/* 009 - Bronze: rastreabilidade da ingestão (Etapa 04).
   A 002 guarda a exportação estruturada; esta migration acrescenta o que a ingestão exige e a 002 não tinha:
     bronze.arquivo        identidade do arquivo por SHA-256 (um arquivo de conteúdo idêntico entra uma única vez)
     bronze.aba            abas de cada arquivo, com contagens e hash de conteúdo
     bronze.celula_raw     cada célula preenchida: arquivo, aba, linha, coluna, célula, fórmula e valor armazenado
     bronze.carga_arquivo  o que aconteceu com cada arquivo em cada execução (carregado, duplicado, falhou)
     bronze.carga_log      log de processamento por execução
   E liga as tabelas estruturadas existentes ao arquivo e à posição de origem (arquivo_id, linha/célula).
   Nenhum dado é corrigido nem convertido: valores ficam em texto, como o Excel os guardou. */

IF OBJECT_ID(N'bronze.arquivo', N'U') IS NULL
CREATE TABLE bronze.arquivo (
    arquivo_id         INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_arquivo PRIMARY KEY,
    sha256             CHAR(64)      NOT NULL CONSTRAINT uq_bronze_arquivo_sha256 UNIQUE,
    nome_arquivo       NVARCHAR(260) NOT NULL,
    tamanho_bytes      BIGINT        NOT NULL,
    modificado_origem  DATETIME2(0)  NULL,
    medicao_id         NVARCHAR(100) NULL,           -- rótulo derivado do nome (ex.: MED 03)
    n_abas             INT           NOT NULL,
    n_celulas          INT           NOT NULL,       -- células preenchidas
    n_formulas         INT           NOT NULL,
    n_erros            INT           NOT NULL,
    primeira_carga_id  INT           NOT NULL CONSTRAINT fk_bronze_arquivo_carga REFERENCES bronze.carga (carga_id),
    registrado_em      DATETIME2(0)  NOT NULL CONSTRAINT df_bronze_arquivo_em DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_bronze_arquivo_sha CHECK (sha256 NOT LIKE '%[^0-9a-f]%'),
    CONSTRAINT ck_bronze_arquivo_cont CHECK (n_abas >= 0 AND n_celulas >= 0 AND n_formulas >= 0 AND n_erros >= 0)
);
GO

IF OBJECT_ID(N'bronze.aba', N'U') IS NULL
CREATE TABLE bronze.aba (
    aba_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_aba PRIMARY KEY,
    arquivo_id      INT           NOT NULL CONSTRAINT fk_bronze_aba_arquivo REFERENCES bronze.arquivo (arquivo_id),
    indice          INT           NOT NULL,          -- posição da aba no arquivo (0 = primeira)
    nome            NVARCHAR(200) NOT NULL,
    estado          NVARCHAR(20)  NOT NULL,          -- visible | hidden | veryHidden
    linhas_dim      INT           NOT NULL,          -- dimensão declarada pela planilha
    colunas_dim     INT           NOT NULL,
    n_preenchidas   INT           NOT NULL,
    n_vazias        BIGINT        NOT NULL,          -- células da dimensão sem valor (não gravadas individualmente)
    n_formulas      INT           NOT NULL,
    n_erros         INT           NOT NULL,
    mescladas       NVARCHAR(MAX) NULL,              -- intervalos mesclados, separados por vírgula
    hash_conteudo   CHAR(64)      NOT NULL,          -- SHA-256 da serialização canônica das células da aba
    CONSTRAINT uq_bronze_aba_indice UNIQUE (arquivo_id, indice),
    CONSTRAINT uq_bronze_aba_nome   UNIQUE (arquivo_id, nome),
    CONSTRAINT ck_bronze_aba_cont CHECK (n_preenchidas >= 0 AND n_vazias >= 0 AND n_formulas >= 0 AND n_erros >= 0)
);
GO

IF OBJECT_ID(N'bronze.celula_raw', N'U') IS NULL
CREATE TABLE bronze.celula_raw (
    celula_id        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_celula_raw PRIMARY KEY,
    carga_id         INT           NOT NULL CONSTRAINT fk_bronze_celula_raw_carga REFERENCES bronze.carga (carga_id),
    arquivo_id       INT           NOT NULL CONSTRAINT fk_bronze_celula_raw_arquivo REFERENCES bronze.arquivo (arquivo_id),
    aba_id           INT           NOT NULL CONSTRAINT fk_bronze_celula_raw_aba REFERENCES bronze.aba (aba_id),
    linha            INT           NOT NULL,
    coluna           INT           NOT NULL,
    celula           NVARCHAR(20)  NOT NULL,          -- referência A1
    eh_formula       BIT           NOT NULL,
    eh_erro          BIT           NOT NULL,          -- valor armazenado é um erro do Excel (#REF!, #DIV/0! ...)
    tipo_valor       NVARCHAR(12)  NOT NULL,          -- numero | texto | data | booleano | erro | sem_valor
    formula          NVARCHAR(MAX) NULL,              -- texto da fórmula (com "="), exatamente como no arquivo
    formula_ref      NVARCHAR(50)  NULL,              -- intervalo de uma fórmula matricial
    valor_armazenado NVARCHAR(MAX) NULL,              -- valor guardado pelo Excel (cache da fórmula, ou o próprio valor)
    CONSTRAINT uq_bronze_celula_raw_posicao UNIQUE (aba_id, linha, coluna),
    CONSTRAINT ck_bronze_celula_raw_tipo CHECK (tipo_valor IN (N'numero', N'texto', N'data', N'booleano', N'erro', N'sem_valor')),
    CONSTRAINT ck_bronze_celula_raw_formula CHECK (eh_formula = 1 OR formula IS NULL)
);
GO

IF OBJECT_ID(N'bronze.carga_arquivo', N'U') IS NULL
CREATE TABLE bronze.carga_arquivo (
    carga_arquivo_id INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_carga_arquivo PRIMARY KEY,
    carga_id         INT           NOT NULL CONSTRAINT fk_bronze_carga_arquivo_carga REFERENCES bronze.carga (carga_id),
    arquivo_id       INT           NULL     CONSTRAINT fk_bronze_carga_arquivo_arquivo REFERENCES bronze.arquivo (arquivo_id),
    sha256           CHAR(64)      NULL,             -- nulo só se o arquivo nem pôde ser lido
    nome_arquivo     NVARCHAR(260) NOT NULL,
    resultado        NVARCHAR(12)  NOT NULL,
    n_abas           INT           NULL,
    n_celulas        INT           NULL,
    n_formulas       INT           NULL,
    n_erros          INT           NULL,
    n_registros_estruturados INT   NULL,             -- linhas gravadas nas tabelas *_raw estruturadas
    iniciado_em      DATETIME2(3)  NOT NULL CONSTRAINT df_bronze_carga_arquivo_ini DEFAULT SYSUTCDATETIME(),
    finalizado_em    DATETIME2(3)  NULL,
    mensagem         NVARCHAR(2000) NULL,
    CONSTRAINT ck_bronze_carga_arquivo_res CHECK (resultado IN (N'carregado', N'duplicado', N'falhou'))
);
GO

IF OBJECT_ID(N'bronze.carga_log', N'U') IS NULL
CREATE TABLE bronze.carga_log (
    log_id       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_carga_log PRIMARY KEY,
    carga_id     INT           NOT NULL CONSTRAINT fk_bronze_carga_log_carga REFERENCES bronze.carga (carga_id),
    ocorrido_em  DATETIME2(3)  NOT NULL CONSTRAINT df_bronze_carga_log_em DEFAULT SYSUTCDATETIME(),
    nivel        NVARCHAR(10)  NOT NULL,
    sha256       CHAR(64)      NULL,
    mensagem     NVARCHAR(2000) NOT NULL,
    CONSTRAINT ck_bronze_carga_log_nivel CHECK (nivel IN (N'info', N'aviso', N'erro'))
);
GO

/* Liga as tabelas estruturadas da 002 ao arquivo de origem e à posição na planilha. */
IF COL_LENGTH(N'bronze.obras_raw', N'arquivo_id') IS NULL
    ALTER TABLE bronze.obras_raw ADD arquivo_id INT NULL CONSTRAINT fk_bronze_obras_raw_arquivo REFERENCES bronze.arquivo (arquivo_id);
IF COL_LENGTH(N'bronze.medicoes_raw', N'arquivo_id') IS NULL
    ALTER TABLE bronze.medicoes_raw ADD arquivo_id INT NULL CONSTRAINT fk_bronze_medicoes_raw_arquivo REFERENCES bronze.arquivo (arquivo_id);
IF COL_LENGTH(N'bronze.categorias_raw', N'arquivo_id') IS NULL
    ALTER TABLE bronze.categorias_raw ADD arquivo_id INT NULL CONSTRAINT fk_bronze_categorias_raw_arquivo REFERENCES bronze.arquivo (arquivo_id);
IF COL_LENGTH(N'bronze.servicos_raw', N'arquivo_id') IS NULL
    ALTER TABLE bronze.servicos_raw ADD arquivo_id INT NULL CONSTRAINT fk_bronze_servicos_raw_arquivo REFERENCES bronze.arquivo (arquivo_id);
IF COL_LENGTH(N'bronze.valores_raw', N'arquivo_id') IS NULL
    ALTER TABLE bronze.valores_raw ADD arquivo_id INT NULL CONSTRAINT fk_bronze_valores_raw_arquivo REFERENCES bronze.arquivo (arquivo_id);
GO

IF COL_LENGTH(N'bronze.categorias_raw', N'aba_origem') IS NULL
    ALTER TABLE bronze.categorias_raw ADD aba_origem NVARCHAR(200) NULL, linha_origem INT NULL;
IF COL_LENGTH(N'bronze.servicos_raw', N'aba_origem') IS NULL
    ALTER TABLE bronze.servicos_raw ADD aba_origem NVARCHAR(200) NULL, linha_origem INT NULL;
IF COL_LENGTH(N'bronze.valores_raw', N'aba_origem') IS NULL
    ALTER TABLE bronze.valores_raw ADD aba_origem NVARCHAR(200) NULL, celula_origem NVARCHAR(20) NULL;
GO

/* Índices das chaves estrangeiras novas e das consultas por arquivo/aba. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_arquivo_carga' AND object_id = OBJECT_ID(N'bronze.arquivo'))
    CREATE INDEX ix_bronze_arquivo_carga ON bronze.arquivo (primeira_carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_celula_raw_carga' AND object_id = OBJECT_ID(N'bronze.celula_raw'))
    CREATE INDEX ix_bronze_celula_raw_carga ON bronze.celula_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_celula_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.celula_raw'))
    CREATE INDEX ix_bronze_celula_raw_arquivo ON bronze.celula_raw (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_carga_arquivo_carga' AND object_id = OBJECT_ID(N'bronze.carga_arquivo'))
    CREATE INDEX ix_bronze_carga_arquivo_carga ON bronze.carga_arquivo (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_carga_arquivo_arquivo' AND object_id = OBJECT_ID(N'bronze.carga_arquivo'))
    CREATE INDEX ix_bronze_carga_arquivo_arquivo ON bronze.carga_arquivo (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_carga_log_carga' AND object_id = OBJECT_ID(N'bronze.carga_log'))
    CREATE INDEX ix_bronze_carga_log_carga ON bronze.carga_log (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_obras_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.obras_raw'))
    CREATE INDEX ix_bronze_obras_raw_arquivo ON bronze.obras_raw (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_medicoes_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.medicoes_raw'))
    CREATE INDEX ix_bronze_medicoes_raw_arquivo ON bronze.medicoes_raw (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_categorias_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.categorias_raw'))
    CREATE INDEX ix_bronze_categorias_raw_arquivo ON bronze.categorias_raw (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_servicos_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.servicos_raw'))
    CREATE INDEX ix_bronze_servicos_raw_arquivo ON bronze.servicos_raw (arquivo_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_valores_raw_arquivo' AND object_id = OBJECT_ID(N'bronze.valores_raw'))
    CREATE INDEX ix_bronze_valores_raw_arquivo ON bronze.valores_raw (arquivo_id);
GO
