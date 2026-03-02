"""
Windows Service wrapper for Wireless Stats.
Install:   python wireless_service_win.py install
Start:     python wireless_service_win.py start
Stop:      python wireless_service_win.py stop
Remove:    python wireless_service_win.py remove
"""

import sys
import os
import time
import threading

# Add the project directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import win32serviceutil
import win32service
import win32event
import servicemanager

from app import app, start_all, SyslogListener, init_db, API_HOST, API_PORT, log


class WirelessStatsService(win32serviceutil.ServiceFramework):
    _svc_name_ = "WirelessStatsService"
    _svc_display_name_ = "Wireless User Statistics Service"
    _svc_description_ = "Captures syslog from HP MSM775 and provides REST API for wireless user stats"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.listener = None
        self.api_thread = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.listener:
            self.listener.stop()
        log.info("Service stop requested")

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self.main()

    def main(self):
        log.info("Windows service starting...")
        init_db()

        # Start syslog listener
        self.listener = SyslogListener()
        self.listener.start()

        # Start Flask API in a thread
        self.api_thread = threading.Thread(
            target=lambda: app.run(
                host=API_HOST, port=API_PORT, threaded=True, use_reloader=False
            ),
            daemon=True,
        )
        self.api_thread.start()

        log.info("Service running - syslog on UDP 514, API on port %d", API_PORT)

        # Wait for stop signal
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        self.listener.stop()
        log.info("Service stopped")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WirelessStatsService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(WirelessStatsService)
