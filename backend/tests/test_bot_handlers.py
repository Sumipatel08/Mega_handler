# Bot handler logic tests (bot.py) with mocked MEGA service + fake Telegram objects, isolated test DB
import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND / ".env")
sys.path.insert(0, str(BACKEND))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
import bot  # noqa: E402
import mega_service as ms  # noqa: E402

OWNER = int(os.environ["TELEGRAM_OWNER_ID"])
GB = 1073741824

# Fake MEGA tree: root R -> A(folder) -> B(folder); files
FOLDERS = {
    "R": {"name": "Cloud Drive", "parent": None, "subs": [("A1", "Alpha")]},
    "A1": {"name": "Alpha", "parent": "R", "subs": [("B1", "Beta")]},
    "B1": {"name": "Beta", "parent": "A1", "subs": []},
}


FILES = {"R": [("F0", "root.txt")], "A1": [("F1", "a.mp4"), ("F2", "b.pdf")]}


class FakeMega:
    def __init__(self):
        self.storage = {}  # acc_id -> (used,total)
        self.calls = []

    async def run(self, acc, fn, *args):
        name = fn.__name__
        self.calls.append((acc["id"], name, args))
        if name == "storage":
            u, t = self.storage.get(acc["id"], (0, 20 * GB))
            return {"used": u, "total": t}
        if name == "list_folder":
            h = args[0] if args and args[0] in FOLDERS else "R"
            f = FOLDERS[h]
            return {"handle": h, "name": f["name"], "is_root": h == "R", "parent": f["parent"],
                    "folders": f["subs"], "files": FILES.get(h, []), "file_count": len(FILES.get(h, []))}
        if name == "node_info":
            h = args[0]
            if h in FOLDERS:
                return {"handle": h, "name": FOLDERS[h]["name"], "is_file": False, "size": 0, "parent": FOLDERS[h]["parent"]}
            for p, fl in FILES.items():
                for fh, fn in fl:
                    if fh == h:
                        return {"handle": h, "name": fn, "is_file": True, "size": 5 * GB, "parent": p}
            raise KeyError(h)
        if name == "folder_link_info":
            return {"name": "TEST_Shared", "size": 3 * GB, "files": 4, "folders": 2}
        if name == "import_folder_link":
            return None
        if name in ("share_link",):
            return "https://mega.nz/file/F1#KEY"
        if name == "is_under":
            node, anc = args
            while node in FOLDERS:
                if node == anc:
                    return True
                node = FOLDERS[node]["parent"]
            return False
        if name in ("delete_node", "move_node"):
            return None
        if name == "mkdir":
            return "NEWH"
        if name == "rename":
            return None
        if name == "link_info":
            return {"handle": "fh", "key": "fk", "name": "movie.mp4", "size": 1 * GB}
        if name == "import_link":
            return {"name": "movie.mp4"}
        raise AssertionError(f"unexpected fn {name}")


@pytest.fixture
def fake(monkeypatch):
    f = FakeMega()
    monkeypatch.setattr(ms, "run", f.run)
    monkeypatch.setattr(ms, "login_check", AsyncMock(return_value=MagicMock(name="mega_session")))

    def storage(m):
        return {"used": 2 * GB, "total": 20 * GB}
    monkeypatch.setattr(ms, "storage", storage)
    return f


def run_with_db(coro_fn):
    async def wrapper():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        dbname = f"TEST_bot_handlers_{uuid.uuid4().hex[:8]}"
        bot.db = client[dbname]
        try:
            await coro_fn(bot.db)
        finally:
            await client.drop_database(dbname)
            client.close()
    asyncio.run(wrapper())


def msg_update(text="", uid=OWNER, args=None):
    upd = MagicMock()
    upd.effective_user.id = uid
    upd.callback_query = None
    sent = MagicMock()
    sent.edit_text = AsyncMock()
    upd.message.reply_text = AsyncMock(return_value=sent)
    upd.message.text = text
    upd.message.delete = AsyncMock()
    upd.effective_message = upd.message
    upd.effective_chat.send_message = AsyncMock(return_value=sent)
    ctx = MagicMock()
    ctx.args = args or []
    return upd, ctx, sent


def cb_update(data, uid=OWNER):
    upd = MagicMock()
    upd.effective_user.id = uid
    q = upd.callback_query
    q.data = data
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message.reply_text = AsyncMock()
    return upd, MagicMock()


