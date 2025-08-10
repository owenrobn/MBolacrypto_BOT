import sqlite3
import os
import uuid
from datetime import datetime
from typing import Optional, List, Tuple

class Database:
    def __init__(self, db_path: str = "contest.db"):
        # Allow override via environment variable (useful for Render Disk mounts)
        env_db_path = os.getenv("DB_PATH")
        self.db_path = env_db_path if env_db_path else db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (referred_by) REFERENCES users (user_id)
                )
            ''')
            
            # Events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_code TEXT UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT,
                    host_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (host_id) REFERENCES users (user_id)
                )
            ''')

            # Ensure group_link column exists (migration for older DBs)
            try:
                cursor.execute("PRAGMA table_info(events)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'group_link' not in columns:
                    cursor.execute("ALTER TABLE events ADD COLUMN group_link TEXT")
            except Exception:
                # In case PRAGMA isn't supported as expected, fail silently
                pass
            
            # Event participants table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER,
                    user_id INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events (id),
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    UNIQUE(event_id, user_id)
                )
            ''')
            
            # Referrals table for tracking (updated to include event context)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    event_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                    FOREIGN KEY (referred_id) REFERENCES users (user_id),
                    FOREIGN KEY (event_id) REFERENCES events (id)
                )
            ''')
            
            conn.commit()
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None, 
                 last_name: str = None, referred_by: int = None, event_id: int = None) -> str:
        """Add a new user and return their referral code."""
        referral_code = str(uuid.uuid4())[:8].upper()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, referral_code, referred_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, referral_code, referred_by))
            
            # If user was referred, add to referrals table
            if referred_by:
                cursor.execute('''
                    INSERT INTO referrals (referrer_id, referred_id, event_id)
                    VALUES (?, ?, ?)
                ''', (referred_by, user_id, event_id))
            
            # If joining an event, add to event participants
            if event_id:
                cursor.execute('''
                    INSERT OR IGNORE INTO event_participants (event_id, user_id)
                    VALUES (?, ?)
                ''', (event_id, user_id))
            
            conn.commit()
            return referral_code
    
    def get_user(self, user_id: int) -> Optional[dict]:
        """Get user information by user_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, referral_code, 
                       referred_by, joined_at, is_active
                FROM users WHERE user_id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'last_name': row[3],
                    'referral_code': row[4],
                    'referred_by': row[5],
                    'joined_at': row[6],
                    'is_active': row[7]
                }
            return None
    
    def get_user_by_referral_code(self, referral_code: str) -> Optional[dict]:
        """Get user information by referral code."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, last_name, referral_code, 
                       referred_by, joined_at, is_active
                FROM users WHERE referral_code = ?
            ''', (referral_code,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'last_name': row[3],
                    'referral_code': row[4],
                    'referred_by': row[5],
                    'joined_at': row[6],
                    'is_active': row[7]
                }
            return None
    
    def get_referral_stats(self, user_id: int) -> dict:
        """Get referral statistics for a user."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Count direct referrals
            cursor.execute('''
                SELECT COUNT(*) FROM referrals WHERE referrer_id = ?
            ''', (user_id,))
            direct_referrals = cursor.fetchone()[0]
            
            # Get list of referred users
            cursor.execute('''
                SELECT u.user_id, u.username, u.first_name, u.joined_at
                FROM users u
                JOIN referrals r ON u.user_id = r.referred_id
                WHERE r.referrer_id = ?
                ORDER BY u.joined_at DESC
            ''', (user_id,))
            
            referred_users = []
            for row in cursor.fetchall():
                referred_users.append({
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'joined_at': row[3]
                })
            
            return {
                'total_referrals': direct_referrals,
                'referred_users': referred_users
            }
    
    def get_leaderboard(self, limit: int = 10) -> List[dict]:
        """Get top referrers leaderboard."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.user_id, u.username, u.first_name, COUNT(r.referred_id) as referral_count
                FROM users u
                LEFT JOIN referrals r ON u.user_id = r.referrer_id
                GROUP BY u.user_id, u.username, u.first_name
                ORDER BY referral_count DESC
                LIMIT ?
            ''', (limit,))
            
            leaderboard = []
            for row in cursor.fetchall():
                leaderboard.append({
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'referral_count': row[3]
                })
            
            return leaderboard
    
    def create_event(self, host_id: int, title: str, description: str = None, group_link: str = None) -> str:
        """Create a new event and return its event code."""
        event_code = str(uuid.uuid4())[:8].upper()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO events (event_code, title, description, group_link, host_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (event_code, title, description, group_link, host_id))
            
            event_id = cursor.lastrowid
            
            # Add host as first participant
            cursor.execute('''
                INSERT INTO event_participants (event_id, user_id)
                VALUES (?, ?)
            ''', (event_id, host_id))
            
            conn.commit()
            return event_code
    
    def get_event_stats(self, event_id: int) -> dict:
        """Get statistics for a specific event."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total participants
            cursor.execute('''
                SELECT COUNT(*) FROM event_participants WHERE event_id = ?
            ''', (event_id,))
            total_participants = cursor.fetchone()[0]
            
            # Total referrals within this event
            cursor.execute('''
                SELECT COUNT(*) FROM referrals WHERE event_id = ?
            ''', (event_id,))
            total_referrals = cursor.fetchone()[0]
            
            # Top referrers in this event
            cursor.execute('''
                SELECT u.user_id, u.first_name, u.username, COUNT(r.referred_id) as referral_count
                FROM users u
                LEFT JOIN referrals r ON u.user_id = r.referrer_id AND r.event_id = ?
                JOIN event_participants ep ON u.user_id = ep.user_id AND ep.event_id = ?
                GROUP BY u.user_id, u.first_name, u.username
                ORDER BY referral_count DESC
                LIMIT 10
            ''', (event_id, event_id))
            
            top_referrers = []
            for row in cursor.fetchall():
                top_referrers.append({
                    'user_id': row[0],
                    'first_name': row[1],
                    'username': row[2],
                    'referral_count': row[3]
                })
            
            # Recent participants
            cursor.execute('''
                SELECT u.user_id, u.first_name, u.username, ep.joined_at
                FROM users u
                JOIN event_participants ep ON u.user_id = ep.user_id
                WHERE ep.event_id = ?
                ORDER BY ep.joined_at DESC
                LIMIT 10
            ''', (event_id,))
            
            recent_participants = []
            for row in cursor.fetchall():
                recent_participants.append({
                    'user_id': row[0],
                    'first_name': row[1],
                    'username': row[2],
                    'joined_at': row[3]
                })
            
            return {
                'total_participants': total_participants,
                'total_referrals': total_referrals,
                'top_referrers': top_referrers,
                'recent_participants': recent_participants
            }
    
    def get_user_referrals_in_event(self, user_id: int, event_id: int) -> dict:
        """Get user's referral stats within a specific event."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Count referrals in this event
            cursor.execute('''
                SELECT COUNT(*) FROM referrals 
                WHERE referrer_id = ? AND event_id = ?
            ''', (user_id, event_id))
            referral_count = cursor.fetchone()[0]
            
            # Get referred users in this event
            cursor.execute('''
                SELECT u.user_id, u.username, u.first_name, r.created_at
                FROM users u
                JOIN referrals r ON u.user_id = r.referred_id
                WHERE r.referrer_id = ? AND r.event_id = ?
                ORDER BY r.created_at DESC
            ''', (user_id, event_id))
            
            referred_users = []
            for row in cursor.fetchall():
                referred_users.append({
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'joined_at': row[3]
                })
            
            return {
                'referral_count': referral_count,
                'referred_users': referred_users
            }
