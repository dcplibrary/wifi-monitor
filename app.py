#!/usr/bin/env python3
"""
Wireless User Statistics Service
- Syslog listener for HP MSM775 controller
- REST API for querying daily/monthly/unique user stats
- Can run standalone or as a Windows service
"""

import socket
import re
import sqlite3
import threading
import os
import sys
import logging
from datetime import datetime
from flask import Flask, jsonify, request

# ---------- Config ----------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "wireless_stats.db"))
LOG_PATH = os.environ.get("LOG_PATH", os.path.join(BASE_DIR, "wireless_service.log"))
SYSLOG_HOST = "0.0.0.0"
SYSLOG_PORT = 514
API_HOST = "0.0.0.0"
API_PORT = 8080
MAC_PATTERN = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
SSID_PATTERN = re.compile(r"(?:SSID[=:\s]+|value=['\"])([^'\")\s,;]+)", re.IGNORECASE)

# ---------- Logging ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("wireless_stats")

# ---------- Database ----------


def get_db():
    """Get a thread-local database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent read/write
    return conn


def init_db():
    conn = get_db()
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
    conn.close()
    log.info("Database initialized at %s", DB_PATH)


def record_event(mac, event_type=None, ssid=None):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mac = mac.upper()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO auth_events (mac_address, event_type, ssid, timestamp) VALUES (?, ?, ?, ?)",
        (mac, event_type, ssid, now.isoformat()),
    )
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
    conn.close()


# ---------- Syslog Listener ----------


def parse_syslog(data):
    text = data.decode("utf-8", errors="replace")
    macs = MAC_PATTERN.findall(text)
    if not macs:
        return None

    event_type = "other"
    if "authenticated" in text.lower() or "auth" in text.lower():
        event_type = "auth"
    elif "associated" in text.lower() or "assoc" in text.lower():
        event_type = "assoc"
    elif "disassoc" in text.lower() or "deauth" in text.lower():
        event_type = "deauth"

    ssid = None
    ssid_match = SSID_PATTERN.search(text)
    if ssid_match:
        ssid = ssid_match.group(1).strip("'\"")

    return {"mac": macs[0], "event_type": event_type, "ssid": ssid}


class SyslogListener(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(2.0)  # Allow periodic check of self.running
        self.sock.bind((SYSLOG_HOST, SYSLOG_PORT))
        log.info("Syslog listener started on UDP %s:%d", SYSLOG_HOST, SYSLOG_PORT)

        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                result = parse_syslog(data)
                if result and result["mac"]:
                    record_event(result["mac"], result["event_type"], result["ssid"])
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    log.error("Syslog error: %s", e)

        self.sock.close()
        log.info("Syslog listener stopped")

    def stop(self):
        self.running = False


# ---------- Flask API ----------

app = Flask(__name__)


@app.route("/")
def index():
    return jsonify({
        "service": "Wireless User Statistics",
        "endpoints": {
            "/api/today": "Today's stats",
            "/api/date/<YYYY-MM-DD>": "Stats for a specific date",
            "/api/month/<YYYY-MM>": "Monthly tally",
            "/api/range?start=YYYY-MM-DD&end=YYYY-MM-DD": "Date range stats",
            "/api/unique-users": "All-time unique users (optional ?days=N)",
            "/api/top-devices?days=N": "Top devices by frequency",
            "/api/ssids?days=N": "Per-SSID breakdown",
        },
    })


@app.route("/api/today")
def api_today():
    today = datetime.now().strftime("%Y-%m-%d")
    return api_date(today)


@app.route("/api/date/<date>")
def api_date(date):
    conn = get_db()
    c = conn.cursor()

    # Unique MACs for this date
    c.execute(
        "SELECT COUNT(DISTINCT mac_address) FROM daily_stats WHERE date = ?",
        (date,),
    )
    unique_count = c.fetchone()[0]

    # Total auth events
    c.execute(
        "SELECT COALESCE(SUM(auth_count), 0) FROM daily_stats WHERE date = ?",
        (date,),
    )
    total_events = c.fetchone()[0]

    # Per-SSID breakdown
    c.execute(
        """
        SELECT COALESCE(ssid, 'Unknown') as ssid, COUNT(DISTINCT mac_address) as unique_macs
        FROM daily_stats WHERE date = ?
        GROUP BY ssid ORDER BY unique_macs DESC
        """,
        (date,),
    )
    ssids = [{"ssid": row["ssid"], "unique_users": row["unique_macs"]} for row in c.fetchall()]

    # List of MACs
    c.execute(
        """
        SELECT mac_address, auth_count, first_seen, last_seen, ssid
        FROM daily_stats WHERE date = ?
        ORDER BY auth_count DESC
        """,
        (date,),
    )
    devices = [dict(row) for row in c.fetchall()]

    conn.close()
    return jsonify({
        "date": date,
        "unique_users": unique_count,
        "total_events": total_events,
        "ssid_breakdown": ssids,
        "devices": devices,
    })


@app.route("/api/month/<month>")
def api_month(month):
    conn = get_db()
    c = conn.cursor()

    # Daily breakdown for the month
    c.execute(
        """
        SELECT date,
               COUNT(DISTINCT mac_address) as unique_users,
               SUM(auth_count) as total_events
        FROM daily_stats
        WHERE date LIKE ?
        GROUP BY date
        ORDER BY date
        """,
        (f"{month}%",),
    )
    daily = [dict(row) for row in c.fetchall()]

    # Month totals
    c.execute(
        "SELECT COUNT(DISTINCT mac_address) FROM daily_stats WHERE date LIKE ?",
        (f"{month}%",),
    )
    unique_for_month = c.fetchone()[0]

    c.execute(
        "SELECT COALESCE(SUM(auth_count), 0) FROM daily_stats WHERE date LIKE ?",
        (f"{month}%",),
    )
    total_events = c.fetchone()[0]

    # Per-SSID for the month
    c.execute(
        """
        SELECT COALESCE(ssid, 'Unknown') as ssid, COUNT(DISTINCT mac_address) as unique_users
        FROM daily_stats WHERE date LIKE ?
        GROUP BY ssid ORDER BY unique_users DESC
        """,
        (f"{month}%",),
    )
    ssids = [dict(row) for row in c.fetchall()]

    conn.close()
    return jsonify({
        "month": month,
        "unique_users_this_month": unique_for_month,
        "total_events": total_events,
        "ssid_breakdown": ssids,
        "daily_breakdown": daily,
    })


@app.route("/api/range")
def api_range():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "Provide ?start=YYYY-MM-DD&end=YYYY-MM-DD"}), 400

    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT date,
               COUNT(DISTINCT mac_address) as unique_users,
               SUM(auth_count) as total_events
        FROM daily_stats
        WHERE date BETWEEN ? AND ?
        GROUP BY date ORDER BY date
        """,
        (start, end),
    )
    daily = [dict(row) for row in c.fetchall()]

    c.execute(
        "SELECT COUNT(DISTINCT mac_address) FROM daily_stats WHERE date BETWEEN ? AND ?",
        (start, end),
    )
    unique_total = c.fetchone()[0]

    conn.close()
    return jsonify({
        "start": start,
        "end": end,
        "unique_users": unique_total,
        "daily_breakdown": daily,
    })


