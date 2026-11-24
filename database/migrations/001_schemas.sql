/* 001 - Schemas da arquitetura medalhão: bronze (bruto), silver (tratado e validado), gold (dimensional). */
IF SCHEMA_ID(N'bronze') IS NULL EXEC (N'CREATE SCHEMA bronze AUTHORIZATION dbo');
GO
IF SCHEMA_ID(N'silver') IS NULL EXEC (N'CREATE SCHEMA silver AUTHORIZATION dbo');
GO
IF SCHEMA_ID(N'gold') IS NULL EXEC (N'CREATE SCHEMA gold AUTHORIZATION dbo');
GO
