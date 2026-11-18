# /newaccount MEGA signup (register/confirm), _new_handle, and share-link reply after save (all MEGA calls mocked)
import hashlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND / ".env")
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mega.crypto import a32_to_str, base64_to_a32, base64_url_decode, base64_url_encode, decrypt_key, encrypt_key, str_to_a32  # noqa: E402
import bot  # noqa: E402
import mega_service as ms  # noqa: E402
from test_bot_handlers import add, msg_update, run_with_db  # noqa: E402

EMAIL = "test_signup@example.com"
CODE = b"ConfirmCodeV2" + b"\x01" * 20 + EMAIL.encode()
B64 = base64_url_encode(CODE)


# ---------- mega_service.register ----------
class TestRegister:
    def _setup(self, monkeypatch, sid="SID123"):
        calls, seen = [], {}

        def api(self, data):
            calls.append(data)
            return {"up": "USERHANDLE", "us": {"k": "x", "tsid": "y"}, "ug": {"u": "USERHANDLE"}, "uc2": 0}[data["a"]]

        def login_process(self, resp, ek):
            seen["ek"], seen["resp"] = ek, resp
            self.sid = sid
        monkeypatch.setattr(ms.Mega, "_api_request", api)
        monkeypatch.setattr(ms.Mega, "_login_process", login_process)
        return calls, seen

    def test_uc2_payload(self, monkeypatch):
        calls, seen = self._setup(monkeypatch)
        pw, name = "TEST_pass1234", "test_signup"
        ms.register(EMAIL, pw, name)
        assert [c["a"] for c in calls] == ["up", "us", "ug", "uc2"]
        up, us, uc2 = calls[0], calls[1], calls[3]
        assert us["user"] == "USERHANDLE"
        assert seen["resp"] == {"k": "x", "tsid": "y"}
        # master key from 'up' (encrypted with ephemeral key passed to _login_process)
        mk = decrypt_key(base64_to_a32(up["k"]), seen["ek"])
        ts = base64_url_decode(up["ts"])
        assert len(ts) == 32 and a32_to_str(encrypt_key(str_to_a32(ts[:16]), mk)) == ts[16:]
        assert base64_url_decode(uc2["n"]) == name.encode()
        assert base64_url_decode(uc2["m"]) == EMAIL.encode()
        assert uc2["v"] == 2
        crv = base64_url_decode(uc2["crv"])
        hak = base64_url_decode(uc2["hak"])
        assert len(crv) == 16 and len(hak) == 16
        salt = hashlib.sha256(b"mega.nz".ljust(200, b"P") + crv).digest()
        dk = hashlib.pbkdf2_hmac("sha512", pw.encode(), salt, 100000, 32)
        assert hak == hashlib.sha256(dk[16:]).digest()[:16]
        assert tuple(decrypt_key(base64_to_a32(uc2["k"]), str_to_a32(dk[:16]))) == tuple(mk)

    def test_no_sid_raises(self, monkeypatch):
        calls, _ = self._setup(monkeypatch, sid=None)
        with pytest.raises(ValueError):
            ms.register(EMAIL, "TEST_pass1234", "x")
        assert [c["a"] for c in calls] == ["up", "us"]


