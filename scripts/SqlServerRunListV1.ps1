[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$SqlInstances,

    [string]$OutputPath = ".\\output",

    [int]$BackupFreshnessHours = 24,

    [int]$JobFailureLookbackHours = 24,

    [int]$BackupRetentionDays = 14,

    [string[]]$BackupPaths = @(),

    [switch]$UseTrustedConnection = $true,

    [string]$SqlUser,

    [string]$SqlPassword
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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

    if ($null -eq $DataTable) { return @() }
    if ($DataTable.Rows.Count -eq 0) { return @() }

    $rows = @()
    foreach ($row in $DataTable.Rows) {
        $obj = [ordered]@{}
        foreach ($col in $DataTable.Columns) {
            $obj[$col.ColumnName] = $row[$col.ColumnName]
        }
        $rows += [pscustomobject]$obj
    }
    return $rows
}

function Get-ServerRunListData {
    param(
        [string]$SqlInstance,
        [string]$ConnectionString,
        [int]$BackupFreshness,
        [int]$FailureLookback
    )

    $results = [ordered]@{
        SqlInstance = $SqlInstance
        CollectedUtc = (Get-Date).ToUniversalTime().ToString('s') + 'Z'
        BackupFreshnessHours = $BackupFreshness
        JobFailureLookbackHours = $FailureLookback
    }

    $backupQuery = @"
SELECT
    d.name AS DatabaseName,
    d.recovery_model_desc AS RecoveryModel,
    MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END) AS LastFullBackup,
    MAX(CASE WHEN b.type = 'L' THEN b.backup_finish_date END) AS LastLogBackup,
    CASE
        WHEN MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END) IS NULL THEN 'MISSING'
        WHEN MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END) < DATEADD(HOUR, -$BackupFreshness, GETDATE()) THEN 'STALE'
        ELSE 'OK'
    END AS FullBackupStatus
FROM sys.databases d
LEFT JOIN msdb.dbo.backupset b ON b.database_name = d.name
WHERE d.name <> 'tempdb'
GROUP BY d.name, d.recovery_model_desc
ORDER BY d.name;
"@
    $results['BackupStatus'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $backupQuery)

    $jobFailuresQuery = @"
SELECT
    j.name AS JobName,
    h.run_date,
    h.run_time,
    h.message
FROM msdb.dbo.sysjobs j
INNER JOIN msdb.dbo.sysjobhistory h ON j.job_id = h.job_id
WHERE h.step_id = 0
  AND h.run_status = 0
  AND msdb.dbo.agent_datetime(h.run_date, h.run_time) >= DATEADD(HOUR, -$FailureLookback, GETDATE())
ORDER BY msdb.dbo.agent_datetime(h.run_date, h.run_time) DESC;
"@
    $results['FailedJobs'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $jobFailuresQuery)

    $cleanupJobsQuery = @"
SELECT
    j.name AS JobName,
    CASE WHEN sja.start_execution_date IS NOT NULL AND sja.stop_execution_date IS NULL THEN 'RUNNING' ELSE 'IDLE' END AS CurrentState,
    h.run_status AS LastRunStatus,
    h.run_date AS LastRunDate,
    h.run_time AS LastRunTime
FROM msdb.dbo.sysjobs j
LEFT JOIN msdb.dbo.sysjobactivity sja ON j.job_id = sja.job_id
    AND sja.session_id = (SELECT MAX(session_id) FROM msdb.dbo.syssessions)
OUTER APPLY (
    SELECT TOP 1 h2.run_status, h2.run_date, h2.run_time
    FROM msdb.dbo.sysjobhistory h2
    WHERE h2.job_id = j.job_id AND h2.step_id = 0
    ORDER BY h2.instance_id DESC
) h
WHERE j.name LIKE '%backup%clean%'
   OR j.name LIKE '%maintenance%clean%'
ORDER BY j.name;
"@
    $results['CleanupJobs'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $cleanupJobsQuery)

    $driveSpaceQuery = @"
CREATE TABLE #d (drive CHAR(1), free_mb INT);
INSERT INTO #d EXEC master..xp_fixeddrives;
SELECT drive, free_mb,
       CAST((free_mb / 1024.0) AS DECIMAL(10,2)) AS free_gb
FROM #d
ORDER BY drive;
DROP TABLE #d;
"@
    try {
        $results['DriveFreeSpace'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $driveSpaceQuery)
    }
    catch {
        $results['DriveFreeSpace'] = @([pscustomobject]@{ Warning = "Could not query xp_fixeddrives: $($_.Exception.Message)" })
    }

    $fileGrowthQuery = @"
