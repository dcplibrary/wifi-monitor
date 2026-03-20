#!/usr/bin/env python3
"""
Import historical authentication events from HP MSM controller CSV exports.

This script parses CSV files exported from the HP MSM controller and backfills
the wifi-monitor database with historical data.

CSV Format Expected:
Severity,ID,Device,System name,Device type,Category,Type,Alarm ID,Description,Timestamp

Usage:
    python3 import_csv.py /path/to/2026-03-03_events.csv
    python3 import_csv.py /path/to/wireless_events/*.csv
"""

import csv
import re
import sqlite3
import sys
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "wireless_stats.db"))

# SSID normalization — must match app.py defaults (v1=STAFF, v2=PATRON)
SSID_V1_NAME = os.environ.get("SSID_V1_NAME", "DCPL-STAFF")
SSID_V2_NAME = os.environ.get("SSID_V2_NAME", "DCPL-PATRON")
SSID_V3_NAME = os.environ.get("SSID_V3_NAME", "DCPL-OPS")

# Regex patterns
MAC_PATTERN = re.compile(r"mac='([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})'")
SSID_PATTERN = re.compile(r"SSID \(value='([^']+)'\)")
INTERFACE_PATTERN = re.compile(r"interface \(value='([^']+)'\)")


def normalize_ssid(ssid):
    """Normalize SSID to friendly names."""
    if not ssid:
        return None
    
    # Already normalized
    m = re.search(r"DCPL-(PATRON|STAFF|OPS)", ssid, re.IGNORECASE)
    if m:
        return f"DCPL-{m.group(1).upper()}"
    
    lower = ssid.lower()
    
    # HP MSM style interface codes
    if re.search(r"v1(?!\d)", lower):
        return SSID_V1_NAME
    if re.search(r"v2(?!\d)", lower):
        return SSID_V2_NAME
    if re.search(r"v3(?!\d)", lower):
        return SSID_V3_NAME
    
    return ssid


def parse_csv_row(row):
    """Parse a CSV row and extract MAC, SSID, and timestamp."""
    try:
        description = row.get("Description", "")
        timestamp_str = row.get("Timestamp", "")
        
        # Only process successful authentications
        if "successfully authenticated" not in description.lower():
            return None
        
        # Extract MAC address
        mac_match = MAC_PATTERN.search(description)
        if not mac_match:
            return None
        mac = mac_match.group(1).upper()
        
        # Extract SSID (try direct SSID field first, then interface)
        ssid = None
        ssid_match = SSID_PATTERN.search(description)
        if ssid_match:
            ssid = ssid_match.group(1)
        else:
            # Try interface value as fallback
            interface_match = INTERFACE_PATTERN.search(description)
            if interface_match:
                ssid = interface_match.group(1)
        
        ssid = normalize_ssid(ssid)
        
        # Parse timestamp (format: "2026-03-03 12:04:52,154")
        # Remove milliseconds and parse
        timestamp_clean = timestamp_str.split(",")[0].strip('"')
        dt = datetime.strptime(timestamp_clean, "%Y-%m-%d %H:%M:%S")
        
        return {
            "mac": mac,
            "ssid": ssid,
            "timestamp": dt,
            "date": dt.strftime("%Y-%m-%d")
        }
    except Exception as e:
        print(f"Warning: Failed to parse row: {e}", file=sys.stderr)
        return None


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def import_csv_file(csv_path):
    """Import authentication events from a CSV file."""
    print(f"\nProcessing: {csv_path}")
    
    # Collect events by date and MAC
    events_by_date_mac = {}
    
    row_count = 0
    parsed_count = 0
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            row_count += 1
            event = parse_csv_row(row)
            
            if event:
                parsed_count += 1
                key = (event["date"], event["mac"])
                
                if key not in events_by_date_mac:
                    events_by_date_mac[key] = {
                        "mac": event["mac"],
                        "date": event["date"],
                        "ssid": event["ssid"],
                        "first_seen": event["timestamp"],
                        "last_seen": event["timestamp"]
                    }
                else:
                    # Update first/last seen
                    if event["timestamp"] < events_by_date_mac[key]["first_seen"]:
                        events_by_date_mac[key]["first_seen"] = event["timestamp"]
                    if event["timestamp"] > events_by_date_mac[key]["last_seen"]:
                        events_by_date_mac[key]["last_seen"] = event["timestamp"]
                    
                    # Prefer non-None SSID
                    if event["ssid"] and not events_by_date_mac[key]["ssid"]:
                        events_by_date_mac[key]["ssid"] = event["ssid"]
    
    print(f"  Total rows: {row_count}")
    print(f"  Parsed auth events: {parsed_count}")
    print(f"  Unique MACs/dates: {len(events_by_date_mac)}")
    
    # Insert into database
    conn = get_db()
    c = conn.cursor()
    
    inserted = 0
    updated = 0
    
    for event_data in events_by_date_mac.values():
        # Check if already exists
        c.execute(
            "SELECT first_seen, last_seen FROM daily_stats WHERE date = ? AND mac_address = ?",
            (event_data["date"], event_data["mac"])
        )
        existing = c.fetchone()
        
        if existing:
            # Update if our data extends the time range
            existing_first = datetime.fromisoformat(existing["first_seen"])
            existing_last = datetime.fromisoformat(existing["last_seen"])
            
            new_first = min(event_data["first_seen"], existing_first)
            new_last = max(event_data["last_seen"], existing_last)
            
            c.execute(
                """
                UPDATE daily_stats 
                SET first_seen = ?, last_seen = ?, ssid = COALESCE(?, ssid)
                WHERE date = ? AND mac_address = ?
                """,
                (
                    new_first.isoformat(),
                    new_last.isoformat(),
                    event_data["ssid"],
                    event_data["date"],
                    event_data["mac"]
                )
            )
            updated += 1
        else:
            # Insert new record
            c.execute(
                """
                INSERT INTO daily_stats (date, mac_address, auth_count, first_seen, last_seen, ssid)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (
                    event_data["date"],
                    event_data["mac"],
                    event_data["first_seen"].isoformat(),
                    event_data["last_seen"].isoformat(),
                    event_data["ssid"]
                )
            )
            inserted += 1
    
    conn.commit()
    conn.close()
    
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    
    return {"inserted": inserted, "updated": updated, "unique_macs": len(events_by_date_mac)}


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 import_csv.py <csv_file> [<csv_file2> ...]", file=sys.stderr)
        print("\nExample:")
        print("  python3 import_csv.py /path/to/2026-03-03_events.csv")
        print("  python3 import_csv.py /path/to/wireless_events/*.csv")
        sys.exit(1)
    
    csv_files = sys.argv[1:]
    
    print(f"WiFi Monitor CSV Import")
    print(f"Database: {DB_PATH}")
    print(f"Files to import: {len(csv_files)}")
    
    total_inserted = 0
    total_updated = 0
    
    for csv_file in csv_files:
        if not os.path.exists(csv_file):
            print(f"\nWarning: File not found: {csv_file}", file=sys.stderr)
            continue
        
        try:
            result = import_csv_file(csv_file)
            total_inserted += result["inserted"]
            total_updated += result["updated"]
        except Exception as e:
            print(f"\nError processing {csv_file}: {e}", file=sys.stderr)
    
    print(f"\n=== Import Complete ===")
    print(f"Total records inserted: {total_inserted}")
    print(f"Total records updated: {total_updated}")


if __name__ == "__main__":
    main()
