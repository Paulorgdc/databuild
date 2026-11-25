/* 012 - Publicação da execução Silver na Gold, pendências e indicadores por medição. */

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'gold.fact_medicao')
           AND name = N'contrato_total' AND is_nullable = 0)
    ALTER TABLE gold.fact_medicao ALTER COLUMN contrato_total DECIMAL(18,2) NULL;
GO

IF OBJECT_ID(N'gold.publicacao', N'U') IS NULL
CREATE TABLE gold.publicacao (
    publicacao_id TINYINT NOT NULL CONSTRAINT pk_gold_publicacao PRIMARY KEY,
    execucao_id CHAR(32) NOT NULL,
    publicado_em DATETIME2(3) NOT NULL CONSTRAINT df_gold_publicacao_em DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_gold_publicacao_execucao UNIQUE (execucao_id),
    CONSTRAINT fk_gold_publicacao_execucao FOREIGN KEY (execucao_id) REFERENCES silver.execucao(execucao_id),
    CONSTRAINT ck_gold_publicacao_unica CHECK (publicacao_id = 1)
);
GO

IF OBJECT_ID(N'gold.fact_pendencia_valor', N'U') IS NULL
CREATE TABLE gold.fact_pendencia_valor (
    medicao_key INT NOT NULL,
    item_key INT NOT NULL,
    campo NVARCHAR(20) NOT NULL,
    situacao NVARCHAR(22) NOT NULL,
    CONSTRAINT pk_gold_fact_pendencia_valor PRIMARY KEY (medicao_key, item_key, campo),
    CONSTRAINT fk_gold_pendencia_valor FOREIGN KEY (medicao_key, item_key)
        REFERENCES gold.fact_valor_item(medicao_key, item_key),
    CONSTRAINT ck_gold_pendencia_situacao CHECK (situacao IN (N'pendente_dados', N'pendente_contratual'))
);
GO

CREATE OR ALTER VIEW gold.vw_qualidade_medicao AS
WITH ocorrencias AS (
    SELECT o.medicao_key, COUNT(*) AS ocorrencias,
           SUM(CASE WHEN c.classe = N'comprovado' THEN 1 ELSE 0 END) AS comprovadas,
           SUM(CASE WHEN c.classe = N'suspeita' THEN 1 ELSE 0 END) AS suspeitas,
           SUM(CASE WHEN c.classe = N'excecao_legitima' THEN 1 ELSE 0 END) AS excecoes,
           SUM(CASE WHEN c.classe = N'depende_validacao_contratual' THEN 1 ELSE 0 END) AS contratuais,
           SUM(CASE WHEN ca.causa = N'cascata' THEN 1 ELSE 0 END) AS cascatas,
           SUM(CONVERT(INT, o.contabiliza_impacto)) AS impactos_contabilizados,
           SUM(o.impacto_contabilizado) AS impacto_contabilizado
    FROM gold.fact_ocorrencia o
    JOIN gold.dim_classe c ON c.classe_key = o.classe_key
    JOIN gold.dim_causa ca ON ca.causa_key = o.causa_key
    GROUP BY o.medicao_key
), pendencias AS (
    SELECT medicao_key,
           SUM(CASE WHEN situacao = N'pendente_dados' THEN 1 ELSE 0 END) AS valores_pendentes_dados,
           SUM(CASE WHEN situacao = N'pendente_contratual' THEN 1 ELSE 0 END) AS valores_pendentes_contrato
    FROM gold.fact_pendencia_valor
    GROUP BY medicao_key
)
SELECT f.medicao_key, f.obra_key, f.data_key, m.numero, m.rotulo, f.n_servicos, f.n_categorias,
       COALESCE(o.ocorrencias, 0) AS ocorrencias, COALESCE(o.comprovadas, 0) AS comprovadas,
       COALESCE(o.suspeitas, 0) AS suspeitas, COALESCE(o.excecoes, 0) AS excecoes,
       COALESCE(o.contratuais, 0) AS contratuais, COALESCE(o.cascatas, 0) AS cascatas,
       COALESCE(o.impactos_contabilizados, 0) AS impactos_contabilizados,
       COALESCE(o.impacto_contabilizado, CAST(0 AS DECIMAL(38,2))) AS impacto_contabilizado,
       COALESCE(p.valores_pendentes_dados, 0) AS valores_pendentes_dados,
       COALESCE(p.valores_pendentes_contrato, 0) AS valores_pendentes_contrato
FROM gold.fact_medicao f
JOIN gold.dim_medicao m ON m.medicao_key = f.medicao_key
LEFT JOIN ocorrencias o ON o.medicao_key = f.medicao_key
LEFT JOIN pendencias p ON p.medicao_key = f.medicao_key;
GO

