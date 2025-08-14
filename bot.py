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
import database  # our new DB module

load_dotenv()

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

# ---------- Globals / Config ----------
STATUS_FILE = "bot_status.json"
LOG_FILE = "message_logs.jsonl"
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
IST = pytz.timezone('Asia/Kolkata')

last_sent_date = None
sent_message = False

# Flask heartbeat (keep-alive)
app = Flask(__name__)

@app.route('/')
def home():
    return 'Mathy bot is alive!'

def run_flask():
    app.run(host='0.0.0.0', port=8080)

flask_thread = Thread(target=run_flask, daemon=True)
flask_thread.start()

# ---------- Discord Intents/Bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# ---------- Helpers ----------
def set_error_flag(value: bool = True):
    try:
        data = {}
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["error"] = value
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"⚠️ Set error flag to {value} in {STATUS_FILE}")
    except Exception as e:
        logger.error(f"❌ Failed to set error flag in {STATUS_FILE}: {e}")

def reset_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["flash_both"] = False
        data["error"] = False
        data["timestamp"] = asyncio.get_event_loop().time()
    else:
        data = {"flash_both": False, "timestamp": asyncio.get_event_loop().time()}
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    logger.info("✅ Bot status reset.")

def log_interaction(user, user_msg, bot_response):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "user": str(user),
        "user_message": user_msg,
        "bot_response": bot_response
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def chunk_message(message, limit=2000):
    chunks = []
    while len(message) > limit:
        split_point = message.rfind("\n", 0, limit)
        if split_point == -1:
            split_point = limit
        chunks.append(message[:split_point])
        message = message[split_point:].lstrip()
    chunks.append(message)
    return chunks

