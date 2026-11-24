/* 003 - Camada Silver: dado tipado, normalizado e validado por chaves e restrições.
   Valores financeiros em DECIMAL (nunca FLOAT): DECIMAL(18,2) para valores contábeis e
   DECIMAL(18,4) para valores armazenados/recalculados, que preservam a precisão do recálculo. */

/* ---------- obras ---------- */
IF OBJECT_ID(N'silver.obra', N'U') IS NULL
CREATE TABLE silver.obra (
    obra_id     INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_obra PRIMARY KEY,
    obra_codigo NVARCHAR(40)  NOT NULL CONSTRAINT uq_silver_obra_codigo UNIQUE,   -- chave de negócio (obra_id do motor)
    nome        NVARCHAR(200) NOT NULL,
    criado_em   DATETIME2(0)  NOT NULL CONSTRAINT df_silver_obra_criado DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_silver_obra_nome CHECK (LEN(LTRIM(RTRIM(nome))) > 0)
);
GO

/* ---------- medições ---------- */
IF OBJECT_ID(N'silver.medicao', N'U') IS NULL
CREATE TABLE silver.medicao (
    medicao_id        INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_medicao PRIMARY KEY,
    obra_id           INT           NOT NULL CONSTRAINT fk_silver_medicao_obra REFERENCES silver.obra (obra_id),
    numero            SMALLINT      NOT NULL,
    rotulo            NVARCHAR(30)  NOT NULL,                 -- ex.: "MED 05"
    periodo_inicio    DATE          NULL,
    periodo_fim       DATE          NULL,
    contrato_total    DECIMAL(18,2) NOT NULL,                 -- total do contrato segundo o resumo da medição
    contrato_controle DECIMAL(18,2) NULL,                     -- contrato segundo o controle financeiro
    arquivo_sha256    CHAR(64)      NULL,                     -- rastreia o arquivo de origem
    n_servicos        INT           NOT NULL CONSTRAINT df_silver_medicao_nserv DEFAULT 0,
    n_categorias      INT           NOT NULL CONSTRAINT df_silver_medicao_ncat DEFAULT 0,
    carga_id          INT           NOT NULL CONSTRAINT fk_silver_medicao_carga REFERENCES bronze.carga (carga_id),
    CONSTRAINT uq_silver_medicao_obra_numero UNIQUE (obra_id, numero),
    CONSTRAINT uq_silver_medicao_obra_rotulo UNIQUE (obra_id, rotulo),
    CONSTRAINT uq_silver_medicao_id_obra     UNIQUE (medicao_id, obra_id),    -- alvo de FK composta (consistência obra x medição)
    CONSTRAINT ck_silver_medicao_numero  CHECK (numero > 0),
    CONSTRAINT ck_silver_medicao_periodo CHECK (periodo_inicio IS NULL OR periodo_fim IS NULL OR periodo_fim >= periodo_inicio),
    CONSTRAINT ck_silver_medicao_contrato CHECK (contrato_total >= 0 AND (contrato_controle IS NULL OR contrato_controle >= 0)),
    CONSTRAINT ck_silver_medicao_sha CHECK (arquivo_sha256 IS NULL OR arquivo_sha256 NOT LIKE '%[^0-9a-fA-F]%'),
    CONSTRAINT ck_silver_medicao_contagens CHECK (n_servicos >= 0 AND n_categorias >= 0)
);
GO

