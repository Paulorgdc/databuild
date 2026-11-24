/* 000 - Cria o banco de dados. Executa no `master`, fora de transação.
   O runner (scripts/dbadmin.py) substitui $(DB_NAME) pelo nome validado do banco.
   Idempotente: não recria nem altera um banco existente. */
IF DB_ID(N'$(DB_NAME)') IS NULL
    CREATE DATABASE [$(DB_NAME)] COLLATE Latin1_General_100_CI_AS_SC;
GO

/* Recuperação SIMPLE: ambiente local/acadêmico, sem backup de log. */
ALTER DATABASE [$(DB_NAME)] SET RECOVERY SIMPLE;
GO
