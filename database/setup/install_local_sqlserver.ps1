<#
.SYNOPSIS
  Instala (uma vez) o SQL Server 2022 Express local e o ODBC Driver 18 usados pelo DataBuild.
.DESCRIPTION
  Requer PowerShell COMO ADMINISTRADOR. Gratuito, sem Docker. Não cria senhas nem logins de aplicação:
  o acesso administrativo é a conta do Windows que roda o script; os logins do pipeline e do Power BI
  são criados depois por `python scripts/dbadmin.py provision`.
  O script: baixa a mídia oficial da Microsoft e confere a assinatura digital; instala a instância
  SQLEXPRESS (mecanismo de banco apenas); habilita autenticação mista (a conta `sa` permanece desabilitada);
  fixa o TCP na porta 1433; reinicia o serviço; instala o ODBC Driver 18 (winget, ou msi oficial).
  Idempotente: se a instância já existe, só reaplica a configuração.
.EXAMPLE
  Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "database\setup\install_local_sqlserver.ps1"'
#>
[CmdletBinding()]
param(
    [string]$Instancia = 'SQLEXPRESS',
    [int]$Porta = 1433,
    [string]$Midia = ''          # caminho de um SQLEXPR_x64_ENU.exe já baixado (opcional)
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Execute como administrador (veja o exemplo na ajuda do script).' }

$servico = "MSSQL`$$Instancia"
if (-not (Get-Service $servico -ErrorAction SilentlyContinue)) {
    if (-not $Midia) {
        $Midia = Join-Path $env:TEMP 'SQLEXPR_x64_ENU.exe'
        Write-Host 'Baixando SQL Server 2022 Express (mídia oficial)...'
        Invoke-WebRequest -UseBasicParsing -OutFile $Midia -Uri 'https://download.microsoft.com/download/3/8/d/38de7036-2433-4207-8eae-06e247e17b25/SQLEXPR_x64_ENU.exe'
    }
    $sig = Get-AuthenticodeSignature $Midia
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw "Assinatura inválida em $Midia" }

    $quem = "$env:USERDOMAIN\$env:USERNAME"
    Write-Host "Instalando a instância $Instancia (administrador do SQL: $quem)..."
    $setupArgs = @('/Q', '/ACTION=Install', '/IACCEPTSQLSERVERLICENSETERMS', "/INSTANCENAME=$Instancia",
              '/FEATURES=SQLEngine', '/TCPENABLED=1', '/UPDATEENABLED=0', "/SQLSYSADMINACCOUNTS=$quem")
    $p = Start-Process -FilePath $Midia -ArgumentList $setupArgs -Wait -PassThru
    if ($p.ExitCode -notin 0, 3010) { throw "Instalação falhou (código $($p.ExitCode)). Veja %ProgramFiles%\Microsoft SQL Server\160\Setup Bootstrap\Log\Summary.txt" }
}

# Autenticação mista + TCP fixo na porta, via registro (a conta sa continua desabilitada).
$raiz = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL').$Instancia
$base = "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\$raiz\MSSQLServer"
Set-ItemProperty -Path $base -Name LoginMode -Value 2
$tcp = "$base\SuperSocketNetLib\Tcp"
Set-ItemProperty -Path $tcp -Name Enabled -Value 1
Set-ItemProperty -Path "$tcp\IPAll" -Name TcpDynamicPorts -Value ''
Set-ItemProperty -Path "$tcp\IPAll" -Name TcpPort -Value "$Porta"
Restart-Service $servico -Force
Set-Service $servico -StartupType Automatic

# ODBC Driver 18 (necessário ao pyodbc, Pandas via SQLAlchemy e, depois, ao Airflow).
if (-not (Get-OdbcDriver -Name 'ODBC Driver 18 for SQL Server' -ErrorAction SilentlyContinue)) {
    winget install --id Microsoft.msodbcsql.18 --source winget --silent --accept-package-agreements --accept-source-agreements
}
Write-Host "Pronto. Servidor: localhost,$Porta  |  Próximo: python scripts/dbadmin.py init-env; migrate; provision; check"
