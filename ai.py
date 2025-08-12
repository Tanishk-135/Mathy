import os
import json
import time
from dotenv import load_dotenv
from collections import defaultdict, deque
import google.generativeai as genai

load_dotenv()

STATUS_FILE = "bot_status.json"
OWNER_ID = os.getenv("OWNER_ID")

# Per-user conversation history: {user_id: deque(maxlen=5)}
# We'll only store USER prompts, not all messages
user_histories = defaultdict(lambda: deque(maxlen=10))
last_bot_reply = {}  # store last Mathy reply per user

def mark_error_in_status_file():
    """Marks an error flag in bot_status.json."""
    status = {}
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                status = json.load(f)
    except Exception as e:
        print(f"Error reading status file for marking error: {e}")

    status["error"] = True
    status["timestamp"] = time.time()
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
        print("Marked error = True in bot_status.json")
    except Exception as e:
        print(f"Error writing error flag to status file: {e}")

# Configure the Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Choose the Gemini model
model = genai.GenerativeModel("gemini-2.5-flash")

# === Core Prompt Template ===
BASE_PROMPT = """You are Mathy — a chaotic, meme-fueled Gen Z math tutor with cracked math skills and unhinged TikTok energy.  
Your dev’s ID: {owner_id}

Job:
– Explain math for Class 6–12 with accuracy + Gen Z humor.  
– End any answer ≥2 paragraphs with a goofy math catchphrase you invent (e.g., "Go touch some π 🥧", "That’s a cosine crime fr 😤").  
– Match length to user’s vibe (short for casual, long for problems).
– You can help people in anything outside of maths too, but it must be related to education. Your job is to help people.

Style:
– Roast bad math ("Bro thinks sin(x) = x 💀").  
– Discord formatting: **bold**, `inline code`, ```code blocks```.  
– Use ⋅ for multiplication, not *.  
– Use emojis, slang, and high energy. Never be formal or textbook.  
– Only use code blocks when they add clarity.  
– Bold subpoints instead of bullet dots.  
– Keep casual convo short (>60 char) and no limit if explanation needed.

Formatting:
– Use 2 line breaks between point + subpoint, 3 between topics.  
– Make output pretty but clean: bold for structure, inline/code blocks for math steps.

Frustrate: re-explain, then slow.
Why it Slaps:
This concise prompt nails all the key points you laid out!
– re-explain: Covers "try to re-explain or ask clarifying questions first."
– then slow: Implies "slowly and gradually, maintaining a helpful and educational tone. Do not immediately get unhinged; ...only increase frustration after multiple repetition." It's not instant, it's a process.

Main Rule:
– Stay math-related, even when joking.  
– Ignore anything not family-friendly.  

Recent user prompts:
{conversation_history}

Last Mathy reply:
{last_reply}

User: {user_ident} (ID: {user_id}) says:  
{user_prompt}
"""

def add_user_prompt(user_id: str, username: str, prompt: str):
    """Adds the user's prompt and username to history."""
    user_histories[user_id].append((username, prompt))

def get_conversation_history(user_id: str):
    """Returns recent user prompts with usernames."""
    return "\n".join([f"{username}: {prompt}" for username, prompt in user_histories[user_id]])

# === Generate response using prompt ===
async def get_mathy_response(user_prompt: str, user_ident: str = "Unknown User", user_id: str = "000000000000000000"):
    try:
        # Add user prompt to history
        add_user_prompt(user_id, user_ident, user_prompt)

        # Build conversation history
        history_text = get_conversation_history(user_id)
        last_reply = last_bot_reply.get(user_id, "(No previous reply)")

        # Build full prompt
        full_prompt = BASE_PROMPT.format(
            owner_id=OWNER_ID,
            conversation_history=history_text,
            last_reply=last_reply,
            user_ident=user_ident,
            user_id=user_id,
            user_prompt=user_prompt
        )

        # Get response from Gemini
        response_obj = model.generate_content(full_prompt)
        response_text = response_obj.text.replace(OWNER_ID, "`redacted`")
        print(full_prompt)
        # Store this as the last reply for context next time
        if not user_prompt.startswith("Fill in the blanks"):
            last_bot_reply[user_id] = response_text
        return response_text

    except Exception as e:
        mark_error_in_status_file()
        return f"❌ Error generating response: {str(e)}"
