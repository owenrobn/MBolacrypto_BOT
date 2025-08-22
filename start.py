#!/usr/bin/env python3
"""
Minimal startup for FreshBot (Render-friendly).
"""

import os
import sys
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from bot import FreshBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

bot_instance = None

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (keep simple)
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def start_health_server(port: int) -> None:
    def _run():
        srv = HTTPServer(("", port), HealthHandler)
        logger.info(f"Health check server on :{port}")
        srv.serve_forever()
    threading.Thread(target=_run, daemon=True).start()

def cleanup():
    logger.info("Shutdown complete")

def _signal(signum, frame):  # noqa: ANN001, D401
    logger.info(f"Signal {signum} received; exiting")
    cleanup()
    sys.exit(0)

def main() -> None:
    global bot_instance

    # Signals
    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    # Start bot
    logger.info("Starting FreshBot…")
    bot_instance = FreshBot()

    webhook_url = os.environ.get("WEBHOOK_URL")

    # Health server only in polling mode (local). On Render (webhook), PTB binds PORT.
    if not webhook_url:
        port = int(os.environ.get("PORT", "10000"))
        start_health_server(port)

    # Run
    bot_instance.run(webhook_url)

if __name__ == "__main__":
    main()
