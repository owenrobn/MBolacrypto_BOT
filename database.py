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

            # User preferences table (Phase 1: broadcast opt-in)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_prefs (
                    user_id INTEGER PRIMARY KEY,
                    opted_in INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')

            # Admins table for in-bot admin management
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Group moderation settings (Phase 2)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS group_settings (
                    chat_id INTEGER PRIMARY KEY,
                    anti_links INTEGER DEFAULT 0,
                    warn_threshold INTEGER DEFAULT 3,
                    mute_minutes_default INTEGER DEFAULT 10,
                    auto_ban_on_repeat INTEGER DEFAULT 1,
                    strikes_reset_on_mute INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Migration: ensure new columns exist
            try:
                cursor.execute("PRAGMA table_info(group_settings)")
                gcols = [row[1] for row in cursor.fetchall()]
                if 'auto_ban_on_repeat' not in gcols:
                    cursor.execute("ALTER TABLE group_settings ADD COLUMN auto_ban_on_repeat INTEGER DEFAULT 1")
                if 'strikes_reset_on_mute' not in gcols:
                    cursor.execute("ALTER TABLE group_settings ADD COLUMN strikes_reset_on_mute INTEGER DEFAULT 1")
            except Exception:
                pass
            
            # Warnings table for moderation
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    chat_id INTEGER,
                    user_id INTEGER,
                    count INTEGER DEFAULT 0,
                    last_reason TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')

            # Track repeated threshold violations (strikes). Strike increments when user hits threshold.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_violations (
                    chat_id INTEGER,
                    user_id INTEGER,
                    strikes INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')

            # Recent activity for tag actives (Phase 2)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS recent_activity (
                    chat_id INTEGER,
                    user_id INTEGER,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
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

    def get_user_events(self, host_id: int) -> List[dict]:
        """Return a list of events hosted by the given user."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, event_code, title, description, group_link, created_at, is_active
                FROM events
                WHERE host_id = ?
                ORDER BY created_at DESC
                LIMIT 50
            ''', (host_id,))
            events = []
            for row in cursor.fetchall():
                events.append({
                    'id': row[0],
                    'event_code': row[1],
                    'title': row[2],
                    'description': row[3],
                    'group_link': row[4],
                    'created_at': row[5],
                    'is_active': bool(row[6])
                })
            return events
    
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

    # ====== Preferences (Opt-in) ======
    def set_opt_in(self, user_id: int, opted_in: bool) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Ensure user exists in users table minimally
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, referral_code)
                VALUES (?, NULL, NULL, NULL, ?)
            ''', (user_id, str(uuid.uuid4())[:8].upper()))

            cursor.execute('''
                INSERT INTO user_prefs (user_id, opted_in, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET opted_in=excluded.opted_in, updated_at=CURRENT_TIMESTAMP
            ''', (user_id, 1 if opted_in else 0))
            conn.commit()

    def is_opted_in(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT opted_in FROM user_prefs WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return bool(row[0]) if row else False

    def get_opted_in_users(self) -> List[int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM user_prefs WHERE opted_in = 1')
            return [r[0] for r in cursor.fetchall()]

    # ====== Admins management ======
    def seed_admins(self, admin_ids: List[int]) -> None:
        if not admin_ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for uid in admin_ids:
                try:
                    cursor.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, NULL)', (uid,))
                except Exception:
                    pass
            conn.commit()

    def add_admin(self, user_id: int, added_by: int | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
            conn.commit()

    def remove_admin(self, user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
            conn.commit()

    def list_admins(self) -> List[int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM admins ORDER BY created_at ASC')
            return [r[0] for r in cursor.fetchall()]

    def is_admin(self, user_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM admins WHERE user_id = ? LIMIT 1', (user_id,))
            return cursor.fetchone() is not None

    # ====== Group moderation (Phase 2) ======
    def get_group_settings(self, chat_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT chat_id, anti_links, warn_threshold, mute_minutes_default, auto_ban_on_repeat, strikes_reset_on_mute FROM group_settings WHERE chat_id = ?', (chat_id,))
            row = cursor.fetchone()
            if not row:
                # Initialize defaults
                cursor.execute('''
                    INSERT OR IGNORE INTO group_settings (chat_id, anti_links, warn_threshold, mute_minutes_default, auto_ban_on_repeat, strikes_reset_on_mute, updated_at)
                    VALUES (?, 0, 3, 10, 1, 1, CURRENT_TIMESTAMP)
                ''', (chat_id,))
                conn.commit()
                return {"chat_id": chat_id, "anti_links": 0, "warn_threshold": 3, "mute_minutes_default": 10, "auto_ban_on_repeat": 1, "strikes_reset_on_mute": 1}
            return {
                "chat_id": row[0],
                "anti_links": int(row[1]),
                "warn_threshold": int(row[2]),
                "mute_minutes_default": int(row[3]),
                "auto_ban_on_repeat": int(row[4]) if row[4] is not None else 1,
                "strikes_reset_on_mute": int(row[5]) if row[5] is not None else 1,
            }

    def set_group_setting(self, chat_id: int, key: str, value) -> None:
        if key not in {"anti_links", "warn_threshold", "mute_minutes_default", "auto_ban_on_repeat", "strikes_reset_on_mute"}:
            raise ValueError("Invalid group setting key")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f'''UPDATE group_settings SET {key} = ?, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?''', (value, chat_id))
            if cursor.rowcount == 0:
                # ensure row exists
                cursor.execute('''
                    INSERT INTO group_settings (chat_id, anti_links, warn_threshold, mute_minutes_default, auto_ban_on_repeat, strikes_reset_on_mute, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (chat_id, 0, 3, 10, 1, 1))
                cursor.execute(f'''UPDATE group_settings SET {key} = ?, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?''', (value, chat_id))
            conn.commit()

    def increment_warning(self, chat_id: int, user_id: int, reason: str | None = None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO warnings (chat_id, user_id, count, last_reason, updated_at)
                VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET count = warnings.count + 1, last_reason = excluded.last_reason, updated_at = CURRENT_TIMESTAMP
            ''', (chat_id, user_id, reason))
            conn.commit()
            cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            row = cursor.fetchone()
            return int(row[0]) if row else 1

    def clear_warnings(self, chat_id: int, user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            conn.commit()

    def get_warnings(self, chat_id: int, user_id: int) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_strikes(self, chat_id: int, user_id: int) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT strikes FROM user_violations WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def add_strike(self, chat_id: int, user_id: int) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO user_violations (chat_id, user_id, strikes, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET strikes = user_violations.strikes + 1, updated_at = CURRENT_TIMESTAMP
            ''', (chat_id, user_id))
            conn.commit()
            cursor.execute('SELECT strikes FROM user_violations WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            row = cursor.fetchone()
            return int(row[0]) if row else 1

    def record_activity(self, chat_id: int, user_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO recent_activity (chat_id, user_id, last_active)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET last_active = CURRENT_TIMESTAMP
            ''', (chat_id, user_id))
            conn.commit()

    def get_active_users(self, chat_id: int, within_minutes: int = 60) -> List[int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id FROM recent_activity
                WHERE chat_id = ? AND last_active >= datetime('now', ?)
                ORDER BY last_active DESC
            ''', (chat_id, f'-{within_minutes} minutes'))
            return [r[0] for r in cursor.fetchall()]
