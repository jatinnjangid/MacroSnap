import json
import urllib.parse
import requests
from google import genai
from google.genai import types
import streamlit as st

from prompts import SYSTEM_PROMPT, WELCOME_MESSAGE_TEMPLATE, SUMMARY_REQUEST_PROMPT_TEMPLATE


GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = "MacroSnapjatinbot"

TWILIO_ACCOUNT_SID = st.secrets.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = st.secrets.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = st.secrets.get("TWILIO_WHATSAPP_FROM", "")

# Reliable production models with automatic fallback
PRIMARY_MODEL = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"


@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=GEMINI_API_KEY)


gemini_client = get_gemini_client()


def init_chat_session(model_name=PRIMARY_MODEL):
    return gemini_client.chats.create(
        model=model_name,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )


def send_telegram(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        return False, "Telegram Bot Token is not configured."
    try:
        raw_id = str(chat_id).strip()
        final_chat_id = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(
            url,
            json={"chat_id": final_chat_id, "text": text},
            timeout=10,
        )
        data = response.json()
        if data.get("ok"):
            return True, "Message sent to Telegram!"
        else:
            description = data.get("description", "Failed to send Telegram message.")
            if "chat not found" in description.lower():
                return False, (
                    f"Chat not found for your account. Please tap [@{BOT_USERNAME}](https://t.me/{BOT_USERNAME}) "
                    "and press Start so the bot has permission to message you."
                )
            return False, description
    except Exception as error:
        return False, str(error)


def send_whatsapp_twilio(phone, text):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        return False, "Twilio WhatsApp sender credentials not fully configured."
    try:
        from twilio.rest import Client
        clean_phone = "".join(filter(lambda c: c.isdigit() or c == "+", phone))
        if not clean_phone.startswith("+"):
            clean_phone = f"+{clean_phone}"
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=text,
            from_=f"whatsapp:{TWILIO_WHATSAPP_FROM}",
            to=f"whatsapp:{clean_phone}",
        )
        return True, f"Sent via WhatsApp (SID: {message.sid})"
    except Exception as err:
        return False, str(err)


def get_latest_telegram_chat():
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("ok") and data.get("result"):
            for update in reversed(data["result"]):
                if "message" in update and "chat" in update["message"]:
                    chat = update["message"]["chat"]
                    sender = update["message"].get("from", {})
                    return {
                        "chat_id": str(chat["id"]),
                        "first_name": chat.get("first_name") or sender.get("first_name", ""),
                        "username": chat.get("username") or sender.get("username", ""),
                    }
        return None
    except Exception:
        return None


def render_message(message):
    with st.chat_message(message["role"]):
        if message["kind"] == "text":
            st.write(message["content"])
        elif message["kind"] == "image":
            st.image(message["content"])


def add_message(role, kind, content):
    st.session_state.messages.append({"role": role, "kind": kind, "content": content})
    render_message(st.session_state.messages[-1])


def ask_gemini(parts):
    if "chat" not in st.session_state or st.session_state.chat is None:
        st.session_state.chat = init_chat_session(PRIMARY_MODEL)

    try:
        response = st.session_state.chat.send_message(parts)
        return response.text
    except Exception:
        try:
            st.session_state.chat = init_chat_session(FALLBACK_MODEL)
            response = st.session_state.chat.send_message(parts)
            return response.text
        except Exception as fallback_error:
            return f"Sorry, something went wrong: {fallback_error}"


if "messages" not in st.session_state:
    st.session_state.messages = []


# ==========================================
# STEP 1: CLIENT SETUP (SaaS Onboarding)
# ==========================================

