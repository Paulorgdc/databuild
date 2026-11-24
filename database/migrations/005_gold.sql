/* 005 - Camada Gold: modelo dimensional (Star Schema) para consumo direto pelo Power BI.
   Dimensões: data, obra, medição, item, regra, classe, severidade, causa.
   Fatos: fact_valor_item (medição x item), fact_medicao (por medição), fact_ocorrencia (por ocorrência).
   Ponte: bridge_cascata_primaria (cascata -> causa raiz primária).
   As chaves substitutas (_key) são geradas aqui; o id de origem fica em *_origem para a carga idempotente.
   Membro "não informado": chave -1 nas dimensões (0 em dim_data), criado em 006_gold_seed.sql. */

/* ---------- dimensões ---------- */
IF OBJECT_ID(N'gold.dim_data', N'U') IS NULL
CREATE TABLE gold.dim_data (
    data_key        INT           NOT NULL CONSTRAINT pk_gold_dim_data PRIMARY KEY,     -- AAAAMMDD; 0 = não informado
    data            DATE          NOT NULL CONSTRAINT uq_gold_dim_data_data UNIQUE,
    ano             SMALLINT      NOT NULL,
    trimestre       TINYINT       NOT NULL,
    mes             TINYINT       NOT NULL,
    nome_mes        NVARCHAR(20)  NOT NULL,
    ano_mes         CHAR(7)       NOT NULL,                                              -- AAAA-MM
    dia             TINYINT       NOT NULL,
    dia_semana      TINYINT       NOT NULL,                                              -- 1 = segunda ... 7 = domingo
    nome_dia_semana NVARCHAR(20)  NOT NULL
);
GO

IF OBJECT_ID(N'gold.dim_obra', N'U') IS NULL
CREATE TABLE gold.dim_obra (
    obra_key       INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_gold_dim_obra PRIMARY KEY,
    obra_id_origem INT           NULL CONSTRAINT uq_gold_dim_obra_origem UNIQUE,          -- silver.obra.obra_id
    obra_codigo    NVARCHAR(40)  NOT NULL,
    nome           NVARCHAR(200) NOT NULL
);
GO

IF OBJECT_ID(N'gold.dim_medicao', N'U') IS NULL
CREATE TABLE gold.dim_medicao (
    medicao_key        INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_gold_dim_medicao PRIMARY KEY,
    medicao_id_origem  INT          NULL CONSTRAINT uq_gold_dim_medicao_origem UNIQUE,   -- silver.medicao.medicao_id
    obra_key           INT          NOT NULL CONSTRAINT fk_gold_dim_medicao_obra REFERENCES gold.dim_obra (obra_key),
    numero             SMALLINT     NOT NULL,
    rotulo             NVARCHAR(30) NOT NULL,
    periodo_inicio_key INT          NOT NULL CONSTRAINT fk_gold_dim_medicao_ini REFERENCES gold.dim_data (data_key),
    periodo_fim_key    INT          NOT NULL CONSTRAINT fk_gold_dim_medicao_fim REFERENCES gold.dim_data (data_key),
    arquivo_sha256     CHAR(64)     NULL
);
GO

IF OBJECT_ID(N'gold.dim_item', N'U') IS NULL
CREATE TABLE gold.dim_item (
    item_key           INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_gold_dim_item PRIMARY KEY,
    obra_key           INT           NOT NULL CONSTRAINT fk_gold_dim_item_obra REFERENCES gold.dim_obra (obra_key),
    codigo             NVARCHAR(40)  NOT NULL,
    tipo               NVARCHAR(14)  NOT NULL,                     -- folha | secao | lote | total | nao_informado
    nivel              TINYINT       NOT NULL,
    descricao          NVARCHAR(500) NULL,                         -- Tipo 1: vale a descrição da medição mais recente
    unidade            NVARCHAR(20)  NULL,
    codigo_servico_ref NVARCHAR(40)  NULL,
    codigo_pai         NVARCHAR(40)  NULL,
    descricao_pai      NVARCHAR(500) NULL,
    CONSTRAINT uq_gold_dim_item UNIQUE (obra_key, codigo)
);
GO

IF OBJECT_ID(N'gold.dim_regra', N'U') IS NULL
CREATE TABLE gold.dim_regra (
    regra_key         INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_gold_dim_regra PRIMARY KEY,
    regra_id          NVARCHAR(80)  NOT NULL,
    versao            NVARCHAR(20)  NOT NULL,
    categoria         NVARCHAR(30)  NOT NULL,
    titulo            NVARCHAR(200) NOT NULL,
    classe_padrao     NVARCHAR(40)  NOT NULL,
    severidade_padrao NVARCHAR(10)  NOT NULL,
    usa_recalculo     BIT           NOT NULL,
    CONSTRAINT uq_gold_dim_regra UNIQUE (regra_id, versao)
);
GO

