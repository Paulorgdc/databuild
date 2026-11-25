/* 011 - Índices nas FKs de origem e execução da Silver. */

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'silver.medicao') AND name=N'ix_silver_medicao_execucao')
    CREATE INDEX ix_silver_medicao_execucao ON silver.medicao(execucao_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'silver.item_medicao') AND name=N'ix_silver_item_medicao_aba')
    CREATE INDEX ix_silver_item_medicao_aba ON silver.item_medicao(aba_origem_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'silver.medicao_origem') AND name=N'ix_silver_medicao_origem_aba')
    CREATE INDEX ix_silver_medicao_origem_aba ON silver.medicao_origem(aba_origem_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'silver.ocorrencia_origem') AND name=N'ix_silver_ocorrencia_origem_aba')
    CREATE INDEX ix_silver_ocorrencia_origem_aba ON silver.ocorrencia_origem(aba_origem_id);