def kb_data(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


async def add(db, email, password="pw123"):
    upd, ctx, sent = msg_update(args=[email, password])
    await bot.cmd_addaccount(upd, ctx)
    return await db.mega_accounts.find_one({"email": email.lower()}, {"_id": 0}), sent


class TestGuard:
    def test_non_owner_message(self, fake):
        async def t(db):
            upd, ctx, _ = msg_update(uid=OWNER + 12345)
            await bot.cmd_start(upd, ctx)
            txt = upd.message.reply_text.call_args[0][0]
            assert "Ye bot private hai" in txt
            upd2, ctx2, _ = msg_update(uid=OWNER + 1, args=["a@b.com", "x"])
            await bot.cmd_addaccount(upd2, ctx2)
            assert await db.mega_accounts.count_documents({}) == 0
        run_with_db(t)

    def test_non_owner_callback(self, fake):
        async def t(db):
            upd, ctx = cb_update("auto", uid=OWNER + 5)
            await bot.on_button(upd, ctx)
            upd.callback_query.answer.assert_awaited_with("Ye bot private hai.", show_alert=True)
            assert (await bot.get_state()).get("auto_switch") is None
        run_with_db(t)


class TestAccounts:
    def test_addaccount_encrypted_and_active(self, fake):
        async def t(db):
            acc, sent = await add(db, "TEST_User1@Example.com", "s3cret")
            assert acc is not None
            assert acc["email"] == "test_user1@example.com"
            assert acc["password_enc"] != "s3cret"
            assert ms.decrypt(acc["password_enc"]) == "s3cret"
            assert acc["label"] == "Account 1"
            assert (await bot.get_state())["active_account_id"] == acc["id"]
            assert "jud gaya" in sent.edit_text.call_args[0][0]
            # duplicate
            upd, ctx, _ = msg_update(args=["test_user1@example.com", "x"])
            await bot.cmd_addaccount(upd, ctx)
            assert "pehle se" in upd.effective_chat.send_message.call_args[0][0]
            # second account does not change active
            acc2, _ = await add(db, "test_user2@example.com")
            assert acc2["label"] == "Account 2"
            assert (await bot.get_state())["active_account_id"] == acc["id"]
            # bad usage
            upd, ctx, _ = msg_update(args=["only_one"])
            await bot.cmd_addaccount(upd, ctx)
            assert "Aise likho" in upd.message.reply_text.call_args[0][0]
        run_with_db(t)

    def test_addaccount_login_fail(self, fake, monkeypatch):
        monkeypatch.setattr(ms, "login_check", AsyncMock(side_effect=Exception("bad creds")))

        async def t(db):
            acc, sent = await add(db, "test_fail@example.com")
            assert acc is None
            assert "Login fail" in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_accounts_keyboard_toggle_switch_remove(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_a1@example.com")
            a2, _ = await add(db, "test_a2@example.com")
            upd, ctx, sent = msg_update()
            await bot.cmd_accounts(upd, ctx)
            kb = sent.edit_text.call_args.kwargs["reply_markup"]
            data = kb_data(kb)
            for x in [f"ua:{a1['id']}", f"rc:{a1['id']}", f"ua:{a2['id']}", f"rc:{a2['id']}", "auto"]:
                assert x in data
            assert "Auto switch: OFF" in [b.text for r in kb.inline_keyboard for b in r][-1]
            # auto toggle
            u, c = cb_update("auto")
            await bot.on_button(u, c)
            assert (await bot.get_state())["auto_switch"] is True
            kb2 = u.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
            assert any("ON" in b.text for r in kb2.inline_keyboard for b in r)
            u, c = cb_update("auto")
            await bot.on_button(u, c)
            assert (await bot.get_state())["auto_switch"] is False
            # ua
            u, c = cb_update(f"ua:{a2['id']}")
            await bot.on_button(u, c)
            assert (await bot.get_state())["active_account_id"] == a2["id"]
            assert "(active)" in u.callback_query.edit_message_text.call_args[0][0]
            # rc -> confirm
            u, c = cb_update(f"rc:{a2['id']}")
            await bot.on_button(u, c)
            kb3 = u.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
            assert f"rx:{a2['id']}" in kb_data(kb3) and "accs" in kb_data(kb3)
            assert await db.mega_accounts.count_documents({}) == 2
            # rx -> removed
            u, c = cb_update(f"rx:{a2['id']}")
            await bot.on_button(u, c)
            assert await db.mega_accounts.find_one({"id": a2["id"]}) is None
            # active falls back to remaining
            assert (await bot.active_account())["id"] == a1["id"]
        run_with_db(t)


class TestFolders:
    def test_folders_view_select_mkdir_rename(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_f1@example.com")
            upd, ctx, sent = msg_update()
            await bot.cmd_folders(upd, ctx)
            kb = sent.edit_text.call_args.kwargs["reply_markup"]
            data = kb_data(kb)
            A = a1["id"]
            assert f"o:{A}:A1" in data and f"s:{A}:R" in data and f"n:{A}:R" in data and "accs" in data
            assert f"i:{A}:F0" in data and f"i:{A}:R" not in data  # root has no options
            # open A1
            u, c = cb_update(f"o:{A}:A1")
            await bot.on_button(u, c)
            d2 = kb_data(u.callback_query.edit_message_text.call_args.kwargs["reply_markup"])
            for x in [f"o:{A}:B1", f"o:{A}:R", f"i:{A}:A1", f"s:{A}:A1", f"n:{A}:A1", f"i:{A}:F1", f"i:{A}:F2"]:
                assert x in d2
            # select A1
            u, c = cb_update(f"s:{A}:A1")
            await bot.on_button(u, c)
            acc = await bot.get_account(a1["id"])
            assert acc["save_folder"] == "A1" and acc["save_folder_name"] == "Alpha"
            assert "Alpha" in u.callback_query.message.reply_text.call_args[0][0]
            assert "(yahi)" in u.callback_query.edit_message_text.call_args[0][0]
            # mkdir pending
            u, c = cb_update(f"n:{A}:A1")
            await bot.on_button(u, c)
            st = await bot.get_state()
            assert st["pending"] == {"type": "mkdir", "h": "A1", "acc": a1["id"], "back": "A1"}
            up, cx, _ = msg_update(text="TEST_NewDir")
            await bot.on_text(up, cx)
            assert (a1["id"], "mkdir", ("TEST_NewDir", "A1")) in fake.calls
            assert "ban gaya" in up.message.reply_text.call_args_list[0][0][0]
            assert (await bot.get_state())["pending"] is None
            # rename pending (save folder renamed -> save_folder_name updated)
            u, c = cb_update(f"r:{A}:A1")
            await bot.on_button(u, c)
            assert (await bot.get_state())["pending"]["type"] == "rename"
            up, cx, _ = msg_update(text="TEST_Renamed")
            await bot.on_text(up, cx)
            assert (a1["id"], "rename", ("A1", "TEST_Renamed")) in fake.calls
            assert (await bot.get_account(a1["id"]))["save_folder_name"] == "TEST_Renamed"
            # no pending -> hint
            up, cx, _ = msg_update(text="hello")
            await bot.on_text(up, cx)
            assert "MEGA link bhejo" in up.message.reply_text.call_args[0][0]
            # /newfolder
            up, cx, _ = msg_update(args=["My", "Dir"])
            await bot.cmd_newfolder(up, cx)
            assert (a1["id"], "mkdir", ("My Dir", "A1")) in fake.calls
        run_with_db(t)


class TestLinks:
    def test_file_link_import_into_save_folder(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_l1@example.com")
            await db.mega_accounts.update_one({"id": a1["id"]}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            url = "https://mega.nz/file/abcDEF#keyXYZ"
            up, cx, sent = msg_update(text=f"save this {url}")
            await bot.on_text(up, cx)
            assert (a1["id"], "import_link", (url, "A1")) in fake.calls
            assert "Save ho gaya" in sent.edit_text.call_args[0][0]
            assert "Alpha" in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_folder_link_imported(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_l2@example.com")
            await db.mega_accounts.update_one({"id": a1["id"]}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            url = "https://mega.nz/folder/abc#key"
            up, cx, sent = msg_update(text=url)
            await bot.on_text(up, cx)
            assert (a1["id"], "folder_link_info", (url,)) in fake.calls
            assert (a1["id"], "import_folder_link", (url, "A1")) in fake.calls
            assert not any(c[1] in ("import_link", "link_info") for c in fake.calls)
            txt = sent.edit_text.call_args[0][0]
            assert "Save ho gaya" in txt and "TEST_Shared" in txt and "2 folders, 4 files" in txt and "Alpha" in txt
        run_with_db(t)

    def test_old_folder_link_format(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_l2b@example.com")
            url = "https://mega.nz/#F!abc!key"
            up, cx, sent = msg_update(text=url)
            await bot.on_text(up, cx)
            assert (a1["id"], "import_folder_link", (url, None)) in fake.calls
        run_with_db(t)

    def test_insufficient_space(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_l3@example.com")
            fake.storage[a1["id"]] = (int(19.5 * GB), 20 * GB)
            up, cx, sent = msg_update(text="https://mega.nz/file/x#y")
            await bot.on_text(up, cx)
            assert "jagah kam" in sent.edit_text.call_args[0][0]
            assert not any(c[1] == "import_link" for c in fake.calls)
        run_with_db(t)

    def test_auto_switch_picks_most_free(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_s1@example.com")
            a2, _ = await add(db, "test_s2@example.com")
            a3, _ = await add(db, "test_s3@example.com")
            fake.storage[a1["id"]] = (18 * GB, 20 * GB)
            fake.storage[a2["id"]] = (5 * GB, 20 * GB)
            fake.storage[a3["id"]] = (10 * GB, 20 * GB)
            # auto off -> active (a1)
            assert (await bot.pick_account_for())["id"] == a1["id"]
            up, cx, _ = msg_update()
            await bot.cmd_auto(up, cx)
            assert (await bot.get_state())["auto_switch"] is True
            assert (await bot.pick_account_for())["id"] == a2["id"]
            url = "https://mega.nz/file/q#w"
            up, cx, sent = msg_update(text=url)
            await bot.on_text(up, cx)
            assert (a2["id"], "import_link", (url, None)) in fake.calls
            assert "Account 2" in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_status_cmd(self, fake):
        async def t(db):
            a1, _ = await add(db, "test_st@example.com")
            up, cx, _ = msg_update()
            await bot.cmd_status(up, cx)
            txt = up.message.reply_text.call_args[0][0]
            assert "Account 1" in txt and "Auto switch: OFF" in txt
        run_with_db(t)
