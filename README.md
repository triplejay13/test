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

## 2) Run phantom-only alerts from CSV

```bash
python3 phantom_cli_alerts.py --csv alerts.csv
```

Follow mode:

```bash
python3 phantom_cli_alerts.py --csv alerts.csv --follow --interval 3
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
