import os
import logging
import sqlite3
import asyncio
import io
import re
from typing import Dict, List

import httpx
from PIL import Image
import telegram
import telegram.ext as tg_ext
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from database import Database

# Load env
load_dotenv()

logger = logging.getLogger("multipurpose_bot")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

db = Database()

AFRICA_INFO: Dict[str, Dict[str, str]] = {
    "NG": {"country": "Nigeria", "capital": "Abuja", "timezone": "Africa/Lagos"},
    "GH": {"country": "Ghana", "capital": "Accra", "timezone": "Africa/Accra"},
    "KE": {"country": "Kenya", "capital": "Nairobi", "timezone": "Africa/Nairobi"},
    "ZA": {"country": "South Africa", "capital": "Pretoria (admin)", "timezone": "Africa/Johannesburg"},
    "EG": {"country": "Egypt", "capital": "Cairo", "timezone": "Africa/Cairo"},
    "DZ": {"country": "Algeria", "capital": "Algiers", "timezone": "Africa/Algiers"},
    "MA": {"country": "Morocco", "capital": "Rabat", "timezone": "Africa/Casablanca"},
    "TZ": {"country": "Tanzania", "capital": "Dodoma", "timezone": "Africa/Dar_es_Salaam"},
}


class MultipurposeBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        if not self.bot_token or not self.bot_username:
            raise RuntimeError("BOT_TOKEN and BOT_USERNAME must be set in environment")

        # Seed admins from env (comma-separated) once; DB is source of truth after
        admin_ids_env = os.getenv('ADMIN_IDS', '')
        try:
            self.admin_ids = {int(x.strip()) for x in admin_ids_env.split(',') if x.strip()}
        except Exception:
            self.admin_ids = set()
        if self.admin_ids:
            try:
                db.seed_admins(sorted(list(self.admin_ids)))
            except Exception as e:
                logger.warning(f"Admin seed failed: {e}")

        # Log PTB version
        try:
            import telegram
            ptb_ver = getattr(telegram, '__version__', 'unknown')
        except Exception:
            ptb_ver = 'unknown'
        logger.info(f"python-telegram-bot version: {ptb_ver}")
        logger.info(f"telegram.ext module file: {getattr(tg_ext, '__file__', 'unknown')}")

    # ========== Core run ==========
    def run(self):
        async def _post_init(app: Application):
            try:
                await app.bot.delete_webhook(drop_pending_updates=True)
                logger.info("Webhook deleted (if existed)")
            except Exception as e:
                logger.warning(f"delete_webhook failed: {e}")

        application = (
            Application.builder()
            .token(self.bot_token)
            .post_init(_post_init)
            .concurrent_updates(True)
            .build()
        )

        # Commands
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.help_command))
        application.add_handler(CommandHandler('tz', self.tz_command))
        application.add_handler(CommandHandler('capital', self.capital_command))
        application.add_handler(CommandHandler('weather', self.weather_command))
        application.add_handler(CommandHandler('fancy', self.fancy_command))
        application.add_handler(CommandHandler('join', self.join_command))
        application.add_handler(CommandHandler('stop', self.stop_command))
        application.add_handler(CommandHandler('broadcast', self.broadcast_command))
        application.add_handler(CommandHandler('myid', self.myid_command))
        application.add_handler(CommandHandler('admins', self.admins_command))
        application.add_handler(CommandHandler('addadmin', self.addadmin_command))
        application.add_handler(CommandHandler('rmadmin', self.rmadmin_command))
        application.add_handler(CommandHandler('end_event', self.end_event_command))
        # Group leaderboard
        application.add_handler(CommandHandler('leaderboard', self.group_leaderboard_command))

        # Callbacks and messages
        application.add_handler(CallbackQueryHandler(self.button_handler))
        application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, self.photo_to_sticker))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        application.add_error_handler(self.error_handler)

        logger.info("🚀 Multipurpose bot is starting...")

        port = os.getenv("PORT")
        webhook_url = os.getenv("WEBHOOK_URL")
        if port and webhook_url:
            logger.info(f"Starting in WEBHOOK mode on port {port}, url: {webhook_url}")
            application.run_webhook(
                listen="0.0.0.0",
                port=int(port),
                webhook_url=webhook_url,
                drop_pending_updates=True,
            )
        else:
            logger.info("Starting in POLLING mode")
            application.run_polling()

    # ========== Menus ==========
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            args = context.args
            referred_by_id = None
            event_id = None
            redirect_to_group = None

            # Parse referral code or event code
            if args:
                code = args[0]
                # event personal code: REF8_EVENTCODE or EVENTCODE
                if '_' in code:
                    left, right = code.split('_', 1)
                    # left could be referral code
                    ref_user = db.get_user_by_referral_code(left)
                    if ref_user:
                        referred_by_id = ref_user['user_id']
                    code = right
                # Now treat code as event_code or user referral code
                # event_code lookup
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT id, group_link FROM events WHERE event_code = ? AND is_active = 1', (code,))
                    row = cursor.fetchone()
                    if row:
                        event_id = row[0]
                        redirect_to_group = row[1]
                # else: pure user referral code handled by add_user below

            # Register/ensure user exists
            existing_user = db.get_user(user.id)
            if not existing_user:
                referral_by = referred_by_id
                user_referral_code = db.add_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    referred_by=referral_by,
                    event_id=event_id,
                )
                welcome_msg = f"👋 Welcome, {user.first_name}!\n\n"
                if referred_by_id:
                    referrer = db.get_user(referred_by_id)
                    if referrer:
                        welcome_msg += f"✅ You joined via {referrer['first_name']}'s referral link!\n\n"
                if redirect_to_group:
                    welcome_msg += "🎪 You've been invited to join a group. Use the button below to join.\n\n"
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your link: https://t.me/{self.bot_username}?start={user_referral_code}\n\n"
                welcome_msg += "Share your link to invite friends and track referrals. 🏆"
            else:
                welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"

            keyboard: List[List[InlineKeyboardButton]] = []

            # Utilities section
            keyboard.append([InlineKeyboardButton("🛠️ Utilities", callback_data="noop")])
            keyboard.append([
                InlineKeyboardButton("🕒 Timezone (/tz)", callback_data="help_tz"),
                InlineKeyboardButton("🌦️ Weather", callback_data="help_weather")
            ])
            keyboard.append([
                InlineKeyboardButton("🏙️ Capital", callback_data="help_capital"),
                InlineKeyboardButton("✨ Fancy Text", callback_data="help_fancy")
            ])

            # Stickers
            keyboard.append([InlineKeyboardButton("🩷 Stickers: send me a photo in private", callback_data="noop")])

            # Referral events section
            keyboard.append([InlineKeyboardButton("🎯 Referral Events", callback_data="noop")])
            keyboard.extend([
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("🎯 My Event Links", callback_data="my_event_links")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
            ])

            # Admin section if authorized
            if db.is_admin(user.id) or user.id in self.admin_ids:
                keyboard.append([InlineKeyboardButton("🛡️ Admin", callback_data="noop")])
                keyboard.append([InlineKeyboardButton("📣 Broadcast (/broadcast)", callback_data="help_broadcast")])
                keyboard.append([InlineKeyboardButton("👑 Admins (/admins)", callback_data="help_admins")])

            keyboard.append([InlineKeyboardButton("ℹ️ Help", callback_data="help")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            if redirect_to_group:
                # also show a join group button at top
                join_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎪 Join Group", url=redirect_to_group)]])
                await update.message.reply_text(welcome_msg, reply_markup=join_kb)
                await update.message.reply_text("Main Menu:", reply_markup=reply_markup)
            else:
                await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"/start error: {e}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "ℹ️ About This Bot\n\n"
            "This is a multipurpose assistant bot with:\n"
            "• Utilities: timezone (/tz), capitals (/capital), weather (/weather)\n"
            "• Text fun: fancy text (/fancy)\n"
            "• Stickers: send a photo in private chat to get a sticker\n"
            "• Referral Events: create/join events, track referrals, view leaderboards\n\n"
            "Use /start to open the menu and explore features. For referrals, grab your link from the menu and share it. If you host events, you can optionally set a Telegram group so referrals are redirected there."
        )
        await update.message.reply_text(msg)

    # ========== Utilities ==========
    async def tz_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Usage: /tz <AFRICA_COUNTRY_CODE> e.g. /tz NG")

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
            async with httpx.AsyncClient(timeout=10) as client:
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": query, "count": 1}
                )
                if geo.status_code != 200:
                    await update.message.reply_text("Failed to geocode. Try a different place.")
                    return
                g = geo.json()
                if not g.get('results'):
                    await update.message.reply_text("Location not found.")
                    return
                lat = g['results'][0]['latitude']
                lon = g['results'][0]['longitude']
                wx = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={"latitude": lat, "longitude": lon, "current_weather": True}
                )
                if wx.status_code != 200:
                    await update.message.reply_text("Weather API error.")
                    return
                w = wx.json().get('current_weather') or {}
                await update.message.reply_text(
                    f"Weather for {query}: {w.get('temperature', '?')}°C, wind {w.get('windspeed', '?')} km/h"
                )
        except Exception as e:
            logger.error(f"/weather error: {e}")
            await update.message.reply_text("Error fetching weather.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Global error handler: log exception and try to notify the user gracefully."""
        try:
            logger.exception("Unhandled exception while handling update: %s", update, exc_info=context.error)
            if isinstance(update, Update) and update.effective_chat:
                try:
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="⚠️ Oops! An error occurred. Please try again.")
                except Exception:
                    pass
        except Exception:
            # Never raise from error handler
            pass

    async def fancy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /fancy <text>")
            return
        text = " ".join(context.args)
        styled = ''.join(ch + '͟' for ch in text)  # simple underline style
        await update.message.reply_text(styled)

    # ========== Opt-in / Broadcast ==========
    async def join_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db.set_opt_in(user.id, True)
        await update.message.reply_text("You're subscribed to broadcasts. Use /stop to unsubscribe.")

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        db.set_opt_in(user.id, False)
        await update.message.reply_text("You have unsubscribed. Send /join to subscribe again.")

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.message.reply_text("You are not authorized to use this command.")
                return
            message_text = ' '.join(context.args).strip()
            if not message_text:
                await update.message.reply_text("Usage: /broadcast <message>")
                return
            recipients = db.get_opted_in_users()
            sent = 0
            failed = 0
            for i in range(0, len(recipients), 25):
                batch = recipients[i:i+25]
                tasks = [context.bot.send_message(chat_id=uid, text=message_text) for uid in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        failed += 1
                    else:
                        sent += 1
                await asyncio.sleep(0.5)
            await update.message.reply_text(f"✅ Broadcast complete. Sent: {sent}, Failed: {failed}.")
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            try:
                await update.message.reply_text("An error occurred while broadcasting.")
            except Exception:
                pass

    # ========== Admin management ==========
    async def myid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Your Telegram user ID: {update.effective_user.id}")

    async def admins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            admin_ids = db.list_admins()
            if not admin_ids:
                await update.message.reply_text("No admins configured yet.")
                return
            ids_str = "\n".join([str(uid) for uid in admin_ids])
            await update.message.reply_text(f"Current admins (user IDs):\n{ids_str}")
        except Exception as e:
            logger.error(f"/admins error: {e}")
            await update.message.reply_text("Failed to list admins.")

    async def addadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.message.reply_text("You are not authorized to add admins.")
                return
            if not context.args:
                await update.message.reply_text("Usage: /addadmin <user_id>")
                return
            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Invalid user_id. It must be a number.")
                return
            if db.is_admin(target_id):
                await update.message.reply_text("User is already an admin.")
                return
            db.add_admin(target_id, added_by=user.id)
            await update.message.reply_text(f"✅ Added admin: {target_id}")
        except Exception as e:
            logger.error(f"/addadmin error: {e}")
            await update.message.reply_text("Failed to add admin.")

    async def rmadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            if not db.is_admin(user.id) and user.id not in self.admin_ids:
                await update.message.reply_text("You are not authorized to remove admins.")
                return
            if not context.args:
                await update.message.reply_text("Usage: /rmadmin <user_id>")
                return
            try:
                target_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("Invalid user_id. It must be a number.")
                return
            if not db.is_admin(target_id):
                await update.message.reply_text("User is not an admin.")
                return
            db.remove_admin(target_id)
            await update.message.reply_text(f"✅ Removed admin: {target_id}")
        except Exception as e:
            logger.error(f"/rmadmin error: {e}")
            await update.message.reply_text("Failed to remove admin.")

    # ========== Stickers ==========
    async def photo_to_sticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat = update.effective_chat
            if chat.type != "private":
                return
            photos = update.message.photo
            if not photos:
                return
            best = photos[-1]
            file = await context.bot.get_file(best.file_id)
            b = await file.download_as_bytearray()
            img = Image.open(io.BytesIO(b)).convert("RGBA")
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

    # ========== Buttons / Flows ==========
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        data = query.data
        try:
            if data == "noop":
                # No operation; keep menu
                await query.answer("Use the menu buttons or /help")
                return
            if data == "stats":
                await self.show_stats(query, user_id)
            elif data == "leaderboard":
                await self.show_leaderboard(query)
            elif data == "my_events":
                await self.show_my_events(query, user_id)
            elif data == "create_event":
                await self.start_create_event(query, context)
            elif data == "help":
                await self.show_help(query)
            elif data == "help_tz":
                await query.edit_message_text("/tz <AFRICA_COUNTRY_CODE> — e.g. /tz NG (Lagos)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="noop")]]))
            elif data == "help_weather":
                await query.edit_message_text("/weather <city or country> — e.g. /weather Nairobi", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="noop")]]))
            elif data == "help_capital":
                await query.edit_message_text("/capital <AFRICA_COUNTRY_CODE> — e.g. /capital GH", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="noop")]]))
            elif data == "help_fancy":
                await query.edit_message_text("/fancy <text> — returns stylized text", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="noop")]]))
            elif data == "my_event_links":
                await self.show_my_event_links(query, user_id)
            elif data.startswith("event_"):
                event_id = int(data.split('_')[1])
                await self.show_event_stats(query, event_id)
            elif data.startswith("event_link_"):
                event_id = int(data.split('_')[2])
                await self.send_event_link(query, event_id)
            elif data.startswith("join_event_"):
                event_id = int(data.split('_')[2])
                await self.join_event_confirm(query, event_id)
            elif data.startswith("set_group_"):
                event_id = int(data.split('_')[2])
                await self.start_set_group_link(query, context, event_id)
            elif data == "skip_group_link":
                await self.skip_group_link(query, context)
        except Exception as e:
            logger.error(f"button_handler error: {e}")
            try:
                await query.edit_message_text("An error occurred. Please try again or use /start.")
            except Exception:
                pass

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            ud = context.user_data
            if ud.get('creating_event'):
                if ud.get('event_step') == 'title':
                    await self.create_event_title(update, context)
                elif ud.get('event_step') == 'description':
                    await self.create_event_description(update, context)
                elif ud.get('event_step') == 'group_link':
                    await self.create_event_group_link(update, context)
            elif ud.get('setting_group_link'):
                await self.set_group_link(update, context)
        except Exception as e:
            logger.error(f"handle_text_message error: {e}")

    # ========== Help (inline) ==========
    async def show_help(self, query):
        help_msg = (
            "ℹ️ About This Bot\n\n"
            "• Utilities: /tz, /capital, /weather\n"
            "• Fancy text: /fancy\n"
            "• Stickers: send a photo to me in private\n"
            "• Referral Events: create events, set group links, track referrals\n"
        )
        await query.edit_message_text(help_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]))

    # ========== Referral: Stats / Leaderboard ==========
    async def show_stats(self, query, user_id: int):
        user = db.get_user(user_id)
        if not user:
            await query.edit_message_text("❌ User not found. Please use /start to register.")
            return
        stats = db.get_referral_stats(user_id)
        msg = (
            "📊 Your Referral Stats\n\n"
            f"🔗 Your referral code: {user['referral_code']}\n"
            f"📱 Your link: https://t.me/{self.bot_username}?start={user['referral_code']}\n\n"
        )
        if stats['total_referrals'] > 0:
            msg += f"✅ Total referrals: {stats['total_referrals']}\n\n"
            for ru in stats['referred_users'][:5]:
                name = ru['first_name'] or ru['username'] or str(ru['user_id'])
                msg += f"• {name} at {ru['joined_at']}\n"
            if len(stats['referred_users']) > 5:
                msg += f"... and {len(stats['referred_users']) - 5} more!\n"
        else:
            msg += "🔄 No referrals yet. Share your link to start!"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]))

    async def show_leaderboard(self, query):
        lb = db.get_leaderboard(10)
        msg = "🏆 Referral Leaderboard\n\n"
        if lb:
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
            for i, u in enumerate(lb):
                if u['referral_count'] > 0:
                    name = u['first_name'] or u['username'] or "Unknown"
                    medal = medals[i] if i < len(medals) else "🏅"
                    msg += f"{medal} {name}: {u['referral_count']} referrals\n"
        else:
            msg += "No participants yet. Be the first to start referring!"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]))

    # Group admin /leaderboard
    async def group_leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command only works in groups where I'm an admin.")
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_ids = {a.user.id for a in admins}
            if user_id not in admin_ids:
                await update.message.reply_text("Only group admins can request the leaderboard.")
                return
            lb = db.get_leaderboard(10)
            msg = "🏆 Referral Leaderboard\n\n"
            if lb:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                for i, u in enumerate(lb):
                    name = u['first_name'] or u['username'] or "Unknown"
                    medal = medals[i] if i < len(medals) else "🏅"
                    msg += f"{medal} {name}: {u['referral_count']} referrals\n"
            else:
                msg += "No participants yet. Be the first to start referring!\n\n"
                msg += f"🚀 Start here: @{self.bot_username}"
            await update.message.reply_text(msg)
        except Exception as e:
            logger.error(f"group_leaderboard error: {e}")
            await update.message.reply_text("Error loading leaderboard.")

    # ========== Events ==========
    async def show_my_events(self, query, user_id: int):
        try:
            events = db.get_user_events(user_id)
            msg = "🎪 Your Hosted Events\n\n"
            kb: List[List[InlineKeyboardButton]] = []
            if events:
                for ev in events[:10]:
                    msg += f"📅 {ev['title']}\n"
                    msg += f"🔗 Code: {ev['event_code']}\n"
                    if ev.get('description'):
                        d = ev['description']
                        msg += f"📝 {d[:50]}{'...' if len(d) > 50 else ''}\n"
                    if ev.get('group_link'):
                        gl = ev['group_link']
                        msg += f"🎪 Group: {gl[:30]}{'...' if len(gl) > 30 else ''}\n"
                        msg += "📱 Referral links redirect to your group!\n"
                    else:
                        msg += "⚠️ No group link set\n"
                    msg += f"📱 Join link: https://t.me/{self.bot_username}?start={ev['event_code']}\n\n"
                    kb.append([InlineKeyboardButton(f"📊 {ev['title']} Stats", callback_data=f"event_{ev['id']}")])
                    if not ev.get('group_link'):
                        kb.append([InlineKeyboardButton("🎪 Set Group Link", callback_data=f"set_group_{ev['id']}")])
                kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")])
            else:
                msg += "No events created yet. Create your first event to start hosting referral programs!"
                kb = [[InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                      [InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_my_events error: {e}")
            await query.edit_message_text("Error loading events. Please try again.")

    async def start_create_event(self, query, context):
        try:
            context.user_data['creating_event'] = True
            context.user_data['event_step'] = 'title'
            await query.edit_message_text(
                "🎪 Create New Event\n\n"
                "Let's create your referral event!\n\n"
                "Please enter the event title:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="noop")]])
            )
        except Exception as e:
            logger.error(f"start_create_event error: {e}")
            await query.edit_message_text("Error starting event creation. Please try again.")

    async def create_event_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            title = update.message.text.strip()
            if len(title) > 100:
                await update.message.reply_text("❌ Event title too long (max 100).")
                return
            context.user_data['event_title'] = title
            context.user_data['event_step'] = 'description'
            await update.message.reply_text(
                f"✅ Event title: {title}\n\nNow enter a description (or send 'skip' to skip):"
            )
        except Exception as e:
            logger.error(f"create_event_title error: {e}")
            await update.message.reply_text("Error processing title. Please try again.")

    async def create_event_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            description = None
            if update.message.text.strip().lower() != 'skip':
                description = update.message.text.strip()
                if len(description) > 500:
                    await update.message.reply_text("❌ Description too long (max 500).")
                    return
            context.user_data['event_description'] = description
            context.user_data['event_step'] = 'group_link'
            await update.message.reply_text(
                "Optionally set a Telegram group/channel link for this event. If set, referrals will be directed there automatically!\n\n"
                "Enter a group link, or send 'skip' to continue without one.\n\n"
                "Supported formats:\n• https://t.me/your_group\n• https://t.me/joinchat/invite_link\n• @your_group_username"
            )
        except Exception as e:
            logger.error(f"create_event_description error: {e}")
            await update.message.reply_text("Error processing description. Please try again.")

    async def create_event_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            group_link = None
            if update.message.text.strip().lower() != 'skip':
                group_link = update.message.text.strip()
                if not self.is_valid_telegram_link(group_link):
                    await update.message.reply_text(
                        "❌ Invalid Telegram link format. Use https://t.me/<group> or @group_username"
                    )
                    return
            title = context.user_data.get('event_title')
            description = context.user_data.get('event_description')
            host_id = update.effective_user.id
            event_code = db.create_event(host_id=host_id, title=title, description=description, group_link=group_link)
            context.user_data.clear()
            # Fetch numeric event_id for callbacks
            event_id = None
            try:
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT id FROM events WHERE event_code = ?', (event_code,))
                    row = cursor.fetchone()
                    if row:
                        event_id = int(row[0])
            except Exception:
                pass
            msg = (
                "✅ Event created!\n\n"
                f"📅 Title: {title}\n"
                f"🔗 Code: {event_code}\n"
            )
            if group_link:
                msg += f"🎪 Group set: {group_link}\n"
            msg += f"\nShare: https://t.me/{self.bot_username}?start={event_code}"
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=(f"event_{event_id}" if event_id else "noop"))],
                  [InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"create_event_group_link error: {e}")
            await update.message.reply_text("Error creating event. Please try again.")

    def is_valid_telegram_link(self, link: str) -> bool:
        if link.startswith("@"):
            return True
        return bool(re.match(r"^https://t.me/[A-Za-z0-9_+/]+$", link))

    async def start_set_group_link(self, query, context, event_id: int):
        try:
            context.user_data['setting_group_link'] = True
            context.user_data['target_event_id'] = event_id
            await query.edit_message_text(
                "🎪 Set Group Link\n\n"
                "Enter the Telegram group or channel link where you want referrals to be directed.\n\n"
                "Supported formats:\n• https://t.me/your_group\n• https://t.me/joinchat/invite_link\n• https://t.me/+invite_link\n• @your_group_username\n\n"
                "Please enter the group link:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ Skip", callback_data="skip_group_link")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="my_events")]
                ])
            )
        except Exception as e:
            logger.error(f"start_set_group_link error: {e}")
            await query.edit_message_text("Error starting group link setup. Please try again.")

    async def set_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            group_link = update.message.text.strip()
            event_id = context.user_data.get('target_event_id')
            if not event_id:
                await update.message.reply_text("❌ Error: No event selected. Please try again.")
                return
            if not self.is_valid_telegram_link(group_link):
                await update.message.reply_text("❌ Invalid Telegram link format.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE events SET group_link = ? WHERE id = ?', (group_link, event_id))
                conn.commit()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                title = row[0] if row else str(event_id)
            msg = (
                "✅ Group link set!\n\n"
                f"📅 Event: {title}\n"
                f"🎪 Group: {group_link}\n"
                "📱 New referrals will be directed to your group."
            )
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=f"event_{event_id}")],
                  [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                  [InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
            context.user_data.clear()
        except Exception as e:
            logger.error(f"set_group_link error: {e}")
            await update.message.reply_text("Error setting group link. Please try again.")

    async def skip_group_link(self, query, context):
        try:
            event_id = context.user_data.get('target_event_id')
            if not event_id:
                await query.edit_message_text("❌ Error: No event selected. Please try again.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                title = row[0] if row else str(event_id)
            msg = (
                "⏭️ Skipped setting a group link. You can add it later from event stats.\n\n"
                f"📅 Event: {title}"
            )
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=f"event_{event_id}")],
                  [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                  [InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
            context.user_data.clear()
        except Exception as e:
            logger.error(f"skip_group_link error: {e}")
            await query.edit_message_text("Error skipping group link. Please try again.")

    async def show_event_stats(self, query, event_id: int):
        try:
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code, title, group_link FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
            if not event_row:
                await query.edit_message_text("❌ Event not found.")
                return
            event_code = event_row[0]
            event_title = event_row[1]
            group_link = event_row[2]

            stats = db.get_event_stats(event_id)
            msg = [
                f"📅 Event: {event_title}",
                f"🔗 Code: {event_code}",
                "",
                f"👥 Total participants: {stats.get('total_participants', 0)}",
                f"🔗 Total referrals: {stats.get('total_referrals', 0)}",
                "",
            ]
            if group_link:
                msg.append(f"🎪 Group: {group_link}")
                msg.append("📱 Referrals will be redirected to this group.")
            if stats.get('top_referrers'):
                msg.append("\n🏆 Top Referrers:")
                for u in stats['top_referrers']:
                    name = u['first_name'] or u['username'] or str(u['user_id'])
                    msg.append(f"• {name}: {u['referral_count']}")
            kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            try:
                # add My Event Link if participant
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ? LIMIT 1', (event_id, query.from_user.id))
                    is_participant = cursor.fetchone() is not None
                if is_participant:
                    kb.insert(0, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except Exception:
                pass
            await query.edit_message_text("\n".join(msg), reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_event_stats error: {e}")
            await query.edit_message_text("Error loading event stats. Please try again.")

    async def show_my_event_links(self, query, user_id: int):
        try:
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
            if not rows:
                await query.edit_message_text("You have not joined any events yet.")
                return
            msg = "🎯 Your Event Links\n\n"
            kb: List[List[InlineKeyboardButton]] = []
            for (ev_id, title, ev_code, gl) in rows[:10]:
                personal = f"https://t.me/{self.bot_username}?start={db.get_user(user_id)['referral_code']}_{ev_code}"
                msg += f"📅 {title}\n"
                msg += f"🔗 Code: {ev_code}\n"
                msg += f"🎯 Your Link: {personal}\n"
                if gl:
                    msg += f"🎪 Group: {gl}\n"
                msg += "\n"
                kb.append([
                    InlineKeyboardButton("🎯 Get Link", callback_data=f"event_link_{ev_id}"),
                    InlineKeyboardButton("📊 Event Stats", callback_data=f"event_{ev_id}")
                ])
            kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")])
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_my_event_links error: {e}")
            await query.edit_message_text("Error loading your event links. Please try again.")

    async def send_event_link(self, query, event_id: int):
        try:
            user_id = query.from_user.id
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
            if not row:
                await query.message.reply_text("❌ Event not found.")
                return
            event_code = row[0]
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.message.reply_text("Please use /start to register first.")
                return
            personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
            await query.message.reply_text(
                f"🎯 Your Event Link:\n{personal_link}\n\nShare this link to invite people directly to this event!"
            )
        except Exception as e:
            logger.error(f"send_event_link error: {e}")
            await query.message.reply_text("Error generating your event link. Please try again.")

    async def join_event_confirm(self, query, event_id: int):
        try:
            user_id = query.from_user.id
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO event_participants (event_id, user_id)
                    VALUES (?, ?)
                ''', (event_id, user_id))
                conn.commit()
                cursor.execute('SELECT title, event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
            if not row:
                await query.edit_message_text("❌ Event not found.")
                return
            event_title = row[0]
            event_code = row[1]
            success_msg = f"🎉 Successfully joined: {event_title}\n\n"
            success_msg += "You can now participate in this event's referral program!\n"
            success_msg += "Use your personal referral link to invite others to this event."
            kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            try:
                user = db.get_user(user_id)
                if user and user.get('referral_code'):
                    personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                    success_msg += f"\n\n🎯 Your Event Link:\n{personal_link}"
                    kb.insert(0, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except Exception:
                pass
            await query.edit_message_text(success_msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"join_event_confirm error: {e}")
            await query.edit_message_text("Error joining event. Please try again.")

    async def end_event_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            args = context.args
            if not args:
                await update.message.reply_text("Usage: /end_event <event_code>\nYou can find the event code in your event details.")
                return
            event_code = args[0].strip()
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, host_id, title FROM events WHERE event_code = ?', (event_code,))
                row = cursor.fetchone()
            if not row:
                await update.message.reply_text("❌ Event not found.")
                return
            event_id, host_id, title = row
            if host_id != user.id:
                await update.message.reply_text("Only the host can end this event.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE events SET is_active = 0 WHERE id = ?', (event_id,))
                conn.commit()
            stats = db.get_event_stats(event_id)
            msg = [
                "✅ Event Ended",
                f"📅 Event: {title}",
                "",
                f"👥 Total participants: {stats.get('total_participants', 0)}",
                f"🔗 Total referrals: {stats.get('total_referrals', 0)}",
            ]
            await update.message.reply_text("\n".join(msg))
        except Exception as e:
            logger.error(f"end_event_command error: {e}")
            await update.message.reply_text("Error ending event. Please try again later.")


if __name__ == "__main__":
    bot = MultipurposeBot()
    bot.run()
