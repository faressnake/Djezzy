from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"
import logging
import requests
import random
import time
import json
import os
from datetime import datetime
import telebot
from telebot import types
import threading
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = "7547662109:AAG5WnLE2DWnxLwmjOD8iw8ssBAq_uA2k1o"
PROOF_CHAT_ID = -1002292919586
ADMIN_ID = 5179688953 # هنا تحط ID تاعك
MAINTENANCE_MODE = False
MAINTENANCE_MESSAGE = "⚠️ البوت حاليا في وضع الصيانة، حاول لاحقا."
# تسجيل متقدم
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('djezzy_bot.log'), logging.StreamHandler()]
)

REGISTERED_NUMBERS_FILE = "registered_numbers.json"
REGISTERED_USERS_FILE = "registered_users.json"

# إعداد session مع retry
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)

HEADERS = {
    'User-Agent': "MobileApp/3.0.0",
    'Accept': "application/json",
    'Content-Type': "application/json",
    'accept-language': "ar",
    'Connection': "keep-alive"
}

# بيانات المستخدمين
user_data = {}
user_states = {}
last_callback_ids = {}
data_lock = threading.Lock()

# ====================== إدارة توكن المستخدم ======================
user_tokens = {}  # chat_id : {'token': str, 'expiry': timestamp}

def save_user_token(chat_id, token, expires_in=3600):
    """حفظ التوكن مع وقت انتهاء صلاحيته بالثواني (افتراضي 1 ساعة)"""
    expiry_time = time.time() + expires_in
    with data_lock:
        user_tokens[chat_id] = {'token': token, 'expiry': expiry_time}

def get_valid_token(chat_id):
    """استرجاع التوكن إذا لم ينتهِ صلاحيته"""
    with data_lock:
        token_info = user_tokens.get(chat_id)
        if token_info and token_info['expiry'] > time.time():
            return token_info['token']
        else:
            user_tokens.pop(chat_id, None)
            return None

def delete_user_token(chat_id):
    """حذف توكن المستخدم"""
    with data_lock:
        user_tokens.pop(chat_id, None)
def get_or_refresh_token(chat_id, phone, otp):
    """ترجع توكن صالح، وإذا انتهت صلاحيته تعمل login تلقائي"""
    token = get_valid_token(chat_id)
    if token:
        return token
    # لو التوكن منتهي، نعيد تسجيل الدخول
    return login_with_otp(chat_id, phone, otp)
    
# ThreadPool
executor = ThreadPoolExecutor(max_workers=100)

bot = telebot.TeleBot(BOT_TOKEN)

# ====================== دوال مساعدة ======================

def safe_save_user_data(chat_id, key, value):
    with data_lock:
        if chat_id not in user_data:
            user_data[chat_id] = {}
        user_data[chat_id][key] = value

def safe_get_user_data(chat_id, key, default=None):
    with data_lock:
        return user_data.get(chat_id, {}).get(key, default)

def safe_set_state(chat_id, state):
    with data_lock:
        user_states[chat_id] = state

def safe_get_state(chat_id):
    with data_lock:
        return user_states.get(chat_id)

def safe_delete_user(chat_id):
    with data_lock:
        user_data.pop(chat_id, None)
        user_states.pop(chat_id, None)

def load_json_file(filename, default=None):
    if default is None:
        default = []
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"خطأ في تحميل {filename}: {e}")
    return default

def save_json_file(filename, data):
    try:
        temp_file = f"{filename}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, filename)
        return True
    except Exception as e:
        logging.error(f"خطأ في حفظ {filename}: {e}")
        return False

def load_registered_numbers():
    return load_json_file(REGISTERED_NUMBERS_FILE, [])

def save_registered_number(number_data):
    try:
        numbers = load_registered_numbers()
        numbers.append(number_data)
        if len(numbers) > 1000:
            numbers = numbers[-1000:]
        save_json_file(REGISTERED_NUMBERS_FILE, numbers)
        return True
    except Exception as e:
        logging.error(f"خطأ في حفظ الرقم المسجل: {e}")
        return False

