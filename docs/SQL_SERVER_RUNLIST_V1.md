# SQL Server Oversight Run List (V2-ready)

Use this run list for each SQL Server before adding additional servers (like ePRF) into the same rotation.

## Run frequency
- **Daily:** backup success, failed jobs, free space red flags.
- **Weekly:** cleanup verification, growth trend review, retention policy exceptions.

## Standard checks
1. **Backup freshness (full backups)**
2. **Failed SQL Agent jobs (last 24h by default)**
3. **Backup cleanup / maintenance job health**
4. **Drive free space snapshot (minimum free MB)**
5. **Backup retention exceptions in backup path**
6. **Risks / actions and owner assignment**

## Automation script
Primary script path: `scripts/Invoke-SqlServerRunList-Auto-v2.ps1`

### Config CSV format
Create a CSV with these headers (extra columns are ignored):

```csv
Server,SqlInstance,BackupPath,Enabled
ARNX20FGLMS,ARNX20FGLMS\FG_MS,E:\SQLBackups,true
LP01EPRFPRD01,LP01EPRFPRD01,\\backupshare\eprf,true
LP01CALCOREPRD,,,false
```

Behavior:
- If `SqlInstance` is blank, script falls back to `Server`.
- If `Enabled` is omitted/blank, row is treated as enabled.
- Rows with both `Server` and `SqlInstance` blank are skipped.

### Example usage
```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Invoke-SqlServerRunList-Auto-v2.ps1 \
  -ConfigCsv .\sql_runlist_servers.csv \
  -OutputFolder .\output \
  -BackupFreshnessHours 24 \
  -JobFailureLookbackHours 24 \
  -BackupRetentionDays 14
```

### Output artifacts
- `sql_runlist_summary_<timestamp>.csv`
- `sql_runlist_report_<timestamp>.html`

## DBNull hardening
This version explicitly normalizes SQL `DBNull` values before numeric conversion, which prevents runtime errors like:

`Cannot convert value "" to type "System.Double". Object cannot be cast from DBNull to other types.`

## Notes
- Script uses direct SQL queries against `master`/`msdb`; permissions can vary.
- If `xp_fixeddrives` cannot be executed, minimum free MB is left empty instead of failing the entire server record.