IF OBJECT_ID(N'gold.dim_classe', N'U') IS NULL
CREATE TABLE gold.dim_classe (
    classe_key TINYINT       NOT NULL CONSTRAINT pk_gold_dim_classe PRIMARY KEY,
    classe     NVARCHAR(40)  NOT NULL CONSTRAINT uq_gold_dim_classe UNIQUE,
    rotulo     NVARCHAR(80)  NOT NULL,
    ordem      TINYINT       NOT NULL
);
GO

IF OBJECT_ID(N'gold.dim_severidade', N'U') IS NULL
CREATE TABLE gold.dim_severidade (
    severidade_key TINYINT      NOT NULL CONSTRAINT pk_gold_dim_severidade PRIMARY KEY,
    severidade     NVARCHAR(10) NOT NULL CONSTRAINT uq_gold_dim_severidade UNIQUE,
    rotulo         NVARCHAR(40) NOT NULL,
    ordem          TINYINT      NOT NULL                           -- 1 = menos grave; usar para ordenar no Power BI
);
GO

IF OBJECT_ID(N'gold.dim_causa', N'U') IS NULL
CREATE TABLE gold.dim_causa (
    causa_key TINYINT      NOT NULL CONSTRAINT pk_gold_dim_causa PRIMARY KEY,
    causa     NVARCHAR(10) NOT NULL CONSTRAINT uq_gold_dim_causa UNIQUE,
    rotulo    NVARCHAR(40) NOT NULL
);
GO

/* ---------- fatos ---------- */
/* Grão: uma linha por medição x item. Valores armazenados e recalculados lado a lado. */
IF OBJECT_ID(N'gold.fact_valor_item', N'U') IS NULL
CREATE TABLE gold.fact_valor_item (
    medicao_key INT NOT NULL CONSTRAINT fk_gold_fact_valor_item_medicao REFERENCES gold.dim_medicao (medicao_key),
    item_key    INT NOT NULL CONSTRAINT fk_gold_fact_valor_item_item    REFERENCES gold.dim_item (item_key),
    valor_contrato        DECIMAL(18,4) NULL,
    val_ant               DECIMAL(18,4) NULL,
    val_med               DECIMAL(18,4) NULL,
    val_ac                DECIMAL(18,4) NULL,
    saldo                 DECIMAL(18,4) NULL,
    valor_contrato_recalc DECIMAL(18,4) NULL,
    val_ant_recalc        DECIMAL(18,4) NULL,
    val_med_recalc        DECIMAL(18,4) NULL,
    val_ac_recalc         DECIMAL(18,4) NULL,
    saldo_recalc          DECIMAL(18,4) NULL,
    CONSTRAINT pk_gold_fact_valor_item PRIMARY KEY (medicao_key, item_key)
);
GO

/* Grão: uma linha por medição (indicadores do total da obra). */
IF OBJECT_ID(N'gold.fact_medicao', N'U') IS NULL
CREATE TABLE gold.fact_medicao (
    medicao_key       INT NOT NULL CONSTRAINT pk_gold_fact_medicao PRIMARY KEY
                          CONSTRAINT fk_gold_fact_medicao_medicao REFERENCES gold.dim_medicao (medicao_key),
    obra_key          INT NOT NULL CONSTRAINT fk_gold_fact_medicao_obra REFERENCES gold.dim_obra (obra_key),
    data_key          INT NOT NULL CONSTRAINT fk_gold_fact_medicao_data REFERENCES gold.dim_data (data_key),
    contrato_total    DECIMAL(18,2) NOT NULL,
    contrato_controle DECIMAL(18,2) NULL,
    valor_medido_mes  DECIMAL(18,4) NULL,
    valor_acumulado   DECIMAL(18,4) NULL,
    saldo             DECIMAL(18,4) NULL,
    n_servicos        INT NOT NULL,
    n_categorias      INT NOT NULL
);
GO

