import asyncio
import json
import os
import re
from cryptography.fernet import Fernet
from mega import Mega
from mega.crypto import a32_to_base64, base64_to_a32, base64_url_decode, base64_url_encode, decrypt_attr, decrypt_key, encrypt_attr, encrypt_key

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


def is_folder_link(url):
    return "/folder/" in url or "#F!" in url


def parse_folder_link(url):
    m = re.search(r"/folder/([\w-]+)#([\w-]+)", url) or re.search(r"#F!([\w-]+)!([\w-]+)", url)
    if not m:
        raise ValueError("Folder link sahi nahi hai")
    return m.group(1), m.group(2)


def _name_key(k):
    return (k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]) if len(k) == 8 else k


def _public_folder_nodes(m, url):
    fh, fk = parse_folder_link(url)
    folder_key = base64_to_a32(fk)
    params = {"id": m.sequence_num, "n": fh}
    m.sequence_num += 1
    r = m._http.post(f"{m.schema}://g.api.{m.domain}/cs", params=params, content=json.dumps([{"a": "f", "c": 1, "r": 1}]),
                     headers={"Content-Type": "application/json"})
    data = r.json()
    data = data[0] if isinstance(data, list) else data
    if isinstance(data, int):
        raise ValueError(f"Folder link nahi khula (MEGA error {data})")
    nodes = []
    for n in data["f"]:
        key = decrypt_key(base64_to_a32(n["k"].split("/")[0].split(":")[1]), folder_key)
        attrs = decrypt_attr(base64_url_decode(n["a"]), _name_key(key)) or {}
        nodes.append({**n, "key": key, "name": attrs.get("n", "?")})
    return nodes


def folder_link_info(m, url):
    nodes = _public_folder_nodes(m, url)
    ids = {n["h"] for n in nodes if n["t"] == 1}
    root = next((n for n in nodes if n["p"] not in ids), nodes[0])
    files = [n for n in nodes if n["t"] == 0]
    return {"name": root["name"], "size": sum(n.get("s", 0) for n in files), "files": len(files), "folders": len(ids)}


def import_folder_link(m, url, dest=None):
    nodes = _public_folder_nodes(m, url)
    ids = {n["h"] for n in nodes if n["t"] == 1}
    items = []
    for n in nodes:
        item = {"h": n["h"], "t": n["t"], "a": n["a"], "k": a32_to_base64(encrypt_key(n["key"], m.master_key))}
        if n["p"] in ids:
            item["p"] = n["p"]
        items.append(item)
    res = m._api_request({"a": "p", "t": dest or m._root_node_id(), "n": items, "v": 3, "i": m.request_id, "sm": 1})
    if isinstance(res, int) and res < 0:
        raise ValueError(f"MEGA error {res}")


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
    file_list = sorted(
        [(h, f["a"]["n"]) for h, f in files.items() if f.get("t") == 0 and f.get("p") == handle and f.get("a")],
        key=lambda x: x[1].lower(),
    )
    file_count = len(file_list)
    parent = None if handle == root else node.get("p")
    return {"handle": handle, "name": name, "is_root": handle == root, "parent": parent, "folders": subs, "files": file_list, "file_count": file_count}


def node_info(m, handle):
    files = m.get_files()
    f = files[handle]
    return {"handle": handle, "name": f["a"]["n"], "is_file": f["t"] == 0, "size": f.get("s", 0), "parent": f.get("p")}


def delete_node(m, handle):
    m.move(handle, 4)


def move_node(m, handle, dest):
    if handle == dest:
        raise ValueError("Folder ko khud mein move nahi kar sakte")
    files = m.get_files()
    p = dest
    while p in files:
        if p == handle:
            raise ValueError("Folder ko uske andar wale folder mein move nahi kar sakte")
        p = files[p].get("p")
    m.move(handle, dest)


def share_link(m, handle):
    link = m.export(node_id=handle)
    hit = re.search(r"#(F?)!([\w-]+)!([\w-]+)", link)
    kind = "folder" if hit.group(1) else "file"
    return f"https://mega.nz/{kind}/{hit.group(2)}#{hit.group(3)}"


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