if not st.session_state.get("onboarded", False):
    st.set_page_config(page_title="MacroSnap — Client Setup", page_icon="🥗", layout="centered")

    # If in the middle of connecting Telegram
    if st.session_state.get("awaiting_telegram_link", False):
        st.title("📲 Connect Telegram")
        st.write(f"Hi **{st.session_state.get('name', 'there')}**! To receive your nutrition summaries automatically:")

        st.markdown(
            f"""
            1. Tap the button below to open our bot on Telegram.
            2. Press **Start** (or send any message like `hi`).
            3. Return here and click **Verify Connection**.
            """
        )

        st.link_button(
            f"🤖 Open @{BOT_USERNAME} on Telegram",
            f"https://t.me/{BOT_USERNAME}",
            use_container_width=True,
        )

        st.write("")

        col_v, col_s = st.columns([1, 1])
        with col_v:
            if st.button("✅ Verify Connection", use_container_width=True, type="primary"):
                latest = get_latest_telegram_chat()
                if latest:
                    st.session_state.telegram_chat_id = latest["chat_id"]
                    st.session_state.telegram_username = latest.get("username", "")
                    st.session_state.onboarded = True
                    st.session_state.awaiting_telegram_link = False
                    st.success(f"Connected as @{latest.get('username') or latest.get('first_name')}!")
                    st.rerun()
                else:
                    st.error(f"No message found yet. Please tap Start in @{BOT_USERNAME} first, then click Verify!")
        with col_s:
            if st.button("Skip for now →", use_container_width=True):
                st.session_state.onboarded = True
                st.session_state.awaiting_telegram_link = False
                st.rerun()

        st.stop()

    # Client Setup Main Form
    st.title("🥗 MacroSnap")
    st.subheader("Welcome to MacroSnap 🥗")
    st.caption("Enter your details to start receiving your nutrition summaries.")

    with st.form("client_setup_form"):
        name = st.text_input("👤 Your Name", placeholder="Rahul Sharma")
        phone = st.text_input("📱 Mobile / WhatsApp Number", placeholder="+91 98765 XXXXX")
        delivery_pref = st.radio(
            "📨 Delivery Preference",
            options=["Telegram", "WhatsApp"],
            horizontal=True,
            help="Choose how you'd like to receive your daily nutrition breakdown."
        )

        submitted = st.form_submit_button("Connect & Continue 🚀", use_container_width=True)

    st.caption("🔒 *Your details are securely stored and used only for delivering your MacroSnap summaries.*")

    if submitted:
        if not name.strip():
            st.warning("Please enter your name.")
        elif not phone.strip():
            st.warning("Please enter your mobile number.")
        else:
            st.session_state.name = name.strip()
            st.session_state.phone = phone.strip()
            st.session_state.delivery_pref = delivery_pref
            st.session_state.chat = init_chat_session(PRIMARY_MODEL)
            st.session_state.messages = []

            if delivery_pref == "Telegram":
                # Check if they have already sent a message recently
                latest = get_latest_telegram_chat()
                if latest:
                    st.session_state.telegram_chat_id = latest["chat_id"]
                    st.session_state.telegram_username = latest.get("username", "")
                    st.session_state.onboarded = True
                    st.rerun()
                else:
                    st.session_state.awaiting_telegram_link = True
                    st.rerun()
            else:
                st.session_state.onboarded = True
                st.rerun()

    st.stop()


# ==========================================
# STEP 2: CLIENT DASHBOARD & TRACKER
# ==========================================

# Sidebar: Client Profile & Preferences
with st.sidebar:
    st.header("👤 Client Profile")
    st.write(f"**Name:** {st.session_state.get('name', 'Client')}")
    st.write(f"**Mobile:** `{st.session_state.get('phone', 'Not set')}`")
    pref = st.session_state.get("delivery_pref", "Telegram")
    st.write(f"**Delivery:** {pref}")

    if pref == "Telegram":
        if st.session_state.get("telegram_chat_id"):
            username_tag = f"(@{st.session_state.telegram_username})" if st.session_state.get("telegram_username") else ""
            st.success(f"Telegram Connected ✅ {username_tag}")
        else:
            st.warning("Telegram Not Linked ⚠️")
            st.link_button(f"Open @{BOT_USERNAME}", f"https://t.me/{BOT_USERNAME}", use_container_width=True)
            if st.button("🔗 Link Telegram", use_container_width=True):
                latest = get_latest_telegram_chat()
                if latest:
                    st.session_state.telegram_chat_id = latest["chat_id"]
                    st.session_state.telegram_username = latest.get("username", "")
                    st.success("Linked successfully!")
                    st.rerun()
                else:
                    st.error("No recent message found. Tap Start on Telegram first!")

    with st.expander("✏️ Edit Profile", expanded=False):
        new_name = st.text_input("Name", value=st.session_state.get("name", ""), key="edit_name")
        new_phone = st.text_input("Mobile", value=st.session_state.get("phone", ""), key="edit_phone")
        new_pref = st.radio(
            "Delivery Method",
            ["Telegram", "WhatsApp"],
            index=0 if pref == "Telegram" else 1,
            key="edit_pref",
            horizontal=True
        )

        if st.button("Save Profile", use_container_width=True):
            st.session_state.name = new_name.strip()
            st.session_state.phone = new_phone.strip()
            st.session_state.delivery_pref = new_pref
            st.success("Profile updated!")
            st.rerun()

    st.divider()
    if st.button("🚪 Switch Client / Reset", use_container_width=True):
        st.session_state.clear()
        st.rerun()


