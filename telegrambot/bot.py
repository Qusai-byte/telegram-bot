import os
import csv
import re
import json
import logging
from datetime import datetime
from typing import List, Dict

from dotenv import load_dotenv
import requests

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ============ إعدادات عامة ============
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COMPANY_NAME = os.getenv("COMPANY_NAME", "شركة برمجيات")
LEADS_CSV = "leads.csv"

# إعداد مزود الذكاء الاصطناعي
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# ============ بيانات خدمات الشركة (عدِّلها بحرية) ============
SERVICES = {
    "web": {
        "name": "تطوير مواقع",
        "desc": "مواقع سريعة آمنة (Next.js/Django) مع لوحة تحكم وتهيئة SEO.",
        "starts_from": "1000$"
    },
    "mobile": {
        "name": "تطبيقات جوال",
        "desc": "تطبيقات iOS/Android (Flutter/React Native) مع نشر للمتاجر.",
        "starts_from": "1500$"
    },
    "ai": {
        "name": "حلول ذكاء اصطناعي",
        "desc": "بوتات محادثة، تلخيص تلقائي، تصنيف بيانات، وذكاء مدمج في المنتجات.",
        "starts_from": "1200$"
    },
    "uiux": {
        "name": "UI/UX",
        "desc": "تصميم تجارب استخدام حديثة مع نماذج أولية تفاعلية.",
        "starts_from": "600$"
    },
    "maintenance": {
        "name": "صيانة ودعم",
        "desc": "مراقبة، نسخ احتياطي، تحديثات أمنية، ودعم شهري.",
        "starts_from": "200$/شهر"
    },
}

# حالات محادثة جمع البيانات
(COLLECT_NAME, COLLECT_EMAIL, COLLECT_NOTE) = range(3)

# ============ أدوات مساعدة ============
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

def ensure_leads_csv():
    if not os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "name", "email", "need", "from", "username", "note"])  # header


def save_lead(name: str, email: str, need: str, update: Update, note: str = ""):
    ensure_leads_csv()
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.utcnow().isoformat(),
            name,
            email,
            need,
            f"tg://user?id={update.effective_user.id}",
            update.effective_user.username or "",
            note,
        ])


def add_user_memory(context: ContextTypes.DEFAULT_TYPE, role: str, content: str, limit: int = 6):
    mem: List[Dict] = context.user_data.get("mem", [])
    mem.append({"role": role, "content": content})
    context.user_data["mem"] = mem[-limit:]


# ============ الذكاء الاصطناعي ============