/* ---------- itens da medição: hierarquia total > lote > seção > serviço (folha) ---------- */
IF OBJECT_ID(N'silver.item_medicao', N'U') IS NULL
CREATE TABLE silver.item_medicao (
    item_id            BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_item_medicao PRIMARY KEY,
    medicao_id         INT           NOT NULL CONSTRAINT fk_silver_item_medicao_medicao REFERENCES silver.medicao (medicao_id),
    codigo             NVARCHAR(40)  NOT NULL,                -- ex.: "A.1.1.5"; "TOTAL" para o total da obra
    tipo               NVARCHAR(10)  NOT NULL,                -- folha = serviço; secao/lote = categorias; total
    pai_item_id        BIGINT        NULL,
    nivel              TINYINT       NOT NULL,
    descricao          NVARCHAR(500) NULL,
    unidade            NVARCHAR(20)  NULL,                    -- só serviços
    codigo_servico_ref NVARCHAR(40)  NULL,                    -- código na tabela de preços (ex.: SINAPI), só serviços
    fonte              NVARCHAR(40)  NULL,                    -- só serviços
    CONSTRAINT uq_silver_item_medicao_codigo UNIQUE (medicao_id, codigo),
    CONSTRAINT uq_silver_item_medicao_id     UNIQUE (medicao_id, item_id),    -- alvo de FKs compostas (item pertence à medição)
    CONSTRAINT fk_silver_item_medicao_pai FOREIGN KEY (medicao_id, pai_item_id) REFERENCES silver.item_medicao (medicao_id, item_id),
    CONSTRAINT ck_silver_item_medicao_tipo  CHECK (tipo IN (N'folha', N'secao', N'lote', N'total')),
    CONSTRAINT ck_silver_item_medicao_nivel CHECK (nivel BETWEEN 0 AND 9),
    CONSTRAINT ck_silver_item_medicao_total CHECK (tipo <> N'total' OR pai_item_id IS NULL),
    CONSTRAINT ck_silver_item_medicao_servico CHECK (tipo = N'folha' OR (unidade IS NULL AND codigo_servico_ref IS NULL AND fonte IS NULL))
);
GO

/* ---------- valores por item e campo (armazenado x recálculo independente) ---------- */
IF OBJECT_ID(N'silver.valor_item', N'U') IS NULL
CREATE TABLE silver.valor_item (
    valor_id          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_valor_item PRIMARY KEY,
    item_id           BIGINT        NOT NULL CONSTRAINT fk_silver_valor_item_item REFERENCES silver.item_medicao (item_id),
    campo             NVARCHAR(20)  NOT NULL,
    valor_armazenado  DECIMAL(18,4) NULL,                     -- valor guardado na planilha
    valor_recalculado DECIMAL(18,4) NULL,                     -- esperado a partir das entradas primárias
    CONSTRAINT uq_silver_valor_item UNIQUE (item_id, campo),
    CONSTRAINT ck_silver_valor_item_campo CHECK (campo IN (N'valor_contrato', N'val_ant', N'val_med', N'val_ac', N'saldo'))
);
GO

/* ---------- fórmulas das células (somente células que têm fórmula) ---------- */
IF OBJECT_ID(N'silver.formula_item', N'U') IS NULL
CREATE TABLE silver.formula_item (
    formula_id    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_formula_item PRIMARY KEY,
    item_id       BIGINT        NOT NULL CONSTRAINT fk_silver_formula_item_item REFERENCES silver.item_medicao (item_id),
    campo         NVARCHAR(20)  NOT NULL,
    formula_texto NVARCHAR(1000) NOT NULL,
    CONSTRAINT uq_silver_formula_item UNIQUE (item_id, campo),
    CONSTRAINT ck_silver_formula_item_campo CHECK (campo IN (N'valor_contrato', N'val_ant', N'val_med', N'val_ac', N'saldo')),
    CONSTRAINT ck_silver_formula_item_texto CHECK (LEN(formula_texto) > 0)
);
GO

