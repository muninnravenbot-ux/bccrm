#!/usr/bin/env python3
"""Contacts CRM over cards.db: one entry per person, with status, star, tags, notes and inline edits.
Usage: python crm.py  ->  http://localhost:8770"""
import json, os, re, sqlite3, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import run  # coerce/save keep edited rows in the same shape the pipeline writes

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("BC_PORT", 8770))
EDITABLE = {"full_name", "job_title", "company", "emails", "phones", "telegram", "website", "linkedin", "address", "city", "country"}
STATUSES = ["New", "To contact", "Contacted", "Replied", "Meeting", "Not a fit"]


def connect():
    db = sqlite3.connect(ROOT / "cards.db")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS crm (card_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'New', starred INTEGER DEFAULT 0,
            tags TEXT DEFAULT '[]', updated_at TEXT);
        CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, card_id INTEGER, text TEXT, created_at TEXT DEFAULT (datetime('now')));""")
    return db


def data():
    db = connect()
    cards = db.execute("SELECT * FROM cards WHERE raw_json IS NOT NULL ORDER BY id").fetchall()
    photos, qrs = {}, {}
    for c in cards:  # every copy and back of a card hangs off its original, and so do their QR codes
        root = c["duplicate_of"] or c["id"]
        photos.setdefault(root, []).append(c["image_path"])
        for part in (c["qr_text"] or "").split("\n---\n"):
            if part.strip() and part.strip() not in qrs.setdefault(root, []):
                qrs[root].append(part.strip())
    crm = {r["card_id"]: r for r in db.execute("SELECT * FROM crm")}
    notes = {}
    for n in db.execute("SELECT * FROM notes ORDER BY created_at DESC"):
        notes.setdefault(n["card_id"], []).append({"id": n["id"], "text": n["text"], "at": n["created_at"]})
    js = lambda v: json.loads(v) if v else []
    people = []
    for c in cards:
        if c["duplicate_of"]:
            continue
        info = json.loads(c["link_info"]) if c["link_info"] else {}
        landed = {p["url"]: p.get("final_url") for p in info.get("pages", []) if p.get("final_url")}
        qr = []
        for part in qrs.get(c["id"], []):
            if part.startswith("BEGIN:VCARD"):
                qr.append({"kind": "vcard", "text": part})
            elif re.match(r"https?://(join\.)?skype\.com", part):
                continue  # Skype is dead
            elif re.match(r"https?://", part):
                qr.append({"kind": "link", "url": part, "final": landed.get(part) if landed.get(part) != part else None})
            else:
                qr.append({"kind": "text", "text": part})
        m = crm.get(c["id"])
        people.append({
            "id": c["id"], "name": c["full_name"], "title": c["job_title"], "company": c["company"], "company_key": c["company_key"],
            "emails": js(c["emails"]), "phones": js(c["phones"]), "telegram": js(c["telegram"]), "website": c["website"],
            "linkedin": c["linkedin"], "address": c["address"], "city": c["city"], "country": c["country"],
            "other": c["other_text"], "back": bool(c["is_back_side"]), "review": bool(c["needs_review"]),
            "photos": photos.get(c["id"], [c["image_path"]]), "source": c["source_photo"], "added": c["created_at"],
            "tg": {h: {"name": v.get("name"), "kind": v.get("kind")} for h, v in info.get("telegram", {}).items() if v.get("exists")},
            "qr": qr,
            "status": m["status"] if m else "New", "starred": bool(m["starred"]) if m else False,
            "tags": json.loads(m["tags"]) if m else [], "notes": notes.get(c["id"], []),
        })
    companies = {}
    for r in db.execute("SELECT * FROM companies"):
        companies[r["key"]] = {"name": r["name"], "website": r["website"], "about": r["about"], "what": r["what_they_do"],
                               "industry": r["industry"], "hq": r["hq"], "socials": json.loads(r["socials"] or "{}")}
    return {"people": people, "companies": companies, "statuses": STATUSES}


def update(card_id, body):
    db = connect()
    with db:
        db.execute("INSERT OR IGNORE INTO crm (card_id) VALUES (?)", (card_id,))
        if "status" in body and body["status"] in STATUSES:
            db.execute("UPDATE crm SET status=?, updated_at=datetime('now') WHERE card_id=?", (body["status"], card_id))
        if "starred" in body:
            db.execute("UPDATE crm SET starred=?, updated_at=datetime('now') WHERE card_id=?", (int(bool(body["starred"])), card_id))
        if "tags" in body:
            tags = sorted({t.strip() for t in body["tags"] if t.strip()})
            db.execute("UPDATE crm SET tags=?, updated_at=datetime('now') WHERE card_id=?", (json.dumps(tags, ensure_ascii=False), card_id))
    fields = {k: v for k, v in (body.get("fields") or {}).items() if k in EDITABLE}
    if fields:  # a hand edit is the best data there is: verified, never re-flagged
        d = run.coerce(json.loads(db.execute("SELECT raw_json FROM cards WHERE id=?", (card_id,)).fetchone()[0]))
        d.update(fields)
        d["confidence"], d["uncertain_fields"] = 1.0, []
        run.save(db, card_id, run.coerce(d))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def thumb(self, name):
        """Small JPEG for list rows, cached in thumbs/ and rebuilt when the crop changes (e.g. after it was turned upright)."""
        src, dst = ROOT / "cards" / name, ROOT / "thumbs" / name
        if not re.fullmatch(r"card_\d+\.jpg", name) or not src.exists():
            return self.send_error(404)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            import cv2
            img = cv2.imread(str(src))
            s = 240 / max(img.shape[:2])
            dst.parent.mkdir(exist_ok=True)
            cv2.imwrite(str(dst), cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        body = dst.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.path = self.path.split("?")[0].split("#")[0]
        if self.path == "/api/data":
            return self.reply(data())
        if self.path.startswith("/thumbs/"):
            return self.thumb(self.path[len("/thumbs/"):])
        if ".." in self.path:
            return self.send_error(404)
        built = (ROOT / "web/dist/index.html").exists()  # the OpenUI app (web/, `npm run build`); crm.html is the no-Node fallback
        if self.path in ("/", "/index.html"):
            self.path = "/web/dist/index.html" if built else "/crm.html"
        elif built and self.path.startswith("/assets/"):
            self.path = "/web/dist" + self.path
        if not (self.path in ("/crm.html", "/web/dist/index.html") or self.path.startswith(("/cards/", "/web/dist/assets/"))):
            return self.send_error(404)  # serve the app and card images only, never cards.db or photos/
        return super().do_GET()

    def do_POST(self):
        if m := re.fullmatch(r"/api/contact/(\d+)", self.path):
            update(int(m.group(1)), self.body())
            return self.reply({"ok": True})
        if m := re.fullmatch(r"/api/contact/(\d+)/notes", self.path):
            text = (self.body().get("text") or "").strip()
            if not text:
                return self.reply({"error": "Write something first"}, 400)
            db = connect()
            with db:
                cur = db.execute("INSERT INTO notes (card_id, text) VALUES (?, ?)", (int(m.group(1)), text))
            row = db.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone()
            return self.reply({"id": row["id"], "text": row["text"], "at": row["created_at"]})
        self.send_error(404)

    def do_DELETE(self):
        if m := re.fullmatch(r"/api/notes/(\d+)", self.path):
            db = connect()
            with db:
                db.execute("DELETE FROM notes WHERE id=?", (int(m.group(1)),))
            return self.reply({"ok": True})
        self.send_error(404)


if __name__ == "__main__":
    connect().close()
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"  # local only: this is your contact book
    print(f"Contacts: http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()
