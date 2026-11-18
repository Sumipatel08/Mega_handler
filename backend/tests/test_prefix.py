# Iteration 7: /prefix command, with_prefix helper, auto-rename of saved folder links
import bot
import mega_service as ms
from test_bot_handlers import OWNER, add, kb_data, msg_update, run_with_db
from test_bigsave_search import FOLDER_URL, patch_run, raiser

FILE_URL = "https://mega.nz/file/x#y"
FOLDER_INFO = {"name": "abcd", "size": 1024, "files": 2, "folders": 1}


def reply(up):
    return up.message.reply_text.call_args[0][0]


# --- with_prefix ---
class TestWithPrefix:
    def test_adds_space(self):
        assert bot.with_prefix("DD FREE DISH", "abcd") == "DD FREE DISH abcd"

    def test_no_space_after_separators(self):
        for p in ["DD ", "DD-", "DD_", "DD|", "DD.", "[DD]", "(DD)", "DD:"]:
            assert bot.with_prefix(p, "abcd") == p + "abcd", p

    def test_no_double_prefix(self):
        assert bot.with_prefix("DD FREE DISH", "DD FREE DISH abcd") == "DD FREE DISH abcd"
        assert bot.with_prefix("[DD]", "[DD]abcd") == "[DD]abcd"


# --- /prefix command ---
class TestCmdPrefix:
    def test_set_show_off(self):
        async def t(db):
            up, cx, _ = msg_update()
            await bot.cmd_prefix(up, cx)
            r = reply(up)
            assert "koi nahi" in r and "/prefix DD FREE DISH" in r and "/prefix off" in r
            up, cx, _ = msg_update(args=["DD", "FREE", "DISH"])
            await bot.cmd_prefix(up, cx)
            r = reply(up)
            assert "abcd ➥ DD FREE DISH abcd" in r and r.endswith(bot.CREDIT)
            assert (await bot.get_state())["prefix"] == "DD FREE DISH"
            up, cx, _ = msg_update()
            await bot.cmd_prefix(up, cx)
            assert "Abhi: DD FREE DISH" in reply(up)
            up, cx, _ = msg_update(args=["OFF"])
            await bot.cmd_prefix(up, cx)
            assert "hata diya" in reply(up)
            assert (await bot.get_state()).get("prefix") is None
        run_with_db(t)

    def test_too_long_rejected(self):
        async def t(db):
            up, cx, _ = msg_update(args=["x" * 51])
            await bot.cmd_prefix(up, cx)
            assert "50" in reply(up)
            assert (await bot.get_state()).get("prefix") is None
            up, cx, _ = msg_update(args=["y" * 50])
            await bot.cmd_prefix(up, cx)
            assert (await bot.get_state())["prefix"] == "y" * 50
        run_with_db(t)

    def test_non_owner_refused(self):
        async def t(db):
            up, cx, _ = msg_update(uid=OWNER + 1, args=["HACK"])
            await bot.cmd_prefix(up, cx)
            assert (await bot.get_state()).get("prefix") is None
        run_with_db(t)

    def test_status_and_help(self, fake):
        async def t(db):
            await add(db, "test_pfx_status@example.com")
            up, cx, _ = msg_update()
            await bot.cmd_status(up, cx)
            assert "Prefix: koi nahi" in reply(up)
            await bot.set_state(prefix="DD")
            up, cx, _ = msg_update()
            await bot.cmd_status(up, cx)
            assert "Prefix: DD" in reply(up)
        run_with_db(t)
        assert "/prefix" in bot.HELP and len(bot.HELP) <= 1024


# --- save_link rename ---
class TestSaveRename:
    def _setup(self, fake, monkeypatch, rename=None):
        ov = {"folder_link_info": dict(FOLDER_INFO), "import_folder_link": "NEWF", "share_link": "https://mega.nz/folder/NEWF#K"}
        if rename is not None:
            ov["rename"] = rename
        patch_run(fake, monkeypatch, ov)

    def test_folder_renamed_before_share(self, fake, monkeypatch):
        async def t(db):
            a1, _ = await add(db, "test_pfx1@example.com")
            await bot.set_state(prefix="DD FREE DISH")
            self._setup(fake, monkeypatch, rename=None)
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            names = [c[1] for c in fake.calls]
            assert (a1["id"], "rename", ("NEWF", "DD FREE DISH abcd")) in fake.calls
            assert names.index("rename") < names.index("share_link")
            assert names.index("import_folder_link") < names.index("rename")
            final = sent.edit_text.call_args[0][0]
            assert "🏷 Naya naam: DD FREE DISH abcd (purana: abcd)" in final
            assert "📁 DD FREE DISH abcd (" in final
            assert "https://mega.nz/folder/NEWF#K" in final
            assert kb_data(sent.edit_text.call_args[1]["reply_markup"]) == [f"r:{a1['id']}:NEWF", f"o:{a1['id']}:NEWF"]
        run_with_db(t)

    def test_rename_failure_still_saved(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_pfx2@example.com")
            await bot.set_state(prefix="DD")
            self._setup(fake, monkeypatch, rename=raiser(KeyError("NEWF")))
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            final = sent.edit_text.call_args[0][0]
            assert bot.sb("SAVE HO GAYA") in final and "Prefix nahi laga" in final
            assert "📁 abcd (" in final and "Naya naam" not in final
            assert "https://mega.nz/folder/NEWF#K" in final
        run_with_db(t)

    def test_folder_without_prefix_not_renamed(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_pfx3@example.com")
            self._setup(fake, monkeypatch)
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            assert "rename" not in [c[1] for c in fake.calls]
            assert "Naya naam" not in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_file_link_not_renamed(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_pfx4@example.com")
            await bot.set_state(prefix="DD")
            patch_run(fake, monkeypatch, {"import_link": "NEWH"})
            up, cx, sent = msg_update(text=FILE_URL)
            await bot.on_text(up, cx)
            assert "rename" not in [c[1] for c in fake.calls]
            final = sent.edit_text.call_args[0][0]
            assert bot.sb("SAVE HO GAYA") in final and "Naya naam" not in final
        run_with_db(t)

    def test_no_handle_no_rename(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_pfx5@example.com")
            await bot.set_state(prefix="DD")
            patch_run(fake, monkeypatch, {"folder_link_info": dict(FOLDER_INFO), "import_folder_link": None})
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            assert "rename" not in [c[1] for c in fake.calls]
            assert bot.sb("SAVE HO GAYA") in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_already_prefixed_name_kept(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_pfx6@example.com")
            await bot.set_state(prefix="DD")
            patch_run(fake, monkeypatch, {"folder_link_info": {**FOLDER_INFO, "name": "DD abcd"},
                                          "import_folder_link": "NEWF", "share_link": "L"})
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            # rename called with unchanged name (harmless) or skipped
            renames = [c for c in fake.calls if c[1] == "rename"]
            assert all(c[2] == ("NEWF", "DD abcd") for c in renames)
        run_with_db(t)


def test_real_rename_uses_mega_rename():
    from unittest.mock import MagicMock
    m = MagicMock()
    m.get_files.return_value = {"NEWF": {"h": "NEWF", "k": (1, 2, 3, 4), "a": {"n": "abcd"}}}
    ms.rename(m, "NEWF", "DD abcd")
    m.rename.assert_called_once_with(("NEWF", m.get_files.return_value["NEWF"]), "DD abcd")
