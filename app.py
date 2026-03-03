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
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
# Try multiple common locations for .env file
env_locations = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ".env",
]
env_loaded = False
for env_path in env_locations:
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        env_loaded = True
        break

# ---------- Config ----------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "wireless_stats.db"))
LOG_PATH = os.environ.get("LOG_PATH", os.path.join(BASE_DIR, "wireless_service.log"))
SYSLOG_HOST = os.environ.get("SYSLOG_HOST", "0.0.0.0")
SYSLOG_PORT = int(os.environ.get("SYSLOG_PORT", 514))
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", 8088))
AUTH_EVENTS_RETENTION_DAYS = int(os.environ.get("AUTH_EVENTS_RETENTION_DAYS", 30))
DAILY_STATS_RETENTION_DAYS = int(os.environ.get("DAILY_STATS_RETENTION_DAYS", 365))

# By default, we DO NOT store raw auth_events rows (to avoid event-count tracking).
# The daily_stats table still tracks per-device first_seen/last_seen for time-of-day analysis.
STORE_AUTH_EVENTS = os.environ.get("STORE_AUTH_EVENTS", "false").lower() in ("1", "true", "yes", "on")

# Optional SSID normalization for HP MSM style identifiers (r1v1, r2v2, etc.)
SSID_V1_NAME = os.environ.get("SSID_V1_NAME", "DCPL-PATRON")
SSID_V2_NAME = os.environ.get("SSID_V2_NAME", "DCPL-STAFF")
SSID_V3_NAME = os.environ.get("SSID_V3_NAME", "DCPL-OPS")

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

if env_loaded:
    log.info("Configuration loaded from .env file")
else:
    log.info("No .env file found, using default configuration")
log.info("API will run on %s:%d", API_HOST, API_PORT)
log.info("Syslog listener will bind to %s:%d", SYSLOG_HOST, SYSLOG_PORT)

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
    """Record a successful auth for MAC for the day.

    We intentionally do NOT track event counts. We only keep per-day first_seen/last_seen
    (and SSID) so we can compute duration and time-of-day activity.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mac = mac.upper()

    conn = get_db()
    c = conn.cursor()

    if STORE_AUTH_EVENTS:
        c.execute(
            "INSERT INTO auth_events (mac_address, event_type, ssid, timestamp) VALUES (?, ?, ?, ?)",
            (mac, event_type, ssid, now.isoformat()),
        )

    # Upsert: keep first_seen from initial insert, update last_seen every time.
    # auth_count remains 1 and is kept only for backwards compatibility.
    c.execute(
        """
        INSERT INTO daily_stats (date, mac_address, auth_count, first_seen, last_seen, ssid)
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(date, mac_address) DO UPDATE SET
            last_seen = excluded.last_seen,
            ssid = COALESCE(excluded.ssid, daily_stats.ssid)
        """,
        (today, mac, now.isoformat(), now.isoformat(), ssid),
    )

    conn.commit()
    conn.close()


def cleanup_old_data():
    """Remove old records based on retention policy."""
    conn = get_db()
    c = conn.cursor()

    auth_deleted = 0
    stats_deleted = 0

    # Calculate cutoff dates
    if AUTH_EVENTS_RETENTION_DAYS > 0:
        auth_cutoff = (datetime.now() - timedelta(days=AUTH_EVENTS_RETENTION_DAYS)).isoformat()
        c.execute("DELETE FROM auth_events WHERE timestamp < ?", (auth_cutoff,))
        auth_deleted = c.rowcount

    if DAILY_STATS_RETENTION_DAYS > 0:
        stats_cutoff = (datetime.now() - timedelta(days=DAILY_STATS_RETENTION_DAYS)).strftime("%Y-%m-%d")
        c.execute("DELETE FROM daily_stats WHERE date < ?", (stats_cutoff,))
        stats_deleted = c.rowcount

    # Optimize database
    c.execute("VACUUM")

    conn.commit()
    conn.close()

    log.info(
        "Database cleanup completed: %d auth_events deleted (>%d days), %d daily_stats deleted (>%d days)",
        auth_deleted, AUTH_EVENTS_RETENTION_DAYS, stats_deleted, DAILY_STATS_RETENTION_DAYS
    )
    return {"auth_events_deleted": auth_deleted, "daily_stats_deleted": stats_deleted}


# ---------- Syslog Listener ----------


def normalize_ssid(ssid: str | None) -> str | None:
    if not ssid:
        return None

    # If syslog already contains friendly DCPL names, keep them.
    m = re.search(r"DCPL-(PATRON|STAFF|OPS)", ssid, re.IGNORECASE)
    if m:
        return f"DCPL-{m.group(1).upper()}"

    lower = ssid.lower()

    # HP MSM style IDs like r1v1, r2v2, etc.
    # v1/v2/v3 naming can be configured via .env.
    if re.search(r"v1(?!\d)", lower):
        return SSID_V1_NAME
    if re.search(r"v2(?!\d)", lower):
        return SSID_V2_NAME
    if re.search(r"v3(?!\d)", lower):
        return SSID_V3_NAME

    return ssid


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

    ssid = normalize_ssid(ssid)

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
                if result and result["mac"] and result["event_type"] == "auth":
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


class CleanupScheduler(threading.Thread):
    """Background thread that runs database cleanup daily."""
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        log.info("Cleanup scheduler started (runs daily at 3 AM)")
        while self.running:
            now = datetime.now()
            # Schedule for 3 AM next day
            next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            
            sleep_seconds = (next_run - now).total_seconds()
            
            # Sleep in 60-second intervals to allow graceful shutdown
            while sleep_seconds > 0 and self.running:
                time.sleep(min(60, sleep_seconds))
                sleep_seconds -= 60
            
            if self.running:
                try:
                    cleanup_old_data()
                except Exception as e:
                    log.error("Cleanup error: %s", e)
        
        log.info("Cleanup scheduler stopped")

    def stop(self):
        self.running = False


# ---------- Flask API ----------

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes


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
            "/api/busy-hours?date=YYYY-MM-DD": "Estimated busy hours (unique users per hour)",
            "/api/cleanup": "Manually trigger database cleanup",
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

    # Total events is kept for backwards compatibility, but we do not track event counts.
    # Treat this as "unique users".
    total_events = unique_count

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
        ORDER BY last_seen DESC
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
               COUNT(DISTINCT mac_address) as total_events
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

    # Backwards-compatible total_events (we do not track event counts)
    total_events = unique_for_month

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
               COUNT(DISTINCT mac_address) as total_events
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
               COUNT(DISTINCT mac_address) as total_events
        FROM daily_stats WHERE date >= date('now', ?)
        GROUP BY ssid ORDER BY unique_users DESC
        """,
        (f"-{days} days",),
    )
    ssids = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({"period_days": days, "ssids": ssids})


