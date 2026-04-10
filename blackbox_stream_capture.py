#!/usr/bin/env python3
"""Capture Alert Stream rows from members.blackboxstocks.com into CSV.

This script uses Playwright to read rows from the visible ALERT STREAM panel
and append normalized rows to a CSV file.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

ROW_RE = re.compile(
    r"^(?P<time>\d{1,2}:\d{2}:\d{1,2})\s+(?P<symbol>[A-Za-z.\-]+)\s+(?P<message>.+?)\s+\$(?P<price>[0-9,]+(?:\.[0-9]+)?)$"
)


def ensure_csv(path: Path) -> None:
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["TIME", "SYMBOL", "MESSAGE", "PRICE"])


def append_rows(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    if not rows:
        return
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def extract_stream_rows(page) -> list[tuple[str, str, str, str]]:
    # Reads text lines from the panel containing "ALERT STREAM".
    lines = page.evaluate(
        """
        () => {
          const panels = [...document.querySelectorAll('div,section')];
          const streamPanel = panels.find(p => /ALERT STREAM/i.test(p.innerText || ''));
          if (!streamPanel) return [];
          return (streamPanel.innerText || '')
            .split('\n')
            .map(s => s.trim())
            .filter(Boolean);
        }
        """
    )

    rows: list[tuple[str, str, str, str]] = []
    for line in lines:
        if line.upper() in {"TIME", "SYMBOL", "MESSAGE", "PRICE"}:
            continue
        match = ROW_RE.match(line)
        if not match:
            continue
        rows.append(
            (
                match.group("time"),
                match.group("symbol").upper(),
                match.group("message"),
                f"${match.group('price')}",
            )
        )
    return rows


def run_capture(output_csv: Path, interval: float, headless: bool) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is required. Install with: python3 -m pip install playwright",
            file=sys.stderr,
        )
        return 1

    ensure_csv(output_csv)
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto("https://members.blackboxstocks.com", wait_until="domcontentloaded")

        print("Login to BlackBox in the opened browser, then keep ALERT STREAM visible.")
        print(f"Capturing rows to {output_csv} every {interval:.1f}s...")

        while True:
            try:
                parsed_rows = extract_stream_rows(page)
                new_rows: list[tuple[str, str, str, str]] = []
                for row in parsed_rows:
                    key = "|".join(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    new_rows.append(row)

                append_rows(output_csv, new_rows)
                for t, s, m, ptxt in new_rows:
                    print(f"[CAPTURED] {t} {s} {m} {ptxt}", flush=True)
            except KeyboardInterrupt:
                browser.close()
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] {exc}", file=sys.stderr, flush=True)
            time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture live BlackBox ALERT STREAM rows into CSV.")
    parser.add_argument("--output", default="alerts.csv", help="Output CSV path")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default opens visible browser)",
    )
    args = parser.parse_args()

    return run_capture(Path(args.output), args.interval, args.headless)


if __name__ == "__main__":
    raise SystemExit(main())
