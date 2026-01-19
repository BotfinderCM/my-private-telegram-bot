import os, re, sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USER_IDS = set()
for x in (os.getenv("ALLOWED_USER_IDS") or "").split(","):
    x = x.strip()
    if x:
        ALLOWED_USER_IDS.add(int(x))

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")

DB_PATH = os.getenv("DB_PATH", "bot.db")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS checks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        conn.commit()

def is_allowed(user_id: int) -> bool:
    return (not ALLOWED_USER_IDS) or (user_id in ALLOWED_USER_IDS)

URL_RE = re.compile(r"^https?://", re.I)
PERMIT_RE = re.compile(r"^\d{6,20}$")

def detect_type(text: str) -> str:
    t = text.strip()
    if URL_RE.match(t): return "listing_url"
    if PERMIT_RE.match(t): return "permit_number"
    return "text"

def fake_lookup(text: str, input_type: str) -> dict:
    unit_guess = None
    m = re.search(r"(unit|apt|apartment|flat)[\-_/ ]?(\d+)", text, re.I)
    if m: unit_guess = m.group(2)
    return {
        "unit_number": unit_guess or "—",
        "building": "—",
        "project": "—",
        "community": "—",
        "permit_status": "unknown",
        "input_type": input_type,
        "flags": ["No data source connected yet (MVP)."]
    }

def format_card(check_id: int, result: dict) -> str:
    flags = result.get("flags", [])
    flag_text = "\n".join([f"• {f}" for f in flags]) if flags else "—"
    return (
        f"✅ Check #{check_id}\n\n"
        f"Unit: {result.get('unit_number','—')}\n"
        f"Building: {result.get('building','—')}\n"
        f"Project: {result.get('project','—')}\n"
        f"Community: {result.get('community','—')}\n"
        f"Permit: {result.get('permit_status','unknown')}\n"
        f"Type: {result.get('input_type','—')}\n\n"
        f"Flags:\n{flag_text}"
    )

app = FastAPI()
tg_app = None

@app.on_event("startup")
async def startup():
    global tg_app
    init_db()
    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("help", cmd_help))
    tg_app.add_handler(CommandHandler("last", cmd_last))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    await tg_app.initialize()
    await tg_app.start()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or not update.message: return
    if not is_allowed(u.id):
        await update.message.reply_text("⛔️ Private bot.")
        return
    await update.message.reply_text(
        "👋 Private Unit Bot\n\nPlak een listing link of permit number.\nCommands: /help  /last"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or not update.message: return
    if not is_allowed(u.id):
        await update.message.reply_text("⛔️ Private bot.")
        return
    await update.message.reply_text(
        "Send:\n- listing link\n- permit number\n\nCommands:\n/last (laatste 5 checks)"
    )

async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or not update.message: return
    if not is_allowed(u.id):
        await update.message.reply_text("⛔️ Private bot.")
        return

    with db() as conn:
        rows = conn.execute(
            "SELECT id, payload, created_at FROM checks WHERE user_id=? ORDER BY id DESC LIMIT 5",
            (u.id,)
        ).fetchall()

    if not rows:
        await update.message.reply_text("Nog geen checks.")
        return

    lines = ["🕘 Laatste checks:"]
    for r in rows:
        lines.append(f"#{r['id']} — {r['payload'][:60]} ({r['created_at']})")
    await update.message.reply_text("\n".join(lines))

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u or not update.message: return
    if not is_allowed(u.id):
        await update.message.reply_text("⛔️ Private bot.")
        return

    payload = update.message.text.strip()
    input_type = detect_type(payload)
    result = fake_lookup(payload, input_type)

    created_at = datetime.utcnow().isoformat() + "Z"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO checks(user_id, payload, created_at) VALUES(?,?,?)",
            (u.id, payload, created_at)
        )
        conn.commit()
        check_id = cur.lastrowid

    await update.message.reply_text(f"(debug) your_user_id={u.id}\n\n{format_card(check_id, result)}")

@app.post("/webhook")
async def webhook(req: Request):
    if tg_app is None:
        raise HTTPException(503, "Bot not ready")
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.get("/")
def root():
    return PlainTextResponse("OK")
