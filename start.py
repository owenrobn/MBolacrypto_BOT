#!/usr/bin/env python3
"""
Startup script for the Telegram bot on Render.
"""

import os
import sys
import logging
import atexit
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from multipurpose_bot import MultipurposeBot

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Global variable to store the bot instance
bot_instance = None

def cleanup():
    """Cleanup function to be called on exit."""
    global bot_instance
    if bot_instance and hasattr(bot_instance, 'app'):
        try:
            logger.info("Shutting down bot gracefully...")
            bot_instance.app.stop()
            bot_instance.app.shutdown()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

def signal_handler(signum, frame):
    """Handle termination signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    cleanup()
    sys.exit(0)

def main():
    """Main startup function."""
    global bot_instance
    
    # Check for existing instances
    if sys.platform == 'win32':
        import ctypes
        mutex_name = "Global\\TelegramBotInstance"
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        last_error = ctypes.get_last_error()
        
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            logger.error("Another instance of the bot is already running!")
            sys.exit(1)
    
    # Register cleanup handlers
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Simple HTTP server for Render health checks
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
    
    def run_http_server():
        port = int(os.environ.get('PORT', 10000))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f'Starting health check server on port {port}...')
        server.serve_forever()
    
    try:
        # Start HTTP server in a separate thread
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()
        
        logger.info("🚀 Starting bot...")
        # Initialize and run bot
        bot_instance = MultipurposeBot()
        bot_instance.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        cleanup()

if __name__ == "__main__":
    main()
