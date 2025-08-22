import sqlite3
import os
import logging
from typing import Optional, Dict, List, Tuple, Any
import time

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "bot_data.db"):
        """Initialize the database connection and create tables if they don't exist."""
        # Allow override via environment variable
        env_db_path = os.getenv("DB_PATH")
        self.db_path = env_db_path if env_db_path else db_path
        self._create_tables()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Create and return a new database connection."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # Enable column access by name
            return conn
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise
    
    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Warnings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reason TEXT,
                    timestamp INTEGER NOT NULL,
                    warned_by INTEGER NOT NULL
                )
            ''')
            
            # User activity table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_activity (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    last_active INTEGER NOT NULL,
                    join_date INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Referrals table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    PRIMARY KEY (referrer_id, referred_id)
                )
            ''')
            
            # Create indexes for better query performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_warnings_user 
                ON warnings(chat_id, user_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_activity_user 
                ON user_activity(user_id, last_active)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_referrals 
                ON referrals(referrer_id, timestamp)
            ''')
            
            conn.commit()
    
    # Warning methods
    def add_warning(self, chat_id: int, user_id: int, reason: str, warned_by: int) -> bool:
        """Add a warning for a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO warnings (chat_id, user_id, reason, timestamp, warned_by)
                    VALUES (?, ?, ?, ?, ?)
                ''', (chat_id, user_id, reason, int(time.time()), warned_by))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error adding warning: {e}")
            return False
    
    def get_warnings(self, chat_id: int, user_id: int) -> List[Dict[str, Any]]:
        """Get all warnings for a user in a chat."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM warnings 
                    WHERE chat_id = ? AND user_id = ?
                    ORDER BY timestamp DESC
                ''', (chat_id, user_id))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting warnings: {e}")
            return []
    
    def clear_warnings(self, chat_id: int, user_id: int) -> bool:
        """Clear all warnings for a user in a chat."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    DELETE FROM warnings 
                    WHERE chat_id = ? AND user_id = ?
                ''', (chat_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error clearing warnings: {e}")
            return False
    
    # User activity methods
    def update_user_activity(self, chat_id: int, user_id: int, username: str = None, 
                           first_name: str = None, last_name: str = None) -> bool:
        """Update or create user activity record."""
        try:
            timestamp = int(time.time())
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Try to update existing record
                cursor.execute('''
                    UPDATE user_activity 
                    SET message_count = message_count + 1, 
                        last_active = ?,
                        username = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name)
                    WHERE chat_id = ? AND user_id = ?
                ''', (timestamp, username, first_name, last_name, chat_id, user_id))
                
                # If no rows were updated, insert new record
                if cursor.rowcount == 0:
                    cursor.execute('''
                        INSERT INTO user_activity 
                        (chat_id, user_id, message_count, last_active, join_date, username, first_name, last_name)
                        VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                    ''', (chat_id, user_id, timestamp, timestamp, username, first_name, last_name))
                
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error updating user activity: {e}")
            return False
    
    def get_active_users_count(self, chat_id: int, start_time: int, end_time: int) -> int:
        """Count active users in a time range."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(DISTINCT user_id) 
                    FROM user_activity 
                    WHERE chat_id = ? AND last_active BETWEEN ? AND ?
                ''', (chat_id, start_time, end_time))
                return cursor.fetchone()[0] or 0
        except sqlite3.Error as e:
            logger.error(f"Error counting active users: {e}")
            return 0
    
    def get_top_active_users(self, chat_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top active users by message count."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id, username, first_name, last_name, message_count
                    FROM user_activity 
                    WHERE chat_id = ? 
                    ORDER BY message_count DESC 
                    LIMIT ?
                ''', (chat_id, limit))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting top active users: {e}")
            return []
    
    # Referral methods
    def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Add a new referral."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO referrals (referrer_id, referred_id, timestamp)
                    VALUES (?, ?, ?)
                ''', (referrer_id, referred_id, int(time.time())))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error adding referral: {e}")
            return False
    
    def get_referral_count(self, user_id: int) -> int:
        """Get the number of successful referrals for a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) FROM referrals 
                    WHERE referrer_id = ?
                ''', (user_id,))
                return cursor.fetchone()[0] or 0
        except sqlite3.Error as e:
            logger.error(f"Error getting referral count: {e}")
            return 0
    
    def get_referrals(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all referrals made by a user."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT r.*, u.username, u.first_name, u.last_name
                    FROM referrals r
                    LEFT JOIN user_activity u ON r.referred_id = u.user_id
                    WHERE r.referrer_id = ?
                    ORDER BY r.timestamp DESC
                ''', (user_id,))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting referrals: {e}")
            return []

    # Maintenance methods
    def vacuum(self) -> bool:
        """Run VACUUM to optimize the database."""
        try:
            with self._get_connection() as conn:
                conn.execute("VACUUM")
                return True
        except sqlite3.Error as e:
            logger.error(f"Error running VACUUM: {e}")
            return False
    
    def backup(self, backup_path: str) -> bool:
        """Create a backup of the database."""
        try:
            with self._get_connection() as source, sqlite3.connect(backup_path) as dest:
                source.backup(dest)
            return True
        except sqlite3.Error as e:
            logger.error(f"Error creating backup: {e}")
            return False

# Singleton instance
db = Database()
