# WiFi Monitor

Wireless user statistics service for **Daviess County Public Library**. Captures syslog authentication events from HP MSM wireless controllers and provides a REST API for tracking daily unique users by MAC address.

## Features

- **Syslog Listener** — Receives UDP syslog from HP MSM controllers on port 514
- **MAC Address Tracking** — Identifies unique wireless users by MAC address per day
- **Time-of-Day Analysis** — Tracks first/last connection times for usage pattern analysis
- **SSID Normalization** — Automatically maps controller SSID codes to friendly names
- **REST API** — Query stats by date, month, date range, SSID, busy hours, and unique user counts
- **Windows Service** — Runs as a background service on Windows Server
- **SQLite Database** — Lightweight, zero-config persistent storage with automatic cleanup
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

## Configuration

The service can be configured using a `.env` file. Copy `.env.example` to `.env` and modify as needed:

```bash
cp .env.example .env
```

Available settings:
- `API_PORT` - Port for the REST API (default: 8088)
- `API_HOST` - Host to bind the API to (default: 0.0.0.0)
- `SYSLOG_PORT` - Port for syslog listener (default: 514)
- `SYSLOG_HOST` - Host to bind syslog listener to (default: 0.0.0.0)
- `DB_PATH` - Path to SQLite database file (default: ./wireless_stats.db)
- `LOG_PATH` - Path to service log file (default: ./wireless_service.log)
- `AUTH_EVENTS_RETENTION_DAYS` - Days to keep raw auth events (default: 30, set to 0 to disable)
- `DAILY_STATS_RETENTION_DAYS` - Days to keep daily stats (default: 365, set to 0 to disable)
- `STORE_AUTH_EVENTS` - Store raw authentication events (default: false, recommended: false)
- `SSID_V1_NAME` - Friendly name for v1 SSIDs (default: DCPL-PATRON)
- `SSID_V2_NAME` - Friendly name for v2 SSIDs (default: DCPL-STAFF)
- `SSID_V3_NAME` - Friendly name for v3 SSIDs (default: DCPL-OPS)

## Requirements

- Python 3.10+
- Flask
- python-dotenv
- pywin32 (Windows only, for service support)

### Installing Python

#### Windows