def ai_generate_reply(user_text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    يولد رد ذكي باستخدام:
    - OpenAI (إذا توفر مفتاح)
    - أو Ollama محلي (USE_OLLAMA=true)
    - وإلا يقدّم ردودًا ذكية مبسطة دون مزود خارجي.
    """
    system_prompt = f"""
    أنت مساعد احترافي يتحدث العربية بطلاقة، يعمل لدى {COMPANY_NAME}.
    مهامك:
    - فهم احتياج العميل واقتراح حلول برمجية عملية مع خطوات واضحة.
    - توضيح التقنيات المناسبة (مثال: React/Next.js، Django/FastAPI، Flutter، PostgreSQL، Docker).
    - إذا طلب العميل سعراً، أعطه مدى تقريبي واطلب تفاصيل أكثر.
    - شجّعه على مشاركة بريده لإرسال عرض مفصل عبر الأمر /contact.
    """.strip()

    # اجمع الذاكرة القصيرة
    mem: List[Dict] = context.user_data.get("mem", [])
    messages = [{"role": "system", "content": system_prompt}] + mem + [
        {"role": "user", "content": user_text}
    ]

    # 1) OpenAI
    if OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.5,
                max_tokens=600,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"OpenAI error: {e}")

    # 2) Ollama محلي (مجاني)
    if USE_OLLAMA:
        try:
            r = requests.post(
                "http://127.0.0.1:11434/api/chat",
                json={"model": OLLAMA_MODEL, "messages": messages},
                timeout=60,
            )
            data = r.json()
            # صيغة رد Ollama المتدفق قد تكون متعددة — هنا نفترض التجميعة النهائية
            if isinstance(data, dict) and "message" in data and "content" in data["message"]:
                return data["message"]["content"].strip()
            # fallback بسيط
            return json.dumps(data)[:800]
        except Exception as e:
            logger.warning(f"Ollama error: {e}")

    # 3) وضع مبسط بدون مزود خارجي
    return (
        "> رد ذكي مبسّط (بدون API)\n"
        "فهمت طلبك: " + user_text + "\n\n"
        "يمكننا تنفيذ الحل عبر: Frontend (Next.js) + Backend (FastAPI) + قاعدة بيانات PostgreSQL + استضافة Docker.\n"
        "للحصول على عرض دقيق اكتب /contact وارسل بريدك ونبذة عن مشروعك."
    )


# ============ الأوامر والمعالجات ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user_memory(context, "assistant", "مرحبا! أنا بوت الشركة.")
    text = (
        f"أهلًا بك مع {COMPANY_NAME}!\n\n"
        "أستطيع مساعدتك في: تطوير مواقع، تطبيقات الجوال، حلول الذكاء الاصطناعي، التصميم UI/UX، والصيانة.\n\n"
        "استخدم الأوامر التالية:\n"
        "/services — عرض الخدمات\n"
        "/contact — ترك بياناتك لنرجع إليك\n"
        "أو اسألني أي سؤال حاليًا."
    )
    await update.message.reply_text(text)


async def services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(s["name"], callback_data=f"svc:{k}")]
        for k, s in SERVICES.items()
    ]
    await update.message.reply_text(
        "اختر الخدمة التي تهمك:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def on_service_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    s = SERVICES.get(key)
    if not s:
        await query.edit_message_text("حدث خطأ — جرّب مرة أخرى.")
        return
    msg = (
        f"**{s['name']}**\n"
        f"{s['desc']}\n\n"
        f"السعر يبدأ من: {s['starts_from']} (يتغير حسب المتطلبات).\n"
        "أخبرني باحتياجك أو اكتب /contact لترك بياناتك."
    )
    await query.edit_message_text(msg, parse_mode="Markdown")


async def contact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lead_need"] = "عام"
    await update.message.reply_text("سنأخذ بعض البيانات البسيطة. ما اسمك الكامل؟")
    return COLLECT_NAME


async def collect_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lead_name"] = update.message.text.strip()
    await update.message.reply_text("بريدك الإلكتروني؟")
    return COLLECT_EMAIL


async def collect_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if not EMAIL_RE.match(email):
        await update.message.reply_text("صيغة البريد غير صحيحة — أعد إدخاله.")
        return COLLECT_EMAIL
    context.user_data["lead_email"] = email
    await update.message.reply_text("صف باختصار احتياجك أو نوع المشروع:")
    return COLLECT_NOTE


async def collect_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    name = context.user_data.get("lead_name", "")
    email = context.user_data.get("lead_email", "")
    need = context.user_data.get("lead_need", "عام")
    save_lead(name, email, need, update, note)
    await update.message.reply_text(
        "شكرًا لك! تم استلام بياناتك وسنتواصل معك قريبًا عبر البريد."
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية. يمكنك البدء من جديد في أي وقت.")
    return ConversationHandler.END

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # أوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("services", services))

    # خدمات عبر الأزرار
    application.add_handler(CallbackQueryHandler(on_service_click, pattern=r"^svc:"))

    # محادثة جمع بيانات
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("contact", contact_start)],
        states={
            COLLECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_name)],
            COLLECT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_email)],
            COLLECT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    # الرد الذكي
    async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_text = update.message.text
        add_user_memory(context, "user", user_text)
        reply = ai_generate_reply(user_text, context)
        add_user_memory(context, "assistant", reply)
        await update.message.reply_text(reply)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))

    logger.info("Bot started... Waiting for messages.")
    application.run_polling()

if __name__ == "__main__":
    main()
