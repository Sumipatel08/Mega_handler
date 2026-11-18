# Folder link saving (mega_service crypto with mocked HTTP) + file actions (share/rename/move/delete) in bot.py
import json
import random
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND / ".env")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mega.crypto import a32_to_base64, base64_to_a32, base64_url_encode, decrypt_attr, decrypt_key, encrypt_attr, encrypt_key, base64_url_decode  # noqa: E402
import bot  # noqa: E402
import mega_service as ms  # noqa: E402
from test_bot_handlers import FakeMega, add, cb_update, kb_data, msg_update, run_with_db  # noqa: E402,F401

GB = 1073741824


def rnd(n):
    return tuple(random.getrandbits(32) for _ in range(n))


# ---------- parse_folder_link / is_folder_link ----------
class TestParse:
    def test_new_and_old_formats(self):
        assert ms.parse_folder_link("https://mega.nz/folder/AbC-12_x#Key_-9z") == ("AbC-12_x", "Key_-9z")
        assert ms.parse_folder_link("https://mega.nz/#F!AbC12x!Key9z") == ("AbC12x", "Key9z")
        assert ms.parse_folder_link("https://mega.nz/folder/AbC#Key/folder/sub") == ("AbC", "Key")
        with pytest.raises(ValueError):
            ms.parse_folder_link("https://mega.nz/folder/AbC")
        assert ms.is_folder_link("https://mega.nz/folder/a#b") and ms.is_folder_link("https://mega.nz/#F!a!b")
        assert not ms.is_folder_link("https://mega.nz/file/a#b") and not ms.is_folder_link("https://mega.nz/#!a!b")


# ---------- realistic encrypted public folder ----------
def build_folder():
    folder_key = rnd(4)
    master = rnd(4)
    root_key, sub_key = rnd(4), rnd(4)
    f1_key, f2_key = rnd(8), rnd(8)

    def node(h, p, t, key, name, s=None):
        ak = ms._name_key(key)
        n = {"h": h, "p": p, "t": t, "u": "owner", "a": base64_url_encode(encrypt_attr({"n": name}, ak)),
             "k": f"ROOTH:{a32_to_base64(encrypt_key(key, folder_key))}"}
        if s is not None:
            n["s"] = s
        return n
    nodes = [
        node("ROOTH", "OUTSIDE", 1, root_key, "TEST_Root"),
        node("SUBH", "ROOTH", 1, sub_key, "TEST_Sub"),
        node("F1H", "ROOTH", 0, f1_key, "movie.mp4", 2 * GB),
        node("F2H", "SUBH", 0, f2_key, "doc.pdf", 1000),
    ]
    keys = {"ROOTH": root_key, "SUBH": sub_key, "F1H": f1_key, "F2H": f2_key}
    url = f"https://mega.nz/folder/ROOTH#{a32_to_base64(folder_key)}"
    m = MagicMock()
    m.schema, m.domain, m.sequence_num, m.master_key, m.request_id = "https", "mega.co.nz", 100, master, "REQID"
    m._http.post.return_value.json.return_value = {"f": nodes}
    m._root_node_id.return_value = "MYROOT"
    m._api_request.return_value = {"f": []}
    return m, url, nodes, keys, master


