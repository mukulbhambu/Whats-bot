from flask import Flask, request
import requests
import os
import redis
from rq import Queue
from google import genai

app = Flask(__name__)

# ================== CONFIG ==================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
REDIS_URL = os.environ.get("REDIS_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

# ================== REDIS QUEUE ==================
redis_conn = redis.from_url(REDIS_URL)
q = Queue(connection=redis_conn)

# ================== WHATSAPP ==================
def send_whatsapp_message(to, text):
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
        }
    )

# ================== AI CHAT ==================
def get_ai_reply(user_message):
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[{"text": user_message}]
        )
        return response.text if hasattr(response, "text") else "⚠️ Error"
    except:
        return "⚠️ AI error"

# ================== QUEUE TASK ==================
def generate_image_task(user, prompt):
    import urllib.parse
    import time

    try:
        url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
        res = requests.get(url)

        if res.status_code != 200:
            send_whatsapp_message(user, "❌ Image failed")
            return

        path = f"img_{int(time.time())}.png"
        with open(path, "wb") as f:
            f.write(res.content)

        # upload to whatsapp
        upload = requests.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            files={
                "file": ("image.png", open(path, "rb"), "image/png"),
                "messaging_product": (None, "whatsapp")
            }
        ).json()

        if "id" not in upload:
            send_whatsapp_message(user, "❌ Upload failed")
            return

        media_id = upload["id"]

        requests.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": user,
                "type": "image",
                "image": {"id": media_id}
            }
        )

        os.remove(path)

    except Exception as e:
        print("Worker Error:", e)

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
            text = msg["text"]["body"]

            # IMAGE COMMAND
            if text.lower().startswith("image"):
                prompt = text.replace("image", "").strip()

                send_whatsapp_message(sender, "🎨 Generating image...")

                # 🔥 ADD TO QUEUE
                q.enqueue(generate_image_task, sender, prompt)

                return "OK", 200

            # NORMAL CHAT
            reply = get_ai_reply(text)
            send_whatsapp_message(sender, reply)

    except Exception as e:
        print("Webhook Error:", e)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
