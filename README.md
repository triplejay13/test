# test

## BlackBox extraction + phantom CLI (phase 1)

There are now **two scripts**:

1. `blackbox_stream_capture.py`: captures visible rows from the BlackBox `ALERT STREAM` panel into a CSV.
2. `phantom_cli_alerts.py`: reads that CSV and emits **phantom-print-only** command-line alerts.

## 1) Extract rows from BlackBox into CSV

```bash
python3 blackbox_stream_capture.py --output alerts.csv --interval 2
```

Then login in the opened browser and keep `ALERT STREAM` visible. The script appends rows to `alerts.csv`.

> Note: this is UI automation (not an official BlackBox developer feed).

### Quick extraction test (recommended)

1. Install Playwright package:
   ```bash
   python3 -m pip install playwright
   ```
2. Install Chromium for Playwright:
   ```bash
   python3 -m playwright install chromium
   ```
3. Run one-pass capture test:
   ```bash
   python3 blackbox_stream_capture.py --output alerts.csv --once --interval 2
   ```
4. Login and keep `ALERT STREAM` visible; in `--once` mode the script waits up to `--wait-timeout` seconds for rows, then exits.
5. Verify captured rows:
   ```bash
   cat alerts.csv
   ```

### Optional auto-login (env vars)

```bash
export BB_EMAIL="you@example.com"
export BB_PASSWORD="your_password"
python3 blackbox_stream_capture.py --output alerts.csv --auto-login --interval 2
```

PowerShell:

```powershell
$env:BB_EMAIL="you@example.com"
$env:BB_PASSWORD="your_password"
python .\blackbox_stream_capture.py --output alerts.csv --auto-login --interval 2
```

If you hit reCAPTCHA/login friction, run without `--auto-login` and use a persistent browser profile:

```powershell
python .\blackbox_stream_capture.py --output alerts.csv --interval 2 --profile-dir .\.bb_profile
```

Login once manually in that profile; future runs can reuse cookies/session.

By default, capture is **phantom-print-only**. To capture every alert row instead, add `--all-alerts`.

## 2) Run phantom-only alerts from CSV

```bash
python3 phantom_cli_alerts.py --csv alerts.csv
```

Follow mode:

```bash
python3 phantom_cli_alerts.py --csv alerts.csv --follow --interval 3
```

## Single-script mode (recommended)

If you do not want to run two scripts, use:

```bash
python3 unified_phantom_alerts.py --interval 2 --profile-dir .bb_profile
```

This single script:

- captures phantom prints from BlackBox
- appends to CSV
- emits de-duped CLI alerts
- optionally sends WhatsApp alerts

### Optional watchlist

```bash
python3 unified_phantom_alerts.py --watchlist QQQ,SPY,TSLA
```

### Optional WhatsApp sender (Cloud API)

```bash
export WA_TOKEN="..."
export WA_PHONE_NUMBER_ID="..."
export WA_TO="15551234567"
python3 unified_phantom_alerts.py --whatsapp
```

PowerShell:

```powershell
$env:WA_TOKEN="..."
$env:WA_PHONE_NUMBER_ID="..."
$env:WA_TO="15551234567"
python .\unified_phantom_alerts.py --whatsapp
```

If you already have a `whatsapp-web.js` sender service, use webhook mode instead of Cloud API:

```powershell
$env:WA_WEBHOOK_URL="http://127.0.0.1:8787/send"
$env:WA_GROUP_NAME="Phantoms"
python .\unified_phantom_alerts.py --whatsapp
```

A sample CSV is included at:

- `examples/alerts.sample.csv`

### Required CSV columns

- `TIME`
- `SYMBOL`
- `MESSAGE`
- `PRICE`

Example message format expected:

- `Phantom Print Volume: 10 Spot: $612.22`

### Notes

- The parser is strict and ignores non-phantom rows.
- De-dupe state is stored in SQLite (`phantom_seen.db` by default).
- This is phase 1: command-line alerts only.
