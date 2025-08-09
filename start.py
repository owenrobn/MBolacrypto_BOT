#!/usr/bin/env python3
"""
Startup script for Referral Contest Bot on Render
Handles initialization and error recovery
"""

import os
import sys
import time
import logging
from enhanced_bot import EnhancedRefContestBot

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Main startup function with error handling and restart logic."""
    max_retries = 5
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info("🚀 Starting Referral Contest Bot...")
            logger.info(f"Attempt {retry_count + 1}/{max_retries}")
            
            # Initialize and run bot
            bot = EnhancedRefContestBot()
            bot.run()
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
            
        except Exception as e:
            retry_count += 1
            logger.error(f"Bot crashed with error: {e}")
            
            if retry_count < max_retries:
                wait_time = min(60 * retry_count, 300)  # Max 5 minutes
                logger.info(f"Restarting in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("Max retries reached. Bot stopping.")
                sys.exit(1)

if __name__ == "__main__":
    main()
