# database.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set in environment.")
    # Railway typically requires SSL; keep RealDictCursor for dict-like rows
    return psycopg2.connect(DATABASE_URL, sslmode="require", cursor_factory=RealDictCursor)

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS mathy_logs (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        question TEXT,
        response TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_problems (
        id SERIAL PRIMARY KEY,
        date DATE UNIQUE NOT NULL,
        problem_text TEXT NOT NULL,
        correct_answer_letter CHAR(1),
        correct_answer_option TEXT,
        message_id BIGINT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()

def save_daily_problem(date, problem_text, letter, option, message_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO daily_problems (date, problem_text, correct_answer_letter, correct_answer_option, message_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE
        SET problem_text = EXCLUDED.problem_text,
            correct_answer_letter = EXCLUDED.correct_answer_letter,
            correct_answer_option = EXCLUDED.correct_answer_option,
            message_id = EXCLUDED.message_id
    """, (date, problem_text, letter, option, message_id))
    conn.commit()
    conn.close()

def get_latest_daily_problem():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT problem_text FROM daily_problems ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row["problem_text"] if row else None

def load_today_problem(date):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM daily_problems WHERE date = %s", (date,))
    result = cur.fetchone()
    conn.close()
    return result

def log_mathy_interaction(user_id, username, question, response):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mathy_logs (user_id, username, question, response) VALUES (%s, %s, %s, %s)",
        (user_id, username, question, response)
    )
    conn.commit()
    conn.close()