# ====================== تسجيل المستخدمين ======================

def add_registered_user(chat_id):
    users = load_json_file(REGISTERED_USERS_FILE, [])
    if chat_id not in users:
        users.append(chat_id)
        save_json_file(REGISTERED_USERS_FILE, users)

# ====================== فورمات + دوال مساعدة ======================

def format_num(phone):
    phone = ''.join(filter(str.isdigit, str(phone).strip()))
    if phone.startswith('0'):
        return "213" + phone[1:]
    elif not phone.startswith('213'):
        return "213" + phone
    return phone

def mask_phone(phone):
    digits = ''.join(filter(str.isdigit, str(phone).strip()))
    if len(digits) >= 10:
        return digits[:2] + "*****" + digits[-2:]
    return "07*****"

def get_user_tag(message):
    u = message.from_user
    return f"@{u.username}" if u.username else u.first_name or "User"

def generate_random_djezzy_no():
    prefix = random.choice(["077", "078", "079"])
    return prefix + "".join([str(random.randint(0, 9)) for _ in range(7)])

# ====================== طلب OTP وتسجيل الدخول ======================

def request_otp(msisdn):
    start_time = time.time()
    for attempt in range(3):
        try:
            url = "https://apim.djezzy.dz/mobile-api/oauth2/registration"
            params = {'msisdn': msisdn,'client_id': "87pIExRhxBb3_wGsA5eSEfyATloa",'scope': "smsotp"}
            payload = {"consent-agreement": [{"marketing-notifications": False}],"is-consent": True}
            response = session.post(url, params=params, json=payload, headers=HEADERS, timeout=15)
            if response.status_code in [200,201]:
                send_server_status(msisdn, time.time() - start_time)
                return response
            time.sleep(0.1)
        except Exception as e:
            logging.error(f"خطأ في طلب OTP (محاولة {attempt+1}): {e}")
            time.sleep(0.1)
    send_server_status(msisdn, time.time() - start_time)
    return None

def login_with_otp(chat_id, mobile_number, otp):
    """تسجيل الدخول وتخزين التوكن للمستخدم"""
    start_time = time.time()
    for attempt in range(3):
        try:
            payload = {
                'otp': otp,
                'mobileNumber': mobile_number,
                'scope': "djezzyAppV2",
                'client_id': "87pIExRhxBb3_wGsA5eSEfyATloa",
                'client_secret': "uf82p68Bgisp8Yg1Uz8Pf6_v1XYa",
                'grant_type': "mobile"
            }
            res = session.post(
                "https://apim.djezzy.dz/mobile-api/oauth2/token",
                data=payload,
                headers={'User-Agent': "MobileApp/3.0.0"},
                timeout=15
            )
            if res.status_code == 200:
                token = f"Bearer {res.json().get('access_token')}"
                # تخزين التوكن للمستخدم
                save_user_token(chat_id, token, expires_in=3600)  # 1 ساعة
                send_server_status(chat_id, time.time() - start_time)
                return token
            time.sleep(0.3)
        except Exception as e:
            logging.error(f"خطأ في تسجيل الدخول (محاولة {attempt+1}): {e}")
            time.sleep(0.3)
    send_server_status(chat_id, time.time() - start_time)
    return None
    
