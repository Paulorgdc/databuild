param(
    [Parameter(Mandatory)][ValidateRange(1, 65535)][int]$Port,
    [Parameter(Mandatory)][string]$QueryFile,
    [string]$DesktopBin = "$env:ProgramFiles\Microsoft Power BI Desktop\bin"
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$client = [Reflection.Assembly]::LoadFrom((Join-Path $DesktopBin 'Microsoft.PowerBI.AdomdClient.dll'))
$type = $client.GetType('Microsoft.AnalysisServices.AdomdClient.AdomdConnection', $true)
$connection = [Activator]::CreateInstance($type, "Data Source=localhost:$Port")
try {
    $connection.Open()
    $command = $connection.CreateCommand()
    $command.CommandText = [IO.File]::ReadAllText($QueryFile)
    $reader = $command.ExecuteReader()
    try {
        $rows = [Collections.Generic.List[object]]::new()
        while ($reader.Read()) {
            $row = [Collections.Generic.List[object]]::new()
            for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                if ($reader.IsDBNull($i)) {
                    $row.Add($null)
                } else {
                    $row.Add([Convert]::ToString($reader.GetValue($i), [Globalization.CultureInfo]::InvariantCulture))
                }
            }
            $rows.Add($row.ToArray())
        }
        ConvertTo-Json -InputObject $rows.ToArray() -Compress
    } finally {
        $reader.Dispose()
        $command.Dispose()
    }
} finally {
    $connection.Dispose()
}
