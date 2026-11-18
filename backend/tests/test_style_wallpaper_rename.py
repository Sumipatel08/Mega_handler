# Iteration 5: stylish bold text (sb/CREDIT), anime wallpapers, /start photo + START_KB, rename-after-save buttons
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bot  # noqa: E402
import mega_service as ms  # noqa: E402
from test_bot_handlers import OWNER, add, cb_update, kb_data, msg_update, run_with_db  # noqa: E402


def wrap_import(fake, monkeypatch, handle="NEWH", folder_handle="A1"):
    orig = fake.run

    async def run(acc, fn, *args):
        n = fn.__name__
        if n == "import_link":
            fake.calls.append((acc["id"], n, args))
            return handle
        if n == "import_folder_link":
            fake.calls.append((acc["id"], n, args))
            return folder_handle
        return await orig(acc, fn, *args)
    monkeypatch.setattr(ms, "run", run)


# --- sb / CREDIT / HELP ---
class TestStyle:
    def test_sb_mapping(self):
        assert bot.sb("A") == "\U0001D5D4" and bot.sb("Z") == "\U0001D5ED"
        assert bot.sb("a") == "\U0001D5EE" and bot.sb("z") == "\U0001D607"
        assert bot.sb("0") == "\U0001D7EC" and bot.sb("9") == "\U0001D7F5"
        assert bot.sb("Hi 5! _-é") [2:] == " " + "\U0001D7F1" + "! _-é"
        assert bot.sb("") == ""

    def test_credit_and_help(self):
        assert bot.sb("Made by DD FREE DISH") in bot.CREDIT
        assert bot.CREDIT in bot.HELP
        assert len(bot.HELP) <= 1024
        assert len(bot.WALLPAPERS) >= 1 and all(u.startswith("https://") for u in bot.WALLPAPERS)
        assert set(kb_data(bot.START_KB)) == {"accs", "fo", "wp"}