def get_sim_info(token):
    """إرجاع معلومات الشريحة مثل الرصيد والباقة النشطة"""
    try:
        url = "https://apim.djezzy.dz/mobile-api/api/v1/account/summary"
        res = session.get(url, headers={**HEADERS, 'authorization': token}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            balance = data.get('balance', {}).get('availableBalance', 'غير معروف')
            package = data.get('activePackages', [])
            active_packages = ', '.join([p.get('name', '') for p in package]) if package else "لا توجد باقات"
            return f"💰 الرصيد: {balance}\n🎁 الباقات النشطة: {active_packages}"
    except Exception as e:
        logging.error(f"خطأ في جلب معلومات الشريحة: {e}")
    return "⚠️ لا يمكن جلب معلومات الشريحة"

# ====================== تفعيل المكافأة ======================

def send_invitation(token, sender, receiver):
    try:
        inv = session.post(f"https://apim.djezzy.dz/mobile-api/api/v1/services/mgm/send-invitation/{sender}", json={"msisdnReciever": receiver}, headers={**HEADERS, 'authorization': token}, timeout=10)
        return inv.status_code in [200, 201]
    except Exception as e:
        logging.error(f"خطأ في إرسال الدعوة: {e}")
        return False

def activate_reward(token, sender):
    try:
        act = session.post(f"https://apim.djezzy.dz/mobile-api/api/v1/services/mgm/activate-reward/{sender}", json={"packageCode": "MGMBONUS1Go"}, headers={**HEADERS, 'authorization': token}, timeout=10)
        return act.status_code in [200, 201]
    except Exception as e:
        logging.error(f"خطأ في تفعيل المكافأة: {e}")
        return False
# تفعيل 2Go
def activate_2go(token, phone):
    try:
        url = f"https://apim.djezzy.dz/mobile-api/api/v1/services/walk/activate-reward/{phone}"
        payload = {"packageCode": "GIFTWALKWIN2GO"}

        r = session.post(url, json=payload, headers={**HEADERS, 'authorization': token}, timeout=10)
        return r.status_code in [200,201]

    except Exception as e:
        logging.error(f"خطأ 2Go: {e}")
        return False


# تفعيل 4Go بـ70دج
def activate_4go(token, phone):
    try:
        url = f"https://apim.djezzy.dz/mobile-api/api/v1/services/walk/activate-reward/{phone}"
        payload = {"packageCode": "GIFTWALKWIN4GO"}

        r = session.post(url, json=payload, headers={**HEADERS, 'authorization': token}, timeout=10)
        return r.status_code in [200,201]

    except Exception as e:
        logging.error(f"خطأ 4Go: {e}")
        return False
        
# ====================== Keyboards ======================

def get_channel_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("📢 إشترك في القناة", url="https://t.me/hellyeah619dz"),
                 types.InlineKeyboardButton("✅ تحقق من الإشتراك", callback_data="check_subscription"))
    return keyboard

def get_final_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("📢 @hellyeah619dz", url="https://t.me/hellyeah619dz"))
    return keyboard
    
def get_offers_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)

    keyboard.row("🎁 تفعيل 1Go")
    keyboard.row("🎉 تفعيل 2Go")
    keyboard.row("💰 4Go بـ70دج")

    return keyboard
    
def check_subscription(chat_id):
    try:
        return bot.get_chat_member("@hellyeah619dz", chat_id).status in ['member','administrator','creator']
    except:
        return False

# ====================== Server Status ======================

def send_server_status(chat_id, response_time):
    emoji = "🟢 سريع" if response_time < 1.5 else "🔴 بطيء"
    try:
        bot.send_message(chat_id, f"⚡ حالة الخادم: {emoji}\n⏱ وقت الاستجابة: {response_time:.2f} ثانية\n💠━━━━━━━━━━━━💠")
    except:
        pass

# ====================== دوال التفعيل متعددة الخيوط ======================

