# test

## Phantom print CLI (no WhatsApp yet)

`phantom_cli_alerts.py` reads a BlackBox-style CSV export and logs **phantom-print-only** alerts to the command line.

### Required CSV columns

- `TIME`
- `SYMBOL`
- `MESSAGE`
- `PRICE`

Example message format expected:

- `Phantom Print Volume: 10 Spot: $612.22`

### Run once

```bash
python3 phantom_cli_alerts.py --csv alerts.csv
```

### Follow mode (poll and emit only new phantom prints)

```bash
python3 phantom_cli_alerts.py --csv alerts.csv --follow --interval 3
```

### Notes

- The parser is strict and ignores non-phantom rows.
- De-dupe state is stored in SQLite (`phantom_seen.db` by default).
- This is phase 1: command-line alerts only.
