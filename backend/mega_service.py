import asyncio
import os
import re
from cryptography.fernet import Fernet
from mega import Mega

_fernet = Fernet(os.environ["MEGA_SECRET_KEY"].encode())
_sessions: dict = {}
_locks: dict = {}

LINK_RE = re.compile(r"https?://(?:www\.)?mega\.(?:nz|co\.nz|io)/\S+", re.I)


def encrypt(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def _login(email: str, password: str):
    return Mega().login(email, password)


async def login_check(email: str, password: str):
    return await asyncio.to_thread(_login, email, password)


def forget(acc_id: str):
    _sessions.pop(acc_id, None)


async def run(acc: dict, fn, *args):
    lock = _locks.setdefault(acc["id"], asyncio.Lock())
    async with lock:
        for attempt in (1, 2):
            if acc["id"] not in _sessions:
                _sessions[acc["id"]] = await asyncio.to_thread(_login, acc["email"], decrypt(acc["password_enc"]))
            try:
                return await asyncio.to_thread(fn, _sessions[acc["id"]], *args)
            except Exception:
                forget(acc["id"])
                if attempt == 2:
                    raise


def storage(m):
    s = m.get_storage_space()
    return {"used": int(s["used"]), "total": int(s["total"])}


def list_folder(m, handle=None):
    files = m.get_files()
    root = m._root_node_id()
    handle = handle if handle in files else root
    node = files[handle]
    name = "Cloud Drive" if handle == root else node["a"]["n"]
    subs = sorted(
        [(h, f["a"]["n"]) for h, f in files.items() if f.get("t") == 1 and f.get("p") == handle and f.get("a")],
        key=lambda x: x[1].lower(),
    )
    file_count = sum(1 for f in files.values() if f.get("t") == 0 and f.get("p") == handle)
    parent = None if handle == root else node.get("p")
    return {"handle": handle, "name": name, "is_root": handle == root, "parent": parent, "folders": subs, "file_count": file_count}


def mkdir(m, name, parent=None):
    parent = parent or m._root_node_id()
    data = m._mkdir(name=name, parent_node_id=parent)
    return data["f"][0]["h"]


def rename(m, handle, new_name):
    files = m.get_files()
    m.rename((handle, files[handle]), new_name)


def link_info(m, url):
    fh, fk = m._parse_url(url).split("!")
    info = m.get_public_file_info(fh, fk)
    return {"handle": fh, "key": fk, "name": info["name"], "size": info["size"]}


def import_link(m, url, dest=None):
    info = link_info(m, url)
    dest = dest or m._root_node_id()
    m.import_public_file(info["handle"], info["key"], dest_node={"h": dest}, dest_name=info["name"])
    return info
