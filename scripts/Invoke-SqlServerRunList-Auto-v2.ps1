[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ConfigCsv,

    [string]$OutputFolder = ".\\output",

    [int]$BackupFreshnessHours = 24,

    [int]$JobFailureLookbackHours = 24,

    [int]$BackupRetentionDays = 14,

    [switch]$UseTrustedConnection = $true,

    [string]$SqlUser,

    [string]$SqlPassword
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NullableValue {
    param($Value)

    if ($null -eq $Value -or $Value -is [System.DBNull]) { return $null }
    return $Value
}

function Convert-ToDoubleSafe {
    param($Value)

    $safeValue = Get-NullableValue -Value $Value
    if ($null -eq $safeValue) { return $null }

    $number = 0.0
    if ([double]::TryParse($safeValue.ToString(), [ref]$number)) {
        return $number
    }

    return $null
}

function New-SqlConnectionString {
    param(
        [string]$ServerInstance,
        [switch]$Trusted,
        [string]$User,
        [string]$Password
    )

    if ($Trusted) {
        return "Server=$ServerInstance;Database=master;Integrated Security=True;Encrypt=False;TrustServerCertificate=True;"
    }

    if ([string]::IsNullOrWhiteSpace($User) -or [string]::IsNullOrWhiteSpace($Password)) {
        throw "SQL authentication selected but SqlUser/SqlPassword were not provided."
    }

    return "Server=$ServerInstance;Database=master;User ID=$User;Password=$Password;Encrypt=False;TrustServerCertificate=True;"
}

function Invoke-DbQuery {
    param(
        [string]$ConnectionString,
        [string]$Query,
        [int]$TimeoutSeconds = 60
    )

    $connection = New-Object System.Data.SqlClient.SqlConnection($ConnectionString)
    try {
        $connection.Open()
        $command = $connection.CreateCommand()
        $command.CommandText = $Query
        $command.CommandTimeout = $TimeoutSeconds

        $adapter = New-Object System.Data.SqlClient.SqlDataAdapter($command)
        $table = New-Object System.Data.DataTable
        [void]$adapter.Fill($table)
        return $table
    }
    finally {
        if ($connection.State -ne [System.Data.ConnectionState]::Closed) {
            $connection.Close()
        }
        $connection.Dispose()
    }
}

function Convert-DataTableToObjects {
    param([System.Data.DataTable]$DataTable)

    if ($null -eq $DataTable -or $DataTable.Rows.Count -eq 0) {
        return @()
    }

    $rows = @()
    foreach ($row in $DataTable.Rows) {
        $obj = [ordered]@{}
        foreach ($col in $DataTable.Columns) {
            $obj[$col.ColumnName] = Get-NullableValue -Value $row[$col.ColumnName]
        }
        $rows += [pscustomobject]$obj
    }

    return $rows
}

function Get-SqlInstancesFromConfig {
    param([string]$CsvPath)

    if (-not (Test-Path -Path $CsvPath)) {
        throw "ConfigCsv not found: $CsvPath"
    }

    $rows = Import-Csv -Path $CsvPath
    if ($rows.Count -eq 0) {
        throw "ConfigCsv is empty: $CsvPath"
    }

    $items = foreach ($row in $rows) {
        $enabledText = (Get-NullableValue $row.Enabled)
        $enabled = $true
        if ($null -ne $enabledText -and $enabledText.ToString().Trim() -ne '') {
            $enabled = $enabledText.ToString().Trim().ToLowerInvariant() -in @('1', 'true', 'yes', 'y')
        }

        if (-not $enabled) { continue }

        $serverName = (Get-NullableValue $row.Server)
        $instanceName = (Get-NullableValue $row.SqlInstance)

        if ([string]::IsNullOrWhiteSpace($instanceName)) {
            $instanceName = $serverName
        }

        if ([string]::IsNullOrWhiteSpace($instanceName)) {
            continue
        }

        [pscustomobject]@{
            Server = $serverName
            SqlInstance = $instanceName
            BackupPath = Get-NullableValue $row.BackupPath
        }
    }

    if ($items.Count -eq 0) {
        throw "No enabled SQL instances were found in $CsvPath"
    }

    return $items
}

function Get-InstanceRunData {
    param(
        [string]$ConnectionString,
        [int]$BackupFreshness,
        [int]$FailureLookback
    )

    $backupQuery = @"
SELECT
    COUNT(*) AS UserDatabaseCount,
    SUM(CASE WHEN LastFullBackup IS NULL THEN 1 ELSE 0 END) AS MissingFullBackups,
    SUM(CASE WHEN LastFullBackup IS NOT NULL AND LastFullBackup < DATEADD(HOUR, -$BackupFreshness, GETDATE()) THEN 1 ELSE 0 END) AS StaleFullBackups
FROM (
    SELECT d.name,
           MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END) AS LastFullBackup
    FROM sys.databases d
    LEFT JOIN msdb.dbo.backupset b ON b.database_name = d.name
    WHERE d.database_id > 4
    GROUP BY d.name
) x;
"@

    $failedJobsQuery = @"
SELECT COUNT(*) AS FailedJobs
FROM msdb.dbo.sysjobhistory h
WHERE h.step_id = 0
  AND h.run_status = 0
  AND msdb.dbo.agent_datetime(h.run_date, h.run_time) >= DATEADD(HOUR, -$FailureLookback, GETDATE());
"@

    $cleanupQuery = @"
SELECT COUNT(*) AS CleanupJobs,
       SUM(CASE WHEN h.run_status = 0 THEN 1 ELSE 0 END) AS CleanupJobsLastRunFailed
FROM msdb.dbo.sysjobs j
OUTER APPLY (
    SELECT TOP 1 h2.run_status
    FROM msdb.dbo.sysjobhistory h2
    WHERE h2.job_id = j.job_id AND h2.step_id = 0
    ORDER BY h2.instance_id DESC
) h
WHERE j.name LIKE '%backup%clean%'
   OR j.name LIKE '%maintenance%clean%';
"@

    $driveQuery = @"
CREATE TABLE #d (drive CHAR(1), free_mb INT);
INSERT INTO #d EXEC master..xp_fixeddrives;
SELECT MIN(free_mb) AS MinFreeMb FROM #d;
DROP TABLE #d;
"@

    $backup = Convert-DataTableToObjects (Invoke-DbQuery -ConnectionString $ConnectionString -Query $backupQuery)
    $failed = Convert-DataTableToObjects (Invoke-DbQuery -ConnectionString $ConnectionString -Query $failedJobsQuery)
    $cleanup = Convert-DataTableToObjects (Invoke-DbQuery -ConnectionString $ConnectionString -Query $cleanupQuery)

    $minFreeMb = $null
    try {
        $drive = Convert-DataTableToObjects (Invoke-DbQuery -ConnectionString $ConnectionString -Query $driveQuery)
        $minFreeMb = Convert-ToDoubleSafe -Value $drive[0].MinFreeMb
    }
    catch {
        $minFreeMb = $null
    }

    return [pscustomobject]@{
        UserDatabaseCount = Convert-ToDoubleSafe -Value $backup[0].UserDatabaseCount
        MissingFullBackups = Convert-ToDoubleSafe -Value $backup[0].MissingFullBackups
        StaleFullBackups = Convert-ToDoubleSafe -Value $backup[0].StaleFullBackups
        FailedJobs = Convert-ToDoubleSafe -Value $failed[0].FailedJobs
        CleanupJobs = Convert-ToDoubleSafe -Value $cleanup[0].CleanupJobs
        CleanupJobsLastRunFailed = Convert-ToDoubleSafe -Value $cleanup[0].CleanupJobsLastRunFailed
        MinFreeMb = $minFreeMb
    }
}

