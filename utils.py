# utils.py
import os
import json
import asyncio
import re
import logging
from datetime import datetime
import pytz
import discord

logger = logging.getLogger()
STATUS_FILE = "bot_status.json"
LOG_FILE = "message_logs.jsonl"
IST = pytz.timezone('Asia/Kolkata')

def set_error_flag(value: bool = True):
    """Set or clear the error flag in the bot status file."""
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
        logger.error(f"❌ Failed to set error flag: {e}")

def reset_status():
    """Reset bot status flags."""
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
    """Log user-bot interactions to JSONL file."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "user": str(user),
        "user_message": user_msg,
        "bot_response": bot_response
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

import re

def chunk_message(message, limit=2000):
    """
    Split long messages into chunks under the Discord character limit,
    while preserving formatting.

    Rules:
    1. '*' count must be even (avoid breaking italics/bold).
       - also must not end right after a lone '*'
    2. '`' count must be even (avoid breaking inline/code blocks).
       - also check for triple backticks ``` pairs
    3. Must not end with '\n'
    """
    chunks = []
    while len(message) > limit:
        split_point = message.rfind("\n", 0, limit)
        if split_point == -1:
            split_point = limit

        # --- Enforce rules ---
        while split_point > 0:
            candidate = message[:split_point]

            # Rule 1: '*' count must be even
            if candidate.count("*") % 2 != 0:
                split_point = message.rfind("\n", 0, split_point - 1)
                continue

            # Rule 1b: don’t end after a lone '*'
            if re.search(r"(?<!\*)\*$", candidate):  
                split_point = message.rfind("\n", 0, split_point - 1)
                continue

            # Rule 2a: '`' count must be even
            if candidate.count("`") % 2 != 0:
                split_point = message.rfind("\n", 0, split_point - 1)
                continue

            # Rule 2b: triple backticks must also be even
            if candidate.count("```") % 2 != 0:
                split_point = message.rfind("\n", 0, split_point - 1)
                continue

            # Rule 3: must not end with newline
            if candidate.endswith("\n"):
                split_point = message.rfind("\n", 0, split_point - 1)
                continue

            break  # ✅ all rules satisfied

        if split_point <= 0:  # failsafe if no valid split found
            split_point = limit

        chunks.append(message[:split_point])
        message = message[split_point:].lstrip()

    chunks.append(message)
    return chunks

async def replace_mentions_with_usernames(message: discord.Message) -> str:
    """Replace <@id> mentions with @username."""
    content = message.content
    guild = message.guild
    user_ids = re.findall(r"<@(\d{17,19})>", content)
    unique_ids = set(user_ids)
    for user_id in unique_ids:
        member = guild.get_member(int(user_id))
        content = re.sub(rf"<@{user_id}>", f"@{member.name}" if member else "@user", content)
    return content.replace("*", "").replace("`", "")
