#!/usr/bin/env python3
"""
Startup script for the Telegram bot on Render.
"""

import os
import sys
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from multipurpose_bot import MultipurposeBot

# Configure logging
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

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP server for health checks."""
    
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()

def start_health_check_server(port: int = 10000):
    """Start a simple HTTP server for health checks."""
    def run_server():
        server_address = ('', port)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info(f"Health check server running on port {port}")
        httpd.serve_forever()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return thread

def cleanup():
    """Cleanup function to be called on exit."""
    global bot_instance
    if bot_instance:
        try:
            logger.info("Shutting down bot gracefully...")
            # Add any cleanup code here if needed
            logger.info("Bot shutdown complete")
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
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start health check server (required for Render)
    start_health_check_server(port=int(os.environ.get('PORT', '10000')))
    
    try:
        # Initialize and start the bot
        logger.info("Starting bot...")
        bot_instance = MultipurposeBot()
        
        # Set up webhook if running on Render
        if 'RENDER' in os.environ:
            webhook_url = os.environ.get('WEBHOOK_URL')
            if webhook_url:
                logger.info(f"Setting webhook to: {webhook_url}")
                bot_instance.set_webhook(webhook_url)
        
        # Register cleanup function
        import atexit
        atexit.register(cleanup)
        
        # Start the bot
        bot_instance.run()
        
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