# ---------- confirm_code / confirm_signup / _new_handle ----------
class TestConfirm:
    def test_confirm_code_variants(self):
        assert ms.confirm_code(f"https://mega.nz/#confirm{B64}") == CODE
        assert ms.confirm_code(f"  confirm{B64}\n") == CODE
        assert ms.confirm_code(B64) == CODE
        assert ms.confirm_code(f"here: https://mega.nz/confirm{B64} thanks") == CODE
        for t in ["hello there", "MyFolderName", "https://mega.nz/file/abc#def", "नमस्ते", "",
                  "confirm" + base64_url_encode(b"x" * 30)]:
            assert ms.confirm_code(t) is None, t

    def test_confirm_signup_parses(self, monkeypatch):
        sent = []

        def api(self, data):
            sent.append(data)
            return ["x", [base64_url_encode(b"Test_Signup@Example.COM"), base64_url_encode(b"n"), "UH", 2]]
        monkeypatch.setattr(ms.Mega, "_api_request", api)
        assert ms.confirm_signup(CODE) == EMAIL
        assert sent[0] == {"a": "ud2", "c": B64}
        # flat list response also accepted
        monkeypatch.setattr(ms.Mega, "_api_request", lambda self, d: [base64_url_encode(EMAIL.encode()), "n", "UH", 2])
        assert ms.confirm_signup(CODE) == EMAIL

    def test_confirm_signup_bad(self, monkeypatch):
        for res in (-9, "weird", [1, 2], []):
            monkeypatch.setattr(ms.Mega, "_api_request", lambda self, d, r=res: r)
            with pytest.raises(Exception):
                ms.confirm_signup(CODE)

    def test_new_handle_and_imports(self):
        res = {"f": [{"h": "CHILD", "p": "ROOTNEW"}, {"h": "ROOTNEW", "p": "DEST"}]}
        assert ms._new_handle(res, "DEST") == "ROOTNEW"
        assert ms._new_handle({"f": [{"h": "ONLY", "p": "X"}]}, "DEST") == "ONLY"
        assert ms._new_handle(0, "DEST") is None and ms._new_handle({}, "DEST") is None
        m = MagicMock()
        m._parse_url.return_value = "fh!fk"
        m.get_public_file_info.return_value = {"name": "a.mp4", "size": 5}
        m._root_node_id.return_value = "ROOT"
        m.import_public_file.return_value = {"f": [{"h": "NEWFILE", "p": "ROOT"}]}
        assert ms.import_link(m, "https://mega.nz/file/fh#fk") == "NEWFILE"
        assert m.import_public_file.call_args.kwargs["dest_node"] == {"h": "ROOT"}


# ---------- bot /newaccount + confirm flow ----------
class TestNewAccountBot:
    def test_newaccount_validation_and_store(self, fake, monkeypatch):
        reg = MagicMock()
        monkeypatch.setattr(ms, "register", reg)

        async def t(db):
            up, cx, _ = msg_update(args=["only"])
            await bot.cmd_newaccount(up, cx)
            assert "Aise likho" in up.message.reply_text.call_args[0][0]
            up, cx, _ = msg_update(args=[EMAIL, "short"])
            await bot.cmd_newaccount(up, cx)
            up.message.delete.assert_awaited()
            assert "8 characters" in up.effective_chat.send_message.call_args[0][0]
            reg.assert_not_called()
            await add(db, "test_dup@example.com")
            up, cx, _ = msg_update(args=["TEST_Dup@example.com", "longpass1"])
            await bot.cmd_newaccount(up, cx)
            assert "pehle se" in up.effective_chat.send_message.call_args[0][0]
            reg.assert_not_called()
            up, cx, sent = msg_update(args=["TEST_Signup@Example.com", "longpass1"])
            await bot.cmd_newaccount(up, cx)
            up.message.delete.assert_awaited()
            reg.assert_called_once_with(EMAIL, "longpass1", "test_signup")
            doc = await db.mega_signups.find_one({"email": EMAIL})
            assert doc and doc["password_enc"] != "longpass1" and ms.decrypt(doc["password_enc"]) == "longpass1"
            assert "confirmation link" in sent.edit_text.call_args[0][0]
        run_with_db(t)

    def test_newaccount_register_fail(self, fake, monkeypatch):
        monkeypatch.setattr(ms, "register", MagicMock(side_effect=Exception("EEXIST")))

        async def t(db):
            up, cx, sent = msg_update(args=[EMAIL, "longpass1"])
            await bot.cmd_newaccount(up, cx)
            assert "Signup nahi hua" in sent.edit_text.call_args[0][0]
            assert await db.mega_signups.count_documents({}) == 0
        run_with_db(t)

    def test_confirm_link_finishes_signup(self, fake, monkeypatch):
        got = []
        monkeypatch.setattr(ms, "confirm_signup", lambda c: got.append(c) or EMAIL)

        async def t(db):
            await db.mega_signups.insert_one({"email": EMAIL, "password_enc": ms.encrypt("longpass1")})
            up, cx, sent = msg_update(text=f"https://mega.nz/#confirm{B64}")
            await bot.on_text(up, cx)
            assert got == [CODE]
            ms.login_check.assert_awaited_with(EMAIL, "longpass1")
            acc = await db.mega_accounts.find_one({"email": EMAIL}, {"_id": 0})
            assert acc and ms.decrypt(acc["password_enc"]) == "longpass1" and acc["label"] == "Account 1"
            assert (await bot.get_state())["active_account_id"] == acc["id"]
            assert await db.mega_signups.count_documents({}) == 0
            txt = sent.edit_text.call_args[0][0]
            assert "tayyar" in txt and EMAIL in txt
            assert not any(c[1] in ("link_info", "import_link") for c in fake.calls)
        run_with_db(t)

    def test_confirm_unknown_email(self, fake, monkeypatch):
        monkeypatch.setattr(ms, "confirm_signup", lambda c: "other_test@example.com")

        async def t(db):
            up, cx, sent = msg_update(text=f"https://mega.nz/#confirm{B64}")
            await bot.on_text(up, cx)
            assert "/addaccount other_test@example.com" in sent.edit_text.call_args[0][0]
            assert await db.mega_accounts.count_documents({}) == 0
        run_with_db(t)

    def test_confirm_fail_and_login_fail(self, fake, monkeypatch):
        monkeypatch.setattr(ms, "confirm_signup", MagicMock(side_effect=ValueError("bad")))

        async def t(db):
            await db.mega_signups.insert_one({"email": EMAIL, "password_enc": ms.encrypt("longpass1")})
            up, cx, sent = msg_update(text=f"https://mega.nz/#confirm{B64}")
            await bot.on_text(up, cx)
            assert "Confirm nahi hua" in sent.edit_text.call_args[0][0]
            monkeypatch.setattr(ms, "confirm_signup", lambda c: EMAIL)
            monkeypatch.setattr(ms, "login_check", AsyncMock(side_effect=Exception("blocked")))
            up, cx, sent = msg_update(text=f"https://mega.nz/#confirm{B64}")
            await bot.on_text(up, cx)
            assert "login nahi hua" in sent.edit_text.call_args[0][0]
            assert await db.mega_accounts.count_documents({}) == 0
            assert await db.mega_signups.count_documents({"email": EMAIL}) == 1
        run_with_db(t)