/* ---------- regras (catálogo versionado) ---------- */
IF OBJECT_ID(N'silver.regra', N'U') IS NULL
CREATE TABLE silver.regra (
    regra_id         NVARCHAR(80)   NOT NULL,
    versao           NVARCHAR(20)   NOT NULL,
    versao_catalogo  NVARCHAR(20)   NOT NULL,
    categoria        NVARCHAR(30)   NOT NULL,
    classe_padrao    NVARCHAR(40)   NOT NULL,
    severidade_padrao NVARCHAR(10)  NOT NULL,
    titulo           NVARCHAR(200)  NOT NULL,
    descricao        NVARCHAR(1000) NOT NULL,
    sugestao         NVARCHAR(1000) NULL,
    usa_recalculo    BIT            NOT NULL CONSTRAINT df_silver_regra_recalculo DEFAULT 0,
    ativa            BIT            NOT NULL CONSTRAINT df_silver_regra_ativa DEFAULT 1,
    CONSTRAINT pk_silver_regra PRIMARY KEY (regra_id, versao),
    CONSTRAINT ck_silver_regra_categoria CHECK (categoria IN (N'estrutura', N'formula', N'aritmetica', N'referencia', N'controle', N'entre_medicoes', N'contrato')),
    CONSTRAINT ck_silver_regra_classe CHECK (classe_padrao IN (N'comprovado', N'suspeita', N'excecao_legitima', N'depende_validacao_contratual')),
    CONSTRAINT ck_silver_regra_severidade CHECK (severidade_padrao IN (N'baixa', N'media', N'alta', N'critica'))
);
GO

/* ---------- ocorrências (inconsistências): primárias e cascatas ---------- */
IF OBJECT_ID(N'silver.ocorrencia', N'U') IS NULL
CREATE TABLE silver.ocorrencia (
    ocorrencia_id       NVARCHAR(20)  NOT NULL CONSTRAINT pk_silver_ocorrencia PRIMARY KEY,   -- id determinístico do motor ("OC-...")
    obra_id             INT           NOT NULL,
    medicao_id          INT           NOT NULL,
    regra_id            NVARCHAR(80)  NOT NULL,
    versao_regra        NVARCHAR(20)  NOT NULL,
    classe              NVARCHAR(40)  NOT NULL,
    severidade          NVARCHAR(10)  NOT NULL,
    causa               NVARCHAR(10)  NOT NULL,                -- primaria | cascata
    aba                 NVARCHAR(100) NULL,
    celula              NVARCHAR(30)  NULL,
    item_id             BIGINT        NULL,
    campo               NVARCHAR(30)  NULL,
    descricao           NVARCHAR(2000) NOT NULL,
    evidencia           NVARCHAR(4000) NULL,
    valor_original      DECIMAL(18,2) NULL,
    valor_esperado      DECIMAL(18,2) NULL,
    diferenca           DECIMAL(18,2) NULL,                    -- original - esperado
    contabiliza_impacto BIT           NOT NULL CONSTRAINT df_silver_ocorrencia_impacto DEFAULT 0,
    sugestao            NVARCHAR(2000) NULL,
    carga_id            INT           NOT NULL CONSTRAINT fk_silver_ocorrencia_carga REFERENCES bronze.carga (carga_id),
    CONSTRAINT uq_silver_ocorrencia_causa UNIQUE (ocorrencia_id, causa),     -- alvo das FKs de causa raiz
    CONSTRAINT fk_silver_ocorrencia_medicao FOREIGN KEY (medicao_id, obra_id) REFERENCES silver.medicao (medicao_id, obra_id),
    CONSTRAINT fk_silver_ocorrencia_regra   FOREIGN KEY (regra_id, versao_regra) REFERENCES silver.regra (regra_id, versao),
    CONSTRAINT fk_silver_ocorrencia_item    FOREIGN KEY (medicao_id, item_id) REFERENCES silver.item_medicao (medicao_id, item_id),
    CONSTRAINT ck_silver_ocorrencia_classe CHECK (classe IN (N'comprovado', N'suspeita', N'excecao_legitima', N'depende_validacao_contratual')),
    CONSTRAINT ck_silver_ocorrencia_severidade CHECK (severidade IN (N'baixa', N'media', N'alta', N'critica')),
    CONSTRAINT ck_silver_ocorrencia_causa CHECK (causa IN (N'primaria', N'cascata')),
    /* O mesmo dinheiro nunca é contado duas vezes: só primária carrega impacto financeiro. */
    CONSTRAINT ck_silver_ocorrencia_impacto CHECK (contabiliza_impacto = 0 OR (causa = N'primaria' AND diferenca IS NOT NULL))
);
GO