SELECT
    DB_NAME(mf.database_id) AS DatabaseName,
    mf.name AS LogicalName,
    mf.type_desc AS FileType,
    CAST(mf.size/128.0 AS DECIMAL(18,2)) AS CurrentSizeMB,
    CASE WHEN mf.max_size = -1 THEN 'UNLIMITED' ELSE CAST(mf.max_size/128.0 AS VARCHAR(50)) END AS MaxSizeMB,
    mf.growth,
    mf.is_percent_growth
FROM sys.master_files mf
ORDER BY DatabaseName, FileType;
"@
    $results['DataLogFiles'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $fileGrowthQuery)

    $errorLogQuery = @"
EXEC master.dbo.xp_readerrorlog 0, 1, N'backup', NULL, DATEADD(HOUR, -$FailureLookback, GETDATE()), GETDATE(), N'desc';
"@
    try {
        $results['BackupErrors'] = Convert-DataTableToObjects -DataTable (Invoke-DbQuery -ConnectionString $ConnectionString -Query $errorLogQuery)
    }
    catch {
        $results['BackupErrors'] = @([pscustomobject]@{ Warning = "Could not query xp_readerrorlog: $($_.Exception.Message)" })
    }

    return [pscustomobject]$results
}

function Get-BackupFileRetentionData {
    param(
        [string[]]$Paths,
        [int]$RetentionDays
    )

    if ($Paths.Count -eq 0) {
        return @([pscustomobject]@{ Note = 'No backup paths provided. Skipping file-system retention check.' })
    }

    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $stale = @()

    foreach ($path in $Paths) {
        if (-not (Test-Path -Path $path)) {
            $stale += [pscustomobject]@{ Path = $path; Status = 'MISSING_PATH'; Message = 'Path does not exist.' }
            continue
        }

        $files = Get-ChildItem -Path $path -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $cutoff -and ($_.Extension -in '.bak', '.trn', '.log') }

        foreach ($file in $files) {
            $stale += [pscustomobject]@{
                Path = $file.FullName
                Status = 'OLDER_THAN_RETENTION'
                LastWriteTime = $file.LastWriteTime
                SizeMB = [math]::Round($file.Length / 1MB, 2)
            }
        }
    }

    if ($stale.Count -eq 0) {
        return @([pscustomobject]@{ Status = 'OK'; Message = "No backup files older than $RetentionDays days were found." })
    }

    return $stale
}

if (-not (Test-Path -Path $OutputPath)) {
    [void](New-Item -Path $OutputPath -ItemType Directory -Force)
}

$allServerResults = @()
$errors = @()

foreach ($instance in $SqlInstances) {
    try {
        Write-Host "Collecting run-list checks for $instance ..."

        $connectionString = New-SqlConnectionString -ServerInstance $instance -Trusted:$UseTrustedConnection -User $SqlUser -Password $SqlPassword

        $serverData = Get-ServerRunListData -SqlInstance $instance -ConnectionString $connectionString -BackupFreshness $BackupFreshnessHours -FailureLookback $JobFailureLookbackHours
        $serverData | Add-Member -MemberType NoteProperty -Name BackupRetentionFindings -Value (Get-BackupFileRetentionData -Paths $BackupPaths -RetentionDays $BackupRetentionDays)

        $allServerResults += $serverData
    }
    catch {
        $errors += [pscustomobject]@{
            SqlInstance = $instance
            Error = $_.Exception.Message
        }
    }
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$jsonPath = Join-Path $OutputPath "sql-runlist-report_$timestamp.json"
$csvPath = Join-Path $OutputPath "sql-runlist-backupstatus_$timestamp.csv"
$errorPath = Join-Path $OutputPath "sql-runlist-errors_$timestamp.csv"

$allServerResults | ConvertTo-Json -Depth 8 | Out-File -FilePath $jsonPath -Encoding UTF8

$backupStatusRows = foreach ($server in $allServerResults) {
    foreach ($row in $server.BackupStatus) {
        [pscustomobject]@{
            SqlInstance = $server.SqlInstance
            DatabaseName = $row.DatabaseName
            RecoveryModel = $row.RecoveryModel
            LastFullBackup = $row.LastFullBackup
            LastLogBackup = $row.LastLogBackup
            FullBackupStatus = $row.FullBackupStatus
        }
    }
}

$backupStatusRows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

if ($errors.Count -gt 0) {
    $errors | Export-Csv -Path $errorPath -NoTypeInformation -Encoding UTF8
}

Write-Host "Run-list collection complete."
Write-Host "JSON report: $jsonPath"
Write-Host "CSV backup status: $csvPath"
if ($errors.Count -gt 0) {
    Write-Warning "Some instances failed. See: $errorPath"
}