# --- /start, /wallpaper, wp/fo/accs callbacks ---
class TestStartWallpaper:
    def test_start_photo(self, fake):
        async def t(db):
            upd, ctx, _ = msg_update(text="/start")
            upd.message.reply_photo = AsyncMock()
            await bot.cmd_start(upd, ctx)
            args, kw = upd.message.reply_photo.call_args
            assert args[0] in bot.WALLPAPERS
            assert kw["caption"] == bot.HELP and bot.CREDIT in kw["caption"]
            assert kw["reply_markup"] is bot.START_KB
        run_with_db(t)

    def test_wallpaper_cmd(self, fake):
        async def t(db):
            upd, ctx, _ = msg_update(text="/wallpaper")
            upd.message.reply_photo = AsyncMock()
            await bot.cmd_wallpaper(upd, ctx)
            args, kw = upd.message.reply_photo.call_args
            assert args[0] in bot.WALLPAPERS and bot.CREDIT in kw["caption"]
        run_with_db(t)

    def test_non_owner_refused(self, fake):
        async def t(db):
            for fn in (bot.cmd_start, bot.cmd_wallpaper):
                upd, ctx, _ = msg_update(uid=OWNER + 99)
                upd.message.reply_photo = AsyncMock()
                await fn(upd, ctx)
                upd.message.reply_photo.assert_not_called()
                assert "private" in upd.message.reply_text.call_args[0][0]
            for data in ("wp", "fo", "accs"):
                upd, ctx = cb_update(data, uid=OWNER + 99)
                upd.callback_query.message.reply_photo = AsyncMock()
                await bot.on_button(upd, ctx)
                upd.callback_query.answer.assert_awaited_with("Ye bot private hai.", show_alert=True)
                upd.callback_query.message.reply_photo.assert_not_called()
                upd.callback_query.message.reply_text.assert_not_called()
                upd.callback_query.edit_message_text.assert_not_called()
        run_with_db(t)

    def test_wp_callback(self, fake):
        async def t(db):
            upd, ctx = cb_update("wp")
            upd.callback_query.message.reply_photo = AsyncMock()
            await bot.on_button(upd, ctx)
            args, kw = upd.callback_query.message.reply_photo.call_args
            assert args[0] in bot.WALLPAPERS and bot.CREDIT in kw["caption"]
        run_with_db(t)

    def test_fo_callback(self, fake):
        async def t(db):
            upd, ctx = cb_update("fo")
            await bot.on_button(upd, ctx)
            assert "Pehle" in upd.callback_query.message.reply_text.call_args[0][0]
            a1, _ = await add(db, "test_fo@example.com")
            await db.mega_accounts.update_one({"id": a1["id"]}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            upd, ctx = cb_update("fo")
            await bot.on_button(upd, ctx)
            args, kw = upd.callback_query.message.reply_text.call_args
            assert "Alpha" in args[0] and f"o:{a1['id']}:B1" in kb_data(kw["reply_markup"])
            upd.callback_query.edit_message_text.assert_not_called()
        run_with_db(t)

    def test_accs_from_photo_vs_text(self, fake):
        async def t(db):
            await add(db, "test_accs@example.com")
            upd, ctx = cb_update("accs")
            upd.callback_query.message.photo = (object(),)
            await bot.on_button(upd, ctx)
            txt = upd.callback_query.message.reply_text.call_args[0][0]
            assert txt.endswith(bot.CREDIT) and bot.sb("AAPKE MEGA ACCOUNTS") in txt
            upd.callback_query.edit_message_text.assert_not_called()
            upd, ctx = cb_update("accs")
            upd.callback_query.message.photo = ()
            await bot.on_button(upd, ctx)
            assert upd.callback_query.edit_message_text.call_args[0][0].endswith(bot.CREDIT)
            upd.callback_query.message.reply_text.assert_not_called()
        run_with_db(t)


# --- save_link reply + rename after save ---
class TestSaveRename:
    def test_file_save_reply_and_kb(self, fake, monkeypatch):
        wrap_import(fake, monkeypatch, handle="F1")

        async def t(db):
            a1, _ = await add(db, "test_sr1@example.com")
            await db.mega_accounts.update_one({"id": a1["id"]}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            up, cx, sent = msg_update(text="https://mega.nz/file/abc#def")
            await bot.on_text(up, cx)
            args, kw = sent.edit_text.call_args
            txt = args[0]
            assert txt.startswith("✅ " + bot.sb("SAVE HO GAYA"))
            assert "movie.mp4" in txt and a1["label"] in txt and "Alpha" in txt and "https://mega.nz/file/F1#KEY" in txt
            assert txt.endswith(bot.CREDIT)
            assert kb_data(kw["reply_markup"]) == [f"r:{a1['id']}:F1", f"i:{a1['id']}:F1"]
            labels = [b.text for row in kw["reply_markup"].inline_keyboard for b in row]
            assert labels == ["✏️ Naam badlo", "⚙️ Options"]
            # tap rename then send new name
            upd, ctx = cb_update(f"r:{a1['id']}:F1")
            await bot.on_button(upd, ctx)
            assert bot.sb("Naya naam likh kar bhejo") in upd.callback_query.message.reply_text.call_args[0][0]
            up2, cx2, _ = msg_update(text="TEST_renamed.mp4")
            await bot.on_text(up2, cx2)
            assert (a1["id"], "rename", ("F1", "TEST_renamed.mp4")) in fake.calls
            calls = up2.message.reply_text.call_args_list
            assert "TEST_renamed.mp4" in calls[0][0][0]
            assert "Alpha" in calls[-1][0][0]  # parent folder view
            assert (await bot.get_state()).get("pending") is None
        run_with_db(t)

    def test_folder_save_kb_and_rename(self, fake, monkeypatch):
        wrap_import(fake, monkeypatch, folder_handle="B1")

        async def t(db):
            a1, _ = await add(db, "test_sr2@example.com")
            await db.mega_accounts.update_one({"id": a1["id"]}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            up, cx, sent = msg_update(text="https://mega.nz/folder/abc#key")
            await bot.on_text(up, cx)
            args, kw = sent.edit_text.call_args
            assert args[0].startswith("✅ " + bot.sb("SAVE HO GAYA")) and "TEST_Shared" in args[0]
            assert kb_data(kw["reply_markup"]) == [f"r:{a1['id']}:B1", f"o:{a1['id']}:B1"]
            assert [b.text for b in kw["reply_markup"].inline_keyboard[0]][1] == "📂 Kholo"
            upd, ctx = cb_update(f"r:{a1['id']}:B1")
            await bot.on_button(upd, ctx)
            up2, cx2, _ = msg_update(text="TEST_MyFolder")
            await bot.on_text(up2, cx2)
            assert (a1["id"], "rename", ("B1", "TEST_MyFolder")) in fake.calls
            last = up2.message.reply_text.call_args_list[-1]
            assert "Alpha" in last[0][0] and f"o:{a1['id']}:B1" in kb_data(last[1]["reply_markup"])
            # save_folder (A1) unchanged since renamed node is not save folder
            assert (await bot.get_account(a1["id"]))["save_folder_name"] == "Alpha"
        run_with_db(t)

    def test_no_handle_no_kb(self, fake):
        async def t(db):
            await add(db, "test_sr3@example.com")
            up, cx, sent = msg_update(text="https://mega.nz/folder/abc#key")
            await bot.on_text(up, cx)
            assert sent.edit_text.call_args[1]["reply_markup"] is None
        run_with_db(t)
