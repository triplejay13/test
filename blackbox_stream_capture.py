#!/usr/bin/env python3
"""Capture Alert Stream rows from members.blackboxstocks.com into CSV.

This script uses Playwright to read rows from the visible ALERT STREAM panel
and append normalized rows to a CSV file.
"""

from __future__ import annotations

import argparse
import csv
import os
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
            .split('\\n')
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


def _fill_first(page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.fill(value)
                return True
        except Exception:
            continue
    return False


def _click_first(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click()
                return True
        except Exception:
            continue
    return False


def try_auto_login(page, email: str, password: str, login_timeout: float) -> bool:
    email_ok = _fill_first(
        page,
        [
            "input[type='email']",
            "input[name='email']",
            "input[name='username']",
            "input[placeholder*='mail' i]",
            "input[placeholder*='user' i]",
        ],
        email,
    )
    pass_ok = _fill_first(
        page,
        [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder*='password' i]",
        ],
        password,
    )
    click_ok = _click_first(
        page,
        [
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "button[type='submit']",
        ],
    )

    if not (email_ok and pass_ok and click_ok):
        return False

    try:
        page.wait_for_function(
            """() => {
                const panels = [...document.querySelectorAll('div,section')];
                return panels.some(p => /ALERT STREAM/i.test(p.innerText || ''));
            }""",
            timeout=int(login_timeout * 1000),
        )
        return True
    except Exception:
        return False


def run_capture(
    output_csv: Path,
    interval: float,
    headless: bool,
    once: bool,
    wait_timeout: float,
    auto_login: bool,
    email_env: str,
    password_env: str,
    login_timeout: float,
    profile_dir: str,
) -> int:
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
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
            )
            page = context.pages[0] if context.pages else context.new_page()
            close_target = context
        else:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            close_target = browser

        page.goto("https://members.blackboxstocks.com", wait_until="domcontentloaded")

        if auto_login:
            email = os.getenv(email_env, "")
            password = os.getenv(password_env, "")
            if not email or not password:
                print(
                    f"[WARN] --auto-login set but missing env vars: {email_env}/{password_env}",
                    file=sys.stderr,
                )
            else:
                success = try_auto_login(page, email, password, login_timeout)
                if success:
                    print("[INFO] Auto-login succeeded.", flush=True)
                else:
                    print(
                        "[WARN] Auto-login could not complete; continuing with manual login.",
                        file=sys.stderr,
                    )

        print("Login to BlackBox in the opened browser, then keep ALERT STREAM visible.")
        print(f"Capturing rows to {output_csv} every {interval:.1f}s...")
        print(f"Waiting up to {wait_timeout:.0f}s for ALERT STREAM panel...")

        try:
            page.wait_for_function(
                """() => {
                    const panels = [...document.querySelectorAll('div,section')];
                    return panels.some(p => /ALERT STREAM/i.test(p.innerText || ''));
                }""",
                timeout=int(wait_timeout * 1000),
            )
        except Exception:
            print(
                "[WARN] ALERT STREAM panel was not detected in time. "
                "Make sure you are logged in and the panel is visible.",
                file=sys.stderr,
            )

        once_deadline = time.time() + wait_timeout if once else None

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
                if once:
                    if new_rows:
                        close_target.close()
                        return 0
                    if once_deadline and time.time() >= once_deadline:
                        print(
                            "[WARN] No rows captured before timeout in --once mode. "
                            "Keep ALERT STREAM visible and try again.",
                            file=sys.stderr,
                        )
                        close_target.close()
                        return 1
            except KeyboardInterrupt:
                close_target.close()
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
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single capture pass and exit (recommended for first test)",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for ALERT STREAM panel before warning (default: 120)",
    )
    parser.add_argument(
        "--auto-login",
        action="store_true",
        help="Attempt login using environment variables (see --email-env/--password-env)",
    )
    parser.add_argument(
        "--email-env",
        default="BB_EMAIL",
        help="Environment variable name for BlackBox email (default: BB_EMAIL)",
    )
    parser.add_argument(
        "--password-env",
        default="BB_PASSWORD",
        help="Environment variable name for BlackBox password (default: BB_PASSWORD)",
    )
    parser.add_argument(
        "--login-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for post-login ALERT STREAM detection (default: 30)",
    )
    parser.add_argument(
        "--profile-dir",
        default=".bb_profile",
        help="Chromium profile dir for persistent login/cookies (default: .bb_profile)",
    )
    args = parser.parse_args()

    return run_capture(
        Path(args.output),
        args.interval,
        args.headless,
        args.once,
        args.wait_timeout,
        args.auto_login,
        args.email_env,
        args.password_env,
        args.login_timeout,
        args.profile_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
