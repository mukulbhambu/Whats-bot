from flask import Flask, request
import requests
import time
from datetime import datetime
import os
import urllib.parse
import psycopg2

from google import genai

app = Flask(__name__)

# ================== CONFIG ==================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

# ================== DB SAFE ==================
def get_db():
    for _ in range(3):  # retry
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn, conn.cursor()
        except Exception as e:
            print("DB connect retry:", e)
            time.sleep(1)
    return None, None

def init_db():
    conn, cursor = get_db()
    if conn:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_memory (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            role TEXT,
            message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.commit()
        cursor.close()
        conn.close()

init_db()

def save_message(user, role, message):
    try:
        conn, cursor = get_db()
        if not conn:
            return

        cursor.execute(
            "INSERT INTO chat_memory (user_id, role, message) VALUES (%s, %s, %s)",
            (user, role, message)
        )
        conn.commit()

        cursor.close()
        conn.close()

    except Exception as e:
        print("DB Save Error:", e)

def get_memory(user):
    try:
        conn, cursor = get_db()
        if not conn:
            return []

        cursor.execute(
            "SELECT role, message FROM chat_memory WHERE user_id=%s ORDER BY id DESC LIMIT 10",
            (user,)
        )

        rows = cursor.fetchall()

        cursor.close()
        conn.close()

        return list(reversed(rows))

    except Exception as e:
        print("DB Read Error:", e)
        return []

# ================== MEMORY ==================
user_style = {}

# ================== UTIL ==================
def send_whatsapp_message(to, text):
    try:
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }

        requests.post(url, headers=headers, json=payload, timeout=10)

    except Exception as e:
        print("Send message error:", e)

def send_whatsapp_image(to, image_path):
    try:
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

        with open(image_path, "rb") as f:
            files = {
                "file": ("image.png", f, "image/png"),
                "messaging_product": (None, "whatsapp")
            }
            res = requests.post(url, headers=headers, files=files, timeout=15).json()

        if "id" not in res:
            print("Upload Failed:", res)
            return

        media_id = res["id"]

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"id": media_id}
        }

        requests.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            headers=headers,
            json=payload,
            timeout=10
        )

    except Exception as e:
        print("Image send error:", e)

# ================== IMAGE ==================
def generate_image(prompt):
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024"

        res = requests.get(url, timeout=20)

        if res.status_code != 200:
            return None

        path = f"img_{int(time.time())}.png"
        with open(path, "wb") as f:
            f.write(res.content)

        return path

    except Exception as e:
        print("Image Error:", e)
        return None

# ================== PROMPT ==================
def enhance_prompt(user_prompt, style=""):
    try:
        text = f"Enhance: {user_prompt}, style: {style}, cinematic, high quality"

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[{"text": text}]
        )

        if hasattr(response, "text") and response.text:
            return response.text.strip()

        return user_prompt

    except:
        return user_prompt

# ================== AI ==================
def get_ai_reply(sender, user_message):

    save_message(sender, "user", user_message)

    history_data = get_memory(sender)

    history = "\n".join(
        f"{'User' if r=='user' else 'AI'}: {m}"
        for r, m in history_data
    )

    prompt = f"""
You are a smart WhatsApp chatbot 🤖
- Use emojis 😊🔥
- Keep replies short

{history}
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[{"text": prompt}]
        )

        reply = response.text if hasattr(response, "text") else "⚠️ AI error"

        save_message(sender, "ai", reply)
        return reply

    except Exception as e:
        print("AI Error:", e)
        return "⚠️ AI error"

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

            elif sender in user_style and user_style[sender]:
                send_whatsapp_message(sender, "🎨 Creating image...")

                style = user_style[sender]
                enhanced = enhance_prompt(text, style)

                img = generate_image(f"{enhanced}, {style}")

                if img:
                    send_whatsapp_image(sender, img)
                    os.remove(img)
                else:
                    send_whatsapp_message(sender, "❌ Failed")

                user_style[sender] = None
                return "OK", 200

            else:
                reply = get_ai_reply(sender, text)
                send_whatsapp_message(sender, reply)

    except Exception as e:
        print("Webhook Error:", e)

    return "OK", 200

# ================== RUN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