# Top Navigation / Action Bar
header_col, action_col = st.columns([5, 2], vertical_alignment="center")

with header_col:
    st.title("🥗 MacroSnap")

client_name = st.session_state.get("name", "there")
client_pref = st.session_state.get("delivery_pref", "Telegram")
client_phone = st.session_state.get("phone", "")

with action_col:
    send_label = "📤 Send Summary"
    if st.button(send_label, use_container_width=True, type="primary"):
        if len(st.session_state.messages) <= 1:
            st.info("💡 Please log at least one meal below so I have something to summarize!")
        else:
            with st.spinner("Generating your daily summary..."):
                summary_prompt = SUMMARY_REQUEST_PROMPT_TEMPLATE.format(name=client_name)
                summary_text = ask_gemini([summary_prompt])

            if client_pref == "Telegram":
                chat_id = st.session_state.get("telegram_chat_id")
                if not chat_id:
                    # Try one quick auto-detect
                    latest = get_latest_telegram_chat()
                    if latest:
                        chat_id = latest["chat_id"]
                        st.session_state.telegram_chat_id = chat_id
                        st.session_state.telegram_username = latest.get("username", "")

                if chat_id:
                    success, info = send_telegram(chat_id, summary_text)
                    if success:
                        st.success("✅ Daily summary sent to your Telegram!")
                    else:
                        st.error(f"Couldn't send: {info}")
                else:
                    st.warning(f"⚠️ Telegram is not linked yet! Open [@{BOT_USERNAME}](https://t.me/{BOT_USERNAME}), send `hello`, then click 'Link Telegram' in the sidebar.")
            else:
                # WhatsApp Delivery
                clean_phone = "".join(filter(lambda c: c.isdigit(), client_phone))
                encoded_msg = urllib.parse.quote(summary_text)
                wa_url = f"https://api.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"

                sent_twilio = False
                if TWILIO_WHATSAPP_FROM:
                    sent_twilio, twilio_info = send_whatsapp_twilio(client_phone, summary_text)

                if sent_twilio:
                    st.success(f"✅ Summary sent via WhatsApp to {client_phone}!")
                else:
                    st.success(f"✅ Summary ready for {client_name}!")
                    st.link_button("💬 Send to WhatsApp Now", wa_url, use_container_width=True)

            with st.expander("👀 View Summary Preview", expanded=False):
                st.text(summary_text)


# Status Ribbon
badge_status = "Connected ✅" if (client_pref == "WhatsApp" or st.session_state.get("telegram_chat_id")) else "Link required ⚠️"
st.caption(f"👤 **{client_name}** • 📱 `{client_phone}` • 📨 Delivering via **{client_pref}** ({badge_status})")


# Conversation History
if not st.session_state.messages:
    welcome_msg = WELCOME_MESSAGE_TEMPLATE.format(name=client_name)
    add_message("assistant", "text", welcome_msg)
else:
    for message in st.session_state.messages:
        render_message(message)


# Meal Tracking Chat Input
user_input = st.chat_input(
    "Ask a question, or attach a photo of your meal...",
    accept_file=True,
    file_type=["jpg", "jpeg", "png"],
)

if user_input:
    photo = user_input.files[0] if user_input.files else None
    text = user_input.text
    parts = []

    if photo is not None:
        photo_bytes = photo.getvalue()
        add_message("user", "image", photo_bytes)
        parts.append(types.Part.from_bytes(data=photo_bytes, mime_type=photo.type))
    if text:
        add_message("user", "text", text)
        parts.append(text)
    elif photo is not None:
        parts.append("What is this meal? Give me the calories and macros.")

    with st.spinner("Analyzing nutrition..."):
        answer = ask_gemini(parts)
    add_message("assistant", "text", answer)
    st.rerun()
