from flask import Flask, request
import requests
import time
from datetime import datetime
import os
import urllib.parse

from google import genai

app = Flask(__name__)

# ================== CONFIG ==================
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ================== CLIENT ==================
client = genai.Client(api_key=GEMINI_API_KEY)

# ================== MEMORY ==================
user_memory = {}
user_lang = {}
user_style = {}
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

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    with open(image_path, "rb") as f:
        files = {
            "file": ("image.png", f, "image/png"),
            "messaging_product": (None, "whatsapp")
        }
        res = requests.post(url, headers=headers, files=files).json()

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
        json=payload
    )

# ================== GREETING ==================
def get_greeting():
    hour = datetime.now().hour
    return "Good Morning ☀️" if hour < 12 else "Good Afternoon 🌤️" if hour < 18 else "Good Evening 🌙"

def send_welcome_message(to, name):
    send_whatsapp_message(to, f"{get_greeting()}, {name}! 👋\n\n🤖 AI chatbot ready!")

# ================== IMAGE UNDERSTANDING ==================
def download_image(media_id):
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    res = requests.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers).json()

    if "url" not in res:
        return None

    img = requests.get(res["url"], headers=headers)

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
            contents=[{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
                ]
            }]
        )

        if hasattr(response, "text") and response.text:
            return response.text
        elif hasattr(response, "candidates"):
            return response.candidates[0].content.parts[0].text

        return "⚠️ Couldn't understand the image."

    except Exception as e:
        print("Image Error:", e)
        return "⚠️ Couldn't understand the image."

# ================== IMAGE GENERATION ==================
STYLE_PROMPTS = {
    "anime": "anime style, vibrant colors, studio ghibli",
    "realistic": "ultra realistic, 4k, cinematic lighting",
    "cartoon": "cartoon style, disney pixar style",
    "cyberpunk": "cyberpunk, neon lights, futuristic city",
    "sketch": "pencil sketch, black and white",
    "fantasy": "fantasy art, magical, epic scene",
}

PROMPT_SUGGESTIONS = [
    "a dragon flying over mountains",
    "a futuristic city at night",
    "a cute cat astronaut",
    "a warrior in battlefield",
    "a girl standing on beach at sunset",
    "a cyberpunk hacker with neon lights"
]

def generate_image(prompt):
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024"

        res = requests.get(url)
        if res.status_code != 200:
            return None

        path = f"generated_{int(time.time())}.png"
        with open(path, "wb") as f:
            f.write(res.content)

        return path

    except Exception as e:
        print("Image Error:", e)
        return None

# ================== PROMPT ENHANCER ==================
def enhance_prompt(user_prompt, style=""):
    try:
        text = f"""
Enhance this image prompt.

Prompt: {user_prompt}
Style: {style}

Make it detailed, cinematic, high quality.
Return only 1 improved sentence.
"""

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[{"text": text}]
        )

        if hasattr(response, "text") and response.text:
            return response.text.strip()
        elif hasattr(response, "candidates"):
            return response.candidates[0].content.parts[0].text.strip()

        return user_prompt

    except:
        return user_prompt

# ================== AI CHAT ==================
def get_ai_reply(sender, user_message):

    user_memory.setdefault(sender, [])
    user_memory[sender].append({"role": "user", "content": user_message})
    user_memory[sender] = user_memory[sender][-MAX_HISTORY:]

    history = "\n".join(
        f"{'User' if m['role']=='user' else 'AI'}: {m['content']}"
        for m in user_memory[sender]
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[{"text": history}]
        )

        if hasattr(response, "text") and response.text:
            reply = response.text
        else:
            reply = "⚠️ AI error"

        user_memory[sender].append({"role": "ai", "content": reply})
        return reply

    except:
        return "⚠️ AI error"

# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Error", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = msg["from"]

        if "text" in msg:
            text = msg["text"]["body"].lower()

            # STYLE MENU
            if text == "image":
                send_whatsapp_message(sender,
                    "🎨 Choose style:\n1 Anime\n2 Realistic\n3 Cartoon\n4 Cyberpunk\n5 Sketch\n6 Fantasy"
                )
                return "OK", 200

            elif text in ["1","2","3","4","5","6"]:
                styles = ["anime","realistic","cartoon","cyberpunk","sketch","fantasy"]
                user_style[sender] = styles[int(text)-1]
                send_whatsapp_message(sender, "Send your prompt 🎨")
                return "OK", 200

            elif sender in user_style:
                send_whatsapp_message(sender, "🎨 Creating image...")

                style = STYLE_PROMPTS[user_style[sender]]
                enhanced = enhance_prompt(text, style)

                send_whatsapp_message(sender, f"✨ {enhanced}")

                img = generate_image(f"{enhanced}, {style}")

                if img:
                    send_whatsapp_image(sender, img)
                    os.remove(img)
                else:
                    send_whatsapp_message(sender, "Failed ❌")

                user_style[sender] = None
                return "OK", 200

            else:
                reply = get_ai_reply(sender, text)
                send_whatsapp_message(sender, reply)

    except Exception as e:
        print("Error:", e)

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