/* ---------- rastreabilidade cascata -> causa raiz primária ---------- */
IF OBJECT_ID(N'silver.ocorrencia_causa_raiz', N'U') IS NULL
CREATE TABLE silver.ocorrencia_causa_raiz (
    cascata_id     NVARCHAR(20) NOT NULL,
    primaria_id    NVARCHAR(20) NOT NULL,
    /* Colunas constantes que, com a FK composta, garantem pelo esquema que o lado "cascata"
       é cascata e o lado "primaria" é primária. */
    cascata_causa  NVARCHAR(10) NOT NULL CONSTRAINT df_silver_causa_raiz_c DEFAULT N'cascata',
    primaria_causa NVARCHAR(10) NOT NULL CONSTRAINT df_silver_causa_raiz_p DEFAULT N'primaria',
    CONSTRAINT pk_silver_ocorrencia_causa_raiz PRIMARY KEY (cascata_id, primaria_id),
    CONSTRAINT ck_silver_causa_raiz_c CHECK (cascata_causa = N'cascata'),
    CONSTRAINT ck_silver_causa_raiz_p CHECK (primaria_causa = N'primaria'),
    CONSTRAINT fk_silver_causa_raiz_cascata  FOREIGN KEY (cascata_id,  cascata_causa)  REFERENCES silver.ocorrencia (ocorrencia_id, causa),
    CONSTRAINT fk_silver_causa_raiz_primaria FOREIGN KEY (primaria_id, primaria_causa) REFERENCES silver.ocorrencia (ocorrencia_id, causa)
);
GO

/* ---------- log de processamento (rastreabilidade operacional) ---------- */
IF OBJECT_ID(N'silver.log_processamento', N'U') IS NULL
CREATE TABLE silver.log_processamento (
    log_id    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_silver_log_processamento PRIMARY KEY,
    carga_id  INT            NOT NULL CONSTRAINT fk_silver_log_carga REFERENCES bronze.carga (carga_id),
    etapa     NVARCHAR(60)   NOT NULL,
    nivel     NVARCHAR(10)   NOT NULL CONSTRAINT df_silver_log_nivel DEFAULT N'info',
    mensagem  NVARCHAR(2000) NOT NULL,
    criado_em DATETIME2(0)   NOT NULL CONSTRAINT df_silver_log_criado DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_silver_log_nivel CHECK (nivel IN (N'info', N'aviso', N'erro'))
);
GO

/* ---------- índices ---------- */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_medicao_carga' AND object_id = OBJECT_ID(N'silver.medicao'))
    CREATE INDEX ix_silver_medicao_carga ON silver.medicao (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_item_medicao_pai' AND object_id = OBJECT_ID(N'silver.item_medicao'))
    CREATE INDEX ix_silver_item_medicao_pai ON silver.item_medicao (medicao_id, pai_item_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_ocorrencia_medicao_classe' AND object_id = OBJECT_ID(N'silver.ocorrencia'))
    CREATE INDEX ix_silver_ocorrencia_medicao_classe ON silver.ocorrencia (medicao_id, classe, severidade)
        INCLUDE (causa, diferenca, contabiliza_impacto);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_ocorrencia_regra' AND object_id = OBJECT_ID(N'silver.ocorrencia'))
    CREATE INDEX ix_silver_ocorrencia_regra ON silver.ocorrencia (regra_id, versao_regra);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_ocorrencia_item' AND object_id = OBJECT_ID(N'silver.ocorrencia'))
    CREATE INDEX ix_silver_ocorrencia_item ON silver.ocorrencia (medicao_id, item_id) WHERE item_id IS NOT NULL;
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_ocorrencia_carga' AND object_id = OBJECT_ID(N'silver.ocorrencia'))
    CREATE INDEX ix_silver_ocorrencia_carga ON silver.ocorrencia (carga_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_causa_raiz_primaria' AND object_id = OBJECT_ID(N'silver.ocorrencia_causa_raiz'))
    CREATE INDEX ix_silver_causa_raiz_primaria ON silver.ocorrencia_causa_raiz (primaria_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_silver_log_carga' AND object_id = OBJECT_ID(N'silver.log_processamento'))
    CREATE INDEX ix_silver_log_carga ON silver.log_processamento (carga_id, criado_em);
GO
