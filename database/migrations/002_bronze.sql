/* 002 - Camada Bronze: dado bruto, como exportado pelo motor da Etapa 02 (scripts/export.py).
   Colunas de origem em texto (sem conversão nem validação); cada linha carrega o lote (carga_id).
   Tipagem, restrições e deduplicação acontecem na Silver. A carga em si é uma etapa futura. */

/* Lote de carga: raiz de rastreabilidade de todo dado que entra no banco. */
IF OBJECT_ID(N'bronze.carga', N'U') IS NULL
CREATE TABLE bronze.carga (
    carga_id        INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_carga PRIMARY KEY,
    origem          NVARCHAR(200)  NOT NULL,                 -- ex.: pasta/arquivo exportado pelo motor
    versao_motor    NVARCHAR(20)   NULL,
    versao_catalogo NVARCHAR(20)   NULL,
    iniciada_em     DATETIME2(0)   NOT NULL CONSTRAINT df_bronze_carga_ini DEFAULT SYSUTCDATETIME(),
    finalizada_em   DATETIME2(0)   NULL,
    status          NVARCHAR(20)   NOT NULL CONSTRAINT df_bronze_carga_status DEFAULT N'iniciada',
    observacao      NVARCHAR(500)  NULL,
    CONSTRAINT ck_bronze_carga_status CHECK (status IN (N'iniciada', N'concluida', N'falhou')),
    CONSTRAINT ck_bronze_carga_fim    CHECK (finalizada_em IS NULL OR finalizada_em >= iniciada_em)
);
GO

IF OBJECT_ID(N'bronze.obras_raw', N'U') IS NULL
CREATE TABLE bronze.obras_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_obras_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_obras_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_obras_raw_em DEFAULT SYSUTCDATETIME(),
    obra_id     NVARCHAR(100) NULL,
    nome        NVARCHAR(500) NULL
);
GO

IF OBJECT_ID(N'bronze.medicoes_raw', N'U') IS NULL
CREATE TABLE bronze.medicoes_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_medicoes_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_medicoes_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_medicoes_raw_em DEFAULT SYSUTCDATETIME(),
    medicao_id        NVARCHAR(100) NULL,
    obra_id           NVARCHAR(100) NULL,
    numero            NVARCHAR(50)  NULL,
    periodo_inicio    NVARCHAR(50)  NULL,
    periodo_fim       NVARCHAR(50)  NULL,
    contrato_total    NVARCHAR(50)  NULL,
    contrato_controle NVARCHAR(50)  NULL,
    arquivo_sha256    NVARCHAR(100) NULL,
    n_servicos        NVARCHAR(50)  NULL,
    n_categorias      NVARCHAR(50)  NULL
);
GO

IF OBJECT_ID(N'bronze.categorias_raw', N'U') IS NULL
CREATE TABLE bronze.categorias_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_categorias_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_categorias_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_categorias_raw_em DEFAULT SYSUTCDATETIME(),
    medicao_id  NVARCHAR(100) NULL,
    codigo      NVARCHAR(100) NULL,
    tipo        NVARCHAR(50)  NULL,
    pai         NVARCHAR(100) NULL,
    nivel       NVARCHAR(50)  NULL,
    descricao   NVARCHAR(1000) NULL
);
GO

IF OBJECT_ID(N'bronze.servicos_raw', N'U') IS NULL
CREATE TABLE bronze.servicos_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_servicos_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_servicos_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_servicos_raw_em DEFAULT SYSUTCDATETIME(),
    medicao_id     NVARCHAR(100) NULL,
    codigo         NVARCHAR(100) NULL,
    pai            NVARCHAR(100) NULL,
    descricao      NVARCHAR(1000) NULL,
    unidade        NVARCHAR(50)  NULL,
    codigo_servico NVARCHAR(100) NULL,
    fonte          NVARCHAR(100) NULL
);
GO

IF OBJECT_ID(N'bronze.valores_raw', N'U') IS NULL
CREATE TABLE bronze.valores_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_valores_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_valores_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_valores_raw_em DEFAULT SYSUTCDATETIME(),
    medicao_id        NVARCHAR(100) NULL,
    codigo            NVARCHAR(100) NULL,
    tipo              NVARCHAR(50)  NULL,
    campo             NVARCHAR(50)  NULL,
    valor_armazenado  NVARCHAR(50)  NULL,
    valor_recalculado NVARCHAR(50)  NULL,
    formula           NVARCHAR(2000) NULL
);
GO