@app.route("/api/unique-users")
def api_unique_users():
    days = request.args.get("days", type=int)
    conn = get_db()
    c = conn.cursor()

    if days:
        c.execute(
            "SELECT COUNT(DISTINCT mac_address) FROM daily_stats WHERE date >= date('now', ?)",
            (f"-{days} days",),
        )
    else:
        c.execute("SELECT COUNT(DISTINCT mac_address) FROM daily_stats")

    total = c.fetchone()[0]

    # Also get the breakdown
    if days:
        c.execute(
            """
            SELECT mac_address, COUNT(DISTINCT date) as days_seen, SUM(auth_count) as total_auths,
                   MIN(date) as first_date, MAX(date) as last_date
            FROM daily_stats WHERE date >= date('now', ?)
            GROUP BY mac_address ORDER BY days_seen DESC
            """,
            (f"-{days} days",),
        )
    else:
        c.execute(
            """
            SELECT mac_address, COUNT(DISTINCT date) as days_seen, SUM(auth_count) as total_auths,
                   MIN(date) as first_date, MAX(date) as last_date
            FROM daily_stats
            GROUP BY mac_address ORDER BY days_seen DESC
            """,
        )

    users = [dict(row) for row in c.fetchall()]
    conn.close()

    return jsonify({
        "period": f"last {days} days" if days else "all time",
        "unique_users": total,
        "devices": users,
    })


@app.route("/api/top-devices")
def api_top_devices():
    days = request.args.get("days", default=7, type=int)
    limit = request.args.get("limit", default=20, type=int)
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT mac_address, COUNT(DISTINCT date) as days_seen, SUM(auth_count) as total_auths
        FROM daily_stats WHERE date >= date('now', ?)
        GROUP BY mac_address ORDER BY days_seen DESC, total_auths DESC
        LIMIT ?
        """,
        (f"-{days} days", limit),
    )
    devices = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({"period_days": days, "devices": devices})


@app.route("/api/ssids")
def api_ssids():
    days = request.args.get("days", default=7, type=int)
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT COALESCE(ssid, 'Unknown') as ssid,
               COUNT(DISTINCT mac_address) as unique_users,
               SUM(auth_count) as total_events
        FROM daily_stats WHERE date >= date('now', ?)
        GROUP BY ssid ORDER BY unique_users DESC
        """,
        (f"-{days} days",),
    )
    ssids = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({"period_days": days, "ssids": ssids})


# ---------- Run ----------

def start_all():
    """Start syslog listener and Flask API."""
    init_db()
    listener = SyslogListener()
    listener.start()
    log.info("Starting API on http://%s:%d", API_HOST, API_PORT)
    return listener


def main():
    listener = start_all()
    try:
        app.run(host=API_HOST, port=API_PORT, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        log.info("Service stopped")


if __name__ == "__main__":
    main()
