import os
import logging
import sqlite3
import asyncio
import io
import re
from typing import Dict

import httpx
from PIL import Image
import telegram
import telegram.ext as tg_ext
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from database import Database

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

# --- Phase 1 Utilities ---
AFRICA_INFO: Dict[str, Dict[str, str]] = {
    # Country code -> info
    "NG": {"country": "Nigeria", "capital": "Abuja", "timezone": "Africa/Lagos"},
    "GH": {"country": "Ghana", "capital": "Accra", "timezone": "Africa/Accra"},
    "KE": {"country": "Kenya", "capital": "Nairobi", "timezone": "Africa/Nairobi"},
    "ZA": {"country": "South Africa", "capital": "Pretoria (admin)", "timezone": "Africa/Johannesburg"},
    "EG": {"country": "Egypt", "capital": "Cairo", "timezone": "Africa/Cairo"},
    "DZ": {"country": "Algeria", "capital": "Algiers", "timezone": "Africa/Algiers"},
    "MA": {"country": "Morocco", "capital": "Rabat", "timezone": "Africa/Casablanca"},
    "TZ": {"country": "Tanzania", "capital": "Dodoma", "timezone": "Africa/Dar_es_Salaam"},
}

class EnhancedRefContestBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        # Admin IDs for privileged commands (e.g., /broadcast). Comma-separated user IDs.
        admin_ids_env = os.getenv('ADMIN_IDS', '')
        try:
            self.admin_ids = {int(x.strip()) for x in admin_ids_env.split(',') if x.strip()}
        except Exception:
            self.admin_ids = set()
        # Seed DB admins from env on startup (DB is source of truth afterwards)
        try:
            if self.admin_ids:
                db.seed_admins(sorted(list(self.admin_ids)))
        except Exception as e:
            logger.warning(f"Failed to seed admins from ADMIN_IDS: {e}")
        
        if not self.bot_token:
            raise ValueError("BOT_TOKEN not found in environment variables")
        # Log PTB version and module paths to verify runtime dependency
        ptb_ver = getattr(telegram, '__version__', 'unknown')
        logger.info(f"python-telegram-bot version: {ptb_ver}")
        logger.info(f"telegram module file: {getattr(telegram, '__file__', 'unknown')}")
        logger.info(f"telegram.ext module file: {getattr(tg_ext, '__file__', 'unknown')}")
        # Guard: require PTB 20.x
        try:
            major = int(str(ptb_ver).split('.')[0]) if ptb_ver not in (None, 'unknown') else None
        except Exception:
            major = None
        if major is None or major < 20:
            raise RuntimeError(
                f"Incompatible python-telegram-bot version detected: {ptb_ver}. "
                "Please ensure PTB 20.x is installed (requirements.txt pins 20.8)."
            )
        logger.info(f"Enhanced bot initialized with username: {self.bot_username}")
    
    def is_valid_telegram_link(self, link: str) -> bool:
        """Validate if the provided link is a valid Telegram group/channel link."""
        telegram_patterns = [
            r'^https://t\.me/[a-zA-Z0-9_]+$',  # Public groups/channels
            r'^https://t\.me/joinchat/[a-zA-Z0-9_-]+$',  # Private groups via invite link
            r'^https://t\.me/\+[a-zA-Z0-9_-]+$',  # New format private groups
            r'^@[a-zA-Z0-9_]+$'  # Username format
        ]
        
        for pattern in telegram_patterns:
            if re.match(pattern, link.strip()):
                return True
        return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command, referral links, and event joins with group redirect."""
        try:
            user = update.effective_user
            args = context.args
            logger.info(f"Received /start from user {user.id} ({user.first_name})")
            
            # Check if user came via referral link or event join
            referred_by_id = None
            event_id = None
            redirect_to_group = None
            
            if args and len(args) > 0:
                code = args[0]
                logger.info(f"User came via code: {code}")
                
                # Check if it's an event code
                event = db.get_event_by_code(code)
                if event:
                    event_id = event['id']
                    redirect_to_group = event.get('group_link')
                    # Show event join message
                    await self.join_event(update, context, event)
                    return
                
                # Check if it's a referral code with event context
                if '_' in code:
                    parts = code.split('_')
                    if len(parts) == 2:
                        referral_code, event_code = parts
                        referrer = db.get_user_by_referral_code(referral_code)
                        event = db.get_event_by_code(event_code)
                        
                        if referrer and event and referrer['user_id'] != user.id:
                            referred_by_id = referrer['user_id']
                            event_id = event['id']
                            redirect_to_group = event.get('group_link')
                else:
                    # Regular referral code
                    referrer = db.get_user_by_referral_code(code)
                    if referrer and referrer['user_id'] != user.id:
                        referred_by_id = referrer['user_id']
            
            # Check if user already exists
            existing_user = db.get_user(user.id)
            
            if not existing_user:
                # Add new user
                logger.info(f"Adding new user: {user.id}")
                user_referral_code = db.add_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    referred_by=referred_by_id,
                    event_id=event_id
                )
                
                welcome_msg = f"👋 Welcome, {user.first_name}!\n\n"
                
                if referred_by_id:
                    referrer = db.get_user(referred_by_id)
                    welcome_msg += f"✅ You joined through {referrer['first_name']}'s referral link!\n\n"
                
                # If there's a group link to redirect to, show it prominently
                if redirect_to_group:
                    welcome_msg += f"🎪 You've been invited to join a special group!\n"
                    welcome_msg += f"👆 Click the button below to join the group\n\n"
                
                welcome_msg += f"🔗 Your unique referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}\n\n"
                welcome_msg += "Share your link to invite friends and track referrals. 🏆"
                
            else:
                user_referral_code = existing_user['referral_code']
                welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"
                
                # If there's a group link to redirect to, show it prominently
                if redirect_to_group:
                    welcome_msg += f"🎪 You've been invited to join a special group!\n"
                    welcome_msg += f"👆 Click the button below to join the group\n\n"
                
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
            
            # Create inline keyboard
            keyboard = []
            
            # If there's a group to redirect to, add join group button first
            if redirect_to_group:
                # Format the group link properly
                group_link = redirect_to_group
                if group_link.startswith('@'):
                    group_link = f"https://t.me/{group_link[1:]}"
                
                keyboard.append([InlineKeyboardButton("🎪 Join Group", url=group_link)])
                keyboard.append([])  # Empty row for spacing
            
            # Add regular menu buttons
            keyboard.extend([
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("🎯 My Event Links", callback_data="my_event_links")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
            logger.info("Successfully sent welcome message with group redirect")
            
        except Exception as e:
            logger.error(f"Error in start handler: {e}")
            await update.message.reply_text("Welcome! Your bot is working. Please try again.")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks."""
        try:
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            logger.info(f"Button pressed: {query.data} by user {user_id}")
            
            if query.data == "stats":
                await self.show_stats(query, user_id)
            elif query.data == "leaderboard":
                await self.show_leaderboard(query)
            elif query.data == "my_events":
                await self.show_my_events(query, user_id)
            elif query.data == "create_event":
                await self.start_create_event(query, context)
            elif query.data == "help":
                await self.show_help(query)
            elif query.data == "back_to_menu":
                await self.back_to_menu(update, context)
            elif query.data.startswith("event_"):
                event_id = int(query.data.split('_')[1])
                await self.show_event_stats(query, event_id)
            elif query.data.startswith("event_link_"):
                event_id = int(query.data.split('_')[2])
                await self.send_event_link(query, event_id)
            elif query.data == "my_event_links":
                await self.show_my_event_links(query, user_id)
            elif query.data.startswith("join_event_"):
                event_id = int(query.data.split('_')[2])
                await self.join_event_confirm(query, event_id)
            elif query.data.startswith("set_group_"):
                event_id = int(query.data.split('_')[2])
                await self.start_set_group_link(query, context, event_id)
            elif query.data == "skip_group_link":
                await self.skip_group_link(query, context)
                
        except Exception as e:
            logger.error(f"Error in button handler: {e}")
            try:
                await query.edit_message_text("An error occurred. Please try again or use /start.")
            except:
                pass

    # ===== Phase 1 Features =====
    async def join_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            db.set_opt_in(user.id, True)
            await update.message.reply_text("You are now subscribed to updates. Send /stop to unsubscribe.")
        except Exception as e:
            logger.error(f"/join error: {e}")
            await update.message.reply_text("Failed to subscribe. Please try again later.")

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            db.set_opt_in(user.id, False)
            await update.message.reply_text("You have unsubscribed. Send /join to subscribe again.")
        except Exception as e:
            logger.error(f"/stop error: {e}")
            await update.message.reply_text("Failed to unsubscribe. Please try again later.")

    async def tz_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /tz <AFRICA_COUNTRY_CODE> e.g. /tz NG")
            return
        code = args[0].upper()
        info = AFRICA_INFO.get(code)
        if not info:
            await update.message.reply_text("Unknown or unsupported country code. Try NG, GH, KE, ZA, EG, DZ, MA, TZ")
            return
        await update.message.reply_text(f"{info['country']} time zone: {info['timezone']}")

    async def capital_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /capital <AFRICA_COUNTRY_CODE> e.g. /capital GH")
            return
        code = args[0].upper()
        info = AFRICA_INFO.get(code)
        if not info:
            await update.message.reply_text("Unknown or unsupported country code. Try NG, GH, KE, ZA, EG, DZ, MA, TZ")
            return
        await update.message.reply_text(f"Capital of {info['country']}: {info['capital']}")

    async def weather_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /weather <city or country>")
            return
        query = " ".join(context.args)
        try:
            # Geocode
            async with httpx.AsyncClient(timeout=10) as client:
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": query, "count": 1}
                )
                if geo.status_code != 200:
                    await update.message.reply_text("Geocoding failed. Try a different location.")
                    return
                data = geo.json()
                results = data.get("results") or []
                if not results:
                    await update.message.reply_text("Location not found. Try a more specific name.")
                    return
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                loc_name = results[0].get("name")

                # Weather
                w = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={"latitude": lat, "longitude": lon, "current_weather": True}
                )
                if w.status_code != 200:
                    await update.message.reply_text("Weather fetch failed. Please try later.")
                    return
                wj = w.json()
                cw = wj.get("current_weather", {})
                temp = cw.get("temperature")
                wind = cw.get("windspeed")
                await update.message.reply_text(
                    f"Weather for {loc_name}:\nTemperature: {temp}°C\nWind: {wind} km/h")
        except Exception as e:
            logger.error(f"/weather error: {e}")
            await update.message.reply_text("Error fetching weather.")

    def _stylize_variants(self, text: str) -> Dict[str, str]:
        bold = "".join(chr(0x1D5D4 + (ord(c) - 97)) if 'a' <= c <= 'z' else
                         chr(0x1D5A0 + (ord(c) - 65)) if 'A' <= c <= 'Z' else c for c in text)
        monospace = "".join(chr(0x1D68A + (ord(c) - 97)) if 'a' <= c <= 'z' else
                             chr(0x1D670 + (ord(c) - 65)) if 'A' <= c <= 'Z' else c for c in text)
        smallcaps_map = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ғ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ","m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"s","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}
        smallcaps = "".join(smallcaps_map.get(c, smallcaps_map.get(c.lower(), c)) for c in text)
        return {"Bold": bold, "Monospace": monospace, "SmallCaps": smallcaps}

    async def fancy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        variants = self._stylize_variants(' '.join(context.args))
        if not variants:
            await update.effective_chat.send_message("Usage: /fancy <text>")
            return
        text = "\n".join([f"• {name}: {val}" for name, val in variants.items()])
        await update.effective_chat.send_message(text)

    async def photo_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Convert a received photo to a sticker and send as a reply (private chats only)."""
        try:
            chat = update.effective_chat
            if chat.type != "private":
                return
  # only in private chat to avoid spam
            photos = update.message.photo
            if not photos:
                return
            best = photos[-1]
            file = await context.bot.get_file(best.file_id)
            b = await file.download_as_bytearray()
            img = Image.open(io.BytesIO(b)).convert("RGBA")
            # Resize to fit sticker constraints (max 512px)
            max_size = 512
            img.thumbnail((max_size, max_size))
            out = io.BytesIO()
            img.save(out, format="WEBP")
            out.seek(0)
            await context.bot.send_sticker(chat_id=chat.id, sticker=out)
        except Exception as e:
            logger.error(f"Error converting photo to sticker: {e}")
            try:
                await update.effective_chat.send_message("Sorry, I couldn't convert that image to a sticker.")
            except Exception:
                pass

    # ====== Admin: Broadcasts ======
    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin-only: broadcast a text message to all opted-in users (/join). Usage: /broadcast <message>"""
        try:
            user = update.effective_user
            if not user:
                return
            # Authorization: check DB admins primarily; allow ENV-seeded admins as fallback
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.effective_chat.send_message("You are not authorized to use this command.")
                return

            message_text = ' '.join(context.args).strip()
            if not message_text:
                await update.effective_chat.send_message("Usage: /broadcast <message>")
                return

            target_ids = db.get_opted_in_users()
            if not target_ids:
                await update.effective_chat.send_message("No users have opted in to receive broadcasts.")
                return

            await update.effective_chat.send_message(f"Broadcasting to {len(target_ids)} users... This may take a moment.")

            sent = 0
            failed = 0
            # Send in small batches to avoid hitting flood limits
            batch_size = 25
            for i in range(0, len(target_ids), batch_size):
                batch = target_ids[i:i+batch_size]
                tasks = []
                for uid in batch:
                    tasks.append(context.bot.send_message(chat_id=uid, text=message_text))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        failed += 1
                    else:
                        sent += 1
                # brief pause between batches
                await asyncio.sleep(0.5)

            await update.effective_chat.send_message(f"✅ Broadcast complete. Sent: {sent}, Failed: {failed}.")
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            try:
                await update.effective_chat.send_message("An error occurred while broadcasting.")
            except Exception:
                pass

    # ====== Admins management (in-bot) ======
    async def myid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            await update.effective_chat.send_message(f"Your Telegram user ID: {user.id}")
        except Exception as e:
            logger.error(f"/myid error: {e}")

    async def admins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            admin_ids = db.list_admins()
            if not admin_ids:
                await update.effective_chat.send_message("No admins configured yet.")
                return
            ids_str = "\n".join([str(uid) for uid in admin_ids])
            await update.effective_chat.send_message(f"Current admins (user IDs):\n{ids_str}")
        except Exception as e:
            logger.error(f"/admins error: {e}")
            await update.effective_chat.send_message("Failed to list admins.")

    async def addadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.effective_chat.send_message("You are not authorized to add admins.")
                return
            if not context.args:
                await update.effective_chat.send_message("Usage: /addadmin <user_id>")
                return
            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.effective_chat.send_message("Invalid user_id. It must be a number.")
                return
            if db.is_admin(target_id):
                await update.effective_chat.send_message("User is already an admin.")
                return
            db.add_admin(target_id, added_by=user.id)
            await update.effective_chat.send_message(f"✅ Added admin: {target_id}")
        except Exception as e:
            logger.error(f"/addadmin error: {e}")
            await update.effective_chat.send_message("Failed to add admin.")

    async def rmadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.effective_chat.send_message("You are not authorized to remove admins.")
                return
            if not context.args:
                await update.effective_chat.send_message("Usage: /rmadmin <user_id>")
                return
            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.effective_chat.send_message("Invalid user_id. It must be a number.")
                return
            if not db.is_admin(target_id):
                await update.effective_chat.send_message("User is not an admin.")
                return
            db.remove_admin(target_id)
            await update.effective_chat.send_message(f"✅ Removed admin: {target_id}")
        except Exception as e:
            logger.error(f"/rmadmin error: {e}")
            await update.effective_chat.send_message("Failed to remove admin.")
    async def show_my_events(self, query, user_id: int):
        """Show user's hosted events with group link management."""
        try:
            events = db.get_user_events(user_id)
            
            events_msg = "🎪 Your Hosted Events\n\n"
            
            if events:
                keyboard = []
                for event in events[:10]:  # Show max 10 events
                    events_msg += f"📅 {event['title']}\n"
                    events_msg += f"🔗 Code: {event['event_code']}\n"
                    if event['description']:
                        events_msg += f"📝 {event['description'][:50]}{'...' if len(event['description']) > 50 else ''}\n"
                    
                    # Show group link status
                    if event.get('group_link'):
                        events_msg += f"🎪 Group: {event['group_link'][:30]}{'...' if len(event['group_link']) > 30 else ''}\n"
                        events_msg += f"📱 Referral links redirect to your group!\n"
                    else:
                        events_msg += f"⚠️ No group link set\n"
                    
                    events_msg += f"📱 Join link: https://t.me/{self.bot_username}?start={event['event_code']}\n\n"
                    
                    # Add buttons for event management
                    keyboard.append([InlineKeyboardButton(f"📊 {event['title']} Stats", callback_data=f"event_{event['id']}")])
                    if not event.get('group_link'):
                        keyboard.append([InlineKeyboardButton(f"🎪 Set Group Link", callback_data=f"set_group_{event['id']}")])
                
                # Back button
                keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
            else:
                events_msg += "No events created yet. Create your first event to start hosting referral programs!"
                keyboard = [
                    [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(events_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_my_events: {e}")
            await query.edit_message_text("Error loading events. Please try again.")
    
    async def start_create_event(self, query, context):
        """Start the event creation process."""
        try:
            # Store user state for event creation
            context.user_data['creating_event'] = True
            context.user_data['event_step'] = 'title'
            
            await query.edit_message_text(
                "🎪 Create New Event\n\n"
                "Let's create your referral event!\n\n"
                "Please enter the event title:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]])
            )
            
        except Exception as e:
            logger.error(f"Error in start_create_event: {e}")
            await query.edit_message_text("Error starting event creation. Please try again.")
    
    async def start_set_group_link(self, query, context, event_id: int):
        """Start the group link setting process."""
        try:
            # Store user state for group link setting
            context.user_data['setting_group_link'] = True
            context.user_data['target_event_id'] = event_id
            
            await query.edit_message_text(
                "🎪 Set Group Link\n\n"
                "Enter the Telegram group or channel link where you want referrals to be directed.\n\n"
                "Supported formats:\n"
                "• https://t.me/your_group\n"
                "• https://t.me/joinchat/invite_link\n"
                "• https://t.me/+invite_link\n"
                "• @your_group_username\n\n"
                "Please enter the group link:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ Skip", callback_data="skip_group_link")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="my_events")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Error in start_set_group_link: {e}")
            await query.edit_message_text("Error starting group link setup. Please try again.")
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages for event creation and group link setting."""
        try:
            user_data = context.user_data
            
            if user_data.get('creating_event'):
                if user_data.get('event_step') == 'title':
                    await self.create_event_title(update, context)
                elif user_data.get('event_step') == 'description':
                    await self.create_event_description(update, context)
                elif user_data.get('event_step') == 'group_link':
                    await self.create_event_group_link(update, context)
            elif user_data.get('setting_group_link'):
                await self.set_group_link(update, context)
            
        except Exception as e:
            logger.error(f"Error in handle_text_message: {e}")
    
    async def create_event_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event title input."""
        try:
            title = update.message.text.strip()
            
            if len(title) > 100:
                await update.message.reply_text("❌ Event title is too long. Please keep it under 100 characters.")
                return
            
            context.user_data['event_title'] = title
            context.user_data['event_step'] = 'description'
            
            await update.message.reply_text(
                f"✅ Event title: {title}\n\n"
                "Now enter a description for your event (or send 'skip' to skip):"
            )
            
        except Exception as e:
            logger.error(f"Error in create_event_title: {e}")
            await update.message.reply_text("Error processing title. Please try again.")
    
    async def create_event_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event description input."""
        try:
            description = None
            
            if update.message.text.strip().lower() != 'skip':
                description = update.message.text.strip()
                if len(description) > 500:
                    await update.message.reply_text("❌ Description is too long. Please keep it under 500 characters.")
                    return
            
            context.user_data['event_description'] = description
            context.user_data['event_step'] = 'group_link'
            
            await update.message.reply_text(
                "📝 Description saved!\n\n"
                "🎪 Now, would you like to set a group link?\n\n"
                "If you set a group link, when people use referral links for this event, "
                "they'll be directed to join your group automatically!\n\n"
                "Enter a Telegram group/channel link, or send 'skip' to create the event without a group link:\n\n"
                "Supported formats:\n"
                "• https://t.me/your_group\n"
                "• https://t.me/joinchat/invite_link\n"
                "• @your_group_username"
            )
            
        except Exception as e:
            logger.error(f"Error in create_event_description: {e}")
            await update.message.reply_text("Error processing description. Please try again.")
    
    async def create_event_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event group link input during creation."""
        try:
            group_link = None
            
            if update.message.text.strip().lower() != 'skip':
                group_link = update.message.text.strip()
                
                if not self.is_valid_telegram_link(group_link):
                    await update.message.reply_text(
                        "❌ Invalid Telegram link format.\n\n"
                        "Please use one of these formats:\n"
                        "• https://t.me/your_group\n"
                        "• https://t.me/joinchat/invite_link\n"
                        "• https://t.me/+invite_link\n"
                        "• @your_group_username\n\n"
                        "Or send 'skip' to create without a group link."
                    )
                    return
            
            # Create the event
            title = context.user_data['event_title']
            description = context.user_data.get('event_description')
            user_id = update.effective_user.id
            
            event_code = db.create_event(user_id, title, description, group_link)
            
            success_msg = f"🎉 Event Created Successfully!\n\n"
            success_msg += f"📅 Title: {title}\n"
            if description:
                success_msg += f"📝 Description: {description}\n"
            if group_link:
                success_msg += f"🎪 Group Link: {group_link}\n"
                success_msg += f"✨ Referral links will now redirect users to your group!\n"
            success_msg += f"🔗 Event Code: {event_code}\n"
            success_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n\n"
            
            if group_link:
                success_msg += "🚀 How it works:\n"
                success_msg += "• Share your event join link or participant referral links\n"
                success_msg += "• New users will be registered and then directed to your group\n"
                success_msg += "• All referrals are tracked automatically!\n\n"
            
            success_msg += "Share this link for people to join your event directly!"
            
            # Get event ID for stats button
            event = db.get_event_by_code(event_code)
            
            keyboard = [
                [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event['id']}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(success_msg, reply_markup=reply_markup)
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in create_event_group_link: {e}")
            await update.message.reply_text("Error creating event. Please try again.")
    
    async def set_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle setting group link for existing event."""
        try:
            group_link = update.message.text.strip()
            event_id = context.user_data.get('target_event_id')
            
            if not self.is_valid_telegram_link(group_link):
                await update.message.reply_text(
                    "❌ Invalid Telegram link format.\n\n"
                    "Please use one of these formats:\n"
                    "• https://t.me/your_group\n"
                    "• https://t.me/joinchat/invite_link\n"
                    "• https://t.me/+invite_link\n"
                    "• @your_group_username"
                )
                return
            
            # Update the event with the group link
            success = db.update_event_group_link(event_id, group_link)
            
            if success:
                success_msg = f"✅ Group Link Set Successfully!\n\n"
                success_msg += f"🎪 Group: {group_link}\n\n"
                success_msg += "🚀 From now on, when people use referral links for this event, "
                success_msg += "they'll be registered in the bot and then directed to join your group!\n\n"
                success_msg += "All referrals will be tracked automatically."
                
                keyboard = [
                    [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event_id}")],
                    [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(success_msg, reply_markup=reply_markup)
            else:
                await update.message.reply_text("❌ Error setting group link. Please try again.")
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in set_group_link: {e}")
            await update.message.reply_text("Error setting group link. Please try again.")
    
    async def show_stats(self, query, user_id: int):
        """Show user's referral statistics."""
        try:
            user = db.get_user(user_id)
            if not user:
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            stats = db.get_referral_stats(user_id)
            
            stats_msg = f"📊 Your Referral Stats\n\n"
            stats_msg += f"🔗 Your referral code: {user['referral_code']}\n"
            stats_msg += f"📱 Your link: https://t.me/{self.bot_username}?start={user['referral_code']}\n\n"
            stats_msg += f"👥 Total referrals: {stats['total_referrals']}\n\n"
            
            if stats['referred_users']:
                stats_msg += "🎯 Recent referrals:\n"
                for i, referred_user in enumerate(stats['referred_users'][:5], 1):
                    name = referred_user['first_name'] or referred_user['username'] or "Unknown"
                    stats_msg += f"{i}. {name}\n"
                
                if len(stats['referred_users']) > 5:
                    stats_msg += f"... and {len(stats['referred_users']) - 5} more!\n"
            else:
                stats_msg += "🔄 No referrals yet. Share your link to start earning!"
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(stats_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_stats: {e}")
            await query.edit_message_text("Error loading stats. Please try again.")
    
    async def show_leaderboard(self, query):
        """Show contest leaderboard."""
        try:
            leaderboard = db.get_leaderboard(10)
            
            leaderboard_msg = "🏆 Contest Leaderboard\n\n"
            
            if leaderboard:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                
                for i, user in enumerate(leaderboard):
                    if user['referral_count'] > 0:
                        name = user['first_name'] or user['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        leaderboard_msg += f"{medal} {name}: {user['referral_count']} referrals\n"
            else:
                leaderboard_msg += "No participants yet. Be the first to start referring!"
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(leaderboard_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_leaderboard: {e}")
            await query.edit_message_text("Error loading leaderboard. Please try again.")
    
    async def show_help(self, query):
        """Show help information."""
        try:
            help_msg = """ℹ️ About This Bot

This is a multipurpose assistant bot with:
• Utilities: timezone (/tz), capitals (/capital), weather (/weather)
• Text fun: fancy text (/fancy)
• Stickers: send a photo in private chat to get a sticker
• Referral Events: create/join events, track referrals, view leaderboards

Use /start to open the menu and explore features. For referrals, grab your link from the menu and share it. If you host events, you can optionally set a Telegram group so referrals are redirected there. """
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(help_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_help: {e}")
            await query.edit_message_text("Help information temporarily unavailable.")
    
    async def show_event_stats(self, query, event_id: int):
        """Show detailed statistics for an event with group link info."""
        try:
            # Get event info
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
            
            if not event_row:
                await query.edit_message_text("❌ Event not found.")
                return
            
            event_code = event_row[1]  # event_code
            event_title = event_row[2]  # title
            group_link = event_row[4]  # group_link
            
            stats = db.get_event_stats(event_id)
            
            stats_msg = f"📊 Event Statistics: {event_title}\n\n"
            stats_msg += f"🔗 Event Code: {event_code}\n"
            stats_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n"
            
            if group_link:
                stats_msg += f"🎪 Target Group: {group_link}\n"
                stats_msg += f"✨ Referrals redirect to group automatically!\n"
            else:
                stats_msg += f"⚠️ No group link set\n"
            
            stats_msg += f"\n👥 Total Participants: {stats['total_participants']}\n"
            stats_msg += f"🔄 Total Referrals: {stats['total_referrals']}\n\n"

            # If the requester is a participant, show their personal event link and stats
            try:
                user_id = query.from_user.id
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?', (event_id, user_id))
                    is_participant = cursor.fetchone() is not None
                if is_participant:
                    user = db.get_user(user_id)
                    if user and user.get('referral_code'):
                        personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                        per_stats = db.get_user_referrals_in_event(user_id, event_id)
                        stats_msg += f"🎯 Your Event Link:\n{personal_link}\n"
                        stats_msg += f"📈 Your referrals in this event: {per_stats['referral_count']}\n\n"
            except Exception as e:
                logger.warning(f"Could not compute personal event link: {e}")

            if stats['top_referrers']:
                stats_msg += "🏆 Top Referrers:\n"
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                for i, referrer in enumerate(stats['top_referrers'][:5]):
                    if referrer['referral_count'] > 0:
                        name = referrer['first_name'] or referrer['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        stats_msg += f"{medal} {name}: {referrer['referral_count']} referrals\n"
            
            if stats['recent_participants']:
                stats_msg += f"\n👋 Recent Participants:\n"
                for participant in stats['recent_participants'][:5]:
                    name = participant['first_name'] or participant['username'] or "Unknown"
                    stats_msg += f"• {name}\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh Stats", callback_data=f"event_{event_id}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            
            # Add group link management button if no group link is set
            if not group_link:
                keyboard.insert(1, [InlineKeyboardButton("🎪 Set Group Link", callback_data=f"set_group_{event_id}")])

            # If participant, add quick button to get personal event link
            try:
                if is_participant:
                    keyboard.insert(1, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except NameError:
                # is_participant not defined due to earlier failure; ignore
                pass
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(stats_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_event_stats: {e}")
            await query.edit_message_text("Error loading event stats. Please try again.")
    
    async def join_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE, event: dict):
        """Handle event join process with group link redirect."""
        try:
            user = update.effective_user
            
            join_msg = f"🎪 Join Event: {event['title']}\n\n"
            if event['description']:
                join_msg += f"📝 {event['description']}\n\n"
            join_msg += f"👤 Hosted by: {event['host_name']}\n"
            
            if event.get('group_link'):
                join_msg += f"🎪 This event has a group: {event['group_link']}\n"
                join_msg += f"✨ You'll be directed to the group after joining!\n"
            
            join_msg += f"\nWould you like to join this event?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Join Event", callback_data=f"join_event_{event['id']}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(join_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in join_event: {e}")
            await update.message.reply_text("Error processing event join. Please try again.")
    
    async def join_event_confirm(self, query, event_id: int):
        """Confirm event join with group redirect."""
        try:
            user_id = query.from_user.id
            
            # Add user to event participants
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO event_participants (event_id, user_id)
                    VALUES (?, ?)
                ''', (event_id, user_id))
                conn.commit()
                
                # Get event info including group link and event code
                cursor.execute('SELECT title, group_link, event_code FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
                event_title = event_row[0]
                group_link = event_row[1]
                event_code = event_row[2]
            
            success_msg = f"🎉 Successfully joined: {event_title}\n\n"
            success_msg += "You can now participate in this event's referral program!\n"
            success_msg += "Use your personal referral link to invite others to this event."
            
            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            
            # If there's a group link, add join group button
            if group_link:
                success_msg += f"\n\n🎪 Don't forget to join the event group!"
                
                # Format the group link properly
                formatted_link = group_link
                if group_link.startswith('@'):
                    formatted_link = f"https://t.me/{group_link[1:]}"
                
                keyboard.insert(0, [InlineKeyboardButton("🎪 Join Event Group", url=formatted_link)])

            # Show the user's personal event link inline
            user = db.get_user(user_id)
            if user and user.get('referral_code'):
                personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                success_msg += f"\n\n🎯 Your Event Link:\n{personal_link}"
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in join_event_confirm: {e}")
            await query.edit_message_text("Error joining event. Please try again.")

    async def show_my_event_links(self, query, user_id: int):
        """List all events the user participates in and provide personal referral links."""
        try:
            # Fetch events where the user is a participant
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT e.id, e.title, e.event_code, e.group_link
                    FROM events e
                    JOIN event_participants ep ON ep.event_id = e.id
                    WHERE ep.user_id = ?
                    ORDER BY e.created_at DESC
                ''', (user_id,))
                rows = cursor.fetchall()
            
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.edit_message_text("Please use /start to register first.")
                return
            
            if not rows:
                keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
                await query.edit_message_text(
                    "You are not participating in any events yet. Join an event to get your custom link!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            msg = "🎯 Your Event Participation Links\n\n"
            keyboard = []
            for (event_id, title, event_code, group_link) in rows[:10]:
                personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                msg += f"📅 {title}\n"
                msg += f"🔗 Link: {personal_link}\n"
                if group_link:
                    msg += f"🎪 Group: {group_link}\n"
                msg += "\n"
                # Add buttons to get link again or open stats
                keyboard.append([
                    InlineKeyboardButton("🎯 Get Link", callback_data=f"event_link_{event_id}"),
                    InlineKeyboardButton("📊 Event Stats", callback_data=f"event_{event_id}")
                ])
            
            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
            
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        
        except Exception as e:
            logger.error(f"Error in show_my_event_links: {e}")
            await query.edit_message_text("Error loading your event links. Please try again.")

    async def send_event_link(self, query, event_id: int):
        """Send the current user's personal referral link for a specific event."""
        try:
            user_id = query.from_user.id
            # Verify participation
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                if not row:
                    await query.message.reply_text("❌ Event not found.")
                    return
                event_code = row[0]
                cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?', (event_id, user_id))
                if cursor.fetchone() is None:
                    await query.message.reply_text("You need to join this event first to get your link.")
                    return
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.message.reply_text("Please use /start to register first.")
                return
            personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
            await query.message.reply_text(
                f"🎯 Your Event Link:\n{personal_link}\n\nShare this link to invite people directly to this event!"
            )
        except Exception as e:
            logger.error(f"Error in send_event_link: {e}")
            await query.message.reply_text("Error generating your event link. Please try again.")
    
    async def back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back to menu callback."""
        try:
            query = update.callback_query
            await query.answer()
            
            # Clear any ongoing event creation state
            context.user_data.clear()
            
            user = query.from_user
            existing_user = db.get_user(user.id)
            
            if existing_user:
                user_referral_code = existing_user['referral_code']
                welcome_msg = f"👋 Main Menu - {user.first_name}\n\n"
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
                
                # Create inline keyboard with all features
                keyboard = [
                    [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                    [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                    [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                    [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                    [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(welcome_msg, reply_markup=reply_markup)
            else:
                await query.edit_message_text("Please use /start to register first.")
                
        except Exception as e:
            logger.error(f"Error in back_to_menu: {e}")
            await query.edit_message_text("Please use /start to return to the main menu.")
    
    async def group_leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /leaderboard command in groups - only for group admins."""
        try:
            # Check if this is a group chat
            if update.effective_chat.type not in ['group', 'supergroup']:
                await update.message.reply_text("This command only works in groups where I'm an admin.")
                return
            
            # Check if the user is an admin in this group
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            try:
                chat_member = await context.bot.get_chat_member(chat_id, user_id)
                if chat_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text("Only group administrators can request the leaderboard.")
                    return
            except Exception as e:
                logger.error(f"Error checking admin status: {e}")
                await update.message.reply_text("I need admin permissions to check your admin status.")
                return
            
            # Check if bot is admin in the group
            try:
                bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
                if bot_member.status != 'administrator':
                    await update.message.reply_text("I need to be an admin in this group to show the leaderboard.")
                    return
            except Exception as e:
                logger.error(f"Error checking bot admin status: {e}")
                await update.message.reply_text("I need admin permissions in this group.")
                return
            
            # Get leaderboard data
            leaderboard = db.get_leaderboard(10)
            
            leaderboard_msg = f"🏆 Referral Leaderboard\n"
            leaderboard_msg += f"📊 Requested by {update.effective_user.first_name}\n\n"
            
            if leaderboard:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                
                for i, user in enumerate(leaderboard):
                    if user['referral_count'] > 0:
                        name = user['first_name'] or user['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        leaderboard_msg += f"{medal} {name}: {user['referral_count']} referrals\n"
                
                leaderboard_msg += f"\n🚀 Want to join the contest? Start the bot @{self.bot_username} to get your referral link!"
            else:
                leaderboard_msg += "No participants yet. Be the first to start referring!\n\n"
                leaderboard_msg += f"🚀 Start the contest: @{self.bot_username}"
            
            await update.message.reply_text(leaderboard_msg)
            logger.info(f"Group leaderboard requested by admin {user_id} in group {chat_id}")
            
        except Exception as e:
            logger.error(f"Error in group_leaderboard_command: {e}")
            await update.message.reply_text("Error loading leaderboard. Please try again later.")
    
    async def skip_group_link(self, query, context):
        """Handle skipping group link setting for existing event."""
        try:
            event_id = context.user_data.get('target_event_id')
            
            if not event_id:
                await query.edit_message_text("❌ Error: No event selected. Please try again.")
                return
            
            # Get event info for confirmation
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
                
                if not event_row:
                    await query.edit_message_text("❌ Event not found.")
                    return
                
                event_title = event_row[0]
            
            success_msg = f"⏭️ Group Link Skipped\n\n"
            success_msg += f"📅 Event: {event_title}\n\n"
            success_msg += "Your event will continue without a group link. "
            success_msg += "You can always add a group link later from the event management menu.\n\n"
            success_msg += "Referral links will direct users to the bot instead of a specific group."
            
            keyboard = [
                [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event_id}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_msg, reply_markup=reply_markup)
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in skip_group_link: {e}")
            await query.edit_message_text("Error skipping group link. Please try again.")

    async def end_event_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """End an event by its public code. Only the host can end their event.

        Usage:
          /end_event <event_code>
        """
        try:
            user = update.effective_user
            args = context.args
            if not args:
                await update.message.reply_text(
                    "Usage: /end_event <event_code>\nYou can find the event code in your event details.")
                return

            event_code = args[0].strip()

            # Look up event by code
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, title, host_id, is_active FROM events WHERE event_code = ?",
                    (event_code,)
                )
                row = cursor.fetchone()

                if not row:
                    await update.message.reply_text("❌ Event not found. Check the event code and try again.")
                    return

                event_id, title, host_id, is_active = row

                if host_id != user.id:
                    await update.message.reply_text("❌ Only the event host can end this event.")
                    return

                if not is_active:
                    await update.message.reply_text("ℹ️ This event is already ended.")
                    return

                # End the event
                cursor.execute("UPDATE events SET is_active = 0 WHERE id = ?", (event_id,))
                conn.commit()

            # Fetch final stats for confirmation
            stats = db.get_event_stats(event_id)
            msg = [
                "✅ Event Ended",
                f"📅 Event: {title}",
                "",
                f"👥 Total participants: {stats.get('total_participants', 0)}",
                f"🔗 Total referrals: {stats.get('total_referrals', 0)}",
            ]
            await update.message.reply_text("\n".join(msg))
            logger.info(f"Event {event_id} ({event_code}) ended by host {user.id}")

        except Exception as e:
            logger.error(f"Error in end_event_command: {e}")
            await update.message.reply_text("Error ending event. Please try again later.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and send a friendly message to the user (if possible)."""
        try:
            logger.exception("Unhandled exception while handling update: %s", update, exc_info=context.error)
            # Try to inform the user if we can
            if isinstance(update, Update):
                # Prefer replying to where it happened
                if update.effective_chat and update.effective_chat.id:
                    try:
                        await context.bot.send_message(chat_id=update.effective_chat.id,
                                                       text="⚠️ Oops! An error occurred. Please try again.")
                    except Exception:
                        pass
        except Exception:
            # Never let error handler raise
            pass
    
    def run(self):
        """Start the bot with improved error handling."""
        try:
            # Define post_init hook to ensure no webhook is set (avoids getUpdates conflict)
            async def _post_init(app: Application):
                try:
                    await app.bot.delete_webhook(drop_pending_updates=True)
                    logger.info("Webhook deleted (if existed) before starting.")
                except Exception as e:
                    logger.warning(f"Failed to delete webhook (may be unset already): {e}")

            # Create application with updated configuration and post-init hook
            application = (
                Application.builder()
                .token(self.bot_token)
                .post_init(_post_init)
                .build()
            )
            
            # Add handlers
            application.add_handler(CommandHandler("start", self.start))
            application.add_handler(CommandHandler("leaderboard", self.group_leaderboard_command))
            application.add_handler(CommandHandler("end_event", self.end_event_command))
            application.add_handler(CommandHandler("join", self.join_command))
            application.add_handler(CommandHandler("stop", self.stop_command))
            application.add_handler(CommandHandler("tz", self.tz_command))
            application.add_handler(CommandHandler("capital", self.capital_command))
            application.add_handler(CommandHandler("weather", self.weather_command))
            application.add_handler(CommandHandler("fancy", self.fancy_command))
            application.add_handler(CommandHandler("broadcast", self.broadcast_command))
            application.add_handler(CommandHandler("myid", self.myid_command))
            application.add_handler(CommandHandler("admins", self.admins_command))
            application.add_handler(CommandHandler("addadmin", self.addadmin_command))
            application.add_handler(CommandHandler("rmadmin", self.rmadmin_command))
            application.add_handler(CallbackQueryHandler(self.button_handler))
            application.add_handler(MessageHandler(filters.PHOTO, self.photo_to_sticker))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
            application.add_error_handler(self.error_handler)
            
            logger.info("🚀 Enhanced multipurpose bot is starting...")

            # Decide between webhook (Render Web) and polling (local/dev)
            port = os.getenv("PORT")
            webhook_url = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")

            if port:
                # Webhook mode for Render Web service
                if not webhook_url:
                    logger.warning("PORT is set but WEBHOOK_URL/RENDER_EXTERNAL_URL not found. Falling back to polling.")
                    application.run_polling()
                else:
                    logger.info(f"Starting in WEBHOOK mode on port {port}, url: {webhook_url}")
                    application.run_webhook(
                        listen="0.0.0.0",
                        port=int(port),
                        webhook_url=webhook_url,
                        drop_pending_updates=True,
                    )
            else:
                # Polling mode
                logger.info("Starting in POLLING mode")
                application.run_polling()
            
        except Exception as e:
            logger.error(f"Critical error starting bot: {e}")
            raise

if __name__ == "__main__":
    try:
        bot = EnhancedRefContestBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your BOT_TOKEN and BOT_USERNAME")
        print("2. Installed the required dependencies: pip install -r requirements.txt")
