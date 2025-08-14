import os
import sys
import io
import re
import json
import asyncio
import logging
import subprocess
from threading import Thread
from datetime import datetime, time as dt_time, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from flask import Flask
import pytz

from ai import get_mathy_response  # Gemini handler (must be async)
from daily import math_problem, math_quote, get_vote_counts
import database  # our DB module

# Import utilities
from utils import (
    set_error_flag,
    reset_status,
    log_interaction,
    chunk_message,
    replace_mentions_with_usernames,
    STATUS_FILE,
    IST
)

# ---------- Environment ----------
load_dotenv()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# ---------- Logging ----------
os.makedirs("logs", exist_ok=True)
log_filename = f"logs/{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger()

# Ensure stdout/stderr can print any unicode safely
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------- Globals ----------
last_sent_date = None
sent_message = False
daily_problem_message = None
correct_answer_letter = None
correct_answer_option = None
problem = None

# ---------- Flask Keep-Alive ----------
app = Flask(__name__)

@app.route('/')
def home():
    return 'Mathy bot is alive!'

def run_flask():
    app.run(host='0.0.0.0', port=8080)

flask_thread = Thread(target=run_flask, daemon=True)
flask_thread.start()

# ---------- Discord Bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# ---------- Restart & Uptime ----------
async def restart_at_safe_time(hour=2, minute=30):
    while True:
        now_ist = datetime.now(pytz.utc).astimezone(IST)
        restart_time = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_ist >= restart_time:
            restart_time += timedelta(days=1)
        wait_seconds = (restart_time - now_ist).total_seconds()
        hours = int(wait_seconds // 3600)
        minutes = int((wait_seconds % 3600) // 60)
        print(f"[Restart Scheduler] Waiting {hours} hrs {minutes} mins until {hour:02d}:{minute:02d} IST...")
        await asyncio.sleep(wait_seconds)
        print("[Restart Scheduler] Restarting now...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

async def uptime_watcher(hours=12):
    await asyncio.sleep(hours * 3600)
    print(f"[Uptime Watcher] {hours} hours reached, scheduling restart...")
    asyncio.create_task(restart_at_safe_time())

# ---------- Daily Problem Scheduler ----------
@tasks.loop(minutes=1)
async def daily_problem_scheduler():
    global last_sent_date, daily_problem_message, correct_answer_letter, correct_answer_option, problem, sent_message

    now_ist = datetime.now(pytz.utc).astimezone(IST)
    today_date = now_ist.date()

    if now_ist.time() < dt_time(8, 0):
        next_daily = datetime.combine(today_date, dt_time(8, 0, 0))
    else:
        next_daily = datetime.combine(today_date + timedelta(days=1), dt_time(8, 0, 0))
    next_daily = IST.localize(next_daily)

    wait_seconds = (next_daily - now_ist).total_seconds()

    if dt_time(8, 0) <= now_ist.time() <= dt_time(8, 30) and last_sent_date != today_date:
        channel = bot.get_channel(1402996264278298695)  # <-- your channel ID
        if channel:
            try:
                problem = await math_problem()

                match = re.search(r"Correct Answer:\s*([A-D])", problem, re.IGNORECASE)
                if match:
                    correct_answer_letter = match.group(1).upper()
                    problem = re.sub(r"Correct Answer:\s*[A-D]\s*", "", problem, flags=re.IGNORECASE).strip()
                else:
                    correct_answer_letter = None

                LETTER_TO_EMOJI = {'A': '🇦', 'B': '🇧', 'C': '🇨', 'D': '🇩'}
                correct_answer_option = LETTER_TO_EMOJI.get(correct_answer_letter)

                daily_problem_message = await channel.send("<@&1378364940322345071> \n\n" + problem)
                logger.info(f"📤 Daily problem sent (Answer: {correct_answer_letter}, emoji: {correct_answer_option})")

                database.save_daily_problem(today_date, problem, correct_answer_letter, correct_answer_option, daily_problem_message.id)

                for emoji in ['🇦', '🇧', '🇨', '🇩']:
                    await daily_problem_message.add_reaction(emoji)

                last_sent_date = today_date
                sent_message = False
            except Exception as e:
                logger.error(f"❌ Failed to send daily problem: {e}")
                set_error_flag(True)
        else:
            logger.error("❌ Channel not found.")
            set_error_flag(True)
    else:
        if not sent_message:
            hours = int(wait_seconds // 3600)
            minutes = int((wait_seconds % 3600) // 60)
            msg = (f"⏳ Sleeping {hours} hrs {minutes} mins until 8 AM..." if minutes else f"⏳ Sleeping {hours} hrs until 8 AM...")
            print(msg)
            sent_message = True

async def init_daily_problem():
    global problem, correct_answer_letter, correct_answer_option, daily_problem_message
    now_ist = datetime.now(pytz.utc).astimezone(IST)
    today_data = database.load_today_problem(now_ist.date())
    if today_data:
        problem = today_data['problem_text']
        correct_answer_letter = today_data['correct_answer_letter']
        correct_answer_option = today_data['correct_answer_option']
        try:
            channel = bot.get_channel(1402996264278298695)
            daily_problem_message = await channel.fetch_message(today_data['message_id'])
            logger.info("✅ Loaded today's problem from DB.")
        except Exception as e:
            logger.warning(f"⚠ Could not fetch message: {e}")
            daily_problem_message = None
    else:
        logger.info("ℹ️ No problem stored for today.")

async def schedule_midnight_vote_summary():
    await bot.wait_until_ready()
    await init_daily_problem()
    channel = bot.get_channel(1402996264278298695)

    while not bot.is_closed():
        now_ist = datetime.now(pytz.utc).astimezone(IST)
        next_midnight_ist = IST.localize(datetime.combine(now_ist.date() + timedelta(days=1), dt_time(0, 0, 0)))
        wait_seconds = (next_midnight_ist - now_ist).total_seconds()
        logger.info(f"⏳ Sleeping until midnight IST for vote summary...")
        await asyncio.sleep(wait_seconds)

        global daily_problem_message, correct_answer_option, problem
        if daily_problem_message and correct_answer_option and channel:
            try:
                results = await get_vote_counts(daily_problem_message)
                if results:
                    total_votes = sum(results.values())
                    correct_votes = results.get(correct_answer_option, 0)
                    summary = (
                        f"📊 **Daily problem voting summary:**\n"
                        f"Total votes: {total_votes}\n"
                        f"Correct votes ({correct_answer_option}): {correct_votes}\n"
                        f"Votes breakdown:\n" +
                        "".join(f"{emoji}: {count}\n" for emoji, count in results.items())
                    )

                    prompt_for_result = f"""{problem}

{summary}

Roast them if bad or solve the problem. Keep under 1000 chars."""
                    response = await get_mathy_response(prompt_for_result)
                    await channel.send("<@&1378364940322345071> \n \n" + response)
                    logger.info("✅ Sent midnight vote summary.")
                else:
                    await channel.send("No votes data available for midnight summary.")
            except Exception as e:
                logger.error(f"❌ Error sending midnight summary: {e}")
                set_error_flag(True)
        else:
            logger.info("ℹ️ No active daily problem for midnight summary.")

# ---------- Events ----------
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    reset_status()

    try:
        database.init_db()
        logger.info("🗄️ Database initialized.")
    except Exception as e:
        logger.error(f"❌ DB init failed: {e}")
        set_error_flag(True)

    activity_map = {
        "Playing": discord.ActivityType.playing,
        "Listening to": discord.ActivityType.listening,
        "Watching": discord.ActivityType.watching
    }
    try:
        quote, activity_type_str = await math_quote()
        activity_type = activity_map.get(activity_type_str, discord.ActivityType.playing)
        quote = quote.replace(activity_type_str, "").replace("*", "").replace(".", "")
        await bot.change_presence(activity=discord.Activity(type=activity_type, name=quote))
        logger.info(f"🧠 Status set to: {quote}")
    except Exception as e:
        logger.error(f"❌ Failed to set status: {e}")
        set_error_flag(True)

    bot.loop.create_task(schedule_midnight_vote_summary())
    daily_problem_scheduler.start()
    asyncio.create_task(restart_at_safe_time())

@bot.event
async def on_member_join(member):
    role_name = "MathMind"
    role = discord.utils.get(member.guild.roles, name=role_name)
    if role:
        await member.add_roles(role)
        print(f"Assigned role '{role_name}' to {member.name}")
    else:
        print(f"Role '{role_name}' not found")

@bot.command()
async def restart(ctx):
    if ctx.author.id == OWNER_ID and OWNER_ID != 0:
        print("🔁 Restarting Mathy in 5 seconds...")
        python = sys.executable
        script = os.path.abspath(sys.argv[0])
        await asyncio.sleep(5)
        subprocess.Popen([python, script])
        await bot.close()
    else:
        await ctx.send("🚫 You don't have permission to restart the bot.")

@bot.command()
async def votes(ctx):
    global daily_problem_message, correct_answer_option
    if daily_problem_message is None:
        await ctx.send("No active daily problem message found.")
        return

    counts = await get_vote_counts(daily_problem_message)
    if not counts:
        await ctx.send("No votes yet!")
        return

    total_votes = sum(counts.values())
    correct_votes = counts.get(correct_answer_option, 0) if correct_answer_option else 0
    response = "**Current votes:**\n" + "".join(f"{emoji} : {count}\n" for emoji, count in counts.items())
    if correct_answer_option:
        response += f"\n✅ Correct option {correct_answer_option} has {correct_votes}/{total_votes} votes."
    await ctx.send(response)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message):
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump({"flash_both": True, "timestamp": asyncio.get_event_loop().time()}, f)

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        try:
            await message.channel.typing()
            response = await get_mathy_response(prompt, str(message.author), str(message.author.id))
            response = re.sub(r"`<@(\d{18})>`", r"<@\1>", response)

            cleaned_message = await replace_mentions_with_usernames(message)
            cleaned_message = cleaned_message.replace("@MathMinds Bot", "@Mathy").replace("*", "").replace("`", "")

            database.log_mathy_interaction(message.author.id, str(message.author), cleaned_message, response)
        except Exception as e:
            response = f"❌ Error generating response: {str(e)}"
            logger.error(f"Error in get_mathy_response: {e}")
            set_error_flag(True)

        for chunk in chunk_message(response):
            await message.channel.send(chunk)

        log_interaction(message.author, prompt, response)
        logger.info(f"Responded to {message.author}: {prompt}")

    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error

# ---------- Run Bot ----------
bot.run(os.getenv("DISCORD_TOKEN"))