function Get-BackupRetentionIssueCount {
    param(
        [string]$Path,
        [int]$RetentionDays
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    if (-not (Test-Path -Path $Path)) { return $null }

    $cutoff = (Get-Date).AddDays(-$RetentionDays)

    $count = (Get-ChildItem -Path $Path -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff -and ($_.Extension -in '.bak', '.trn', '.log') } |
        Measure-Object).Count

    return [double]$count
}

if (-not (Test-Path -Path $OutputFolder)) {
    [void](New-Item -Path $OutputFolder -ItemType Directory -Force)
}

$configItems = Get-SqlInstancesFromConfig -CsvPath $ConfigCsv
$summaryRows = @()

foreach ($item in $configItems) {
    $serverLabel = if ([string]::IsNullOrWhiteSpace($item.Server)) { $item.SqlInstance } else { $item.Server }
    Write-Host "Checking server: $serverLabel ..."
    Write-Host "  SQL instance: $($item.SqlInstance)"

    $summary = [ordered]@{
        Server = $serverLabel
        SqlInstance = $item.SqlInstance
        Status = 'OK'
        Error = $null
        UserDatabaseCount = $null
        MissingFullBackups = $null
        StaleFullBackups = $null
        FailedJobs = $null
        CleanupJobs = $null
        CleanupJobsLastRunFailed = $null
        MinFreeMb = $null
        BackupRetentionIssues = $null
    }

    try {
        $connectionString = New-SqlConnectionString -ServerInstance $item.SqlInstance -Trusted:$UseTrustedConnection -User $SqlUser -Password $SqlPassword
        $data = Get-InstanceRunData -ConnectionString $connectionString -BackupFreshness $BackupFreshnessHours -FailureLookback $JobFailureLookbackHours

        $summary.UserDatabaseCount = $data.UserDatabaseCount
        $summary.MissingFullBackups = $data.MissingFullBackups
        $summary.StaleFullBackups = $data.StaleFullBackups
        $summary.FailedJobs = $data.FailedJobs
        $summary.CleanupJobs = $data.CleanupJobs
        $summary.CleanupJobsLastRunFailed = $data.CleanupJobsLastRunFailed
        $summary.MinFreeMb = $data.MinFreeMb
        $summary.BackupRetentionIssues = Get-BackupRetentionIssueCount -Path $item.BackupPath -RetentionDays $BackupRetentionDays
    }
    catch {
        $summary.Status = 'ERROR'
        $summary.Error = $_.Exception.Message
    }

    $summaryRows += [pscustomobject]$summary
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$summaryCsv = Join-Path $OutputFolder "sql_runlist_summary_$timestamp.csv"
$htmlPath = Join-Path $OutputFolder "sql_runlist_report_$timestamp.html"

$summaryRows | Export-Csv -Path $summaryCsv -NoTypeInformation -Encoding UTF8

$htmlRows = $summaryRows | ConvertTo-Html -Fragment
$html = @"
<html>
<head>
  <title>SQL Run List Report</title>
  <style>
    body { font-family: Segoe UI, Arial, sans-serif; margin: 16px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ccc; padding: 6px 8px; }
    th { background: #f2f2f2; }
  </style>
</head>
<body>
  <h1>SQL Run List Report</h1>
  <p>Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')</p>
  $htmlRows
</body>
</html>
"@

$html | Out-File -FilePath $htmlPath -Encoding UTF8

Write-Host ""
Write-Host "Done."
Write-Host "Summary CSV : $summaryCsv"
Write-Host "HTML Report : $htmlPath"
