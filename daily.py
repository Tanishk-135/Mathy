# daily.py
import os
import google.generativeai as genai
from dotenv import load_dotenv
from ai import get_mathy_response
import random

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel("gemini-2.5-flash")

async def math_problem():
    prompt = """Your job:
✅ Generate a unique, engaging math problem suitable for high school students (class 11) that encourages critical thinking.
✅ Avoid repetition or common textbook-style phrasing.
✅ Format it like:

📘 **Daily Math Challenge**

_Problem_: ...
Correct Answer: <correct_option eg A, B, C, D>
✅ Include 4 options at the end vertically and in seperate inline code blocks.

Style rules:
– Use Gen Z humor, Skibidi energy, and goofy ahh slang.
– Be accurate, but never boring.
– Use Discord formatting: **bold**, `inline code`, and ```code blocks```.
– Use emojis, TikTok slang, baby rage, and MrBeast-level energy.
– NEVER be formal. NEVER be dry. NEVER be a textbook.
– Keep it under 60 words.
– Make it engaging and fresh af.

Beautify:
– Use **bold** and `inline code` blocks when needed.
– Make it pretty and easy on the eyes.

Main Rule:
– Be completely related to math, even on the things that are not related.
"""
    try:
        response = model.generate_content(prompt)
        response_text = response.text
        start_marker = "📘 **Daily Math Challenge**"
        start_index = response_text.find(start_marker)
        if start_index != -1:
            response_text = response_text[start_index:].lstrip()
        return response_text
    except Exception as e:
        return f"❌ Error generating math problem: {str(e)}"

prompt_type=["Listening to", "Playing", "Watching"]
choice=random.choice(prompt_type)
QUOTE_PROMPT = f"""
Fill in the blanks, {choice} _____. Give the answer related to math and directly without any explanation or additional text and be creative not dull answers and in plain text no bold or any formatting, can add emojies.
"""

async def math_quote():
    response = await get_mathy_response(QUOTE_PROMPT)
    print(f"🧠 Daily Quote: {response.strip()}")
    return response.strip(), choice

async def get_vote_counts(message):
    counts = {}
    if message is None:
        return counts

    # Fetch the message fresh from Discord to get updated reactions
    channel = message.channel
    fresh_message = await channel.fetch_message(message.id)

    for reaction in fresh_message.reactions:
        if reaction.emoji in ['🇦', '🇧', '🇨', '🇩']:
            counts[reaction.emoji] = reaction.count - 1  # subtract bot's own reaction

    return counts


