/* 006 - Dados de referência da Gold (não são dados da empresa): calendário, dimensões fixas e membros "não informado".
   Idempotente: só insere o que falta. */

/* Membro "não informado" do calendário (chave 0). */
IF NOT EXISTS (SELECT 1 FROM gold.dim_data WHERE data_key = 0)
    INSERT gold.dim_data (data_key, data, ano, trimestre, mes, nome_mes, ano_mes, dia, dia_semana, nome_dia_semana)
    VALUES (0, '19000101', 1900, 1, 1, N'não informado', '1900-01', 1, 1, N'não informado');
GO

/* Calendário 2020-01-01 a 2035-12-31. dia_semana não depende de SET DATEFIRST: 1900-01-01 foi segunda-feira. */
;WITH dias AS (
    SELECT CAST('20200101' AS DATE) AS d
    UNION ALL
    SELECT DATEADD(DAY, 1, d) FROM dias WHERE d < '20351231'
)
INSERT gold.dim_data (data_key, data, ano, trimestre, mes, nome_mes, ano_mes, dia, dia_semana, nome_dia_semana)
SELECT YEAR(d) * 10000 + MONTH(d) * 100 + DAY(d), d, YEAR(d), DATEPART(QUARTER, d), MONTH(d),
       CHOOSE(MONTH(d), N'janeiro', N'fevereiro', N'março', N'abril', N'maio', N'junho', N'julho',
                        N'agosto', N'setembro', N'outubro', N'novembro', N'dezembro'),
       CONVERT(CHAR(7), d, 126),
       DAY(d),
       DATEDIFF(DAY, '19000101', d) % 7 + 1,
       CHOOSE(DATEDIFF(DAY, '19000101', d) % 7 + 1, N'segunda-feira', N'terça-feira', N'quarta-feira',
                        N'quinta-feira', N'sexta-feira', N'sábado', N'domingo')
FROM dias
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_data AS x WHERE x.data = dias.d)
OPTION (MAXRECURSION 0);
GO

/* Membros "não informado" de obra e item (chave -1), para fatos sem item associado. */
IF NOT EXISTS (SELECT 1 FROM gold.dim_obra WHERE obra_key = -1)
BEGIN
    SET IDENTITY_INSERT gold.dim_obra ON;
    INSERT gold.dim_obra (obra_key, obra_id_origem, obra_codigo, nome) VALUES (-1, NULL, N'nao_informado', N'Não informado');
    SET IDENTITY_INSERT gold.dim_obra OFF;
END;
GO

IF NOT EXISTS (SELECT 1 FROM gold.dim_item WHERE item_key = -1)
BEGIN
    SET IDENTITY_INSERT gold.dim_item ON;
    INSERT gold.dim_item (item_key, obra_key, codigo, tipo, nivel, descricao)
    VALUES (-1, -1, N'nao_informado', N'nao_informado', 0, N'Ocorrência sem item associado');
    SET IDENTITY_INSERT gold.dim_item OFF;
END;
GO

/* Dimensões fixas (ordem de leitura de baixo para cima na gravidade). */
INSERT gold.dim_classe (classe_key, classe, rotulo, ordem)
SELECT v.k, v.c, v.r, v.o
FROM (VALUES (1, N'comprovado',                  N'Erro comprovado',                 1),
             (2, N'suspeita',                    N'Suspeita',                        2),
             (3, N'depende_validacao_contratual', N'Depende de validação contratual', 3),
             (4, N'excecao_legitima',            N'Exceção legítima',                4)) AS v (k, c, r, o)
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_classe AS x WHERE x.classe_key = v.k);
GO

INSERT gold.dim_severidade (severidade_key, severidade, rotulo, ordem)
SELECT v.k, v.s, v.r, v.o
FROM (VALUES (1, N'baixa',   N'Baixa',   1),
             (2, N'media',   N'Média',   2),
             (3, N'alta',    N'Alta',    3),
             (4, N'critica', N'Crítica', 4)) AS v (k, s, r, o)
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_severidade AS x WHERE x.severidade_key = v.k);
GO

INSERT gold.dim_causa (causa_key, causa, rotulo)
SELECT v.k, v.c, v.r
FROM (VALUES (1, N'primaria', N'Erro primário'),
             (2, N'cascata',  N'Cascata (efeito herdado)')) AS v (k, c, r)
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_causa AS x WHERE x.causa_key = v.k);
GO