async def restart_at_safe_time(hour=2, minute=30):
    while True:
        now_ist = datetime.now(pytz.utc).astimezone(IST)
        restart_time = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_ist >= restart_time:
            restart_time += timedelta(days=1)
        wait_seconds = (restart_time - now_ist).total_seconds()
        hours = int(wait_seconds // 3600)
        minutes = int((wait_seconds % 3600) // 60)
        print(f"[Restart Scheduler] Waiting {hours} hrs {minutes} minutes until restart window at {hour:02d}:{minute:02d} IST...")
        await asyncio.sleep(wait_seconds)
        print("[Restart Scheduler] Restarting now...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

async def uptime_watcher(hours=12):
    await asyncio.sleep(hours * 3600)
    print(f"[Uptime Watcher] {hours} hours reached, scheduling restart...")
    asyncio.create_task(restart_at_safe_time())

async def replace_mentions_with_usernames(message: discord.Message) -> str:
    content = message.content
    guild = message.guild
    user_ids = re.findall(r"<@(\d{17,19})>", content)
    unique_ids = set(user_ids)
    for user_id in unique_ids:
        member = guild.get_member(int(user_id))
        content = re.sub(rf"<@{user_id}>", f"@{member.name}" if member else "@user", content)
    return content.replace("*", "").replace("`", "")

# ---------- Daily Problem Scheduling ----------
daily_problem_message = None
correct_answer_letter = None
correct_answer_option = None
problem = None

@tasks.loop(minutes=1)
async def daily_problem_scheduler():
    global last_sent_date, daily_problem_message, correct_answer_letter, correct_answer_option, problem, sent_message

    now_ist = datetime.now(pytz.utc).astimezone(IST)
    today_date = now_ist.date()

    # Next 8 AM IST
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

                # Extract and remove the "Correct Answer: X" marker if present
                match = re.search(r"Correct Answer:\s*([A-D])", problem, re.IGNORECASE)
                if match:
                    correct_answer_letter = match.group(1).upper()
                    problem = re.sub(r"Correct Answer:\s*[A-D]\s*", "", problem, flags=re.IGNORECASE).strip()
                else:
                    correct_answer_letter = None

                LETTER_TO_EMOJI = {'A': '🇦', 'B': '🇧', 'C': '🇨', 'D': '🇩'}
                correct_answer_option = LETTER_TO_EMOJI.get(correct_answer_letter)

                daily_problem_message = await channel.send("<@&1378364940322345071> \n\n" + problem)
                logger.info(f"📤 Daily problem sent (Answer letter: {correct_answer_letter}, emoji: {correct_answer_option})")

                # Save to DB
                database.save_daily_problem(today_date, problem, correct_answer_letter, correct_answer_option, daily_problem_message.id)

                # Add reactions
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
            print(
                f"⏳ Sleeping {hours} hrs {minutes} mins until 8 AM for daily math problem..."
                if minutes != 0 else
                f"⏳ Sleeping {hours} hrs until 8 AM for daily math problem..."
            )
            sent_message = True

async def init_daily_problem():
    """Load today's problem from DB into globals."""
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
            logger.warning(f"⚠ Could not fetch message from Discord: {e}")
            daily_problem_message = None
    else:
        logger.info("ℹ️ No problem stored for today.")

async def schedule_midnight_vote_summary():
    """Run vote summary at midnight IST daily."""
    await bot.wait_until_ready()
    await init_daily_problem()

    channel = bot.get_channel(1402996264278298695)  # Problem channel ID

    while not bot.is_closed():
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        now_ist = now_utc.astimezone(IST)

        next_midnight_ist = IST.localize(datetime.combine(now_ist.date() + timedelta(days=1), dt_time(0, 0, 0)))
        wait_seconds = (next_midnight_ist - now_ist).total_seconds()
        hours = int(wait_seconds // 3600)
        minutes = int((wait_seconds % 3600) // 60)
        logger.info(
            f"⏳ Sleeping {hours} hrs {minutes} minutes until midnight IST for vote summary..."
            if minutes != 0 else f"⏳ Sleeping {hours} hrs until midnight IST for vote summary..."
        )
        await asyncio.sleep(wait_seconds)

        global daily_problem_message, correct_answer_option, problem
        if daily_problem_message and correct_answer_option and channel:
            try:
                results = await get_vote_counts(daily_problem_message)
                if results:
                    total_votes = sum(results.values())
                    correct_votes = results.get(correct_answer_option, 0)

                    summary = (
                        f"📊 **Daily problem voting summary at midnight IST:**\n"
                        f"Total votes: {total_votes}\n"
                        f"Correct votes ({correct_answer_option}): {correct_votes}\n"
                        f"Votes breakdown:\n"
                    )
                    for emoji, count in results.items():
                        summary += f"{emoji}: {count}\n"

                    prompt_for_result = f"""Mathy, I am sharing you a result of this math problem.
{problem}

Here is the result.

{summary}

Roast them if bad or do anything based on the situation, also don't leave them hanging and confused, actually solve that problem.
Also keep it under 1000 characters and actually see the votes and judge using the hardness of the question, basically tell YOUR verdict on the votes."""
                    response = await get_mathy_response(prompt_for_result)
                    logger.info(f"Vote summary message to send:\n{summary}")

                    await channel.send("<@&1378364940322345071> \n \n" + response)
                    logger.info("✅ Sent midnight vote summary.")
                else:
                    await channel.send("No votes data available for midnight summary.")
            except Exception as e:
                logger.error(f"❌ Error sending midnight vote summary: {e}")
                set_error_flag(True)
        else:
            logger.info("ℹ️ No active daily problem or channel for midnight vote summary.")

# ---------- Bot Events & Commands ----------
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user}")
    reset_status()

    # Ensure DB tables exist
    try:
        database.init_db()
        logger.info("🗄️ Database initialized.")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
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
        logger.error(f"❌ Failed to set quote status: {e}")
        set_error_flag(True)

    # Background tasks
    bot.loop.create_task(schedule_midnight_vote_summary())
    daily_problem_scheduler.start()
    asyncio.create_task(restart_at_safe_time())

@bot.event
async def on_member_join(member):
    role_name = "MathMind"
    guild = member.guild
    role = discord.utils.get(guild.roles, name=role_name)
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

    response = "**Current votes:**\n"
    for emoji, count in counts.items():
        response += f"{emoji} : {count}\n"
    if correct_answer_option:
        response += f"\n✅ Correct option {correct_answer_option} has {correct_votes} vote(s) out of {total_votes} total."
    await ctx.send(response)

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message):
        # Trigger Arduino to flash both LEDs by writing status file
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump({"flash_both": True, "timestamp": asyncio.get_event_loop().time()}, f)

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        try:
            await message.channel.typing()

            response = await get_mathy_response(
                user_prompt=prompt,
                user_ident=str(message.author),
                user_id=str(message.author.id)
            )

            # normalize accidental code-fenced mentions back to <@id>
            response = re.sub(r"`<@(\d{18})>`", r"<@\1>", response)

            # Clean message (replace mentions with usernames for logging) and sanitize special chars
            cleaned_message = await replace_mentions_with_usernames(message)
            cleaned_message = cleaned_message.replace("<@1376515962915913778>", "@Mathy").replace("*", "").replace("`", "")

            # DB log
            database.log_mathy_interaction(
                message.author.id,
                str(message.author),
                cleaned_message,
                response
            )

        except Exception as e:
            response = f"❌ Error generating response: {str(e)}"
            logger.error(f"Error in get_mathy_response: {e}")
            set_error_flag(True)

        # Send in chunks (<=2000 chars)
        for chunk in chunk_message(response):
            await message.channel.send(chunk)

        log_interaction(message.author, prompt, response)
        logger.info(f"Sent a response to {message.author}'s prompt: {prompt}")

    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error

# ---------- Run Bot ----------
bot.run(os.getenv("DISCORD_TOKEN"))
