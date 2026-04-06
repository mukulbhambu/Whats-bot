from flask import Flask, request
import requests
import time
from datetime import datetime
import os

from google import genai
from google.api_core import exceptions

from huggingface_hub import InferenceClient
from PIL import Image

app = Flask(__name__)

# ================== CONFIG ==================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HF_TOKEN = os.environ.get("HF_TOKEN")

# ================== CLIENTS ==================
client = genai.Client(api_key=GEMINI_API_KEY)

hf_client = InferenceClient(
    provider="nscale",
    api_key=HF_TOKEN
)

# ================== MEMORY ==================
user_memory = {}
user_lang = {}
MAX_HISTORY = 10


# ================== UTIL ==================
def typing_delay():
    time.sleep(0.5)


def send_whatsapp_message(to, text):
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


def send_whatsapp_image(to, image_path):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}"
    }

    # ✅ FIX: proper file handling + mime type
    with open(image_path, "rb") as f:
        files = {
            "file": ("image.png", f, "image/png"),
            "messaging_product": (None, "whatsapp")
        }

        res = requests.post(url, headers=headers, files=files).json()

    # 🔍 Debug
    print("Upload response:", res)

    if "id" not in res:
        print("Upload Failed:", res)
        return

    media_id = res["id"]

    # send image
    send_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id}
    }

    requests.post(send_url, headers=headers, json=payload)

# ================== GREETING ==================
def get_greeting():
    hour = datetime.now().hour
    return "Good Morning ☀️" if hour < 12 else "Good Afternoon 🌤️" if hour < 18 else "Good Evening 🌙"


def send_welcome_message(to, name):
    text = f"{get_greeting()}, {name}! 👋\n\n🤖 AI chatbot ready!"
    typing_delay()
    send_whatsapp_message(to, text)


# ================== IMAGE UNDERSTANDING ==================
def download_image(media_id):
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    res = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}",
        headers=headers
    ).json()

    if "url" not in res:
        print("Media Error:", res)
        return None

    media_url = res["url"]
    img = requests.get(media_url, headers=headers)

    file_path = "image.jpg"
    with open(file_path, "wb") as f:
        f.write(img.content)

    return file_path


def analyze_image(file_path, prompt="Describe this image"):
    try:
        with open(file_path, "rb") as f:
            image_bytes = f.read()

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return response.text

    except Exception as e:
        print("Image Error:", e)
        return "⚠️ Couldn't understand the image."


# ================== IMAGE GENERATION ==================
STYLE_PROMPTS = {
    "anime": "anime style, vibrant colors, studio ghibli, highly detailed",
    "realistic": "ultra realistic, 4k, cinematic lighting, high detail",
    "cartoon": "cartoon style, disney pixar style, colorful",
    "cyberpunk": "cyberpunk, neon lights, futuristic city, night",
    "sketch": "pencil sketch, black and white, detailed drawing",
    "fantasy": "fantasy art, magical, epic scene, detailed",
    "3d": "3D render, octane render, highly detailed",
}
def generate_image(prompt):
    import uuid

    # 🎨 detect style
    style_text = ""
    for style in STYLE_PROMPTS:
        if style in prompt:
            style_text = STYLE_PROMPTS[style]
            break

    # 🧠 enhance prompt
    final_prompt = f"{prompt}, {style_text}" if style_text else prompt

    print("Final Prompt:", final_prompt)
    for attempt in range(3):  # retry logic
        try:
            image = hf_client.text_to_image(
                final_prompt,
                model="stabilityai/stable-diffusion-xl-base-1.0"
            )

            file_path = "generated.png"
            image.save(file_path)  # PIL image save

            return file_path

        except Exception as e:
            print("HF Error:", e)
            time.sleep(5)

    return None


# ================== AI CHAT ==================
def get_ai_reply(sender, user_message):

    user_memory.setdefault(sender, [])
    user_memory[sender].append({"role": "user", "content": user_message})
    user_memory[sender] = user_memory[sender][-MAX_HISTORY:]

    lang = user_lang.get(sender, "auto")

    history = "\n".join(
        f"{'User' if m['role']=='user' else 'AI'}: {m['content']}"
        for m in user_memory[sender]
    )

    prompt = f"""
You are a smart WhatsApp chatbot.

Rules:
- Reply in user's language (Hindi / English / Hinglish)
- Language preference: {lang}
- Keep replies short and friendly
- Use emojis 😊

Conversation:
{history}
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt
        )

        reply = response.text
        user_memory[sender].append({"role": "ai", "content": reply})
        return reply

    except exceptions.ResourceExhausted:
        return "⚠️ Too many requests. Try later."
    except Exception as e:
        print("AI Error:", e)
        return "⚠️ AI error."


# ================== VERIFY ==================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Error", 403


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    try:
        value = data["entry"][0]["changes"][0]["value"]
        messages = value.get("messages")

        if not messages:
            return "OK", 200

        msg = messages[0]
        sender = msg["from"]

        contacts = value.get("contacts", [])
        name = contacts[0]["profile"]["name"].split()[0] if contacts else "User"

        # ===== TEXT =====
        if "text" in msg:
            text = msg["text"]["body"].lower()
            words = text.split()

            if text == "hindi":
                user_lang[sender] = "Hindi"
                return send_whatsapp_message(sender, "अब हिंदी में बात करेंगे 🇮🇳"), 200

            elif text == "english":
                user_lang[sender] = "English"
                return send_whatsapp_message(sender, "Switching to English 🇬🇧"), 200

            elif text == "reset":
                user_memory[sender] = []
                return send_whatsapp_message(sender, "🧠 Memory cleared"), 200

            elif any(w in ["menu", "start"] for w in words):
                send_welcome_message(sender, name)
                return "OK", 200

            # 🎨 IMAGE GENERATION
            elif any(w in text for w in ["generate", "draw", "image", "photo", "picture"]):
                typing_delay()
                send_whatsapp_message(sender, "🎨 Creating your image...")

                img_path = generate_image(text)

                if img_path:
                    send_whatsapp_image(sender, img_path)

                    time.sleep(1)  # ✅ give time to release file

                    try:
                        os.remove(img_path)
                    except Exception as e:
                        print("File delete error:", e)
                else:
                    send_whatsapp_message(sender, "⚠️ Image generation failed")

            # 🤖 AI CHAT
            else:
                typing_delay()
                reply = get_ai_reply(sender, text)
                send_whatsapp_message(sender, reply)

        # ===== IMAGE INPUT =====
        elif "image" in msg:
            media_id = msg["image"]["id"]

            send_whatsapp_message(sender, "📸 Analyzing image...")
            img_path = download_image(media_id)

            if not img_path:
                send_whatsapp_message(sender, "⚠️ Failed to download image")
                return "OK", 200

            caption = msg["image"].get("caption", "Describe this image")
            reply = analyze_image(img_path, caption)

            send_whatsapp_message(sender, reply)
            os.remove(img_path)

    except Exception as e:
        print("Webhook Error:", e)

    return "OK", 200


# ================== RUN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