IF OBJECT_ID(N'bronze.ocorrencias_raw', N'U') IS NULL
CREATE TABLE bronze.ocorrencias_raw (
    bronze_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_ocorrencias_raw PRIMARY KEY,
    carga_id    INT NOT NULL CONSTRAINT fk_bronze_ocorrencias_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em DATETIME2(0) NOT NULL CONSTRAINT df_bronze_ocorrencias_raw_em DEFAULT SYSUTCDATETIME(),
    ocorrencia_id       NVARCHAR(100) NULL,
    obra_id             NVARCHAR(100) NULL,
    medicao_id          NVARCHAR(100) NULL,
    regra               NVARCHAR(200) NULL,
    versao_regra        NVARCHAR(50)  NULL,
    classe              NVARCHAR(50)  NULL,
    severidade          NVARCHAR(50)  NULL,
    causa               NVARCHAR(50)  NULL,
    causa_raiz_id       NVARCHAR(1000) NULL,   -- ids de ocorrências primárias separados por vírgula
    aba                 NVARCHAR(200) NULL,
    celula              NVARCHAR(100) NULL,
    codigo_item         NVARCHAR(100) NULL,
    campo               NVARCHAR(100) NULL,
    descricao           NVARCHAR(2000) NULL,
    evidencia           NVARCHAR(4000) NULL,
    valor_original      NVARCHAR(50)  NULL,
    valor_esperado      NVARCHAR(50)  NULL,
    diferenca           NVARCHAR(50)  NULL,
    contabiliza_impacto NVARCHAR(50)  NULL,
    sugestao            NVARCHAR(2000) NULL
);
GO

/* Catálogo de regras exportado (catalogo.json) como documento JSON bruto. */
IF OBJECT_ID(N'bronze.catalogo_regras_raw', N'U') IS NULL
CREATE TABLE bronze.catalogo_regras_raw (
    bronze_id       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_bronze_catalogo_regras_raw PRIMARY KEY,
    carga_id        INT NOT NULL CONSTRAINT fk_bronze_catalogo_regras_raw_carga REFERENCES bronze.carga (carga_id),
    carregado_em    DATETIME2(0) NOT NULL CONSTRAINT df_bronze_catalogo_regras_raw_em DEFAULT SYSUTCDATETIME(),
    versao_catalogo NVARCHAR(20) NULL,
    payload         NVARCHAR(MAX) NOT NULL,
    CONSTRAINT ck_bronze_catalogo_regras_raw_json CHECK (ISJSON(payload) = 1)
);
GO

/* Índices nas chaves de lote (consultas por carga e verificação de FK). */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_obras_raw_carga' AND object_id = OBJECT_ID(N'bronze.obras_raw'))
    CREATE INDEX ix_bronze_obras_raw_carga ON bronze.obras_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_medicoes_raw_carga' AND object_id = OBJECT_ID(N'bronze.medicoes_raw'))
    CREATE INDEX ix_bronze_medicoes_raw_carga ON bronze.medicoes_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_categorias_raw_carga' AND object_id = OBJECT_ID(N'bronze.categorias_raw'))
    CREATE INDEX ix_bronze_categorias_raw_carga ON bronze.categorias_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_servicos_raw_carga' AND object_id = OBJECT_ID(N'bronze.servicos_raw'))
    CREATE INDEX ix_bronze_servicos_raw_carga ON bronze.servicos_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_valores_raw_carga' AND object_id = OBJECT_ID(N'bronze.valores_raw'))
    CREATE INDEX ix_bronze_valores_raw_carga ON bronze.valores_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_ocorrencias_raw_carga' AND object_id = OBJECT_ID(N'bronze.ocorrencias_raw'))
    CREATE INDEX ix_bronze_ocorrencias_raw_carga ON bronze.ocorrencias_raw (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bronze_catalogo_regras_raw_carga' AND object_id = OBJECT_ID(N'bronze.catalogo_regras_raw'))
    CREATE INDEX ix_bronze_catalogo_regras_raw_carga ON bronze.catalogo_regras_raw (carga_id);
GO
