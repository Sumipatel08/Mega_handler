# Iteration 6: big link save (no free-space block, quota error handling) + /search across all accounts
from unittest.mock import MagicMock

import bot
import mega_service as ms
from test_bot_handlers import GB, OWNER, add, kb_data, msg_update, run_with_db


def patch_run(fake, monkeypatch, overrides):
    orig = fake.run

    async def run(acc, fn, *args):
        n = fn.__name__
        if n in overrides:
            fake.calls.append((acc["id"], n, args))
            res = overrides[n]
            return await res(acc, *args) if callable(res) else res
        return await orig(acc, fn, *args)
    monkeypatch.setattr(ms, "run", run)


def raiser(exc):
    async def f(acc, *args):
        raise exc
    return f


FOLDER_URL = "https://mega.nz/folder/abc#key"


# --- save_link big folder ---
class TestBigSave:
    def test_25gb_folder_on_20gb_account_imports_with_warning(self, fake, monkeypatch):
        async def t(db):
            a1, _ = await add(db, "test_big1@example.com")
            fake.storage[a1["id"]] = (0, 20 * GB)
            patch_run(fake, monkeypatch, {
                "folder_link_info": {"name": "TEST_Huge", "size": 25 * GB, "files": 10, "folders": 3},
                "import_folder_link": "NEWF",
            })
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            assert (a1["id"], "import_folder_link", (FOLDER_URL, None)) in fake.calls
            texts = [c[0][0] for c in sent.edit_text.call_args_list]
            assert any("Save ho raha hai" in x and "⚠️ Size 25.00 GB" in x for x in texts)
            final = texts[-1]
            assert bot.sb("SAVE HO GAYA") in final
            assert "TEST_Huge (25.00 GB)" in final
            assert "⚠️ Size 25.00 GB hai, khali sirf 20.00 GB" in final
            assert "jagah kam" not in final
            assert final.endswith(bot.CREDIT)
            kb = sent.edit_text.call_args[1]["reply_markup"]
            assert kb_data(kb) == [f"r:{a1['id']}:NEWF", f"o:{a1['id']}:NEWF"]
        run_with_db(t)

    def test_no_warning_when_space_enough(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_big2@example.com")
            patch_run(fake, monkeypatch, {"import_link": "NEWH"})
            up, cx, sent = msg_update(text="https://mega.nz/file/x#y")
            await bot.on_text(up, cx)
            final = sent.edit_text.call_args[0][0]
            assert bot.sb("SAVE HO GAYA") in final and "⚠️" not in final
        run_with_db(t)

    def test_quota_minus17_error(self, fake, monkeypatch):
        async def t(db):
            a1, _ = await add(db, "test_big3@example.com")
            fake.storage[a1["id"]] = (0, 20 * GB)
            patch_run(fake, monkeypatch, {
                "folder_link_info": {"name": "TEST_Huge", "size": 25 * GB, "files": 1, "folders": 1},
                "import_folder_link": raiser(ValueError("MEGA error -17")),
            })
            up, cx, sent = msg_update(text=FOLDER_URL)
            await bot.on_text(up, cx)
            final = sent.edit_text.call_args[0][0]
            assert "MEGA ne mana kar diya" in final and "storage full" in final and "/auto" in final
            assert "Link save nahi hua" not in final
        run_with_db(t)

    def test_quota_requesterror_text(self, fake, monkeypatch):
        from mega.errors import RequestError

        async def t(db):
            await add(db, "test_big4@example.com")
            patch_run(fake, monkeypatch, {"import_link": raiser(RequestError(-17))})
            up, cx, sent = msg_update(text="https://mega.nz/file/x#y")
            await bot.on_text(up, cx)
            final = sent.edit_text.call_args[0][0]
            assert "MEGA ne mana kar diya" in final and "/auto" in final
        run_with_db(t)

    def test_other_import_error(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_big5@example.com")
            patch_run(fake, monkeypatch, {"import_link": raiser(ValueError("boom ENOENT"))})
            up, cx, sent = msg_update(text="https://mega.nz/file/x#y")
            await bot.on_text(up, cx)
            final = sent.edit_text.call_args[0][0]
            assert "Link save nahi hua" in final and "boom ENOENT" in final
            assert "mana kar diya" not in final
        run_with_db(t)


# --- mega_service.search (real function, fake Mega session) ---
def fake_session():
    m = MagicMock()
    n = lambda h, p, t, name=None: (h, {"h": h, "p": p, "t": t, **({"a": {"n": name}} if name else {"a": False})})  # noqa: E731
    m.get_files.return_value = dict([
        n("ROOT", "", 2), n("INBOX", "", 3), n("BIN", "", 4),
        n("A", "ROOT", 1, "Movies"), n("B", "A", 1, "Hindi MOVIE pack"),
        n("F1", "B", 0, "movie1.mkv"), n("F2", "ROOT", 0, "Another.Movie.mp4"),
        n("F3", "BIN", 0, "deleted movie.mp4"), n("D1", "BIN", 1, "old movies"),
        n("F4", "INBOX", 0, "inbox movie"), n("F5", "A", 0, "notes.txt"),
        n("F6", "A", 0),
    ])
    m._root_node_id.return_value = "ROOT"
    return m


class TestSearchService:
    def test_case_insensitive_paths_order_excludes_bin_inbox(self):
        res = ms.search(fake_session(), "MoViE")
        names = [r["name"] for r in res]
        assert names == ["Hindi MOVIE pack", "Movies", "Another.Movie.mp4", "movie1.mkv"]
        by = {r["handle"]: r for r in res}
        assert by["A"]["path"] == "Cloud Drive" and by["A"]["is_file"] is False
        assert by["B"]["path"] == "Cloud Drive/Movies"
        assert by["F1"]["path"] == "Cloud Drive/Movies/Hindi MOVIE pack" and by["F1"]["is_file"] is True
        assert by["F2"]["path"] == "Cloud Drive"
        assert not {"F3", "D1", "F4"} & set(by)

    def test_no_match(self):
        assert ms.search(fake_session(), "zzzz") == []


# --- bot /search ---
def hit(h, name, is_file, path="Cloud Drive"):
    return {"handle": h, "name": name, "is_file": is_file, "path": path}


class TestCmdSearch:
    def test_usage_short_query(self, fake):
        async def t(db):
            await add(db, "test_q0@example.com")
            for args in ([], ["a"], [" "]):
                up, cx, _ = msg_update(args=args)
                await bot.cmd_search(up, cx)
                assert "Aise likho: /search" in up.message.reply_text.call_args[0][0]
        run_with_db(t)

    def test_no_accounts(self, fake):
        async def t(db):
            up, cx, _ = msg_update(args=["movie"])
            await bot.cmd_search(up, cx)
            assert "account jodo" in up.message.reply_text.call_args[0][0]
        run_with_db(t)

    def test_non_owner(self, fake):
        async def t(db):
            await add(db, "test_q1@example.com")
            up, cx, _ = msg_update(uid=OWNER + 7, args=["movie"])
            await bot.cmd_search(up, cx)
            assert "private" in up.message.reply_text.call_args[0][0]
            assert not any(c[1] == "search" for c in fake.calls)
        run_with_db(t)

    def test_results_across_accounts_with_failed(self, fake, monkeypatch):
        async def t(db):
            a1, _ = await add(db, "test_q2@example.com")
            a2, _ = await add(db, "test_q3@example.com")
            a3, _ = await add(db, "test_q4@example.com")
            data = {a1["id"]: [hit("FA", "Movies", False)], a2["id"]: [hit("FB", "movie.mkv", True, "Cloud Drive/X")]}

            async def search(acc, q):
                assert q == "my movie"
                if acc["id"] == a3["id"]:
                    raise RuntimeError("login fail")
                return data[acc["id"]]
            patch_run(fake, monkeypatch, {"search": search})
            up, cx, sent = msg_update(args=["my", "movie"])
            await bot.cmd_search(up, cx)
            assert [c[0] for c in fake.calls if c[1] == "search"] == [a1["id"], a2["id"], a3["id"]]
            text = sent.edit_text.call_args[0][0]
            kb = sent.edit_text.call_args[1]["reply_markup"]
            assert bot.sb("2 RESULTS") in text
            assert "👤 Account 1 ➥ Cloud Drive" in text and "👤 Account 2 ➥ Cloud Drive/X" in text
            assert "⚠️ In accounts se connect nahi hua: Account 3" in text
            assert text.endswith(bot.CREDIT)
            assert kb_data(kb) == [f"o:{a1['id']}:FA", f"i:{a2['id']}:FB"]
            assert all(len(d.encode()) <= 64 for d in kb_data(kb))
        run_with_db(t)

    def test_no_results(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_q5@example.com")
            patch_run(fake, monkeypatch, {"search": []})
            up, cx, sent = msg_update(args=["nothing"])
            await bot.cmd_search(up, cx)
            text = sent.edit_text.call_args[0][0]
            assert "kahin nahi mila" in text and text.endswith(bot.CREDIT)
        run_with_db(t)

    def test_max_30_and_more(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_q6@example.com")
            patch_run(fake, monkeypatch, {"search": [hit(f"H{i}", f"file{i}.mp4", True) for i in range(45)]})
            up, cx, sent = msg_update(args=["file"])
            await bot.cmd_search(up, cx)
            text = sent.edit_text.call_args[0][0]
            assert len(kb_data(sent.edit_text.call_args[1]["reply_markup"])) == 30
            assert "aur 15 results" in text and bot.sb("45 RESULTS") in text
            assert len(text) <= 4000
        run_with_db(t)

    def test_result_button_opens_non_active_account(self, fake, monkeypatch):
        async def t(db):
            await add(db, "test_q7@example.com")
            a2, _ = await add(db, "test_q8@example.com")
            from test_bot_handlers import cb_update
            upd, cx = cb_update(f"o:{a2['id']}:A1")
            await bot.on_button(upd, cx)
            assert (a2["id"], "list_folder", ("A1",)) in fake.calls
            assert "Alpha" in upd.callback_query.edit_message_text.call_args[0][0]
            upd, cx = cb_update(f"i:{a2['id']}:F1")
            await bot.on_button(upd, cx)
            assert (a2["id"], "node_info", ("F1",)) in fake.calls
            assert "a.mp4" in upd.callback_query.edit_message_text.call_args[0][0]
            assert (await bot.get_state()).get("active_account_id") != a2["id"]
        run_with_db(t)

    def test_search_registered_and_in_help(self):
        assert "/search" in bot.HELP and len(bot.HELP) <= 1024
        import inspect
        assert '("search", cmd_search)' in inspect.getsource(bot.start_bot)


