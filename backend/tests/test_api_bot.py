# API tests: /api/bot/status and /api/accounts (masking, no password_enc)
import os
import uuid
import requests
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


class TestBotStatus:
    def test_status(self):
        r = requests.get(f"{BASE_URL}/api/bot/status", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert d["running"] is True
        assert d["username"] == "Mega_handler_bot"
        assert d["owner_set"] is True
        assert d["error"] is None
        assert isinstance(d["auto_switch"], bool)


class TestAccounts:
    def test_accounts_masked_no_password(self):
        mc = MongoClient(os.environ["MONGO_URL"])
        col = mc[os.environ["DB_NAME"]].mega_accounts
        acc_id = "T" + uuid.uuid4().hex[:7]
        email = f"test_{acc_id.lower()}@example.com"
        col.insert_one({"id": acc_id, "label": "TEST_Acc", "email": email, "password_enc": "SECRET",
                        "used": 100, "total": 1000, "save_folder": None, "save_folder_name": "Cloud Drive",
                        "created_at": "2099-01-01T00:00:00+00:00"})
        try:
            r = requests.get(f"{BASE_URL}/api/accounts", timeout=20)
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, list)
            mine = [a for a in data if a["id"] == acc_id]
            assert len(mine) == 1
            a = mine[0]
            assert a["email"] == "te***@example.com"
            assert a["used"] == 100 and a["total"] == 1000
            assert "password_enc" not in r.text and "SECRET" not in r.text
            for x in data:
                assert "password_enc" not in x
        finally:
            col.delete_one({"id": acc_id})
            mc.close()