/* Grão: uma linha por ocorrência. impacto_contabilizado soma só primárias (nunca conta o mesmo dinheiro duas vezes). */
IF OBJECT_ID(N'gold.fact_ocorrencia', N'U') IS NULL
CREATE TABLE gold.fact_ocorrencia (
    ocorrencia_key       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_gold_fact_ocorrencia PRIMARY KEY,
    ocorrencia_id        NVARCHAR(20)  NOT NULL CONSTRAINT uq_gold_fact_ocorrencia_id UNIQUE,   -- chave degenerada (rastreia a Silver)
    obra_key             INT     NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_obra REFERENCES gold.dim_obra (obra_key),
    medicao_key          INT     NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_medicao REFERENCES gold.dim_medicao (medicao_key),
    item_key             INT     NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_item REFERENCES gold.dim_item (item_key),
    regra_key            INT     NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_regra REFERENCES gold.dim_regra (regra_key),
    classe_key           TINYINT NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_classe REFERENCES gold.dim_classe (classe_key),
    severidade_key       TINYINT NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_sev REFERENCES gold.dim_severidade (severidade_key),
    causa_key            TINYINT NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_causa REFERENCES gold.dim_causa (causa_key),
    data_key             INT     NOT NULL CONSTRAINT fk_gold_fact_ocorrencia_data REFERENCES gold.dim_data (data_key),
    aba                  NVARCHAR(100) NULL,
    celula               NVARCHAR(30)  NULL,
    campo                NVARCHAR(30)  NULL,
    qtd_ocorrencia       TINYINT       NOT NULL CONSTRAINT df_gold_fact_ocorrencia_qtd DEFAULT 1,
    valor_original       DECIMAL(18,2) NULL,
    valor_esperado       DECIMAL(18,2) NULL,
    diferenca            DECIMAL(18,2) NULL,
    contabiliza_impacto  BIT           NOT NULL,
    impacto_contabilizado DECIMAL(18,2) NOT NULL CONSTRAINT df_gold_fact_ocorrencia_imp DEFAULT 0,
    CONSTRAINT ck_gold_fact_ocorrencia_qtd CHECK (qtd_ocorrencia = 1),
    CONSTRAINT ck_gold_fact_ocorrencia_imp CHECK (
        (contabiliza_impacto = 1 AND impacto_contabilizado = ISNULL(diferenca, 0)) OR
        (contabiliza_impacto = 0 AND impacto_contabilizado = 0))
);
GO

IF OBJECT_ID(N'gold.bridge_cascata_primaria', N'U') IS NULL
CREATE TABLE gold.bridge_cascata_primaria (
    cascata_key  BIGINT NOT NULL CONSTRAINT fk_gold_bridge_cascata  REFERENCES gold.fact_ocorrencia (ocorrencia_key),
    primaria_key BIGINT NOT NULL CONSTRAINT fk_gold_bridge_primaria REFERENCES gold.fact_ocorrencia (ocorrencia_key),
    CONSTRAINT pk_gold_bridge_cascata_primaria PRIMARY KEY (cascata_key, primaria_key),
    CONSTRAINT ck_gold_bridge_distintas CHECK (cascata_key <> primaria_key)
);
GO

/* ---------- índices nas chaves estrangeiras dos fatos (filtro e junção do Power BI) ---------- */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_valor_item_item' AND object_id = OBJECT_ID(N'gold.fact_valor_item'))
    CREATE INDEX ix_gold_fact_valor_item_item ON gold.fact_valor_item (item_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_medicao_obra' AND object_id = OBJECT_ID(N'gold.fact_medicao'))
    CREATE INDEX ix_gold_fact_medicao_obra ON gold.fact_medicao (obra_key, data_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_medicao' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_medicao ON gold.fact_ocorrencia (medicao_key, classe_key, severidade_key)
        INCLUDE (causa_key, diferenca, impacto_contabilizado);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_item' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_item ON gold.fact_ocorrencia (item_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_regra' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_regra ON gold.fact_ocorrencia (regra_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_obra_data' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_obra_data ON gold.fact_ocorrencia (obra_key, data_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_bridge_primaria' AND object_id = OBJECT_ID(N'gold.bridge_cascata_primaria'))
    CREATE INDEX ix_gold_bridge_primaria ON gold.bridge_cascata_primaria (primaria_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_dim_medicao_obra' AND object_id = OBJECT_ID(N'gold.dim_medicao'))
    CREATE INDEX ix_gold_dim_medicao_obra ON gold.dim_medicao (obra_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_dim_medicao_periodo_inicio' AND object_id = OBJECT_ID(N'gold.dim_medicao'))
    CREATE INDEX ix_gold_dim_medicao_periodo_inicio ON gold.dim_medicao (periodo_inicio_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_dim_medicao_periodo_fim' AND object_id = OBJECT_ID(N'gold.dim_medicao'))
    CREATE INDEX ix_gold_dim_medicao_periodo_fim ON gold.dim_medicao (periodo_fim_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_medicao_data' AND object_id = OBJECT_ID(N'gold.fact_medicao'))
    CREATE INDEX ix_gold_fact_medicao_data ON gold.fact_medicao (data_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_classe' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_classe ON gold.fact_ocorrencia (classe_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_severidade' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_severidade ON gold.fact_ocorrencia (severidade_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_causa' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_causa ON gold.fact_ocorrencia (causa_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_gold_fact_ocorrencia_data' AND object_id = OBJECT_ID(N'gold.fact_ocorrencia'))
    CREATE INDEX ix_gold_fact_ocorrencia_data ON gold.fact_ocorrencia (data_key);
GO