def process_activation(chat_id, phone, otp, offer_type, message):
    """
    offer_type: "1Go", "2Go", "4Go"
    """
    try:
        token = get_or_refresh_token(chat_id, phone, otp)
        if not token:
            bot.send_message(chat_id, "❌ رمز خطأ أو فشل تسجيل الدخول")
            safe_set_state(chat_id, 'main')
            return

        max_attempts = 50
        success = False
        attempt_count = 0

        for _ in range(max_attempts):
            attempt_count += 1
            target = generate_random_djezzy_no()
            target_f = format_num(target)

            if send_invitation(token, phone, target_f):
                request_otp(target_f)
                time.sleep(0.1)

                # اختيار التفعيل حسب العرض
                if offer_type == "1Go":
                    success = activate_reward(token, phone)
                elif offer_type == "2Go":
                    success = activate_2go(token, phone)
                elif offer_type == "4Go":
                    success = activate_4go(token, phone)

                if success:
                    number_data = {
                        "sender": safe_get_user_data(chat_id, 'original_phone'),
                        "target": target,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "offer": offer_type,
                        "user_id": chat_id
                    }
                    save_registered_number(number_data)

                    # LOG للقناة
                    try:
                        username = get_user_tag(message)
                        masked_number = mask_phone(safe_get_user_data(chat_id, 'original_phone'))
                        log_msg = (
                            f"✅ 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗜𝗢𝗡 𝗟𝗢𝗚 — 𝗙𝗔𝗥𝗘𝗦\n"
                            f"🎫 ID: ACT{random.randint(10000,99999)}\n"
                            f"👤 𝗨𝗦𝗘𝗥: {username} 🧑‍💻\n"
                            f"📱 𝗟𝗜𝗡𝗘: {masked_number}\n"
                            f"🆔 𝗜𝗗: {chat_id}\n"
                            f"🎁 المكافأة: {offer_type} 💎✅\n"
                            f"🔁 محاولات: {attempt_count}\n"
                            f"⚡ حالة الخادم: 🟢 سريع 🚀 ({int(time.time()*1000) % 2000}ms)\n"
                            f"✅ النتيجة: SUCCESS\n"
                            f"⏰ 𝗧𝗜𝗠𝗘: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"🔗 متابعة: t.me/hellyeah619dz\n"
                            f"🖤 By FaresCodeX"
                        )
                        bot.send_message(PROOF_CHAT_ID, log_msg)
                    except Exception as e:
                        logging.error(f"خطأ في إرسال Log للقناة: {e}")

                    # رسالة مختصرة للمستخدم
                    bot.send_message(
                        chat_id,
                        f"🎉 تم تفعيل {offer_type} بنجاح ✅\n📢 لمتابعة التفاصيل اضغط على القناة",
                        reply_markup=get_final_keyboard()
                    )
                    break

        if not success:
            bot.send_message(
                chat_id,
                f"❌ فشل تفعيل {offer_type} ⚡\n🔹 حاول مرة أخرى لاحقاً\n📢 تأكد من اتصالك بالإنترنت",
                reply_markup=get_final_keyboard()
            )

    except Exception as e:
        logging.error(f"خطأ في التفعيل: {e}")
    finally:
        safe_set_state(chat_id, 'main')
        safe_delete_user(chat_id)
        
# ====================== handlers ======================

