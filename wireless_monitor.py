#!/usr/bin/env python3
"""
Wireless User Statistics Monitor
Captures syslog from HP MSM775 controller, extracts MAC addresses,
and tracks daily unique wireless users.
"""

import socket
import re
import sqlite3
import signal
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wireless_stats.db")
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 514
MAC_PATTERN = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
SSID_PATTERN = re.compile(r"(?:SSID[=:\s]+|value=['\"])([^'\")\s,;]+)", re.IGNORECASE)

# ---------- Database ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS auth_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mac_address TEXT NOT NULL,
            event_type TEXT,
            ssid TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT NOT NULL,
            mac_address TEXT NOT NULL,
            auth_count INTEGER DEFAULT 1,
            first_seen DATETIME,
            last_seen DATETIME,
            ssid TEXT,
            PRIMARY KEY (date, mac_address)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_auth_ts ON auth_events(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_stats(date)")
    conn.commit()
    return conn


def record_event(conn, mac, event_type=None, ssid=None):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mac = mac.upper()
    c = conn.cursor()

    # Insert raw event
    c.execute(
        "INSERT INTO auth_events (mac_address, event_type, ssid, timestamp) VALUES (?, ?, ?, ?)",
        (mac, event_type, ssid, now.isoformat()),
    )

    # Upsert daily stats
    c.execute(
        """
        INSERT INTO daily_stats (date, mac_address, auth_count, first_seen, last_seen, ssid)
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(date, mac_address) DO UPDATE SET
            auth_count = auth_count + 1,
            last_seen = ?
        """,
        (today, mac, now.isoformat(), now.isoformat(), ssid, now.isoformat()),
    )
    conn.commit()


# ---------- Syslog Parser ----------

def parse_syslog(data):
    """Extract MAC address, event type, and SSID from a syslog message."""
    text = data.decode("utf-8", errors="replace")
    macs = MAC_PATTERN.findall(text)
    if not macs:
        return None

    # Try to identify the client MAC (skip gateway/AP MACs if possible)
    # The MSM775 typically logs the client MAC in auth events
    event_type = None
    ssid = None

    if "authenticated" in text.lower() or "auth" in text.lower():
        event_type = "auth"
    elif "associated" in text.lower() or "assoc" in text.lower():
        event_type = "assoc"
    elif "disassoc" in text.lower() or "deauth" in text.lower():
        event_type = "deauth"
    else:
        event_type = "other"

    # Extract SSID if present
    ssid_match = SSID_PATTERN.search(text)
    if ssid_match:
        ssid = ssid_match.group(1).strip("'\"")

    # Return the first MAC found (typically the client)
    return {"mac": macs[0], "event_type": event_type, "ssid": ssid, "raw": text}


# ---------- Syslog Listener ----------

def start_listener(conn):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LISTEN_HOST, LISTEN_PORT))
    print(f"[*] Listening for syslog on UDP {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[*] Database: {DB_PATH}")
    print("[*] Press Ctrl+C to stop and view summary\n")

    today = datetime.now().strftime("%Y-%m-%d")
    today_macs = set()
    event_count = 0

    while True:
        try:
            data, addr = sock.recvfrom(65535)
            result = parse_syslog(data)
            if result and result["mac"]:
                record_event(conn, result["mac"], result["event_type"], result["ssid"])
                mac_upper = result["mac"].upper()
                event_count += 1

                # Reset daily tracking at midnight
                current_date = datetime.now().strftime("%Y-%m-%d")
                if current_date != today:
                    print(f"\n[*] Day rolled over: {today} had {len(today_macs)} unique MACs")
                    today = current_date
                    today_macs = set()

                is_new = mac_upper not in today_macs
                today_macs.add(mac_upper)

                status = "NEW" if is_new else "   "
                print(
                    f"[{status}] {datetime.now().strftime('%H:%M:%S')} "
                    f"MAC={mac_upper} type={result['event_type']} "
                    f"ssid={result['ssid'] or 'N/A'} "
                    f"| Today: {len(today_macs)} unique, {event_count} events"
                )
        except KeyboardInterrupt:
            print("\n\n[*] Shutting down...")
            sock.close()
            break


# ---------- Reporting ----------

