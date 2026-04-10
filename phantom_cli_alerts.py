#!/usr/bin/env python3
"""Phantom-print-only command-line alerting from a BlackBox-style CSV stream."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Handles integer volumes (10), comma-formatted (1,500), and k/m/b suffixes (198k, 2.5m).
PHANTOM_RE = re.compile(
    r"Phantom\s+Print\s+Volume:\s*([0-9,]+(?:\.[0-9]+)?[kKmMbB]?)\s*Spot:\s*\$?([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def parse_volume(s: str) -> int:
    """Parse volume string with optional k/m/b suffix to an integer."""
    s = s.replace(",", "").strip()
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    if s and s[-1].lower() in mult:
        return int(float(s[:-1]) * mult[s[-1].lower()])
    return int(float(s))


@dataclass(frozen=True)
class PhantomPrintEvent:
    time_ui: str
    symbol: str
    stream_price: float
    raw_message: str
    phantom_volume: int
    phantom_spot: float

    @property
    def fingerprint(self) -> str:
        raw = f"{self.symbol}|{self.time_ui}|{self.phantom_volume}|{self.phantom_spot:.4f}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_float(value: str) -> float:
    cleaned = value.replace("$", "").replace(",", "").strip()
    return float(cleaned)


def parse_event(row: dict[str, str]) -> Optional[PhantomPrintEvent]:
    message = (row.get("MESSAGE") or "").strip()
    match = PHANTOM_RE.search(message)
    if not match:
        return None

    try:
        volume = parse_volume(match.group(1))
        spot = float(match.group(2))
        stream_price = parse_float((row.get("PRICE") or "0").strip())
    except (ValueError, AttributeError):
        return None

    return PhantomPrintEvent(
        time_ui=(row.get("TIME") or "").strip(),
        symbol=(row.get("SYMBOL") or "").strip().upper(),
        stream_price=stream_price,
        raw_message=message,
        phantom_volume=volume,
        phantom_spot=spot,
    )


class Deduper:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_events (
                fingerprint TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def __enter__(self) -> "Deduper":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def is_new(self, fingerprint: str) -> bool:
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                "INSERT INTO seen_events (fingerprint, created_at) VALUES (?, ?)",
                (fingerprint, created_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def iter_rows(csv_path: Path) -> Iterable[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"TIME", "SYMBOL", "MESSAGE", "PRICE"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                "CSV is missing required columns: TIME,SYMBOL,MESSAGE,PRICE"
            )
        for row in reader:
            yield row


def emit_alert(event: PhantomPrintEvent) -> None:
    line = (
        f"[ALERT] PHANTOM {event.symbol} | t={event.time_ui} | "
        f"stream=${event.stream_price:.2f} | volume={event.phantom_volume:,} | "
        f"spot=${event.phantom_spot:.2f}"
    )
    print(line, flush=True)


def process_once(csv_path: Path, deduper: Deduper) -> int:
    seen_now = 0
    for row in iter_rows(csv_path):
        event = parse_event(row)
        if event is None:
            continue
        if deduper.is_new(event.fingerprint):
            emit_alert(event)
            seen_now += 1
    return seen_now


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log phantom-print-only alerts from a BlackBox-style CSV export."
    )
    parser.add_argument("--csv", required=True, help="Path to CSV export")
    parser.add_argument("--db", default="phantom_seen.db",
        help="SQLite DB for de-dupe state (default: phantom_seen.db)")
    parser.add_argument("--follow", action="store_true",
        help="Re-read CSV on an interval and emit only new phantom prints")
    parser.add_argument("--interval", type=float, default=3.0,
        help="Poll interval in seconds when --follow is set (default: 3)")

    args = parser.parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    with Deduper(Path(args.db)) as deduper:
        if not args.follow:
            process_once(csv_path, deduper)
            return 0

        print(f"Watching {csv_path} for phantom prints every {args.interval:.1f}s...", flush=True)
        while True:
            try:
                process_once(csv_path, deduper)
            except Exception as exc:
                print(f"[WARN] {exc}", file=sys.stderr, flush=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
