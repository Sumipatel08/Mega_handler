import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as K, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from telegram.helpers import escape_markdown

import mega_service as ms


def em(t):
    return escape_markdown(str(t), version=1)

log = logging.getLogger("bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
db = None
application: Application | None = None
info = {"running": False, "username": None, "error": None}

HELP = (
    "*MEGA Manager Bot*\n\n"
    "/newaccount `email password` - aapke email se naya MEGA account banao (email ka confirm link bot ko bhejna hoga)\n"
    "/addaccount `email password` - pehle se bana MEGA account jodo\n"
    "/accounts - saare accounts, khali jagah, switch/hatao\n"
    "/folders - folder browser (buttons se kholo, rename, select)\n"
    "/newfolder `naam` - selected folder mein naya folder\n"
    "/auto - auto switch ON/OFF (link sabse zyada khali jagah wale account mein)\n"
    "/status - abhi ka account aur save folder\n"
    "/myid - aapki Telegram ID\n\n"
    "Koi bhi MEGA file ya folder link bhejo, bot use cloud mein save kar dega.\n"
    "/folders mein file/folder dabao: share link, rename, move, delete."
)


def owner_id() -> int:
    return int(os.environ.get("TELEGRAM_OWNER_ID") or 0)


def gb(n: int) -> str:
    return f"{n / 1073741824:.2f} GB"


async def get_state() -> dict:
    return await db.bot_state.find_one({"_id": "owner"}) or {}


async def set_state(**kw):
    await db.bot_state.update_one({"_id": "owner"}, {"$set": kw}, upsert=True)


async def get_account(acc_id):
    return await db.mega_accounts.find_one({"id": acc_id}, {"_id": 0})


async def active_account():
    s = await get_state()
    acc = await get_account(s.get("active_account_id")) if s.get("active_account_id") else None
    return acc or await db.mega_accounts.find_one({}, {"_id": 0}, sort=[("created_at", 1)])


async def refresh_storage(acc):
    st = await ms.run(acc, ms.storage)
    await db.mega_accounts.update_one({"id": acc["id"]}, {"$set": {**st, "checked_at": datetime.now(timezone.utc).isoformat()}})
    return st


def is_owner(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == owner_id()


async def guard(update: Update) -> bool:
    if is_owner(update):
        return True
    uid = update.effective_user.id if update.effective_user else "?"
    if update.callback_query:
        await update.callback_query.answer("Ye bot private hai.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(f"Ye bot private hai. Aapki Telegram ID: {uid}")
    return False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        await update.message.reply_text(HELP, parse_mode="Markdown")


async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Aapki Telegram ID: {update.effective_user.id}")


async def cmd_addaccount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    chat = update.effective_chat
    if len(ctx.args) != 2:
        await update.message.reply_text("Aise likho: /addaccount email password")
        return
    email, password = ctx.args[0].strip().lower(), ctx.args[1]
    try:
        await update.message.delete()
    except Exception:
        pass
    if await db.mega_accounts.find_one({"email": email}):
        await chat.send_message("Ye account pehle se juda hua hai.")
        return
    msg = await chat.send_message("Login check ho raha hai...")
    try:
        m = await ms.login_check(email, password)
        st = await asyncio.to_thread(ms.storage, m)
    except Exception as e:
        await msg.edit_text(f"Login fail hua. Email/password check karo.\n({e})")
        return
    acc = await store_account(email, password, m, st)
    await msg.edit_text(f"{acc['label']} jud gaya: {email}\nKhali jagah: {gb(st['total'] - st['used'])} / {gb(st['total'])}\n(Password wala message delete kar diya gaya)")


async def store_account(email, password, m, st):
    count = await db.mega_accounts.count_documents({})
    acc = {
        "id": uuid.uuid4().hex[:8], "label": f"Account {count + 1}", "email": email,
        "password_enc": ms.encrypt(password), "save_folder": None, "save_folder_name": "Cloud Drive",
        **st, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.mega_accounts.insert_one(dict(acc))
    ms._sessions[acc["id"]] = m
    if count == 0:
        await set_state(active_account_id=acc["id"])
    return acc


async def cmd_newaccount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    chat = update.effective_chat
    if len(ctx.args) != 2:
        await update.message.reply_text("Aise likho: /newaccount aapka@email.com password\n(Email aapka asli hona chahiye, MEGA us par confirm link bhejega)")
        return
    email, password = ctx.args[0].strip().lower(), ctx.args[1]
    try:
        await update.message.delete()
    except Exception:
        pass
    if len(password) < 8:
        await chat.send_message("Password kam se kam 8 characters ka rakho.")
        return
    if await db.mega_accounts.find_one({"email": email}):
        await chat.send_message("Ye email pehle se bot mein juda hai.")
        return
    msg = await chat.send_message("MEGA par account bana raha hoon...")
    try:
        await asyncio.to_thread(ms.register, email, password, email.split("@")[0])
    except Exception as e:
        await msg.edit_text(f"Signup nahi hua: {e}\n(Shayad is email se MEGA account pehle se hai, tab /addaccount use karo)")
        return
    await db.mega_signups.update_one(
        {"email": email}, {"$set": {"email": email, "password_enc": ms.encrypt(password), "created_at": datetime.now(timezone.utc).isoformat()}}, upsert=True
    )
    await msg.edit_text(
        f"Signup shuru ho gaya: {email}\n\n"
        "MEGA ne aapke email par ek confirmation link bheja hai (ye OTP nahi, ek link hota hai). "
        "Email kholo, 'Verify my email' button ka link copy karke yahan bhej do. Bot account confirm karke jod dega.\n"
        "(Password wala message delete kar diya gaya)"
    )


async def finish_signup(update: Update, code: bytes):
    msg = await update.message.reply_text("Account confirm ho raha hai...")
    try:
        email = await asyncio.to_thread(ms.confirm_signup, code)
    except Exception as e:
        await msg.edit_text(f"Confirm nahi hua. Link sahi aur naya hai? ({e})")
        return
    sign = await db.mega_signups.find_one({"email": email})
    if not sign:
        await msg.edit_text(f"{email} confirm ho gaya MEGA par. Bot mein jodne ke liye /addaccount {email} password bhejo.")
        return
    password = ms.decrypt(sign["password_enc"])
    try:
        m = await ms.login_check(email, password)
        st = await asyncio.to_thread(ms.storage, m)
    except Exception as e:
        await msg.edit_text(f"Account confirm ho gaya par login nahi hua: {e}\nThodi der baad /addaccount se jodo.")
        return
    await db.mega_signups.delete_one({"email": email})
    acc = await store_account(email, password, m, st) if not await db.mega_accounts.find_one({"email": email}) else None
    label = acc["label"] if acc else "Account"
    await msg.edit_text(f"Naya MEGA account tayyar aur juda: {email} ({label})\nJagah: {gb(st['total'])}\nAb koi bhi MEGA link bhejo, main use is account mein save karke apna share link bhej dunga.")


async def accounts_view():
    accs = await db.mega_accounts.find({}, {"_id": 0}).sort("created_at", 1).to_list(100)
    s = await get_state()
    if not accs:
        return "Koi account nahi juda. /addaccount email password se jodo.", None
    active = (await active_account())["id"]
    lines, rows = ["*Aapke MEGA accounts*\n"], []
    for a in accs:
        try:
            st = await refresh_storage(a)
        except Exception:
            st = {"used": a.get("used", 0), "total": a.get("total", 0)}
        mark = " (active)" if a["id"] == active else ""
        lines.append(f"*{em(a['label'])}*{mark}\n{em(a['email'])}\nKhali: {gb(st['total'] - st['used'])} / {gb(st['total'])}\n")
        rows.append([B(f"Use {a['label']}", callback_data=f"ua:{a['id']}"), B(f"Hatao {a['label']}", callback_data=f"rc:{a['id']}")])
    auto = s.get("auto_switch", False)
    rows.append([B(f"Auto switch: {'ON' if auto else 'OFF'}", callback_data="auto")])
    return "\n".join(lines), K(rows)


async def cmd_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("Accounts check ho rahe hain...")
    text, kb = await accounts_view()
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def folder_view(acc, handle=None):
    f = await ms.run(acc, ms.list_folder, handle)
    selected = acc.get("save_folder") == f["handle"] or (f["is_root"] and not acc.get("save_folder"))
    text = (
        f"*{em(acc['label'])}* > {em(f['name'])}\n"
        f"{len(f['folders'])} folders, {f['file_count']} files\n"
        f"Save folder: {em(acc.get('save_folder_name') or 'Cloud Drive')}{' (yahi)' if selected else ''}"
    )
    moving = (await get_state()).get("moving")
    moving = moving if moving and moving["acc"] == acc["id"] else None
    if moving:
        text += f"\n\n*Move:* {em(moving['name'])}\nJis folder mein le jaana hai wahan jao aur 'Yaha move karo' dabao."
    a = acc["id"]
    rows = [[B(f"📁 {name}", callback_data=f"o:{a}:{h}")] for h, name in f["folders"][:35]]
    if not moving:
        rows += [[B(f"📄 {name}", callback_data=f"i:{a}:{h}")] for h, name in f["files"][:35]]
    nav = []
    if not f["is_root"]:
        nav.append(B("⬆️ Upar", callback_data=f"o:{a}:{f['parent']}"))
        if not moving:
            nav.append(B("⚙️ Is folder ke options", callback_data=f"i:{a}:{f['handle']}"))
    rows.append(nav) if nav else None
    if moving:
        rows.append([B("📥 Yaha move karo", callback_data=f"mv:{a}:{f['handle']}"), B("❌ Cancel", callback_data=f"mc:{a}:{f['handle']}")])
    else:
        rows.append([B("✅ Yaha save karo", callback_data=f"s:{a}:{f['handle']}"), B("➕ Naya folder", callback_data=f"n:{a}:{f['handle']}")])
    rows.append([B("🔁 Account badlo", callback_data="accs")])
    return text, K(rows)


async def item_menu(q, acc, handle):
    n = await ms.run(acc, ms.node_info, handle)
    a = acc["id"]
    kind = "File" if n["is_file"] else "Folder"
    size = f"\nSize: {gb(n['size'])}" if n["is_file"] else ""
    kb = K([
        [B("🔗 Share link", callback_data=f"sh:{a}:{handle}"), B("✏️ Rename", callback_data=f"r:{a}:{handle}")],
        [B("📦 Move", callback_data=f"m:{a}:{handle}"), B("🗑 Delete", callback_data=f"d:{a}:{handle}")],
        [B("⬅️ Wapas", callback_data=f"o:{a}:{n['parent']}")] if n["is_file"] else [B("📂 Kholo", callback_data=f"o:{a}:{handle}"), B("⬅️ Wapas", callback_data=f"o:{a}:{n['parent']}")],
    ])
    await q.edit_message_text(f"*{kind}:* {em(n['name'])}{size}", parse_mode="Markdown", reply_markup=kb)


async def cmd_folders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    acc = await active_account()
    if not acc:
        await update.message.reply_text("Pehle /addaccount se account jodo.")
        return
    await set_state(moving=None, pending=None)
    msg = await update.message.reply_text("Folders load ho rahe hain...")
    try:
        text, kb = await folder_view(acc, acc.get("save_folder"))
        await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        await msg.edit_text(f"Error: {e}")


async def make_folder(acc, name, parent):
    h = await ms.run(acc, ms.mkdir, name, parent)
    return h


async def cmd_newfolder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    name = " ".join(ctx.args).strip()
    acc = await active_account()
    if not acc or not name:
        await update.message.reply_text("Aise likho: /newfolder Folder ka naam" if acc else "Pehle /addaccount se account jodo.")
        return
    try:
        await make_folder(acc, name, acc.get("save_folder"))
        await update.message.reply_text(f"Folder '{name}' ban gaya ({acc['label']} > {acc.get('save_folder_name') or 'Cloud Drive'}).")
    except Exception as e:
        await update.message.reply_text(f"Folder nahi bana: {e}")


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    auto = not (await get_state()).get("auto_switch", False)
    await set_state(auto_switch=auto)
    await update.message.reply_text(f"Auto switch {'ON: links sabse zyada khali jagah wale account mein jayenge' if auto else 'OFF: links active account mein jayenge'}.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    acc = await active_account()
    s = await get_state()
    if not acc:
        await update.message.reply_text("Koi account nahi juda.")
        return
    await update.message.reply_text(
        f"Active: {acc['label']} ({acc['email']})\nSave folder: {acc.get('save_folder_name') or 'Cloud Drive'}\nAuto switch: {'ON' if s.get('auto_switch') else 'OFF'}"
    )


async def pick_account_for():
    s = await get_state()
    if not s.get("auto_switch"):
        return await active_account()
    best, best_free = None, -1
    for a in await db.mega_accounts.find({}, {"_id": 0}).to_list(100):
        try:
            st = await refresh_storage(a)
        except Exception:
            continue
        free = st["total"] - st["used"]
        if free > best_free:
            best, best_free = a, free
    return best


async def save_link(update: Update, url: str):
    msg = await update.message.reply_text("Link check ho raha hai...")
    is_folder = ms.is_folder_link(url)
    any_acc = await active_account()
    if not any_acc:
        await msg.edit_text("Pehle /addaccount se account jodo.")
        return
    try:
        li = await ms.run(any_acc, ms.folder_link_info if is_folder else ms.link_info, url)
        acc = await pick_account_for()
        if not acc:
            await msg.edit_text("Kisi bhi account se connect nahi ho paya. Thodi der baad try karo.")
            return
        st = await refresh_storage(acc)
        if st["total"] - st["used"] < li["size"]:
            await msg.edit_text(f"{acc['label']} mein jagah kam hai ({gb(st['total'] - st['used'])} khali, file {gb(li['size'])}). /auto ON karo ya naya account jodo.")
            return
        extra = f"\n{li['folders']} folders, {li['files']} files" if is_folder else ""
        await msg.edit_text(f"Save ho raha hai: {li['name']} ({gb(li['size'])}){extra}...")
        new_h = await ms.run(acc, ms.import_folder_link if is_folder else ms.import_link, url, acc.get("save_folder"))
        share = ""
        if new_h:
            try:
                share = "\n\nAapke account ka link:\n" + await ms.run(acc, ms.share_link, new_h)
            except Exception as e:
                share = f"\n\n(Share link nahi bana: {e})"
        await msg.edit_text(
            f"Save ho gaya: {'📁 ' if is_folder else ''}{li['name']}{extra}\nAccount: {acc['label']}\nFolder: {acc.get('save_folder_name') or 'Cloud Drive'}{share}",
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.edit_text(f"Link save nahi hua. Link sahi hai? ({e})")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = update.message.text.strip()
    code = ms.confirm_code(text)
    if code:
        await finish_signup(update, code)
        return
    links = ms.LINK_RE.findall(text)
    if links:
        for url in links:
            await save_link(update, url)
        return
    pending = (await get_state()).get("pending")
    if not pending:
        await update.message.reply_text("MEGA link bhejo ya /help dekho.")
        return
    await set_state(pending=None)
    acc = await get_account(pending["acc"])
    try:
        if pending["type"] == "rename":
            await ms.run(acc, ms.rename, pending["h"], text)
            if acc.get("save_folder") == pending["h"]:
                await db.mega_accounts.update_one({"id": acc["id"]}, {"$set": {"save_folder_name": text}})
            await update.message.reply_text(f"Naam badal kar '{text}' ho gaya.")
        else:
            await make_folder(acc, text, pending["h"])
            await update.message.reply_text(f"Folder '{text}' ban gaya.")
        tv, kb = await folder_view(await get_account(acc["id"]), pending.get("back") or pending["h"])
        await update.message.reply_text(tv, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        await update.message.reply_text(f"Nahi hua: {e}")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    q = update.callback_query
    await q.answer()
    action, _, arg = q.data.partition(":")
    try:
        if action in ("accs", "auto", "ua", "rx"):
            if action == "auto":
                await set_state(auto_switch=not (await get_state()).get("auto_switch", False))
            elif action == "ua":
                await set_state(active_account_id=arg)
            elif action == "rx":
                await db.mega_accounts.delete_one({"id": arg})
                ms.forget(arg)
            text, kb = await accounts_view()
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            return
        if action == "rc":
            acc = await get_account(arg)
            if not acc:
                await q.edit_message_text("Ye account pehle hi hata diya gaya hai.")
                return
            kb = K([[B("Haan, hatao", callback_data=f"rx:{arg}"), B("Nahi", callback_data="accs")]])
            await q.edit_message_text(f"{acc['label']} ({acc['email']}) bot se hata dein? MEGA par account aur files waise hi rahenge.", reply_markup=kb)
            return
        acc_id, _, arg = arg.partition(":")
        acc = await get_account(acc_id)
        if not acc:
            await q.message.reply_text("Ye account ab juda nahi hai. /folders dobara kholo.")
            return
        if action == "o":
            text, kb = await folder_view(acc, arg)
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        elif action == "s":
            f = await ms.run(acc, ms.list_folder, arg)
            await db.mega_accounts.update_one({"id": acc["id"]}, {"$set": {"save_folder": f["handle"], "save_folder_name": f["name"]}})
            text, kb = await folder_view(await get_account(acc["id"]), arg)
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            await q.message.reply_text(f"Ab links '{f['name']}' mein save honge ({acc['label']}).")
        elif action == "i":
            await item_menu(q, acc, arg)
        elif action == "sh":
            link = await ms.run(acc, ms.share_link, arg)
            await q.message.reply_text(f"Share link:\n{link}", disable_web_page_preview=True)
        elif action == "d":
            node = await ms.run(acc, ms.node_info, arg)
            kb = K([[B("Haan, delete karo", callback_data=f"dx:{acc['id']}:{arg}"), B("Nahi", callback_data=f"i:{acc['id']}:{arg}")]])
            await q.edit_message_text(f"'{node['name']}' delete karein? Ye Rubbish Bin mein chala jayega.", reply_markup=kb)
        elif action == "dx":
            node = await ms.run(acc, ms.node_info, arg)
            hit = acc.get("save_folder") and await ms.run(acc, ms.is_under, acc["save_folder"], arg)
            await ms.run(acc, ms.delete_node, arg)
            if hit:
                await db.mega_accounts.update_one({"id": acc["id"]}, {"$set": {"save_folder": None, "save_folder_name": "Cloud Drive"}})
            text, kb = await folder_view(await get_account(acc["id"]), node["parent"])
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
            await q.message.reply_text(f"'{node['name']}' Rubbish Bin mein chala gaya.")
        elif action == "m":
            node = await ms.run(acc, ms.node_info, arg)
            await set_state(moving={"acc": acc["id"], "h": arg, "name": node["name"]})
            text, kb = await folder_view(acc, node["parent"])
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        elif action in ("mv", "mc"):
            moving = (await get_state()).get("moving")
            await set_state(moving=None)
            if action == "mv" and moving:
                await ms.run(acc, ms.move_node, moving["h"], arg)
                await q.message.reply_text(f"'{moving['name']}' move ho gaya.")
            text, kb = await folder_view(acc, arg)
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        elif action in ("r", "n"):
            back = (await ms.run(acc, ms.node_info, arg))["parent"] if action == "r" else arg
            await set_state(pending={"type": "rename" if action == "r" else "mkdir", "h": arg, "acc": acc["id"], "back": back})
            await q.message.reply_text("Naya naam likh kar bhejo:" if action == "r" else "Naye folder ka naam likh kar bhejo:")
    except Exception as e:
        await q.message.reply_text(f"Error: {e}")


async def start_bot(database):
    global db, application
    db = database
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        info["error"] = "TELEGRAM_BOT_TOKEN missing"
        return
    try:
        application = Application.builder().token(token).build()
        for name, fn in [("start", cmd_start), ("help", cmd_start), ("myid", cmd_myid), ("addaccount", cmd_addaccount), ("newaccount", cmd_newaccount),
                         ("accounts", cmd_accounts), ("folders", cmd_folders), ("newfolder", cmd_newfolder),
                         ("auto", cmd_auto), ("status", cmd_status)]:
            application.add_handler(CommandHandler(name, fn))
        application.add_handler(CallbackQueryHandler(on_button))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        me = await application.bot.get_me()
        info.update(running=True, username=me.username, error=None)
    except Exception as e:
        log.exception("bot start failed")
        info.update(running=False, error=str(e))


async def stop_bot():
    if application and info["running"]:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