1. Download Python 3.10+ from [python.org](https://www.python.org/downloads/)
2. Run the installer and **check** "Add Python to PATH"
3. Verify installation:

```powershell
python --version
```

#### macOS

Using Homebrew:

```bash
brew install python@3.10
```

Or download from [python.org](https://www.python.org/downloads/)

Verify installation:

```bash
python3 --version
```

#### Linux

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-pip
```

RHEL/CentOS/Fedora:

```bash
sudo dnf install python3 python3-pip
```

Verify installation:

```bash
python3 --version
```

## Installation

### Configuration

Copy the example environment file and customize as needed:

```bash
cp .env.example .env
```

Edit `.env` to change settings like:
- `API_PORT` — REST API port (default: 8088)
- `SYSLOG_PORT` — Syslog listener port (default: 514)
- Data retention periods

### Quick Start (any OS)

```bash
cp .env.example .env
pip install -r requirements.txt
sudo python3 app.py
```

The service will start:
- **Syslog listener** on UDP port 514
- **REST API** on http://localhost:8088 (or port specified in `.env`)

### Windows Service Install

1. Copy the `wireless_stats` folder to the Windows server
2. Copy `.env.example` to `.env` and edit as needed:
   ```powershell
   copy .env.example .env
   notepad .env
   ```
3. Open a **Command Prompt as Administrator**
4. Run:

```powershell
pip install -r requirements.txt
python wireless_service_win.py install
python wireless_service_win.py start
```

Or use the one-click installer:
- Right-click `install_windows.bat` → **Run as administrator**

> **Tip:** If PowerShell scripts won't run due to execution policy, you can bypass it temporarily:
> ```powershell
> powershell -ExecutionPolicy Bypass -File script.ps1
> ```
> Or permanently allow local scripts:
> ```powershell
> Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Manage the Windows Service

```powershell
net stop WirelessStatsService
net start WirelessStatsService
python wireless_service_win.py remove
```

The service is also visible in `services.msc` as **Wireless User Statistics Service**.

## Updating the Service

### Update via Git (Recommended)

If you have Git installed:

**Windows:**
```powershell
net stop WirelessStatsService
cd C:\path\to\wifi-monitor
git pull origin main
net start WirelessStatsService
```

**Linux/macOS:**
```bash
sudo systemctl stop wifi-monitor  # If using systemd
cd /path/to/wifi-monitor
git pull origin main
sudo systemctl start wifi-monitor
```

### Update without Git

Update scripts are provided to download the latest version directly from GitHub without requiring Git.

**Windows:**

Right-click PowerShell and select **Run as Administrator**, then:

```powershell
cd C:\path\to\wifi-monitor
powershell -ExecutionPolicy Bypass -File .\update.ps1
```

Or if execution policy allows:

```powershell
.\update.ps1
```

> **Note:** If you get an "execution policy" error, use the `-ExecutionPolicy Bypass` flag as shown above. This bypasses the policy for just this script without changing system settings.

**Linux/macOS:**

```bash
cd /path/to/wifi-monitor
./update.sh
```

Both scripts will:
1. Stop the service (if running)
2. Download the latest version from GitHub
3. Update all files except `.env`, `wireless_stats.db`, and logs
4. Restart the service

**Custom repository or branch:**

```powershell
# Windows
.\update.ps1 -Repo "username/repo" -Branch "develop"
```

```bash
# Linux/macOS
REPO="username/repo" BRANCH="develop" ./update.sh
```

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
- REST API on http://localhost:8088 (or port specified in `.env`)
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
  -p 8088:8088 \
  -v wifi-data:/app/data \
  ghcr.io/dcplibrary/wifi-monitor:latest
```

### macOS (CLI mode)

```bash
sudo python3 wireless_monitor.py
```

Press `Ctrl+C` to stop and print a summary report.

## API Endpoints

Base URL: `http://localhost:8088` (or your configured port)

### `GET /api/today`

Current day's stats.

```json
{
  "date": "2026-03-02",
  "unique_users": 23,
  "ssid_breakdown": [
    {"ssid": "DCPL-PATRON", "unique_users": 19},
    {"ssid": "DCPL-STAFF", "unique_users": 4}
  ],
  "devices": [
    {
      "mac_address": "AA:BB:CC:DD:EE:FF",
      "first_seen": "2026-03-02T09:15:23",
      "last_seen": "2026-03-02T16:42:18",
      "ssid": "DCPL-PATRON"
    }
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
  "ssid_breakdown": [...],
  "daily_breakdown": [
    {"date": "2026-03-01", "unique_users": 45},
    {"date": "2026-03-02", "unique_users": 23}
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

### `GET /api/busy-hours?date={YYYY-MM-DD}`

Estimate busy hours of day based on user connection times. Defaults to today.

```
/api/busy-hours?date=2026-03-02
```

```json
{
  "date": "2026-03-02",
  "busy_hours": [
    {"hour": 0, "unique_users": 0},
    {"hour": 9, "unique_users": 12},
    {"hour": 14, "unique_users": 23},
    {"hour": 23, "unique_users": 2}
  ]
}
```

### `POST /api/cleanup`

Manually trigger database cleanup to remove old records based on retention policy.

```bash
curl -X POST http://localhost:8088/api/cleanup
```

## Database Maintenance

The service automatically cleans up old data daily at 3 AM to keep the database size manageable:

- **Raw authentication events** — Stored only if `STORE_AUTH_EVENTS=true` (disabled by default for efficiency), deleted after `AUTH_EVENTS_RETENTION_DAYS` (default: 30)
- **Daily statistics** — Deleted after `DAILY_STATS_RETENTION_DAYS` (default: 365)

You can manually trigger cleanup anytime via the API:

```bash
curl -X POST http://localhost:8088/api/cleanup
```

The cleanup process also runs `VACUUM` to reclaim disk space.

### Event Count Tracking

By design, this service **does not track event counts** (i.e., how many times a user authenticated). Instead:
- Each unique MAC address is counted once per day
- `first_seen` and `last_seen` timestamps track arrival/departure times
- This approach minimizes storage and provides cleaner unique user statistics

If you need to track raw authentication events for audit purposes, set `STORE_AUTH_EVENTS=true` in `.env`.

## Importing Historical Data

You can backfill the database with historical authentication data from HP MSM controller CSV exports.

### CSV Format

The import script expects CSV files exported from the HP MSM controller with this format:

```
Severity,ID,Device,System name,Device type,Category,Type,Alarm ID,Description,Timestamp
```

The script will:
- Parse MAC addresses from the Description field
- Extract SSID information and normalize it (e.g., `r1v2` → `DCPL-PATRON`)
- Track first/last authentication times per MAC per day
- Skip or merge duplicate records intelligently

### Usage

**Import a single file:**

```bash
python3 import_csv.py /path/to/2026-03-03_events.csv
```

**Import multiple files:**

```bash
python3 import_csv.py /path/to/*.csv
python3 import_csv.py 2026-03-02_events.csv 2026-03-03_events.csv
```

**Windows:**

```powershell
python import_csv.py C:\path\to\wireless_events\2026-03-03_events.csv
```

The script will output:
- Total rows processed
- Number of authentication events parsed
- Unique MACs per day
- Records inserted vs. updated

> **Note:** If the database already has data for a date/MAC, the script will intelligently merge the time ranges (extending first_seen/last_seen as needed) rather than duplicating data.

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
├── import_csv.py             # CSV import script for historical data
├── update.ps1                # Windows update script (no Git required)
├── update.sh                 # Linux/macOS update script (no Git required)
├── install_windows.bat       # One-click Windows installer
├── Dockerfile                # Docker image definition
├── docker-compose.yml        # Docker Compose config
├── wifi-monitor.service      # systemd unit file (Linux)
├── requirements.txt          # Python dependencies
├── .env.example              # Configuration template
├── wireless_stats.db         # SQLite database (auto-created)
├── wireless_service.log      # Service log (auto-created)
└── README.md
```

## Database

SQLite database (`wireless_stats.db`) with two tables:

- **auth_events** — Optional raw syslog events (only stored if `STORE_AUTH_EVENTS=true`)
- **daily_stats** — Daily stats per unique MAC (date + MAC primary key):
  - `first_seen` — First authentication timestamp for the day
  - `last_seen` — Most recent authentication timestamp for the day
  - `ssid` — Normalized SSID name (e.g., DCPL-PATRON)
  - `auth_count` — Kept for backwards compatibility (always 1)

The database uses WAL mode for concurrent read/write access from the syslog listener and API threads.

### SSID Normalization

The service automatically normalizes HP MSM controller SSID identifiers:
- SSIDs containing `v1` → Mapped to `SSID_V1_NAME` (default: DCPL-PATRON)
- SSIDs containing `v2` → Mapped to `SSID_V2_NAME` (default: DCPL-STAFF)
- SSIDs containing `v3` → Mapped to `SSID_V3_NAME` (default: DCPL-OPS)
- SSIDs already containing `DCPL-*` → Preserved as-is

Configure custom names in `.env` if your network uses different naming conventions.

## License

Internal use — Daviess County Public Library.
