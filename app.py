from flask import Flask, request
import requests
import os
import time
import urllib.parse

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import redis
from rq import Queue

from google import genai

app = Flask(__name__)

# ================== CONFIG ==================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

# ================== REDIS QUEUE ==================
redis_conn = redis.from_url(REDIS_URL)
task_queue = Queue(connection=redis_conn)

# ================== DB POOL ==================
db_pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

def get_conn():
    return db_pool.getconn()

def release_conn(conn):
    db_pool.putconn(conn)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_memory (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        role TEXT,
        message TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    release_conn(conn)

init_db()

# ================== DB ==================
def save_message(user, role, message):
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO chat_memory (user_id, role, message) VALUES (%s, %s, %s)",
            (user, role, message)
        )

        conn.commit()
        cur.close()
        release_conn(conn)
    except Exception as e:
        print("DB Save Error:", e)

def get_memory(user):
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT role, message FROM chat_memory WHERE user_id=%s ORDER BY id DESC LIMIT 10",
            (user,)
        )

        rows = cur.fetchall()

        cur.close()
        release_conn(conn)

        return list(reversed(rows))
    except Exception as e:
        print("DB Read Error:", e)
        return []

# ================== MEMORY ==================
user_style = {}
user_last_message = {}

# ================== SPAM ==================
def is_spam(user):
    now = time.time()
    if user in user_last_message and now - user_last_message[user] < 1:
        return True
    user_last_message[user] = now
    return False

# ================== WHATSAPP ==================
def send_whatsapp_message(to, text):
    try:
        requests.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text}
            },
            timeout=10
        )
    except Exception as e:
        print("Send error:", e)

def send_whatsapp_image(to, image_path):
    try:
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"

        with open(image_path, "rb") as f:
            res = requests.post(
                url,
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                files={
                    "file": ("image.png", f, "image/png"),
                    "messaging_product": (None, "whatsapp")
                }
            ).json()

        if "id" not in res:
            print("Upload Failed:", res)
            return

        requests.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "image",
                "image": {"id": res["id"]}
            }
        )

    except Exception as e:
        print("Image send error:", e)

# ================== ROUTES ==================
@app.route("/")
def home():
    return "Bot running 🚀", 200

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Error", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]

        if is_spam(sender):
            return "OK", 200

        if "text" in msg:
            text = msg["text"]["body"].lower()

            if text == "image":
                send_whatsapp_message(sender,
                    "🎨 Choose style:\n1 Anime\n2 Realistic\n3 Cartoon\n4 Cyberpunk\n5 Sketch\n6 Fantasy"
                )
                return "OK", 200

            elif text in ["1","2","3","4","5","6"]:
                styles = ["anime","realistic","cartoon","cyberpunk","sketch","fantasy"]
                user_style[sender] = styles[int(text)-1]
                send_whatsapp_message(sender, "Send prompt 🎨")
                return "OK", 200

            # 🚀 SEND TO BACKGROUND WORKER
            task_queue.enqueue("worker.process_message", sender, text)

    except Exception as e:
        print("Webhook Error:", e)

    return "OK", 200


# ================== ADMIN ==================
@app.route("/admin")
def admin():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT user_id, COUNT(*) FROM chat_memory GROUP BY user_id")
    users = cur.fetchall()

    cur.close()
    release_conn(conn)

    return str(users)