@app.route("/api/busy-hours")
def api_busy_hours():
    """Estimate busy hours of day by distributing unique users across connection times."""
    date = request.args.get("date")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    c = conn.cursor()

    # Fetch all unique users for the date with their first/last seen timestamps
    c.execute(
        """
        SELECT mac_address, first_seen, last_seen
        FROM daily_stats
        WHERE date = ?
        """,
        (date,),
    )
    rows = c.fetchall()
    conn.close()

    # Build hourly histogram
    hourly_counts = {h: set() for h in range(24)}

    for row in rows:
        mac = row["mac_address"]
        first = row["first_seen"]
        last = row["last_seen"]

        if not first or not last:
            continue

        try:
            first_dt = datetime.fromisoformat(first)
            last_dt = datetime.fromisoformat(last)
        except Exception:
            continue

        # If user connected for multiple hours, count them in each hour
        start_hour = first_dt.hour
        end_hour = last_dt.hour

        if start_hour == end_hour:
            hourly_counts[start_hour].add(mac)
        else:
            for h in range(start_hour, end_hour + 1):
                hourly_counts[h % 24].add(mac)

    # Convert to list of {hour, unique_users}
    result = [{"hour": h, "unique_users": len(macs)} for h, macs in sorted(hourly_counts.items())]

    return jsonify({
        "date": date,
        "busy_hours": result,
    })


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """Manually trigger database cleanup."""
    try:
        result = cleanup_old_data()
        return jsonify({
            "status": "success",
            "message": "Database cleanup completed",
            "deleted": result
        })
    except Exception as e:
        log.error("Manual cleanup failed: %s", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ---------- Run ----------

def start_all():
    """Start syslog listener, cleanup scheduler, and Flask API."""
    init_db()
    listener = SyslogListener()
    listener.start()
    
    scheduler = CleanupScheduler()
    scheduler.start()
    
    log.info("Starting API on http://%s:%d", API_HOST, API_PORT)
    return listener, scheduler


def main():
    listener, scheduler = start_all()
    try:
        app.run(host=API_HOST, port=API_PORT, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        scheduler.stop()
        log.info("Service stopped")


if __name__ == "__main__":
    main()
