/* 007 - Carga Silver -> Gold. Idempotente: atualiza dimensões, recarrega os fatos por inteiro, numa transação.
   Dimensões nunca são apagadas (preservam histórico); fatos são derivados e recalculados a cada execução.
   Quem chama precisa de SELECT na Silver e INSERT/UPDATE/DELETE na Gold (papel do pipeline). */
CREATE OR ALTER PROCEDURE gold.usp_carregar_gold
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    /* ---- fatos: limpar na ordem inversa das dependências ---- */
    DELETE FROM gold.bridge_cascata_primaria;
    DELETE FROM gold.fact_ocorrencia;
    DELETE FROM gold.fact_valor_item;
    DELETE FROM gold.fact_medicao;

    /* ---- dim_obra ---- */
    UPDATE d SET obra_codigo = s.obra_codigo, nome = s.nome
    FROM gold.dim_obra AS d
    JOIN silver.obra   AS s ON s.obra_id = d.obra_id_origem
    WHERE d.obra_codigo <> s.obra_codigo OR d.nome <> s.nome;

    INSERT gold.dim_obra (obra_id_origem, obra_codigo, nome)
    SELECT s.obra_id, s.obra_codigo, s.nome
    FROM silver.obra AS s
    WHERE NOT EXISTS (SELECT 1 FROM gold.dim_obra AS d WHERE d.obra_id_origem = s.obra_id);

    /* ---- dim_medicao ---- */
    UPDATE d SET numero = m.numero, rotulo = m.rotulo, arquivo_sha256 = m.arquivo_sha256,
                 periodo_inicio_key = ISNULL(YEAR(m.periodo_inicio) * 10000 + MONTH(m.periodo_inicio) * 100 + DAY(m.periodo_inicio), 0),
                 periodo_fim_key    = ISNULL(YEAR(m.periodo_fim)    * 10000 + MONTH(m.periodo_fim)    * 100 + DAY(m.periodo_fim), 0)
    FROM gold.dim_medicao AS d
    JOIN silver.medicao   AS m ON m.medicao_id = d.medicao_id_origem;

    INSERT gold.dim_medicao (medicao_id_origem, obra_key, numero, rotulo, periodo_inicio_key, periodo_fim_key, arquivo_sha256)
    SELECT m.medicao_id, o.obra_key, m.numero, m.rotulo,
           ISNULL(YEAR(m.periodo_inicio) * 10000 + MONTH(m.periodo_inicio) * 100 + DAY(m.periodo_inicio), 0),
           ISNULL(YEAR(m.periodo_fim)    * 10000 + MONTH(m.periodo_fim)    * 100 + DAY(m.periodo_fim), 0),
           m.arquivo_sha256
    FROM silver.medicao AS m
    JOIN gold.dim_obra  AS o ON o.obra_id_origem = m.obra_id
    WHERE NOT EXISTS (SELECT 1 FROM gold.dim_medicao AS d WHERE d.medicao_id_origem = m.medicao_id);

    /* ---- dim_item (Tipo 1: atributos da medição mais recente em que o item aparece) ---- */
    ;WITH base AS (
        SELECT m.obra_id, i.codigo, i.tipo, i.nivel, i.descricao, i.unidade, i.codigo_servico_ref,
               p.codigo AS codigo_pai, p.descricao AS descricao_pai,
               ROW_NUMBER() OVER (PARTITION BY m.obra_id, i.codigo ORDER BY m.numero DESC) AS rn
        FROM silver.item_medicao AS i
        JOIN silver.medicao      AS m ON m.medicao_id = i.medicao_id
        LEFT JOIN silver.item_medicao AS p ON p.medicao_id = i.medicao_id AND p.item_id = i.pai_item_id
    ), src AS (
        SELECT o.obra_key, b.* FROM base AS b JOIN gold.dim_obra AS o ON o.obra_id_origem = b.obra_id WHERE b.rn = 1
    )
    UPDATE d SET tipo = s.tipo, nivel = s.nivel, descricao = s.descricao, unidade = s.unidade,
                 codigo_servico_ref = s.codigo_servico_ref, codigo_pai = s.codigo_pai, descricao_pai = s.descricao_pai
    FROM gold.dim_item AS d
    JOIN src AS s ON s.obra_key = d.obra_key AND s.codigo = d.codigo
    WHERE d.item_key > 0;

    ;WITH base AS (
        SELECT m.obra_id, i.codigo, i.tipo, i.nivel, i.descricao, i.unidade, i.codigo_servico_ref,
               p.codigo AS codigo_pai, p.descricao AS descricao_pai,
               ROW_NUMBER() OVER (PARTITION BY m.obra_id, i.codigo ORDER BY m.numero DESC) AS rn
        FROM silver.item_medicao AS i
        JOIN silver.medicao      AS m ON m.medicao_id = i.medicao_id
        LEFT JOIN silver.item_medicao AS p ON p.medicao_id = i.medicao_id AND p.item_id = i.pai_item_id
    )
    INSERT gold.dim_item (obra_key, codigo, tipo, nivel, descricao, unidade, codigo_servico_ref, codigo_pai, descricao_pai)
    SELECT o.obra_key, b.codigo, b.tipo, b.nivel, b.descricao, b.unidade, b.codigo_servico_ref, b.codigo_pai, b.descricao_pai
    FROM base AS b
    JOIN gold.dim_obra AS o ON o.obra_id_origem = b.obra_id
    WHERE b.rn = 1
      AND NOT EXISTS (SELECT 1 FROM gold.dim_item AS d WHERE d.obra_key = o.obra_key AND d.codigo = b.codigo);

    /* ---- dim_regra ---- */
    UPDATE d SET categoria = r.categoria, titulo = r.titulo, classe_padrao = r.classe_padrao,
                 severidade_padrao = r.severidade_padrao, usa_recalculo = r.usa_recalculo
    FROM gold.dim_regra AS d
    JOIN silver.regra   AS r ON r.regra_id = d.regra_id AND r.versao = d.versao;

    INSERT gold.dim_regra (regra_id, versao, categoria, titulo, classe_padrao, severidade_padrao, usa_recalculo)
    SELECT r.regra_id, r.versao, r.categoria, r.titulo, r.classe_padrao, r.severidade_padrao, r.usa_recalculo
    FROM silver.regra AS r
    WHERE NOT EXISTS (SELECT 1 FROM gold.dim_regra AS d WHERE d.regra_id = r.regra_id AND d.versao = r.versao);

    /* ---- fact_medicao ---- */
    INSERT gold.fact_medicao (medicao_key, obra_key, data_key, contrato_total, contrato_controle,
                              valor_medido_mes, valor_acumulado, saldo, n_servicos, n_categorias)
    SELECT dm.medicao_key, dm.obra_key, dm.periodo_fim_key, m.contrato_total, m.contrato_controle,
           MAX(CASE WHEN v.campo = N'val_med' THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'val_ac'  THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'saldo'   THEN v.valor_armazenado END),
           m.n_servicos, m.n_categorias
    FROM silver.medicao AS m
    JOIN gold.dim_medicao AS dm ON dm.medicao_id_origem = m.medicao_id
    LEFT JOIN silver.item_medicao AS i ON i.medicao_id = m.medicao_id AND i.tipo = N'total'
    LEFT JOIN silver.valor_item   AS v ON v.item_id = i.item_id
    GROUP BY dm.medicao_key, dm.obra_key, dm.periodo_fim_key, m.contrato_total, m.contrato_controle, m.n_servicos, m.n_categorias;

    /* ---- fact_valor_item ---- */
    INSERT gold.fact_valor_item (medicao_key, item_key, valor_contrato, val_ant, val_med, val_ac, saldo,
                                 valor_contrato_recalc, val_ant_recalc, val_med_recalc, val_ac_recalc, saldo_recalc)
    SELECT dm.medicao_key, di.item_key,
           MAX(CASE WHEN v.campo = N'valor_contrato' THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'val_ant'        THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'val_med'        THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'val_ac'         THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'saldo'          THEN v.valor_armazenado END),
           MAX(CASE WHEN v.campo = N'valor_contrato' THEN v.valor_recalculado END),
           MAX(CASE WHEN v.campo = N'val_ant'        THEN v.valor_recalculado END),
           MAX(CASE WHEN v.campo = N'val_med'        THEN v.valor_recalculado END),
           MAX(CASE WHEN v.campo = N'val_ac'         THEN v.valor_recalculado END),
           MAX(CASE WHEN v.campo = N'saldo'          THEN v.valor_recalculado END)
    FROM silver.valor_item AS v
    JOIN silver.item_medicao AS i  ON i.item_id = v.item_id
    JOIN silver.medicao      AS m  ON m.medicao_id = i.medicao_id
    JOIN gold.dim_medicao    AS dm ON dm.medicao_id_origem = m.medicao_id
    JOIN gold.dim_item       AS di ON di.obra_key = dm.obra_key AND di.codigo = i.codigo
    GROUP BY dm.medicao_key, di.item_key;

    /* ---- fact_ocorrencia ---- */
    INSERT gold.fact_ocorrencia (ocorrencia_id, obra_key, medicao_key, item_key, regra_key, classe_key, severidade_key,
                                 causa_key, data_key, aba, celula, campo, valor_original, valor_esperado, diferenca,
                                 contabiliza_impacto, impacto_contabilizado)
    SELECT o.ocorrencia_id, dm.obra_key, dm.medicao_key, ISNULL(di.item_key, -1), dr.regra_key,
           dc.classe_key, ds.severidade_key, dca.causa_key, dm.periodo_fim_key, o.aba, o.celula, o.campo,
           o.valor_original, o.valor_esperado, o.diferenca, o.contabiliza_impacto,
           CASE WHEN o.contabiliza_impacto = 1 THEN ISNULL(o.diferenca, 0) ELSE 0 END
    FROM silver.ocorrencia AS o
    JOIN gold.dim_medicao  AS dm  ON dm.medicao_id_origem = o.medicao_id
    JOIN gold.dim_regra    AS dr  ON dr.regra_id = o.regra_id AND dr.versao = o.versao_regra
    JOIN gold.dim_classe   AS dc  ON dc.classe = o.classe
    JOIN gold.dim_severidade AS ds ON ds.severidade = o.severidade
    JOIN gold.dim_causa    AS dca ON dca.causa = o.causa
    LEFT JOIN silver.item_medicao AS i  ON i.item_id = o.item_id
    LEFT JOIN gold.dim_item       AS di ON di.obra_key = dm.obra_key AND di.codigo = i.codigo;

    /* ---- ponte cascata -> primária ---- */
    INSERT gold.bridge_cascata_primaria (cascata_key, primaria_key)
    SELECT fc.ocorrencia_key, fp.ocorrencia_key
    FROM silver.ocorrencia_causa_raiz AS c
    JOIN gold.fact_ocorrencia AS fc ON fc.ocorrencia_id = c.cascata_id
    JOIN gold.fact_ocorrencia AS fp ON fp.ocorrencia_id = c.primaria_id;

    COMMIT TRANSACTION;
END;
GO