# ---------- save_link share-link reply ----------
class TestShareAfterSave:
    def _wrap(self, fake, monkeypatch, share_exc=None):
        orig = fake.run

        async def run(acc, fn, *args):
            if fn.__name__ == "import_link":
                fake.calls.append((acc["id"], "import_link", args))
                return "NEWH"
            if fn.__name__ == "share_link" and share_exc:
                fake.calls.append((acc["id"], "share_link", args))
                raise share_exc
            return await orig(acc, fn, *args)
        monkeypatch.setattr(ms, "run", run)

    def test_share_link_in_reply(self, fake, monkeypatch):
        self._wrap(fake, monkeypatch)

        async def t(db):
            a1, _ = await add(db, "test_sh1@example.com")
            up, cx, sent = msg_update(text="https://mega.nz/file/abc#def")
            await bot.on_text(up, cx)
            assert (a1["id"], "share_link", ("NEWH",)) in fake.calls
            txt = sent.edit_text.call_args[0][0]
            assert "Save ho gaya" in txt and "Aapke account ka link" in txt and "https://mega.nz/file/F1#KEY" in txt
        run_with_db(t)

    def test_share_failure_still_saved(self, fake, monkeypatch):
        self._wrap(fake, monkeypatch, share_exc=RuntimeError("export down"))

        async def t(db):
            await add(db, "test_sh2@example.com")
            up, cx, sent = msg_update(text="https://mega.nz/file/abc#def")
            await bot.on_text(up, cx)
            txt = sent.edit_text.call_args[0][0]
            assert "Save ho gaya" in txt and "Share link nahi bana" in txt and "export down" in txt
            assert "Link save nahi hua" not in txt
        run_with_db(t)

    def test_no_handle_no_share(self, fake):
        async def t(db):
            await add(db, "test_sh3@example.com")
            up, cx, sent = msg_update(text="https://mega.nz/folder/abc#key")
            await bot.on_text(up, cx)
            assert not any(c[1] == "share_link" for c in fake.calls)
            assert "Save ho gaya" in sent.edit_text.call_args[0][0]
        run_with_db(t)