def print_report(conn, days=7):
    c = conn.cursor()
    print("\n" + "=" * 70)
    print("WIRELESS USER STATISTICS REPORT")
    print("=" * 70)

    # Daily unique users
    print(f"\n--- Daily Unique Users (last {days} days) ---")
    print(f"{'Date':<14} {'Unique MACs':<14} {'Total Events'}")
    print("-" * 42)
    c.execute(
        """
        SELECT date, COUNT(DISTINCT mac_address) as unique_macs, SUM(auth_count) as total
        FROM daily_stats
        WHERE date >= date('now', ?)
        GROUP BY date
        ORDER BY date DESC
        """,
        (f"-{days} days",),
    )
    for row in c.fetchall():
        print(f"{row[0]:<14} {row[1]:<14} {row[2]}")

    # Overall unique MACs
    c.execute("SELECT COUNT(DISTINCT mac_address) FROM daily_stats")
    total_unique = c.fetchone()[0]
    print(f"\nTotal unique MACs (all time): {total_unique}")

    # Top 10 most frequent MACs
    print(f"\n--- Top 10 Most Frequent Devices (last {days} days) ---")
    print(f"{'MAC Address':<20} {'Days Seen':<12} {'Total Auths'}")
    print("-" * 46)
    c.execute(
        """
        SELECT mac_address, COUNT(DISTINCT date) as days_seen, SUM(auth_count) as total
        FROM daily_stats
        WHERE date >= date('now', ?)
        GROUP BY mac_address
        ORDER BY days_seen DESC, total DESC
        LIMIT 10
        """,
        (f"-{days} days",),
    )
    for row in c.fetchall():
        print(f"{row[0]:<20} {row[1]:<12} {row[2]}")

    # Per-SSID breakdown
    print(f"\n--- Users per SSID (last {days} days) ---")
    print(f"{'SSID':<25} {'Unique MACs'}")
    print("-" * 38)
    c.execute(
        """
        SELECT COALESCE(ssid, 'Unknown'), COUNT(DISTINCT mac_address)
        FROM daily_stats
        WHERE date >= date('now', ?)
        GROUP BY ssid
        ORDER BY COUNT(DISTINCT mac_address) DESC
        """,
        (f"-{days} days",),
    )
    for row in c.fetchall():
        print(f"{row[0]:<25} {row[1]}")

    print("\n" + "=" * 70)


def export_csv(conn, days=30):
    import csv
    outfile = os.path.join(os.path.dirname(DB_PATH), "wireless_report.csv")
    c = conn.cursor()
    c.execute(
        """
        SELECT date, mac_address, auth_count, first_seen, last_seen, ssid
        FROM daily_stats
        WHERE date >= date('now', ?)
        ORDER BY date DESC, mac_address
        """,
        (f"-{days} days",),
    )
    with open(outfile, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "MAC Address", "Auth Count", "First Seen", "Last Seen", "SSID"])
        writer.writerows(c.fetchall())
    print(f"[*] Exported to {outfile}")


# ---------- Import existing pcap ----------

def import_pcap(conn, pcap_file):
    """Import MAC addresses from an existing tcpdump pcap file."""
    import subprocess

    print(f"[*] Importing from {pcap_file}...")
    try:
        result = subprocess.run(
            ["tcpdump", "-r", pcap_file, "-A"],
            capture_output=True,
            text=True,
        )
        count = 0
        for line in result.stdout.split("\n"):
            macs = MAC_PATTERN.findall(line)
            for mac in macs:
                record_event(conn, mac, "imported", None)
                count += 1
        print(f"[*] Imported {count} MAC events from pcap")
    except FileNotFoundError:
        print("[!] tcpdump not found, cannot import pcap")


# ---------- Main ----------

def main():
    conn = init_db()

    def handle_exit(sig, frame):
        print_report(conn)
        conn.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "report":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            print_report(conn, days)

        elif cmd == "export":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            export_csv(conn, days)

        elif cmd == "import":
            if len(sys.argv) < 3:
                print("Usage: wireless_monitor.py import <pcap_file>")
                sys.exit(1)
            import_pcap(conn, sys.argv[2])

        elif cmd == "listen":
            start_listener(conn)

        else:
            print("Usage: wireless_monitor.py [listen|report|export|import <file>]")
            print("  listen          - Start capturing syslog (default)")
            print("  report [days]   - Show stats for last N days (default: 7)")
            print("  export [days]   - Export CSV for last N days (default: 30)")
            print("  import <file>   - Import from existing pcap file")
    else:
        start_listener(conn)

    conn.close()


if __name__ == "__main__":
    main()
