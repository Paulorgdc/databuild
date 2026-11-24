/* 004 - Visões da Silver: serviços, categorias, erros primários, cascatas e verificações de qualidade. */

CREATE OR ALTER VIEW silver.vw_servico AS
SELECT i.item_id, i.medicao_id, m.obra_id, m.rotulo AS medicao, i.codigo, p.codigo AS codigo_pai,
       i.descricao, i.unidade, i.codigo_servico_ref, i.fonte
FROM silver.item_medicao AS i
JOIN silver.medicao      AS m ON m.medicao_id = i.medicao_id
LEFT JOIN silver.item_medicao AS p ON p.medicao_id = i.medicao_id AND p.item_id = i.pai_item_id
WHERE i.tipo = N'folha';
GO

CREATE OR ALTER VIEW silver.vw_categoria AS
SELECT i.item_id, i.medicao_id, m.obra_id, m.rotulo AS medicao, i.codigo, i.tipo, i.nivel,
       p.codigo AS codigo_pai, i.descricao
FROM silver.item_medicao AS i
JOIN silver.medicao      AS m ON m.medicao_id = i.medicao_id
LEFT JOIN silver.item_medicao AS p ON p.medicao_id = i.medicao_id AND p.item_id = i.pai_item_id
WHERE i.tipo <> N'folha';
GO

/* Erros primários: o valor armazenado diverge do que a própria linha deveria produzir. */
CREATE OR ALTER VIEW silver.vw_erro_primario AS
SELECT o.ocorrencia_id, o.obra_id, o.medicao_id, m.rotulo AS medicao, o.regra_id, o.versao_regra,
       o.classe, o.severidade, o.aba, o.celula, o.item_id, o.campo, o.descricao,
       o.valor_original, o.valor_esperado, o.diferenca, o.contabiliza_impacto,
       (SELECT COUNT(*) FROM silver.ocorrencia_causa_raiz AS c WHERE c.primaria_id = o.ocorrencia_id) AS n_cascatas
FROM silver.ocorrencia AS o
JOIN silver.medicao    AS m ON m.medicao_id = o.medicao_id
WHERE o.causa = N'primaria';
GO

/* Cascatas: efeito herdado de erro anterior, com as causas raiz primárias. */
CREATE OR ALTER VIEW silver.vw_cascata AS
SELECT o.ocorrencia_id, o.obra_id, o.medicao_id, m.rotulo AS medicao, o.regra_id, o.versao_regra,
       o.classe, o.severidade, o.aba, o.celula, o.item_id, o.campo, o.descricao,
       o.valor_original, o.valor_esperado, o.diferenca,
       (SELECT STRING_AGG(c.primaria_id, N',') WITHIN GROUP (ORDER BY c.primaria_id)
          FROM silver.ocorrencia_causa_raiz AS c WHERE c.cascata_id = o.ocorrencia_id) AS causas_raiz
FROM silver.ocorrencia AS o
JOIN silver.medicao    AS m ON m.medicao_id = o.medicao_id
WHERE o.causa = N'cascata';
GO

/* Verificações de qualidade: devem devolver 0 linhas em um banco consistente. */
CREATE OR ALTER VIEW silver.vw_qualidade_cascata_sem_causa AS
SELECT o.ocorrencia_id
FROM silver.ocorrencia AS o
WHERE o.causa = N'cascata'
  AND NOT EXISTS (SELECT 1 FROM silver.ocorrencia_causa_raiz AS c WHERE c.cascata_id = o.ocorrencia_id);
GO

CREATE OR ALTER VIEW silver.vw_qualidade_impacto_duplicado AS
SELECT o.medicao_id, o.aba, o.celula, COUNT(*) AS n
FROM silver.ocorrencia AS o
WHERE o.contabiliza_impacto = 1
GROUP BY o.medicao_id, o.aba, o.celula
HAVING COUNT(*) > 1;
GO
