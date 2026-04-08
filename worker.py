import time
import os
import urllib.parse
import psycopg2
import requests

DATABASE_URL = os.environ.get("DATABASE_URL")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

# ================= DB =================
def get_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn, conn.cursor()
    except:
        return None, None

# ================= WhatsApp =================
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
            }
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
            print("Upload failed:", res)
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

# ================= IMAGE =================
def generate_image(prompt):
    try:
        url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
        res = requests.get(url)

        if res.status_code != 200:
            return None

        path = f"img_{int(time.time())}.png"
        with open(path, "wb") as f:
            f.write(res.content)

        return path

    except Exception as e:
        print("Image error:", e)
        return None

# ================= WORKER =================
def process_tasks():
    print("🚀 Worker started...")

    while True:
        conn, cur = get_db()

        if not conn:
            time.sleep(2)
            continue

        cur.execute(
            "SELECT id, user_id, task_type, prompt FROM tasks WHERE status='pending' LIMIT 1"
        )

        task = cur.fetchone()

        if not task:
            cur.close()
            conn.close()
            time.sleep(2)
            continue

        task_id, user, task_type, prompt = task

        try:
            if task_type == "image":
                send_whatsapp_message(user, "🎨 Generating your image...")

                img = generate_image(prompt)

                if img:
                    send_whatsapp_image(user, img)
                    os.remove(img)
                else:
                    send_whatsapp_message(user, "❌ Image failed")

            cur.execute("UPDATE tasks SET status='done' WHERE id=%s", (task_id,))
            conn.commit()

        except Exception as e:
            print("Worker error:", e)

        cur.close()
        conn.close()

        time.sleep(1)

if __name__ == "__main__":
    process_tasks()