class TestFolderCrypto:
    def test_public_nodes_decrypt(self):
        m, url, nodes, keys, _ = build_folder()
        out = ms._public_folder_nodes(m, url)
        assert [n["name"] for n in out] == ["TEST_Root", "TEST_Sub", "movie.mp4", "doc.pdf"]
        for n in out:
            assert tuple(n["key"]) == keys[n["h"]]
        args, kw = m._http.post.call_args
        assert args[0] == "https://g.api.mega.co.nz/cs"
        assert kw["params"] == {"id": 100, "n": "ROOTH"}
        assert json.loads(kw["content"]) == [{"a": "f", "c": 1, "r": 1}]
        assert m.sequence_num == 101

    def test_list_response_and_error(self):
        m, url, nodes, _, _ = build_folder()
        m._http.post.return_value.json.return_value = [{"f": nodes}]
        assert len(ms._public_folder_nodes(m, url)) == 4
        m._http.post.return_value.json.return_value = -9
        with pytest.raises(ValueError, match="-9"):
            ms._public_folder_nodes(m, url)
        m._http.post.return_value.json.return_value = [-11]
        with pytest.raises(ValueError, match="-11"):
            ms._public_folder_nodes(m, url)

    def test_folder_link_info(self):
        m, url, *_ = build_folder()
        info = ms.folder_link_info(m, url)
        assert info == {"name": "TEST_Root", "size": 2 * GB + 1000, "files": 2, "folders": 2}

    def test_import_payload(self):
        m, url, nodes, keys, master = build_folder()
        ms.import_folder_link(m, url, "DEST")
        req = m._api_request.call_args[0][0]
        assert req["a"] == "p" and req["t"] == "DEST" and req["v"] == 3 and req["sm"] == 1 and req["i"] == "REQID"
        items = {i["h"]: i for i in req["n"]}
        assert set(items) == {"ROOTH", "SUBH", "F1H", "F2H"}
        assert "p" not in items["ROOTH"]
        assert items["SUBH"]["p"] == "ROOTH" and items["F1H"]["p"] == "ROOTH" and items["F2H"]["p"] == "SUBH"
        src = {n["h"]: n for n in nodes}
        for h, it in items.items():
            assert it["a"] == src[h]["a"] and it["t"] == src[h]["t"]
            assert tuple(decrypt_key(base64_to_a32(it["k"]), master)) == keys[h]
            # attrs still decryptable with re-wrapped key
            k = decrypt_key(base64_to_a32(it["k"]), master)
            assert decrypt_attr(base64_url_decode(it["a"]), ms._name_key(k))["n"]

    def test_import_default_dest_and_error(self):
        m, url, *_ = build_folder()
        ms.import_folder_link(m, url)
        assert m._api_request.call_args[0][0]["t"] == "MYROOT"
        m._api_request.return_value = -17
        with pytest.raises(ValueError, match="-17"):
            ms.import_folder_link(m, url)


# ---------- move_node / share_link ----------
class TestNodeOps:
    def files(self):
        return {"R": {"p": ""}, "A": {"p": "R"}, "B": {"p": "A"}, "C": {"p": "B"}, "X": {"p": "R"}}

    def test_move_rejects_self_and_descendant(self):
        m = MagicMock()
        m.get_files.return_value = self.files()
        for dest in ("A", "B", "C"):
            with pytest.raises(ValueError):
                ms.move_node(m, "A", dest)
        m.move.assert_not_called()
        ms.move_node(m, "A", "X")
        m.move.assert_called_once_with("A", "X")
        ms.move_node(m, "C", "R")
        m.move.assert_called_with("C", "R")

    def test_share_link_conversion(self):
        m = MagicMock()
        m.export.return_value = "https://mega.co.nz/#!fileH!fileK-_1"
        assert ms.share_link(m, "h") == "https://mega.nz/file/fileH#fileK-_1"
        m.export.assert_called_with(node_id="h")
        m.export.return_value = "https://mega.co.nz/#F!folH!folK"
        assert ms.share_link(m, "h") == "https://mega.nz/folder/folH#folK"

    def test_delete_moves_to_rubbish(self):
        m = MagicMock()
        ms.delete_node(m, "h")
        m.move.assert_called_once_with("h", 4)


# ---------- bot folder view + item actions ----------
def edited(u):
    return u.callback_query.edit_message_text.call_args