CREATE OR ALTER PROCEDURE gold.usp_publicar_execucao
    @execucao_id CHAR(32)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRY
        BEGIN TRANSACTION;
        DECLARE @lock INT, @n INT, @publicada CHAR(32);
        EXEC @lock = sys.sp_getapplock @Resource = N'DataBuild.Gold', @LockMode = N'Exclusive',
            @LockOwner = N'Transaction', @LockTimeout = 0;
        IF @lock < 0 THROW 51100, N'Já existe uma publicação Gold em execução.', 1;

        EXEC @lock = sys.sp_getapplock @Resource = N'DataBuild.Silver', @LockMode = N'Shared',
            @LockOwner = N'Transaction', @LockTimeout = 0;
        IF @lock < 0 THROW 51100, N'Uma publicação Silver está em execução.', 1;

        SELECT @n = e.n_arquivos
        FROM silver.execucao e WHERE e.execucao_id = @execucao_id AND e.status = N'concluida';
        IF @n IS NULL OR @n <= 0
            THROW 51101, N'A execução Silver precisa estar concluída e conter medições.', 1;
        IF @n <> (SELECT COUNT(*) FROM silver.medicao WHERE execucao_id = @execucao_id)
            OR EXISTS (SELECT 1 FROM silver.medicao WHERE execucao_id IS NULL OR execucao_id <> @execucao_id)
            THROW 51102, N'As medições Silver precisam pertencer integralmente à execução selecionada.', 1;

        SELECT @publicada = execucao_id FROM gold.publicacao WHERE publicacao_id = 1;
        IF @publicada IS NOT NULL
        BEGIN
            IF @publicada <> @execucao_id THROW 51103, N'A Gold já contém outra publicação.', 1;
            COMMIT TRANSACTION;
            SELECT N'inalterada' AS status, @execucao_id AS execucao_id;
            RETURN;
        END;

        /* A rotina dimensional legada reconstrói fatos. Cada tabela de destino precisa estar vazia. */
        IF EXISTS (SELECT 1 FROM gold.fact_medicao WITH (TABLOCKX, HOLDLOCK))
            OR EXISTS (SELECT 1 FROM gold.fact_valor_item WITH (TABLOCKX, HOLDLOCK))
            OR EXISTS (SELECT 1 FROM gold.fact_ocorrencia WITH (TABLOCKX, HOLDLOCK))
            OR EXISTS (SELECT 1 FROM gold.bridge_cascata_primaria WITH (TABLOCKX, HOLDLOCK))
            OR EXISTS (SELECT 1 FROM gold.fact_pendencia_valor WITH (TABLOCKX, HOLDLOCK))
            OR EXISTS (SELECT 1 FROM gold.dim_medicao)
            OR EXISTS (SELECT 1 FROM gold.dim_regra)
            OR EXISTS (SELECT 1 FROM gold.dim_obra WHERE obra_key <> -1)
            OR EXISTS (SELECT 1 FROM gold.dim_item WHERE item_key <> -1)
            THROW 51104, N'A Gold contém registros sem publicação registrada.', 1;

        IF EXISTS (SELECT 1 FROM silver.vw_qualidade_cascata_sem_causa)
            OR EXISTS (SELECT 1 FROM silver.vw_qualidade_impacto_duplicado)
            OR EXISTS (SELECT 1 FROM silver.ocorrencia WHERE contabiliza_impacto = 1
                AND (causa <> N'primaria' OR classe <> N'comprovado'))
            THROW 51105, N'A origem Silver falhou nas verificações de impacto ou de cascatas.', 1;

        EXEC gold.usp_carregar_gold;

        INSERT gold.fact_pendencia_valor (medicao_key, item_key, campo, situacao)
        SELECT m.medicao_key, d.item_key, v.campo, v.situacao
        FROM silver.valor_item v
        JOIN silver.item_medicao i ON i.item_id = v.item_id
        JOIN gold.dim_medicao m ON m.medicao_id_origem = i.medicao_id
        JOIN gold.dim_item d ON d.obra_key = m.obra_key AND d.codigo = i.codigo
        WHERE v.situacao IN (N'pendente_dados', N'pendente_contratual');

        IF @n <> (SELECT COUNT(*) FROM gold.fact_medicao)
            OR (SELECT COUNT(DISTINCT item_id) FROM silver.valor_item) <> (SELECT COUNT(*) FROM gold.fact_valor_item)
            OR (SELECT COUNT(*) FROM silver.ocorrencia) <> (SELECT COUNT(*) FROM gold.fact_ocorrencia)
            OR (SELECT COUNT(*) FROM silver.ocorrencia_causa_raiz) <> (SELECT COUNT(*) FROM gold.bridge_cascata_primaria)
            OR (SELECT COUNT(*) FROM silver.valor_item WHERE situacao IN (N'pendente_dados', N'pendente_contratual'))
                <> (SELECT COUNT(*) FROM gold.fact_pendencia_valor)
            OR (SELECT COALESCE(SUM(diferenca), 0) FROM silver.ocorrencia WHERE contabiliza_impacto = 1)
                <> (SELECT COALESCE(SUM(impacto_contabilizado), 0) FROM gold.fact_ocorrencia)
            THROW 51106, N'A reconciliação Gold/Silver falhou durante a publicação.', 1;

        INSERT gold.publicacao (publicacao_id, execucao_id) VALUES (1, @execucao_id);
        COMMIT TRANSACTION;
        SELECT N'concluida' AS status, @execucao_id AS execucao_id;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

/* O login de pipeline chama a publicação protegida. A cadeia de propriedade mantém a chamada interna. */
DENY EXECUTE ON OBJECT::gold.usp_carregar_gold TO rl_databuild_pipeline;
GO
