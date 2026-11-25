/* 010 - Execução da promoção, origem Bronze e escrita JDBC dos valores tipados. */

IF OBJECT_ID(N'silver.execucao', N'U') IS NULL
CREATE TABLE silver.execucao (
    execucao_id CHAR(32) NOT NULL CONSTRAINT pk_silver_execucao PRIMARY KEY,
    manifesto CHAR(64) NOT NULL,
    versao NVARCHAR(32) NOT NULL,
    status NVARCHAR(12) NOT NULL,
    n_arquivos INT NOT NULL,
    iniciada_em DATETIME2(3) NOT NULL CONSTRAINT df_silver_execucao_inicio DEFAULT SYSUTCDATETIME(),
    finalizada_em DATETIME2(3) NULL,
    etapa NVARCHAR(30) NOT NULL,
    erro_tipo NVARCHAR(80) NULL,
    CONSTRAINT ck_silver_execucao_status CHECK (status IN (N'iniciada', N'concluida', N'falhou'))
);
CREATE UNIQUE INDEX ux_silver_execucao_manifesto ON silver.execucao (manifesto) WHERE status = N'concluida';
GO

ALTER TABLE silver.medicao ADD
    execucao_id CHAR(32) NULL CONSTRAINT fk_silver_medicao_execucao REFERENCES silver.execucao(execucao_id),
    arquivo_origem_id INT NULL CONSTRAINT fk_silver_medicao_arquivo REFERENCES bronze.arquivo(arquivo_id);
ALTER TABLE silver.medicao ALTER COLUMN contrato_total DECIMAL(18,2) NULL;
GO

CREATE UNIQUE INDEX ux_silver_medicao_arquivo ON silver.medicao(arquivo_origem_id) WHERE arquivo_origem_id IS NOT NULL;

ALTER TABLE silver.item_medicao ADD
    aba_origem_id INT NULL CONSTRAINT fk_silver_item_aba REFERENCES bronze.aba(aba_id),
    linha_origem INT NULL,
    celula_origem NVARCHAR(30) NULL;

IF OBJECT_ID(N'silver.medicao_origem', N'U') IS NULL
CREATE TABLE silver.medicao_origem (
    medicao_id INT NOT NULL CONSTRAINT fk_silver_medicao_origem_medicao REFERENCES silver.medicao(medicao_id),
    campo NVARCHAR(30) NOT NULL,
    aba_origem_id INT NOT NULL CONSTRAINT fk_silver_medicao_origem_aba REFERENCES bronze.aba(aba_id),
    linha_origem INT NOT NULL,
    celula_origem NVARCHAR(30) NOT NULL,
    CONSTRAINT pk_silver_medicao_origem PRIMARY KEY (medicao_id, campo)
);
GO

ALTER TABLE silver.valor_item ADD
    celula_origem NVARCHAR(30) NULL,
    situacao NVARCHAR(22) NULL CONSTRAINT ck_silver_valor_item_situacao
        CHECK (situacao IN (N'pendente_contratual', N'pendente_dados', N'com_ocorrencia', N'sem_ocorrencia'));
ALTER TABLE silver.valor_item DROP CONSTRAINT ck_silver_valor_item_campo;
ALTER TABLE silver.valor_item ADD CONSTRAINT ck_silver_valor_item_campo CHECK (campo IN
    (N'valor_contrato', N'val_ant', N'val_med', N'val_ac', N'saldo', N'qtd_contrato', N'preco',
     N'qtd_ant', N'qtd_med', N'qtd_ac', N'saldo_pct'));

ALTER TABLE silver.formula_item DROP CONSTRAINT ck_silver_formula_item_campo;
ALTER TABLE silver.formula_item ADD CONSTRAINT ck_silver_formula_item_campo CHECK (campo IN
    (N'valor_contrato', N'val_ant', N'val_med', N'val_ac', N'saldo', N'qtd_contrato', N'preco',
     N'qtd_ant', N'qtd_med', N'qtd_ac', N'saldo_pct'));

IF OBJECT_ID(N'silver.ocorrencia_origem', N'U') IS NULL
CREATE TABLE silver.ocorrencia_origem (
    ocorrencia_id NVARCHAR(20) NOT NULL CONSTRAINT fk_silver_ocorrencia_origem_ocorrencia REFERENCES silver.ocorrencia(ocorrencia_id),
    aba_origem_id INT NOT NULL CONSTRAINT fk_silver_ocorrencia_origem_aba REFERENCES bronze.aba(aba_id),
    linha_origem INT NOT NULL,
    celula_origem NVARCHAR(30) NOT NULL,
    papel NVARCHAR(10) NOT NULL,
    CONSTRAINT pk_silver_ocorrencia_origem PRIMARY KEY (ocorrencia_id, aba_origem_id, celula_origem, papel),
    CONSTRAINT ck_silver_ocorrencia_origem_papel CHECK (papel IN (N'alvo', N'anterior', N'contexto'))
);
CREATE UNIQUE INDEX ux_silver_ocorrencia_impacto
    ON silver.ocorrencia (medicao_id, aba, celula) WHERE contabiliza_impacto = 1;
GO

IF OBJECT_ID(N'silver.stg_valor_item', N'U') IS NULL
CREATE TABLE silver.stg_valor_item (
    execucao_id CHAR(32) NOT NULL CONSTRAINT fk_silver_stg_valor_execucao REFERENCES silver.execucao(execucao_id),
    arquivo_id INT NOT NULL,
    codigo NVARCHAR(40) NOT NULL,
    campo NVARCHAR(20) NOT NULL,
    valor_armazenado DECIMAL(18,4) NULL,
    valor_recalculado DECIMAL(18,4) NULL,
    formula_texto NVARCHAR(1000) NULL,
    celula_origem NVARCHAR(30) NOT NULL,
    situacao NVARCHAR(22) NOT NULL,
    CONSTRAINT pk_silver_stg_valor PRIMARY KEY (execucao_id, arquivo_id, codigo, campo)
);