class TestBotFileActions:
    def test_item_menu_share_delete(self, fake):
        async def t(db):
            a, _ = await add(db, "test_fa1@example.com")
            A = a["id"]
            # file item menu
            u, c = cb_update(f"i:{A}:F1")
            await bot.on_button(u, c)
            d = kb_data(edited(u).kwargs["reply_markup"])
            assert d == [f"sh:{A}:F1", f"r:{A}:F1", f"m:{A}:F1", f"d:{A}:F1", f"o:{A}:A1"]
            assert "a.mp4" in edited(u)[0][0] and "Size" in edited(u)[0][0]
            # folder item menu has Kholo
            u, c = cb_update(f"i:{A}:A1")
            await bot.on_button(u, c)
            d = kb_data(edited(u).kwargs["reply_markup"])
            assert f"o:{A}:A1" in d and f"o:{A}:R" in d
            # share
            u, c = cb_update(f"sh:{A}:F1")
            await bot.on_button(u, c)
            assert "https://mega.nz/file/F1#KEY" in u.callback_query.message.reply_text.call_args[0][0]
            # delete confirm
            u, c = cb_update(f"d:{A}:F1")
            await bot.on_button(u, c)
            assert kb_data(edited(u).kwargs["reply_markup"]) == [f"dx:{A}:F1", f"i:{A}:F1"]
            assert not any(x[1] == "delete_node" for x in fake.calls)
            # dx deletes save folder -> reset
            await db.mega_accounts.update_one({"id": A}, {"$set": {"save_folder": "A1", "save_folder_name": "Alpha"}})
            u, c = cb_update(f"dx:{A}:A1")
            await bot.on_button(u, c)
            assert (A, "delete_node", ("A1",)) in fake.calls
            acc = await bot.get_account(A)
            assert acc["save_folder"] is None and acc["save_folder_name"] == "Cloud Drive"
            assert "Rubbish Bin" in u.callback_query.message.reply_text.call_args[0][0]
            assert f"o:{A}:A1" in kb_data(edited(u).kwargs["reply_markup"])  # back at root view
        run_with_db(t)

    def test_move_flow(self, fake):
        async def t(db):
            a, _ = await add(db, "test_fa2@example.com")
            b, _ = await add(db, "test_fa2b@example.com")
            A = a["id"]
            u, c = cb_update(f"m:{A}:F1")
            await bot.on_button(u, c)
            assert (await bot.get_state())["moving"] == {"acc": A, "h": "F1", "name": "a.mp4"}
            txt, kb = edited(u)[0][0], edited(u).kwargs["reply_markup"]
            d = kb_data(kb)
            assert "Move:" in txt and "a.mp4" in txt
            assert f"mv:{A}:A1" in d and f"mc:{A}:A1" in d
            assert not any(x.startswith(("i:", "s:", "n:")) for x in d)
            # another account's view is not in move mode
            text, kb2 = await bot.folder_view(await bot.get_account(b["id"]), None)
            d2 = kb_data(kb2)
            assert not any(x.startswith("mv:") for x in d2) and f"s:{b['id']}:R" in d2
            # navigate and move
            u, c = cb_update(f"mv:{A}:B1")
            await bot.on_button(u, c)
            assert (A, "move_node", ("F1", "B1")) in fake.calls
            assert (await bot.get_state())["moving"] is None
            assert "move ho gaya" in u.callback_query.message.reply_text.call_args[0][0]
            # cancel
            u, c = cb_update(f"m:{A}:F2")
            await bot.on_button(u, c)
            u, c = cb_update(f"mc:{A}:A1")
            await bot.on_button(u, c)
            assert (await bot.get_state())["moving"] is None
            assert not any(x[1] == "move_node" and x[2][0] == "F2" for x in fake.calls)
            d = kb_data(edited(u).kwargs["reply_markup"])
            assert f"s:{A}:A1" in d and f"i:{A}:F1" in d
        run_with_db(t)

    def test_rename_file_returns_to_parent(self, fake):
        async def t(db):
            a, _ = await add(db, "test_fa3@example.com")
            A = a["id"]
            u, c = cb_update(f"r:{A}:F2")
            await bot.on_button(u, c)
            assert (await bot.get_state())["pending"] == {"type": "rename", "h": "F2", "acc": A, "back": "A1"}
            up, cx, _ = msg_update(text="TEST_b2.pdf")
            await bot.on_text(up, cx)
            assert (A, "rename", ("F2", "TEST_b2.pdf")) in fake.calls
            # last list_folder call is for parent A1, not the file handle
            lf = [x for x in fake.calls if x[1] == "list_folder"]
            assert lf[-1][2] == ("A1",)
            kb = up.message.reply_text.call_args_list[-1].kwargs["reply_markup"]
            assert f"i:{A}:F2" in kb_data(kb)
        run_with_db(t)

    def test_unknown_account_callback(self, fake):
        async def t(db):
            u, c = cb_update("i:nope:F1")
            await bot.on_button(u, c)
            assert "juda nahi" in u.callback_query.message.reply_text.call_args[0][0]
        run_with_db(t)
