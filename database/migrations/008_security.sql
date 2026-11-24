/* 008 - Papéis e permissões (sem logins e sem senhas; os logins são criados por `dbadmin.py provision`
   a partir de variáveis de ambiente).
   rl_databuild_pipeline     : processamento do pipeline (lê e grava bronze, silver e gold, executa procedimentos).
   rl_databuild_powerbi_reader: somente leitura da Gold; acesso negado explicitamente a bronze e silver. */
IF DATABASE_PRINCIPAL_ID(N'rl_databuild_pipeline') IS NULL CREATE ROLE rl_databuild_pipeline AUTHORIZATION dbo;
GO
IF DATABASE_PRINCIPAL_ID(N'rl_databuild_powerbi_reader') IS NULL CREATE ROLE rl_databuild_powerbi_reader AUTHORIZATION dbo;
GO

GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::bronze TO rl_databuild_pipeline;
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::silver TO rl_databuild_pipeline;
GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::gold   TO rl_databuild_pipeline;
GO

GRANT SELECT ON SCHEMA::gold TO rl_databuild_powerbi_reader;
DENY  SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::bronze TO rl_databuild_powerbi_reader;
DENY  SELECT, INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::silver TO rl_databuild_powerbi_reader;
DENY  INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::gold TO rl_databuild_powerbi_reader;
GO
