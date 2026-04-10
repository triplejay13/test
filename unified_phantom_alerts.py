#!/usr/bin/env python3
"""Single-process BlackBox phantom capture + CLI alert + optional WhatsApp send."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from blackbox_stream_capture import (
    append_rows,
    ensure_csv,
    extract_stream_rows,
    try_auto_login,
)
from phantom_cli_alerts import Deduper, emit_alert, parse_event


def parse_watchlist(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def passes_watchlist(symbol: str, watchlist: set[str]) -> bool:
    return not watchlist or symbol.upper() in watchlist


def send_whatsapp_message(token: str, phone_id: str, to: str, message: str) -> None:
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"WhatsApp API returned status {resp.status}")


def send_webhook_message(webhook_url: str, message: str, group_name: str) -> None:
    payload = {
        "text": message,
        "groupName": group_name,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Webhook returned status {resp.status}")


def format_whatsapp(event) -> str:
    return (
        "🚨 Phantom Print\n"
        f"{event.symbol} @ {event.time_ui}\n"
        f"Stream: ${event.stream_price:.2f}\n"
        f"Volume: {event.phantom_volume:,}\n"
        f"Spot: ${event.phantom_spot:.2f}"
    )


def run(args) -> int:
    ensure_csv(Path(args.output))
    deduper = Deduper(Path(args.db))
    watchlist = parse_watchlist(args.watchlist)

    wa_token = os.getenv(args.whatsapp_token_env, "")
    wa_phone_id = os.getenv(args.whatsapp_phone_id_env, "")
    wa_to = os.getenv(args.whatsapp_to_env, "")
    wa_webhook_url = os.getenv(args.whatsapp_webhook_env, "")
    wa_group_name = os.getenv(args.whatsapp_group_env, args.whatsapp_group_name)

    if args.whatsapp and (not wa_webhook_url and (not wa_token or not wa_phone_id or not wa_to)):
        print(
            "[WARN] WhatsApp enabled but no transport configured. "
            f"Set {args.whatsapp_webhook_env} for webhook mode OR set "
            f"{args.whatsapp_token_env}, {args.whatsapp_phone_id_env}, {args.whatsapp_to_env} for Cloud API.",
            file=sys.stderr,
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is required. Install with: python3 -m pip install playwright", file=sys.stderr)
        return 1

    seen_rows: set[str] = set()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=args.profile_dir,
            headless=args.headless,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://members.blackboxstocks.com", wait_until="domcontentloaded")

        if args.auto_login:
            email = os.getenv(args.email_env, "")
            password = os.getenv(args.password_env, "")
            if email and password:
                if try_auto_login(page, email, password, args.login_timeout):
                    print("[INFO] Auto-login succeeded.")
                else:
                    print("[WARN] Auto-login failed; continuing manual.", file=sys.stderr)

        print("Single-script mode running: capture + phantom alert + optional WhatsApp")

        while True:
            try:
                rows = extract_stream_rows(page, phantom_only=True)
                new_rows = []
                for row in rows:
                    key = "|".join(row)
                    if key in seen_rows:
                        continue
                    seen_rows.add(key)
                    new_rows.append(row)

                append_rows(Path(args.output), new_rows)

                for t, s, m, ptxt in new_rows:
                    event = parse_event({"TIME": t, "SYMBOL": s, "MESSAGE": m, "PRICE": ptxt})
                    if event is None:
                        continue
                    if not passes_watchlist(event.symbol, watchlist):
                        continue
                    if not deduper.is_new(event.fingerprint):
                        continue

                    emit_alert(event)

                    if args.whatsapp and wa_webhook_url:
                        try:
                            send_webhook_message(
                                wa_webhook_url,
                                format_whatsapp(event),
                                wa_group_name,
                            )
                            print(f"[WHATSAPP-WEBHOOK] sent for {event.symbol} {event.time_ui}")
                        except (urllib.error.URLError, RuntimeError) as exc:
                            print(f"[WARN] WhatsApp webhook send failed: {exc}", file=sys.stderr)
                    elif args.whatsapp and wa_token and wa_phone_id and wa_to:
                        try:
                            send_whatsapp_message(
                                wa_token,
                                wa_phone_id,
                                wa_to,
                                format_whatsapp(event),
                            )
                            print(f"[WHATSAPP] sent for {event.symbol} {event.time_ui}")
                        except (urllib.error.URLError, RuntimeError) as exc:
                            print(f"[WARN] WhatsApp send failed: {exc}", file=sys.stderr)

            except KeyboardInterrupt:
                context.close()
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] {exc}", file=sys.stderr)
            time.sleep(args.interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single script: capture phantom prints + CLI alerts + optional WhatsApp."
    )
    parser.add_argument("--output", default="alerts.csv", help="CSV output path")
    parser.add_argument("--db", default="phantom_seen.db", help="SQLite dedupe DB")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    parser.add_argument("--profile-dir", default=".bb_profile", help="Persistent Chromium profile dir")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--auto-login", action="store_true", help="Try auto-login from env vars")
    parser.add_argument("--email-env", default="BB_EMAIL", help="Email env var name")
    parser.add_argument("--password-env", default="BB_PASSWORD", help="Password env var name")
    parser.add_argument("--login-timeout", type=float, default=30.0, help="Auto-login wait timeout")
    parser.add_argument("--watchlist", default="", help="Comma-separated tickers, optional")

    parser.add_argument("--whatsapp", action="store_true", help="Enable WhatsApp sends")
    parser.add_argument("--whatsapp-token-env", default="WA_TOKEN", help="WhatsApp API token env name")
    parser.add_argument("--whatsapp-phone-id-env", default="WA_PHONE_NUMBER_ID", help="WhatsApp phone id env name")
    parser.add_argument("--whatsapp-to-env", default="WA_TO", help="WhatsApp destination env name")
    parser.add_argument(
        "--whatsapp-webhook-env",
        default="WA_WEBHOOK_URL",
        help="Webhook URL env name for whatsapp-web.js bridge (preferred when available)",
    )
    parser.add_argument(
        "--whatsapp-group-name",
        default="Phantoms",
        help="Group name for webhook payload (default: Phantoms)",
    )
    parser.add_argument(
        "--whatsapp-group-env",
        default="WA_GROUP_NAME",
        help="Environment variable for webhook group name override (default: WA_GROUP_NAME)",
    )

    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