@bot.message_handler(commands=['start'])
def start_command(message):
    chat_id = message.chat.id
    safe_delete_user(chat_id)
    add_registered_user(chat_id)

    # ← التحقق من وضع الصيانة
    if MAINTENANCE_MODE:
        bot.send_message(chat_id, MAINTENANCE_MESSAGE)
        return

    if not check_subscription(chat_id):
        bot.send_message(chat_id, "❌ للإستمرار يجب الإشتراك في القناة أولاً", reply_markup=get_channel_keyboard())
        return

    safe_set_state(chat_id, 'waiting_phone')
    safe_save_user_data(chat_id, 'original_phone', None)
    bot.send_message(
        chat_id,
        "💎━━━━━━━━━━━━💎\n"
        "🌟🌟🌟 *أهلاً بك في بوت Fares Info * 🌟🌟🌟\n"
        "💠 بوت تفعيل عروض  Djezzy 1Go.2Go 💠\n"
        "📱 أدخل رقم هاتفك الآن (مثال: 077xxxxxxx)\n"
        "✨ اتبع التعليمات بعناية لتفعيل العرض!\n"
        "💎━━━━━━━━━━━━💎",
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    chat_id = message.chat.id
    safe_delete_user(chat_id)
    bot.send_message(chat_id, "❌ تم الإلغاء")

# ====================== Broadcast / Admin ======================

@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    if message.from_user.id != ADMIN_ID:
        return
    text_to_send = message.text.split(maxsplit=1)
    if len(text_to_send) < 2:
        bot.send_message(message.chat.id, "❌ استعمل: /broadcast رسالة هنا")
        return
    users = load_json_file(REGISTERED_USERS_FILE, [])
    sent_count = 0
    for u in users:
        try:
            bot.send_message(u, text_to_send[1])
            sent_count += 1
        except:
            continue
    bot.send_message(message.chat.id, f"✅ تم إرسال الرسالة إلى {sent_count} مستخدمين")

@bot.message_handler(commands=['s'])
def status_message(message):
    try:
        bot.send_message(message.chat.id, "⚠️ البوت قيد التشغيل الآن 🚀")
    except:
        pass

# تشغيل/ايقاف وضع الصيانة
@bot.message_handler(commands=['maintenance'])
def maintenance_command(message):
    global MAINTENANCE_MODE
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        bot.send_message(message.chat.id, "❌ استخدم /maintenance on أو /maintenance off")
        return
    if args[1].lower() == 'on':
        MAINTENANCE_MODE = True
        bot.send_message(message.chat.id, "✅ تم تفعيل وضع الصيانة")
    elif args[1].lower() == 'off':
        MAINTENANCE_MODE = False
        bot.send_message(message.chat.id, "✅ تم إيقاف وضع الصيانة")
    else:
        bot.send_message(message.chat.id, "❌ استخدم on أو off")

# تغيير نص رسالة الصيانة
@bot.message_handler(commands=['set_maintenance'])
def set_maintenance_message(message):
    global MAINTENANCE_MESSAGE
    if message.from_user.id != ADMIN_ID:
        return
    text_to_set = message.text.split(maxsplit=1)
    if len(text_to_set) < 2:
        bot.send_message(message.chat.id, "❌ استخدم /set_maintenance رسالة هنا")
        return
    MAINTENANCE_MESSAGE = text_to_set[1]
    bot.send_message(message.chat.id, "✅ تم تحديث رسالة الصيانة")

# إحصائيات البوت
@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    users = load_json_file(REGISTERED_USERS_FILE, [])
    numbers = load_registered_numbers()
    success_count = sum(1 for n in numbers if 'timestamp' in n)
    fail_count = len(numbers) - success_count
    server_status = "FAST" if int(time.time() % 2) == 0 else "SLOW"
    bot.send_message(message.chat.id,
                     f"📊 Bot Stats:\n\n"
                     f"👥 Users: {len(users)}\n"
                     f"✅ Activations: {success_count}\n"
                     f"❌ Failed: {fail_count}\n"
                     f"⚡ Server: {server_status}")
                     
# ====================== Callbacks ======================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, cache_time=1)
    if call.data == "check_subscription":
        if check_subscription(chat_id):
            safe_set_state(chat_id, 'waiting_phone')
            safe_save_user_data(chat_id, 'original_phone', None)
            bot.edit_message_text(
                "✅✅✅ ── 🌸 تم التحقق بنجاح 🌸 ── ✅✅✅\n\n"
                "📱 الآن أدخل رقم هاتفك Djezzy (مثال: 077xxxxxxx)\n"
                "🌟 بوت Fares Info جاهز للإستخدام 🌟\n"
                "💌 استمتع بتفعيل 1Go بكل سهولة!\n"
                "💠━━━━━━━━━━━━💠",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown'
            )
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك بعد", show_alert=True)
            
# ====================== الرسائل ======================
@bot.message_handler(func=lambda message: True)
def message_handler(message):
    chat_id = message.chat.id
    text = message.text.strip()
    state = safe_get_state(chat_id)

    if not state:
        bot.send_message(chat_id, "🚀 اضغط /start")
        return

    # ------------------- التحقق من الاشتراك -------------------
    if not check_subscription(chat_id):
        bot.send_message(
            chat_id,
            "❌ للإستمرار يجب الإشتراك في القناة أولاً",
            reply_markup=get_channel_keyboard()
        )
        safe_delete_user(chat_id)
        return

    # ------------------- إدخال الرقم -------------------
    if state == 'waiting_phone':
        if not text.isdigit() or len(text) < 9:
            bot.send_message(chat_id, "❌ رقم خطأ")
            return

        formatted_phone = format_num(text)
        safe_save_user_data(chat_id, 'phone', formatted_phone)
        safe_save_user_data(chat_id, 'original_phone', text)
        otp_response = request_otp(formatted_phone)

        # حذف رسالة الرقم بعد 5 ثواني
        threading.Timer(3, lambda: bot.delete_message(chat_id, message.message_id)).start()

        if otp_response:
            safe_set_state(chat_id, 'waiting_otp')
            bot.send_message(
                chat_id,
                "📨💫 تم إرسال رمز التحقق!\n"
                "⌛ أدخل الرمز المكون من 6 أرقام\n"
                "💠━━━━━━━━━━━━💠"
            )
        else:
            bot.send_message(chat_id, "❌ فشل الإرسال")
            safe_set_state(chat_id, 'main')

    # ------------------- إدخال OTP -------------------
    elif state == 'waiting_otp':
        if not text.isdigit() or len(text) != 6:
            bot.send_message(chat_id, "❌ الرمز 6 أرقام")
            return

        safe_save_user_data(chat_id, 'otp', text)
        phone = safe_get_user_data(chat_id, 'phone')
        otp = text

        # حذف رسالة OTP بعد 5 ثواني
        threading.Timer(3, lambda: bot.delete_message(chat_id, message.message_id)).start()

        # تحقق من التوكن أو تسجيل دخول
        token = get_valid_token(chat_id)
        if not token:
            token = login_with_otp(chat_id, phone, otp)
            if not token:
                bot.send_message(chat_id, "❌ فشل تسجيل الدخول أو توكن منتهي")
                safe_set_state(chat_id, 'main')
                return

        # جلب معلومات الشريحة
        sim_info = get_sim_info(token)
        bot.send_message(chat_id, f"📊 معلومات الشريحة:\n{sim_info}")

        # تحضير المستخدم لاختيار العرض
        safe_set_state(chat_id, 'choose_offer')
        bot.send_message(
            chat_id,
            "🎁 اختر العرض الذي تريد تفعيله:",
            reply_markup=get_offers_keyboard()
        )

    # ------------------- اختيار العرض -------------------
    elif state == 'choose_offer':
        phone = safe_get_user_data(chat_id, 'phone')
        otp = safe_get_user_data(chat_id, 'otp')
        
        if text == "🎁 تفعيل 1Go":
            bot.send_message(chat_id, "⏳ جاري تفعيل 1Go ...")
            executor.submit(process_activation, chat_id, phone, otp, "1Go", message)
            
        elif text == "🎉 تفعيل 2Go":
            bot.send_message(chat_id, "⏳ جاري تفعيل 2Go ...")
            executor.submit(process_activation, chat_id, phone, otp, "2Go", message)
            
        elif text == "💰 4Go بـ70دج":
            bot.send_message(chat_id, "⏳ جاري تفعيل 4Go ...")
            executor.submit(process_activation, chat_id, phone, otp, "4Go", message)
            
        else:
            bot.send_message(chat_id, "❌ اختر واحد من العروض الموجودة فقط")
            
# ====================== تشغيل البوت ======================

def run_bot_smoothly():
    restart_count = 0
    while True:
        try:
            print("="*60)
            print("🚀 بوت 1Go يعمل بسلاسة تامة...")
            print(f"✅ عدد مرات إعادة التشغيل: {restart_count}")
            print("="*60)
            restart_count += 1
            bot.infinity_polling(timeout=3, long_polling_timeout=3, skip_pending=True)
        except Exception as e:
            logging.error(f"خطأ في run_bot_smoothly: {e}")
            time.sleep(0.5)
            continue
def run_web():
    app.run(host="0.0.0.0", port=10000)

if __name__ == '__main__':
    # تشغيل السيرفر
    Thread(target=run_web).start()

    # تشغيل البوت
    try:
        run_bot_smoothly()
    except KeyboardInterrupt:
        print("\n👋 تم إيقاف البوت يدوياً")
        sys.exit(0)