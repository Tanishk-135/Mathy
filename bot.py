import subprocess
import sys
import os
from datetime import datetime, time, timedelta
import logging
import discord
import asyncio
import json
import time as time_module  # avoid conflict with datetime.time
import pytz
from discord.ext import commands, tasks
from ai import get_mathy_response  # Gemini handler (must be async)
from datetime import datetime, time as dt_time
import pytz
import re
from daily import math_problem, math_quote, get_vote_counts
from dotenv import load_dotenv
import io
from threading import Thread
from flask import Flask

load_dotenv()

# Setup logging
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

STATUS_FILE = "bot_status.json"
LOG_FILE = "message_logs.jsonl"
OWNER_ID=os.getenv("OWNER_ID")
last_sent_date = None
sent_message=False
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
app = Flask(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.members=True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# === Helper to set error flag ===
def set_error_flag(value: bool = True):
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
        else:
            data = {}
        data["error"] = value
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
        logger.info(f"⚠️ Set error flag to {value} in {STATUS_FILE}")
    except Exception as e:
        logger.error(f"❌ Failed to set error flag in {STATUS_FILE}: {e}")

# === Reset bot status on startup ===
def reset_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, "r") as f:
            data = json.load(f)
        data["flash_both"] = False
        data["error"] = False
        data["timestamp"] = time_module.time()
    else:
        data = {"flash_both": False, "timestamp": time_module.time()}
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)
    logger.info("✅ Bot status reset.")

# === Log message & response ===
def log_interaction(user, user_msg, bot_response):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "user": str(user),
        "user_message": user_msg,
        "bot_response": bot_response
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# === Chunk response into ≤2000 char messages ===
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

@tasks.loop(minutes=1)
async def daily_problem_scheduler():
    global last_sent_date, daily_problem_message, correct_answer_letter, correct_answer_option, problem, sent_message

    IST = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(pytz.utc).astimezone(IST)

    # Calculate next midnight IST
    next_daily = datetime.combine(now_ist.date() + timedelta(days=1), time(8, 0, 0))
    next_daily = IST.localize(next_daily)

    wait_seconds = (next_daily - now_ist).total_seconds()
    today_date = now_ist.date()

    # Check if it's between 8:00 and 8:30 AM AND we haven't sent today
    if dt_time(8, 0) <= now_ist.time() <= dt_time(8, 30) and last_sent_date != today_date:
        channel = bot.get_channel(1402996264278298695)
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
                logger.info(f"📤 Daily problem sent to #{channel.name} (Answer letter: {correct_answer_letter}, emoji: {correct_answer_option})")

                for emoji in LETTER_TO_EMOJI.values():
                    await daily_problem_message.add_reaction(emoji)

                # ✅ Update the flag so it doesn't send again today
                last_sent_date = today_date

            except Exception as e:
                logger.error(f"❌ Failed to send daily problem: {e}")
                set_error_flag(True)
        else:
            logger.error("❌ Channel not found.")
            set_error_flag(True)
    else:
        if not sent_message:
            print(f"⏳ Sleeping {wait_seconds/3600:.2f} hrs until 8 AM for daily math problem...")
        sent_message=True

daily_problem_message = None
correct_answer_letter = None     # Store the letter 'A'/'B'/'C'/'D'
correct_answer_option = None     # Store the emoji '🇦', '🇧', etc.
problem = None

IST = pytz.timezone('Asia/Kolkata')

async def schedule_midnight_vote_summary():
    """Background task to run vote summary at midnight IST daily."""
    await bot.wait_until_ready()
    channel = bot.get_channel(1402996264278298695)  # Problem channel ID

    while not bot.is_closed():
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        now_ist = now_utc.astimezone(IST)

        # Calculate next midnight IST
        next_midnight_ist = datetime.combine(now_ist.date() + timedelta(days=1), time(0, 0, 0))
        next_midnight_ist = IST.localize(next_midnight_ist)

        wait_seconds = (next_midnight_ist - now_ist).total_seconds()
        logger.info(f"⏳ Sleeping {wait_seconds/3600:.2f} hrs until midnight IST for vote summary...")
        await asyncio.sleep(wait_seconds)

        # Run the vote summary at midnight
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
Also keep it under 2k characters and actually see the votes and judge using the hardness of the question, basically tell YOUR verdict on the votes."""
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

@bot.event
async def on_ready():
    global daily_problem_message, correct_answer_letter, correct_answer_option, problem
    logger.info(f"✅ Logged in as {bot.user}")
    reset_status()

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

    # Start the background tasks
    bot.loop.create_task(schedule_midnight_vote_summary())
    daily_problem_scheduler.start()

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
    if ctx.author.id == OWNER_ID:
        print("🔁 Restarting Mathy in 5 seconds...")

        python = sys.executable
        script = os.path.abspath(sys.argv[0])  # full path to bot.py

        await asyncio.sleep(5)

        # Launch bot again
        subprocess.Popen([python, script])

        await bot.close()

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
async def on_message(message):
    if message.author == bot.user:
        return

    if bot.user.mentioned_in(message):
        # Trigger Arduino to flash both LEDs
        with open(STATUS_FILE, "w") as f:
            json.dump({"flash_both": True, "timestamp": time_module.time()}, f)

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        try:
            await message.channel.typing()
            response = await get_mathy_response(prompt)
        except Exception as e:
            response = f"❌ Error generating response: {str(e)}"
            logger.error(f"Error in get_mathy_response: {e}")
            set_error_flag(True)

        for chunk in chunk_message(response):
            await message.channel.send(chunk)

        log_interaction(message.author, prompt, response)
        logger.info(f"📥 {message.author}: {prompt}\n📤 Mathy: {response}")

    await bot.process_commands(message)

@bot.command(name="clear")
async def clear(ctx, amount: int = 10):
    # Check user permissions (optional)
    if not ctx.author.guild_permissions.manage_messages:
        await ctx.send("🚫 You don't have permission to clear messages.")
        return

    # Limit max messages to delete to avoid abuse
    if amount > 500:
        await ctx.send("❌ I can only delete up to 500 messages at a time.")
        return

    # Bulk delete messages
    deleted = await ctx.channel.purge(limit=amount)
    await ctx.send(f"🧹 Cleared {len(deleted)} messages.", delete_after=5)

@app.route('/')
def home():
    return 'Mathy bot is alive!'

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Start Flask server in a separate thread so it doesn’t block your bot
flask_thread = Thread(target=run_flask)
flask_thread.daemon = True
flask_thread.start()

# === Run Bot ===
bot.run(os.getenv("DISCORD_TOKEN"))