# WiFi Monitor

Wireless user statistics service for **Daviess County Public Library**. Captures syslog authentication events from HP MSM wireless controllers and provides a REST API for tracking daily unique users by MAC address.

## Features

- **Syslog Listener** — Receives UDP syslog from HP MSM controllers on port 514
- **MAC Address Tracking** — Identifies unique wireless users by MAC address per day
- **REST API** — Query stats by date, month, date range, SSID, and unique user counts
- **Windows Service** — Runs as a background service on Windows Server
- **SQLite Database** — Lightweight, zero-config persistent storage
- **CSV Export** — Export stats for reporting

## Compatible Controllers

- HP MSM775 Wireless Controller
- HP MSM760 Wireless Controller
- HP MSM710/720 Access Controllers
- Other HP MSM series controllers with remote syslog support

## Controller Configuration

Configure the MSM controller to forward authentication events via syslog.

### Steps

1. Log into the MSM controller web UI (e.g. `https://10.0.20.2`)
2. Navigate to **Tools** > **Remote log**
3. Check **Remote logging entry** to enable
4. Configure the **Remote syslog server** section:
   - **Name:** Name of the receiving machine (e.g. `WiFi-Monitor-Server`)
   - **Server address:** IP address of the machine running this service
   - **Protocol:** `UDP`
   - **Port:** `514`
   - **Facility:** `local1`
   - **Message prefix:** *(leave blank)*
   - **Source:** `Local`
5. Configure the **Filter definitions** section:
   - **Severity level:** ☑ `Lower than` → `warning`
   - **Message:** ☑ `Matches` regular expression → `successfully auth`
   - **Process name:** ☑ `Is` → `eventmgr`
   - **Combine filters using:** `AND`
6. Click **Save**

> **Note:** The filter ensures only successful authentication events from the event manager are forwarded, reducing noise and database size.

## Requirements

- Python 3.10+
- Flask
- pywin32 (Windows only, for service support)

## Installation

### Quick Start (any OS)

```bash
pip install -r requirements.txt
sudo python3 app.py
```

The service will start:
- **Syslog listener** on UDP port 514
- **REST API** on http://localhost:8080

### Windows Service Install

1. Copy the `wireless_stats` folder to the Windows server
2. Open a **Command Prompt as Administrator**
3. Run:

```powershell
pip install -r requirements.txt
python wireless_service_win.py install
python wireless_service_win.py start
```

Or use the one-click installer:
- Right-click `install_windows.bat` → **Run as administrator**

### Manage the Windows Service

```powershell
net stop WirelessStatsService
net start WirelessStatsService
python wireless_service_win.py remove
```

The service is also visible in `services.msc` as **Wireless User Statistics Service**.

### Linux Install

```bash
git clone git@github.com:dcplibrary/wifi-monitor.git
cd wifi-monitor
pip install -r requirements.txt
sudo python3 app.py
```

To run as a systemd service:

```bash
sudo cp wifi-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable wifi-monitor
sudo systemctl start wifi-monitor
```

Check status:

```bash
sudo systemctl status wifi-monitor
journalctl -u wifi-monitor -f
```

### Docker

```bash
git clone git@github.com:dcplibrary/wifi-monitor.git
cd wifi-monitor
docker compose up -d
```

This starts the service with:
- Syslog listener on UDP 514
- REST API on http://localhost:8080
- Persistent data volume (`wifi-data`)
- Auto-restart on reboot

Manage the container:

```bash
docker compose logs -f          # View logs
docker compose restart           # Restart
docker compose down              # Stop
docker compose down -v           # Stop and remove data
```

To use a pre-built image without cloning:

```bash
docker run -d \
  --name wifi-monitor \
  --restart unless-stopped \
  -p 514:514/udp \
  -p 8080:8080 \
  -v wifi-data:/app/data \
  ghcr.io/dcplibrary/wifi-monitor:latest
```

### macOS (CLI mode)

```bash
sudo python3 wireless_monitor.py
```

Press `Ctrl+C` to stop and print a summary report.

## API Endpoints

Base URL: `http://localhost:8080`

### `GET /api/today`

Current day's stats.

```json
{
  "date": "2026-03-02",
  "unique_users": 23,
  "total_events": 86,
  "ssid_breakdown": [
    {"ssid": "DCPL-PATRON", "unique_users": 19},
    {"ssid": "DCPL-STAFF", "unique_users": 4}
  ]
}
```

### `GET /api/date/{YYYY-MM-DD}`

Stats for a specific date.

```
/api/date/2026-03-01
```

### `GET /api/month/{YYYY-MM}`

Monthly tally with daily breakdown.

```
/api/month/2026-03
```

```json
{
  "month": "2026-03",
  "unique_users_this_month": 142,
  "total_events": 5280,
  "ssid_breakdown": [...],
  "daily_breakdown": [
    {"date": "2026-03-01", "unique_users": 45, "total_events": 620},
    {"date": "2026-03-02", "unique_users": 23, "total_events": 86}
  ]
}
```

### `GET /api/unique-users`

All-time unique MAC addresses. Add `?days=N` to limit.

```
/api/unique-users
/api/unique-users?days=30
```

### `GET /api/range?start={date}&end={date}`

Stats for a date range.

```
/api/range?start=2026-02-01&end=2026-02-28
```

### `GET /api/top-devices?days=N&limit=N`

Most frequently seen devices. Defaults to last 7 days, top 20.

### `GET /api/ssids?days=N`

Per-SSID breakdown. Defaults to last 7 days.

## CLI Commands (wireless_monitor.py)

```bash
python3 wireless_monitor.py                  # Start syslog listener (default)
python3 wireless_monitor.py report           # Last 7 days report
python3 wireless_monitor.py report 30        # Last 30 days report
python3 wireless_monitor.py export           # Export CSV (last 30 days)
python3 wireless_monitor.py import file.pcap # Import from tcpdump pcap
```

## Files

```
wireless_stats/
├── app.py                    # Main service: syslog + Flask API
├── wireless_monitor.py       # Standalone CLI monitor
├── wireless_service_win.py   # Windows service wrapper
├── install_windows.bat       # One-click Windows installer
├── Dockerfile                # Docker image definition
├── docker-compose.yml        # Docker Compose config
├── wifi-monitor.service      # systemd unit file (Linux)
├── requirements.txt          # Python dependencies
├── wireless_stats.db         # SQLite database (auto-created)
├── wireless_service.log      # Service log (auto-created)
└── README.md
```

## Database

SQLite database (`wireless_stats.db`) with two tables:

- **auth_events** — Raw syslog events (MAC, type, SSID, timestamp)
- **daily_stats** — Aggregated daily stats per MAC (unique constraint on date + MAC)

The database uses WAL mode for concurrent read/write access from the syslog listener and API threads.

## License

Internal use — Daviess County Public Library.
