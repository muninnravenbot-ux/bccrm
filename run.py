#!/usr/bin/env python3
"""Business-card pipeline: ./photos -> crop -> QR + GLM extract -> cards.db -> group by company -> web enrich -> ./export.
Usage: python run.py"""
import base64, csv, html, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
PHOTOS, CARDS, DEBUG, EXPORT = (ROOT / d for d in ("photos", "cards", "debug", "export"))
DB = ROOT / "cards.db"
# Any OpenAI-compatible chat-completions endpoint works. Defaults: Z.AI Coding Plan (its /api/coding/ endpoint is covered
# by the plan; the general /api/paas/v4/ endpoint bills the API balance). glm-5.3-flash is the plan's only vision model.
ZAI_URL = os.environ.get("BC_CHAT_URL", "https://api.z.ai/api/coding/paas/v4/chat/completions")
ZAI_KEY = os.environ.get("BC_API_KEY") or os.environ.get("ZAI_API_KEY", "")
MODEL = os.environ.get("BC_VISION_MODEL", "glm-5.3-flash")  # ponytail: glm-5.3-flash garbles Hebrew/RTL; use a stronger vision model if RTL cards matter
TEXT_MODEL = os.environ.get("BC_TEXT_MODEL", "glm-5.3")  # reads QR pages and web results
SEARCH_MCP = os.environ.get("BC_SEARCH_MCP", "https://api.z.ai/api/mcp/web_search_prime/mcp")  # set to "" to skip company enrichment
EXTRA = {"thinking": {"type": "disabled"}} if "z.ai" in ZAI_URL else {}  # GLM reasons by default and can spend all tokens on it
# Fallback model, used where the main one fails on real cards: its content filter refuses a card (Z.AI blocked gambling
# cards), or the card is in a right-to-left script (glm-5.3-flash reversed Hebrew names). A second OpenAI-compatible
# endpoint if configured, otherwise the Claude Code CLI if it is installed and logged in. BC_FALLBACK=off disables it.
FALLBACK_URL = os.environ.get("BC_FALLBACK_CHAT_URL", "")
FALLBACK_KEY = os.environ.get("BC_FALLBACK_API_KEY", "")
FALLBACK_MODEL = os.environ.get("BC_FALLBACK_MODEL", "claude-sonnet-5-5")
CLAUDE_CLI = None if os.environ.get("BC_FALLBACK") == "off" else shutil.which("claude")
RTL = re.compile(r"[֐-ࣿ]")  # Hebrew, Arabic, Syriac, Thaana...
WORKERS = 4
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "me.com",
            "mail.ru", "yandex.ru", "ya.ru", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "aol.com", "ukr.net"}
# link-in-bio / social hosts: many unrelated companies share them, so they never identify a company
SHARED_HOSTS = {"mssg.me", "linktr.ee", "t.me", "taplink.cc", "bio.link", "linkedin.com", "wa.me", "instagram.com",
                "facebook.com", "x.com", "twitter.com", "youtube.com", "scanned.page", "qrco.de", "bit.ly", "beacons.ai"}

FIELDS = ["full_name", "first_name", "last_name", "company", "job_title", "emails", "phones", "telegram", "website",
          "address", "city", "country", "linkedin", "other_text", "language", "is_back_side", "confidence",
          "uncertain_fields"]
LIST_FIELDS = {"emails", "phones", "telegram", "uncertain_fields"}
STR = {"type": ["string", "null"]}
SCHEMA = {"type": "object", "required": FIELDS, "properties": {
    **{f: STR for f in FIELDS},
    **{f: {"type": "array", "items": {"type": "string"}} for f in LIST_FIELDS},
    "is_back_side": {"type": "boolean"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}

PROMPT = """Extract the contact data from the business card image ({path}).
Rules:
- Any language/script. Keep original spelling and script exactly; never translate or transliterate.
- Phones: E.164 (e.g. +40721234567) when the country is clear from the number, address or domain; otherwise as printed.
- telegram: Telegram handles as "@handle" (from "TG:", the Telegram icon, or t.me/ links).
- Skype is a dead service: ignore Skype IDs completely (also not in other_text).
- website: only if printed or in the QR; never guess it.
- is_back_side: true if this looks like the back of a card (logo/tagline/map only, or secondary info).
- other_text: any remaining text not captured in other fields. null for anything absent.
- confidence: 0-1 for the whole card. uncertain_fields: names of fields you had to guess or read with difficulty.
{qr}"""
QR_HINT = """- The card has a QR code that decodes to the text below. Use it as a data source and merge it with what is
  printed (vCard/MECARD -> name, phones, emails, company, title, address; URL -> website or linkedin).
QR content:
{text}"""


# ---------- step 1: crop ----------

def load(path):
    if path.suffix.lower() in (".heic", ".heif"):
        import pillow_heif
        from PIL import Image, ImageOps
        pillow_heif.register_heif_opener()
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    return cv2.imread(str(path))  # ponytail: JPEG EXIF rotation ignored by cv2.imread here; use PIL path for all if phones misrotate


def order(pts):
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def find_cards(img):
    """Return (quads in full-res coords, dark_bg). Several detectors, merged; tuned on real desk-mat photos.
    ponytail: dark cards with rounded corners on a black mat still get missed; shoot those on a white sheet."""
    scale = min(1500 / max(img.shape[:2]), 1)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    border = np.concatenate([gray[:10].ravel(), gray[-10:].ravel(), gray[:, :10].ravel(), gray[:, -10:].ravel()])
    dark_bg = np.median(border) < 128
    quads = []
    for d in (dark_bg, not dark_bg):  # brightness: light cards on dark, and dark cards on light (white sheet on a desk)
        _, mask = cv2.threshold(gray, 0, 255, (cv2.THRESH_BINARY if d else cv2.THRESH_BINARY_INV) + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask | cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8)),
                                cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        quads += card_quads(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8)), scale)
    for lo, hi in ((20, 60), (30, 90), (40, 120)):
        # Mat flood: the mat is the biggest region not crossed by an edge; anything else is a card. This finds dark
        # and coloured cards whose brightness matches the mat, as long as their outline shows an edge.
        edges = cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8))
        _, lab, stats, _ = cv2.connectedComponentsWithStats((edges == 0).astype(np.uint8), connectivity=4)
        not_mat = (lab != 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8) * 255
        quads += card_quads(cv2.morphologyEx(not_mat, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)), scale)
    quads = merge(quads)
    H, W = img.shape[:2]
    inner = [q for q in quads if q[:, 0].min() > 0.005 * W and q[:, 1].min() > 0.005 * H
             and q[:, 0].max() < 0.995 * W and q[:, 1].max() < 0.995 * H]
    if inner:  # a box cut by the photo edge is a partial card (shot whole elsewhere), the keyboard, or legs
        quads = inner
    if len(quads) >= 2:  # what's left that is far bigger than the typical card: a sheet or a merged blob
        med = np.median([cv2.contourArea(q) for q in quads])
        quads = [q for q in quads if cv2.contourArea(q) < 2.5 * med]
    quads.sort(key=lambda q: (round(q[:, 1].mean() / (img.shape[0] / 8)), q[:, 0].mean()))  # reading order: rows, then left->right
    return quads, dark_bg


def card_quads(mask, scale):
    # CCOMP: objects lying inside a hole (cards on a sheet on a desk) are outer contours too; holes (card text) are skipped
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    area_img = mask.shape[0] * mask.shape[1]
    quads = []
    for c, h_ in zip(contours, hier[0] if hier is not None else []):
        if h_[3] != -1:
            continue
        (cx, cy), (w, h), ang = rect = cv2.minAreaRect(c)
        if not (0.004 * area_img < w * h < 0.9 * area_img):
            continue  # too tiny, or the whole frame
        ratio = max(w, h) / max(min(w, h), 1)
        if not 1.2 < ratio < 2.4 or cv2.contourArea(c) / (w * h) < 0.8:
            continue  # not card-shaped (business cards are ~1.6-1.8) or too ragged
        approx = cv2.approxPolyDP(cv2.convexHull(c), 0.02 * cv2.arcLength(c, True), True)
        pts = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(rect)
        quads.append(order(pts.astype(np.float32)) / scale)
    return quads


def merge(quads):
    """The detectors overlap. Drop: boxes holding 2+ distinct smaller boxes (sheet, merged blob), then fragments
    inside a kept box (a card's QR or name panel), then near-duplicates of a kept box."""
    area = [cv2.contourArea(q) for q in quads]
    ctr = [tuple(map(float, q.mean(0))) for q in quads]
    inside = lambda i, j: i != j and cv2.pointPolygonTest(quads[i], ctr[j], False) > 0 and area[j] < 0.6 * area[i]
    iou = lambda a, b: (lambda x: x / (cv2.contourArea(a) + cv2.contourArea(b) - x + 1e-9))(cv2.intersectConvexConvex(a, b)[0])
    drop = set()
    for i in range(len(quads)):
        kids = []
        for j in (j for j in range(len(quads)) if inside(i, j)):
            if all(iou(quads[j], quads[k]) < 0.5 for k in kids):
                kids.append(j)
        if len(kids) >= 2:
            drop.add(i)
    drop |= {j for j in range(len(quads)) if j not in drop and any(i not in drop and inside(i, j) for i in range(len(quads)))}
    keep = []
    for i in (i for i in range(len(quads)) if i not in drop):
        if all(iou(quads[i], quads[k]) < 0.5 for k in keep):
            keep.append(i)
    return [quads[i] for i in keep]


def warp(img, q):
    w = int(max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[3] - q[2])))
    h = int(max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2])))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(q, dst), (w, h))


def read_qr(card):
    try:  # zxing-cpp decodes coloured/logo QRs that OpenCV misses (59 more of 521 real crops)
        import zxingcpp
        texts = [r.text for r in zxingcpp.read_barcodes(card) if r.format == zxingcpp.BarcodeFormat.QRCode and r.text]
        if texts:
            return "\n---\n".join(texts)
    except ImportError:
        pass
    det = cv2.QRCodeDetector()
    for im in (card, cv2.resize(card, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)):
        ok, texts, _, _ = det.detectAndDecodeMulti(im)
        texts = [t for t in (texts if ok else []) if t]
        if texts:
            return "\n---\n".join(texts)
    return None


def next_card_no():
    nums = [int(m.group(1)) for p in CARDS.glob("card_*.jpg") if (m := re.match(r"card_(\d+)", p.stem))]
    return max(nums, default=0) + 1  # from files on disk, so a crash mid-run never leads to overwriting a crop


def crop_new_photos(db):
    done = {r[0] for r in db.execute("SELECT name FROM photos")}
    new = sorted(p for p in PHOTOS.iterdir() if p.suffix.lower() in IMAGE_EXT and p.name not in done)
    found = 0
    for photo in new:
        img = load(photo)
        if img is None:
            print(f"  ! cannot read {photo.name}, skipped")
            continue
        if max(img.shape[:2]) < 2400:  # chat apps shrink photos to ~1280-2000 px; on real cards that meant misread digits and emails
            print(f"  ! {photo.name} is only {img.shape[1]}x{img.shape[0]}: expect misreads. Use the phone's original file "
                  f"(or send it as a file, not a photo).")
        quads, dark_bg = find_cards(img)
        if not quads:  # a card held in the hand, or a close-up: read the whole photo as one card rather than lose it
            h, w = img.shape[:2]
            quads = [np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])]
            print(f"  {photo.name}: no card edges found, reading the whole photo as one card")
        dbg, n, rows = img.copy(), next_card_no(), []
        for i, q in enumerate(quads):
            card = warp(img, q)
            path = CARDS / f"card_{n + i:04d}.jpg"
            cv2.imwrite(str(path), card, [cv2.IMWRITE_JPEG_QUALITY, 92])
            rows.append((str(path.relative_to(ROOT)), photo.name, read_qr(card)))
            cv2.polylines(dbg, [q.astype(np.int32)], True, (0, 0, 255), max(3, img.shape[1] // 400))
            cv2.putText(dbg, str(n + i), tuple(q[0].astype(int) + 20), cv2.FONT_HERSHEY_SIMPLEX,
                        img.shape[1] / 800, (0, 0, 255), max(2, img.shape[1] // 500))
        cv2.imwrite(str(DEBUG / f"{photo.stem}_debug.jpg"), dbg)
        with db:  # crops logged + photo marked done atomically
            db.executemany("INSERT INTO cards (image_path, source_photo, qr_text) VALUES (?,?,?)", rows)
            db.execute("INSERT INTO photos VALUES (?,?,?,datetime('now'))", (photo.name, len(rows), "dark" if dark_bg else "light"))
        found += len(rows)
        qr = sum(1 for r in rows if r[2])
        print(f"  {photo.name}: {len(rows)} cards ({'dark' if dark_bg else 'light'} background{f', {qr} with QR' if qr else ''})")
        for r in rows:
            print(f"    {r[0]} <- {photo.name}")
    return len(new), found


# ---------- step 1b: turn crops upright ----------

ROTATIONS = (0, 90, 180, 270)  # clockwise degrees
CV_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
ORIENT_PROMPT = ("The same business card is shown 4 times, rotated differently, in tiles numbered 1-4. In which tile does the main "
                 "text (the person's name) read normally, left-to-right and not upside down? If the card has no text, answer 0. "
                 'Return ONLY JSON {"tile": n}')


def has_fallback():
    return os.environ.get("BC_FALLBACK") != "off" and bool(FALLBACK_URL or CLAUDE_CLI)


def fallback_vision(prompt, jpg, schema):
    """Ask the fallback model about one image; returns the parsed JSON object. Raises if no fallback is available."""
    if os.environ.get("BC_FALLBACK") == "off":
        raise RuntimeError("fallback disabled (BC_FALLBACK=off)")
    if FALLBACK_URL:
        body = {"model": FALLBACK_MODEL, "max_tokens": 4000, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}},
                    {"type": "text", "text": prompt + "\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)}]}]}
        req = urllib.request.Request(FALLBACK_URL, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + FALLBACK_KEY})
        return json.loads(json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"])
    if CLAUDE_CLI:
        with tempfile.NamedTemporaryFile(suffix=".jpg", dir=ROOT, delete=False) as f:
            f.write(jpg)
        try:
            out = subprocess.run([CLAUDE_CLI, "-p", f"{prompt}\n\nThe image is the file {f.name}: open it with the Read tool.",
                                  "--model", FALLBACK_MODEL, "--tools", "Read", "--allowedTools", "Read", "--setting-sources", "",
                                  "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema),
                                  "--system-prompt", "You read business card images precisely. Read the image with the Read tool, then answer."],
                                 capture_output=True, text=True, timeout=300, cwd=ROOT)
            res = json.loads(out.stdout)
            if res.get("is_error") or not res.get("structured_output"):
                raise RuntimeError(f"claude CLI: {(res.get('result') or out.stderr)[:200]}")
            return res["structured_output"]
        finally:
            os.unlink(f.name)
    raise RuntimeError("no fallback model: set BC_FALLBACK_CHAT_URL or install the claude CLI")


def ask_upright(img, order):
    """One vote: a 2x2 grid of the rotations in `order`, the model names the upright tile. Returns degrees or None."""
    tiles = []
    for n, deg in enumerate(order, 1):
        t = cv2.rotate(img, CV_ROT[deg]) if deg else img
        s = 460 / max(t.shape[:2])
        t = cv2.resize(t, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        c = np.full((520, 520, 3), 255, np.uint8)
        y, x = 50 + (470 - t.shape[0]) // 2, (520 - t.shape[1]) // 2
        c[y:y + t.shape[0], x:x + t.shape[1]] = t
        cv2.putText(c, str(n), (12, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 220), 4)
        tiles.append(c)
    grid = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    jpg = cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
    body = {"model": MODEL, "max_tokens": 50, "temperature": 0, **EXTRA, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}},
                                                      {"type": "text", "text": ORIENT_PROMPT}]}]}
    req = urllib.request.Request(ZAI_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + ZAI_KEY})
    try:
        content = json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        if not (refused(e) and has_fallback()):
            raise
        content = json.dumps(fallback_vision(ORIENT_PROMPT, jpg, {"type": "object", "properties": {"tile": {"type": "integer"}}, "required": ["tile"]}))
    try:
        tile = int(json.loads(content).get("tile") or 0)
    except (ValueError, TypeError, AttributeError):  # empty or non-JSON reply: cards with no text (logo/QR backs) get this
        return None
    return order[tile - 1] if 1 <= tile <= 4 else None


def upright(path):
    """Rotate a crop in place so its text reads upright. Two votes with the tiles in different orders (a single vote
    confused 90 and 270 on 1 of 15 real cards); a third breaks a tie. No clear answer -> left as it is."""
    img = cv2.imread(str(path))
    votes = [ask_upright(img, (0, 90, 180, 270)), ask_upright(img, (180, 270, 0, 90))]
    if votes[0] != votes[1]:
        votes.append(ask_upright(img, (270, 0, 90, 180)))
    best = max(set(votes), key=votes.count)
    deg = best if best is not None and votes.count(best) >= 2 else 0
    if deg:
        cv2.imwrite(str(path), cv2.rotate(img, CV_ROT[deg]), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return deg


def refused(e):
    """The provider's content filter rejected the image (Z.AI code 1301: gambling/adult cards trip it). Retrying never helps."""
    if hasattr(e, "refusal"):  # decided already: the error body can only be read once
        return e.refusal
    if isinstance(e, urllib.error.HTTPError) and e.code == 400:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        e.refusal = "1301" in body or "contentFilter" in body or "sensitive" in body
        return e.refusal
    return getattr(e, "refusal", False)


def orient_new(db):
    todo = db.execute("SELECT id, image_path FROM cards WHERE oriented_at IS NULL").fetchall()
    if not todo:
        return
    print(f"Turning {len(todo)} cards upright...")
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(upright, ROOT / p): cid for cid, p in todo}
        for fut in as_completed(futs):
            try:
                deg = fut.result()
                with db:  # recorded so a re-run never rotates the same crop twice
                    db.execute("UPDATE cards SET rotation=?, oriented_at=datetime('now') WHERE id=?", (deg, futs[fut]))
            except Exception as e:  # noqa: BLE001 - left as it is; retried next run unless the provider refused it
                if refused(e):
                    with db:
                        db.execute("UPDATE cards SET rotation=0, oriented_at=datetime('now') WHERE id=?", (futs[fut],))
                    print(f"  ! orient #{futs[fut]}: the model provider refused this image (content filter); left as photographed")
                else:
                    print(f"  ! orient #{futs[fut]}: {e}")


# ---------- step 2: extract ----------

def extract(path, qr_text):
    prompt = PROMPT.format(path="attached", qr=QR_HINT.format(text=qr_text) if qr_text else "")
    prompt += "\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(SCHEMA)
    img = base64.b64encode((ROOT / path).read_bytes()).decode()
    body = {"model": MODEL, "max_tokens": 4000, "temperature": 0, **EXTRA,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
                {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(ZAI_URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + ZAI_KEY})
    err = None
    for attempt in range(3):
        try:
            res = json.load(urllib.request.urlopen(req, timeout=180))
            d = coerce(json.loads(res["choices"][0]["message"]["content"]))
            rtl = (d["language"] or "").lower()[:2] in ("he", "ar", "fa", "ur", "yi") or RTL.search(
                " ".join(filter(None, (d["full_name"], d["job_title"], d["company"]))))
            if rtl and has_fallback():  # glm-5.3-flash reversed Hebrew names on real cards; the fallback reads RTL correctly
                try:
                    print(f"  {path}: right-to-left card, re-reading with {FALLBACK_MODEL}")
                    return coerce(fallback_vision(prompt.replace("(attached)", "(in the file below)"), (ROOT / path).read_bytes(), SCHEMA))
                except Exception as e:  # noqa: BLE001 - keep the main model's read, flagged
                    d["uncertain_fields"] = sorted(set(d["uncertain_fields"]) | {"full_name (right-to-left script)"})
            return d
        except Exception as e:  # noqa: BLE001 - any failure is retried, then left for the next run
            err = e
            if refused(e):
                break  # the content filter will refuse it every time
            time.sleep(5 * 2 ** attempt)
    if refused(err):
        if has_fallback():  # another model reads it instead
            try:
                print(f"  {path}: main model refused this card (content filter), reading it with {FALLBACK_MODEL}")
                return coerce(fallback_vision(prompt.replace("(attached)", "(in the file below)"), (ROOT / path).read_bytes(), SCHEMA))
            except Exception as e:  # noqa: BLE001
                print(f"  ! fallback failed for {path}: {e}")
        # no model would read it: keep the card, ask a human, never retry forever
        return coerce({"other_text": "No model would read this card (content filter). Fill it in from the photo.",
                       "confidence": 0, "uncertain_fields": ["all fields (provider refused the image)"]})
    raise RuntimeError(f"{path}: {err}")


def coerce(d):
    """json_object mode doesn't enforce the schema: fill missing keys and fix types so the DB row is always complete."""
    d = {f: d.get(f) for f in FIELDS}
    for f in LIST_FIELDS:
        v = d[f]
        d[f] = [str(x) for x in v if x] if isinstance(v, list) else [str(v)] if v else []
    try:
        d["confidence"] = max(0.0, min(1.0, float(d["confidence"])))
    except (TypeError, ValueError):
        d["confidence"] = 0.0  # unknown confidence -> needs_review
    d["is_back_side"] = d["is_back_side"] is True or str(d["is_back_side"]).lower() == "true"
    for f in FIELDS:
        if f not in LIST_FIELDS and f not in ("confidence", "is_back_side") and d[f] is not None:
            d[f] = str(d[f]).strip() or None
    d["telegram"] = ["@" + re.sub(r"^(https?://)?(t\.me/|@)", "", t.strip()).strip("/") for t in d["telegram"]]
    d["emails"] = [e for e in d["emails"] if "@" in e]  # the model sometimes files Skype/handles under emails
    d["phones"] = ["+" + re.sub(r"\D", "", p) if p.strip().startswith("+") else p for p in d["phones"]]  # E.164: no separators
    if d["other_text"]:  # Skype is dead: drop any line the model kept anyway
        d["other_text"] = "\n".join(l for l in re.split(r"[\n;|]", d["other_text"])
                                    if not re.search(r"skype|\blive:", l, re.I)).strip() or None
    if not d["website"]:  # no printed website: the company email's domain is the site
        dom = next((e.split("@")[-1].lower().strip(".") for e in d["emails"] if e.split("@")[-1].lower() not in FREEMAIL), None)
        d["website"] = dom
    return d


def merge_qr(d, qr):
    """Fold QR payloads into the record deterministically (the model may ignore the hint): t.me -> telegram,
    linkedin.com/in -> linkedin, vCard email/phone/name/org fill gaps, any other link -> website if none printed."""
    if not qr:
        return d
    for part in qr.split("\n---\n"):
        if part.startswith("BEGIN:VCARD"):
            for line in part.splitlines():
                key, _, val = line.partition(":")
                key, val = key.split(";")[0].upper(), val.strip()
                if not val:
                    continue
                if key == "EMAIL" and val.lower() not in (e.lower() for e in d["emails"]):
                    d["emails"].append(val)
                elif key == "TEL":
                    tel = "+" + re.sub(r"\D", "", val)[2:] if val.startswith("00") else val
                    if re.sub(r"\D", "", tel)[-9:] not in (re.sub(r"\D", "", p)[-9:] for p in d["phones"]):
                        d["phones"].append(tel)
                elif key == "FN" and not d["full_name"]:
                    d["full_name"] = val
                elif key == "ORG" and not d["company"]:
                    d["company"] = val.strip(";")
            continue
        for url in re.findall(r"https?://\S+", part):
            if m := re.match(r"https?://(www\.)?t\.me/([\w_]+)", url):
                h = "@" + m.group(2)
                if h.lower() not in (t.lower() for t in d["telegram"]):
                    d["telegram"].append(h)
            elif "linkedin.com/in/" in url:
                d["linkedin"] = d["linkedin"] or url
            elif not d["website"] and domain_of(url) not in SHARED_HOSTS | {"skype.com", "wa.me", "free-qr.com", "qr.io", "me-qr.com"}:
                d["website"] = url
    return d


def domain_of(url):
    host = re.sub(r"^(https?://)?(www\.)?", "", (url or "").lower()).split("/")[0].split("?")[0].strip(".")
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])  # partners.foo.co.uk -> foo.co.uk
    return ".".join(parts[-2:]) if len(parts) >= 2 else None


def save(db, card_id, d):
    review = d["confidence"] < 0.8 or bool(d["uncertain_fields"])
    vals = [json.dumps(d[f], ensure_ascii=False) if f in LIST_FIELDS else d[f] for f in FIELDS]
    with db:
        db.execute(f"UPDATE cards SET {', '.join(f + '=?' for f in FIELDS)}, raw_json=?, needs_review=? WHERE id=?",
                   [*vals, json.dumps(d, ensure_ascii=False), int(review), card_id])


# ---------- step 3: duplicates ----------

def flag_duplicates(db):
    rows = db.execute("SELECT id, emails, phones, full_name, company, is_back_side, telegram, job_title FROM cards WHERE raw_json IS NOT NULL ORDER BY id").fetchall()
    norm = lambda s: " ".join((s or "").casefold().split())
    fronts = [(cid, norm(co)) for cid, _, _, _, co, back, _, _ in rows if co and not back]
    seen, dup = {}, {}
    for cid, emails, phones, name, company, back, telegram, title in rows:
        keys = {"e:" + e.strip().lower() for e in json.loads(emails or "[]") if e.strip()}
        keys |= {"t:" + t.lower() for t in json.loads(telegram or "[]") if len(t) > 3}  # Telegram-only cards are common
        keys |= {"p:" + re.sub(r"\D", "", p)[-9:] for p in json.loads(phones or "[]") if len(re.sub(r"\D", "", p)) >= 7}
        if name and company:
            keys.add("nc:" + " ".join(name.casefold().split()) + "|" + " ".join(company.casefold().split()))
        if name and title and len(name.split()) > 1:  # name+title cards whose contact sits in an undecoded QR
            keys.add("nt:" + norm(name) + "|" + norm(title))
        match = min((seen[k] for k in keys if k in seen), default=None)
        if back and not keys and norm(company):  # logo-only back: link to a front whose company starts the same way
            match = next((f for f, co in fronts if co.startswith(norm(company)) or norm(company).startswith(co)), None)
        dup[cid] = dup.get(match) or match  # point at the original, not at another duplicate
        for k in keys:
            seen.setdefault(k, cid)
    with db:
        db.executemany("UPDATE cards SET duplicate_of=? WHERE id=?", [(v, k) for k, v in dup.items()])
        db.execute("UPDATE cards SET needs_review=0 WHERE duplicate_of IS NOT NULL")  # review the original, not every copy
    flag_disagreements(db)
    return sum(v is not None for v in dup.values())


def flag_disagreements(db):
    """Two photos of the same card that read different emails or phones: one of them is a misread (seen on real cards:
    a dropped digit, a letter off in a domain). Flag the original for review, unless a person already verified it."""
    digits = lambda ps: {re.sub(r"\D", "", p)[-9:] for p in json.loads(ps or "[]") if len(re.sub(r"\D", "", p)) >= 7}
    emails = lambda es: {e.strip().lower() for e in json.loads(es or "[]") if "@" in e}
    rows = {r[0]: r for r in db.execute("SELECT id, duplicate_of, emails, phones, confidence, is_back_side, raw_json FROM cards WHERE raw_json IS NOT NULL")}
    flagged = {}
    for cid, orig, em, ph, _, back, _ in rows.values():
        o = rows.get(orig)
        if not o or back or o[5] or o[4] == 1.0:  # no original, a card back, or the original was verified by hand
            continue
        for name, a, b in (("emails", emails(em), emails(o[2])), ("phones", digits(ph), digits(o[3]))):
            if a and b and a != b:
                flagged.setdefault(orig, set()).add(name)
    with db:
        for oid, fields in flagged.items():
            d = json.loads(rows[oid][6])
            d["uncertain_fields"] = sorted(set(d.get("uncertain_fields") or []) | {f + " (photos disagree)" for f in fields})
            db.execute("UPDATE cards SET needs_review=1, uncertain_fields=?, raw_json=? WHERE id=?",
                       (json.dumps(d["uncertain_fields"], ensure_ascii=False), json.dumps(d, ensure_ascii=False), oid))
    if flagged:
        print(f"  {len(flagged)} cards flagged: their photos disagree on an email or phone")


# ---------- step 3b: follow links (Telegram handles, QR pages) ----------

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.geturl(), r.read(600_000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def telegram_check(handle):
    """Public t.me page: exists? display name, bio, user vs channel. No login, nothing sent."""
    _, page = fetch(f"https://t.me/{handle.lstrip('@')}")
    grab = lambda pat: (lambda m: html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None)(re.search(pat, page, re.S))
    name = grab(r'<div class="tgme_page_title"[^>]*>\s*<span[^>]*>(.*?)</span>')
    extra = grab(r'<div class="tgme_page_extra">\s*(.*?)\s*</div>') or ""
    return {"exists": bool(name), "name": name, "bio": grab(r'<div class="tgme_page_description[^"]*"[^>]*>(.*?)</div>') if name else None,
            "kind": "channel" if re.search(r"subscriber|member", extra) else "user" if name else None}


def page_text(page):
    """Visible text plus meta/og tags and JSON-LD: vCard/profile pages often keep the contact data there."""
    meta = " ".join(html.unescape(m) for m in re.findall(r'<meta[^>]+content="([^"]{3,400})"', page))
    ld = " ".join(re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S))
    body = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    hrefs = " ".join(sorted(set(re.findall(r'href="((?:mailto:|tel:|https?://(?:t\.me|wa\.me|www\.linkedin\.com|linkedin\.com|x\.com|twitter\.com|instagram\.com|facebook\.com))[^"]+)"', page))))
    return re.sub(r"\s+", " ", f"{meta} {ld} {body} LINKS: {hrefs}")[:15000]


LINK_PROMPT = """This page was opened from the QR code on {who}'s business card. Extract the contact data it shows about
that person (or their company if the page is a company page). Return ONLY JSON: {{"full_name", "job_title", "company",
"emails": [], "phones": [] (E.164), "telegram": ["@handle"], "linkedin", "website", "about": "1 sentence"}}. null/[] for
anything not on the page; never invent. If the page is empty, a login wall or unrelated, return all nulls.
PAGE ({url}):
{text}"""


def follow_links(d, qr):
    """Returns (record with gaps filled, info dict). Printed data is never overwritten; new emails/phones/handles are appended."""
    info = {"telegram": {}, "pages": []}
    urls = [u for u in re.findall(r"https?://\S+", qr or "") if not re.match(r"https?://(www\.)?t\.me/", u)]
    for url in dict.fromkeys(urls):
        if domain_of(url) in ("skype.com", "wa.me"):
            continue
        try:
            final, page = fetch(url)
            text = page_text(page)
            body = {"model": TEXT_MODEL, "max_tokens": 1500, "temperature": 0, **EXTRA,
                    "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": LINK_PROMPT.format(
                        who=d["full_name"] or d["company"] or "someone", url=final, text=text)}]}
            req = urllib.request.Request(ZAI_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + ZAI_KEY})
            x = json.loads(json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001 - a dead link is information too
            info["pages"].append({"url": url, "error": str(e)[:200]})
            continue
        info["pages"].append({"url": url, "final_url": final, **{k: x.get(k) for k in ("full_name", "job_title", "company", "about")}})
        for f in ("full_name", "job_title", "company", "linkedin"):
            if not d[f] and x.get(f):
                d[f] = x[f]
        if x.get("website") and (not d["website"] or domain_of(d["website"]) in SHARED_HOSTS):
            d["website"] = x["website"]
        for f in ("emails", "phones", "telegram"):
            for v in x.get(f) or []:
                if v and str(v).lower() not in (s.lower() for s in d[f]):
                    d[f].append(str(v))
    return d, info


def check_handles(d, info):
    """Sequential on purpose: parallel t.me requests get rate-limited into the generic 'contact @x' page, which reads
    as 'no such user'. A miss is retried after a pause before it counts."""
    # A page without a preview is NOT proof the handle is wrong: t.me hides it for some real accounts (privacy settings);
    # 21 of 64 real handles, several confirmed by the card's own QR, had no preview. So it is recorded, not flagged.
    # ponytail: a logged-in Telegram client (Telethon/Telegram Web) can resolve those for certain.
    for h in d["telegram"]:
        try:
            c = telegram_check(h)
            if not c["exists"]:
                time.sleep(4)
                c = telegram_check(h)
            info["telegram"][h] = c if c["exists"] else {"exists": None, "note": "no public preview on t.me"}
        except Exception as e:  # noqa: BLE001
            info["telegram"][h] = {"error": str(e)[:200]}
        time.sleep(1)
    return d, info


def follow_all_links(db):
    todo = db.execute("SELECT id, raw_json, qr_text FROM cards WHERE raw_json IS NOT NULL AND links_checked_at IS NULL "
                      "AND duplicate_of IS NULL AND (telegram != '[]' OR qr_text LIKE '%http%')").fetchall()
    if not todo:
        return 0
    print(f"Following links on {len(todo)} cards (QR pages, Telegram handles)...")
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(follow_links, coerce(json.loads(raw)), qr): cid for cid, raw, qr in todo}
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                d, info = check_handles(*fut.result())
                keep = json.loads(db.execute("SELECT raw_json FROM cards WHERE id=?", (cid,)).fetchone()[0])
                d["confidence"] = keep.get("confidence", d["confidence"])  # an audited 1.0 stays 1.0
                save(db, cid, coerce(d))
                with db:
                    db.execute("UPDATE cards SET link_info=?, links_checked_at=datetime('now') WHERE id=?",
                               (json.dumps(info, ensure_ascii=False), cid))
            except Exception as e:  # noqa: BLE001 - retried next run
                print(f"  ! links #{cid}: {e}")
    return len(todo)


# ---------- step 4: group by company ----------

def group_companies(db):
    """Different people at one company share a company_key. Union-find over each card's company domains (email
    domains + website domains) and its normalised company name: anything that co-occurs on a card is one company.
    The key is the group's most common domain; a group without any domain gets its name, and is not enriched."""
    norm = lambda s: "n:" + re.sub(r"\b(s\.?r\.?l|ltd|llc|inc|gmbh|s\.?a\.?s?|group|limited|corp)\b\.?|[^\w]", "", (s or "").casefold())
    rows = db.execute("SELECT id, company, website, emails, duplicate_of FROM cards WHERE raw_json IS NOT NULL").fetchall()
    parent = {}
    find = lambda x: x if parent.setdefault(x, x) == x else find(parent[x])
    card_nodes, dom_count = {}, {}
    for cid, company, website, emails, _ in rows:
        doms = [domain_of(e.split("@")[-1]) for e in json.loads(emails or "[]") if e.split("@")[-1].lower() not in FREEMAIL]
        doms += [domain_of(w) for w in re.split(r"[\s,;]+", website or "") if w]
        doms = [d for d in doms if d and "." in d and d not in SHARED_HOSTS]
        nodes = doms + ([norm(company)] if norm(company) != "n:" else [])
        for d in doms:
            dom_count[d] = dom_count.get(d, 0) + 1
        for a in nodes:
            find(a)  # register single-node cards too
        for a in nodes[1:]:
            parent[find(a)] = find(nodes[0])
        card_nodes[cid] = nodes
    groups = {}
    for n in parent:
        groups.setdefault(find(n), []).append(n)
    label = {}
    for members in groups.values():
        doms = [m for m in members if not m.startswith("n:")]
        key = max(doms, key=lambda d: dom_count[d]) if doms else members[0][2:]
        for m in members:
            label[m] = key
    keys = {cid: (label[find(nodes[0])] if nodes else None) for cid, nodes in card_nodes.items()}
    dup_of = {cid: dup for cid, *_, dup in rows}
    updates = [(keys.get(dup_of[cid]) if dup_of[cid] and not keys[cid] else keys[cid], cid) for cid in keys]  # backs inherit their front's company
    with db:
        db.executemany("UPDATE cards SET company_key=? WHERE id=?", updates)
        db.execute("DELETE FROM companies WHERE key NOT IN (SELECT company_key FROM cards WHERE company_key IS NOT NULL)")
        for key, in db.execute("SELECT DISTINCT company_key FROM cards WHERE company_key IS NOT NULL").fetchall():
            name = db.execute("SELECT company FROM cards WHERE company_key=? AND company IS NOT NULL GROUP BY company "
                              "ORDER BY COUNT(*) DESC LIMIT 1", (key,)).fetchone()
            db.execute("INSERT OR IGNORE INTO companies (key, name, website) VALUES (?,?,?)",
                       (key, name[0] if name else key, key if "." in key else None))


# ---------- step 5: enrich companies from the web ----------

def mcp_search(query):
    """One web_search_prime call over MCP streamable HTTP (stdlib only). Returns [{title, link, content}]."""
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + ZAI_KEY}
    sid = None

    def rpc(method, params, notify=False):
        nonlocal sid
        msg = {"jsonrpc": "2.0", "method": method, "params": params, **({} if notify else {"id": 1})}
        r = urllib.request.urlopen(urllib.request.Request(SEARCH_MCP, json.dumps(msg).encode(),
                                                          dict(hdr, **({"Mcp-Session-Id": sid} if sid else {}))), timeout=60)
        sid = r.headers.get("Mcp-Session-Id") or sid
        body = r.read().decode()
        if notify:
            return None
        return json.loads(body if body.lstrip().startswith("{") else [l[5:] for l in body.splitlines() if l.startswith("data:")][-1])

    rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "business-cards", "version": "1"}})
    rpc("notifications/initialized", {}, notify=True)
    text = rpc("tools/call", {"name": "web_search_prime", "arguments": {"search_query": query[:70]}})["result"]["content"][0]["text"]
    res = json.loads(text)
    return json.loads(res) if isinstance(res, str) else res  # the tool double-encodes its JSON


ENRICH_PROMPT = """Company from business cards: {name} (domain: {domain}). People met: {people}.
Web search results (JSON):
{results}

Using ONLY these results, return ONLY JSON: {{"about": "1-2 sentences: who they are", "what_they_do": "products/services, 1 sentence",
"industry": str, "hq": "city, country", "socials": {{"linkedin": url, "x": url, "telegram": url, "instagram": url, "facebook": url, "youtube": url}},
"sources": [urls you used]}}. Use null for anything the results don't show; never invent URLs. If the results are about a
different company with a similar name, return nulls."""


def enrich(key, name, people):
    results = mcp_search(f"{name} {key}") + mcp_search(f"{name} linkedin")
    body = {"model": TEXT_MODEL, "max_tokens": 2000, "temperature": 0, **EXTRA,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": ENRICH_PROMPT.format(
                name=name, domain=key, people=people,
                results=json.dumps([{k: r.get(k) for k in ("title", "link", "content")} for r in results], ensure_ascii=False))}]}
    req = urllib.request.Request(ZAI_URL, json.dumps(body).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + ZAI_KEY})
    d = json.loads(json.load(urllib.request.urlopen(req, timeout=180))["choices"][0]["message"]["content"])
    links = {r.get("link") for r in results}
    socials = {k: v for k, v in (d.get("socials") or {}).items() if v}
    socials = {k: v for k, v in socials.items() if any(v.rstrip("/") in (l or "") or (l or "").rstrip("/") in v for l in links)}  # only URLs seen in results
    return d.get("about"), d.get("what_they_do"), d.get("industry"), d.get("hq"), json.dumps(socials), json.dumps(d.get("sources") or [])


def enrich_companies(db):
    # name-only companies ("MLB" off a logo back) are too ambiguous to search: they came back as Major League Baseball
    if not SEARCH_MCP:
        return 0, 0
    todo = db.execute("SELECT key, name FROM companies WHERE enriched_at IS NULL AND key LIKE '%.%'").fetchall()
    if not todo:
        return 0, 0
    print(f"Enriching {len(todo)} companies from the web...")
    ok = failed = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {}
        for key, name in todo:
            people = ", ".join(f"{n or '?'} ({t or '-'})" for n, t in db.execute(
                "SELECT full_name, job_title FROM cards WHERE company_key=? AND duplicate_of IS NULL", (key,)))
            futs[pool.submit(enrich, key, name, people)] = key
        for fut in as_completed(futs):
            try:
                with db:
                    db.execute("UPDATE companies SET about=?, what_they_do=?, industry=?, hq=?, socials=?, sources=?, "
                               "enriched_at=datetime('now') WHERE key=?", (*fut.result(), futs[fut]))
                ok += 1
            except Exception as e:  # noqa: BLE001 - left un-enriched; next run retries
                failed += 1
                print(f"  ! enrich {futs[fut]}: {e}")
    return ok, failed


# ---------- step 6: export ----------

def export(db):
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM cards ORDER BY needs_review DESC, id").fetchall()
    companies = {r["key"]: r for r in db.execute("SELECT * FROM companies")}
    db.row_factory = None
    cols = rows[0].keys() if rows else []
    for name, subset in (("cards.csv", rows), ("needs_review.csv", [r for r in rows if r["needs_review"]])):
        with open(EXPORT / name, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Cyrillic/Hebrew correctly
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows([[r[c] for c in cols] for r in sorted(subset, key=lambda r: (r["company_key"] or "~", r["id"]))])
    ccols = ["key", "name", "website", "about", "what_they_do", "industry", "hq", "socials", "sources", "enriched_at"]
    with open(EXPORT / "companies.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(ccols + ["people"])
        for key, c in sorted(companies.items()):
            people = "; ".join(sorted({r["full_name"] for r in rows if r["company_key"] == key and r["full_name"] and not r["duplicate_of"]}))
            w.writerow([c[k] for k in ccols] + [people])

    show = [c for c in cols if c not in ("image_path", "raw_json", "company_key")]
    esc = lambda v: html.escape(str(v))
    groups = {}
    for r in rows:
        groups.setdefault(r["company_key"], []).append(r)
    order_ = sorted(groups, key=lambda k: (not any(r["needs_review"] for r in groups[k]), k is None, str(k)))  # review first
    sections = []
    for key in order_:
        c = companies.get(key)
        head = f"<h2>{esc(c['name'] if c else 'No company')}" + (f" <small>{esc(key)}</small>" if key else "") + "</h2>"
        if c and c["enriched_at"]:
            socials = " ".join(f'<a href="{esc(u)}">{esc(k)}</a>' for k, u in json.loads(c["socials"] or "{}").items())
            head += "<p class=co>" + " · ".join(esc(x) for x in (c["about"], c["what_they_do"], c["industry"], c["hq"]) if x) + \
                    (f"<br>{socials}" if socials else "") + "</p>"
        cards_html = []
        for r in groups[key]:
            cells = "".join(f"<tr><th>{k}</th><td>{esc(r[k])}</td></tr>" for k in show if r[k] not in (None, "", "[]"))
            cards_html.append(f'<div class="card{" review" if r["needs_review"] else ""}"><img src="../{esc(r["image_path"])}">'
                              f"<table>{cells}</table></div>")
        sections.append(head + "".join(cards_html))
    (EXPORT / "review.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Business cards</title><style>"
        "body{font:14px system-ui;margin:16px;background:#fff;color:#111}.card{display:flex;gap:16px;border-bottom:1px solid #ccc;padding:12px 0}"
        ".review{background:#fff4e0}img{width:420px;max-width:45%;object-fit:contain;align-self:start}h2{margin:28px 0 4px}"
        "h2 small{color:#888;font-weight:normal}.co{color:#444;margin:0 0 8px}"
        "th{text-align:left;padding-right:12px;color:#666;vertical-align:top}td{white-space:pre-wrap}</style>"
        f"<h1>Business cards ({len(rows)} cards, {len(companies)} companies, {sum(r['needs_review'] or 0 for r in rows)} need review, "
        "grouped by company, companies with cards to review first)</h1>" + "".join(sections), encoding="utf-8")


# ---------- main ----------

def main():
    if not ZAI_KEY:
        sys.exit("No API key: set BC_API_KEY (or ZAI_API_KEY) in the environment. See README, Configuration.")
    for d in (PHOTOS, CARDS, DEBUG, EXPORT):
        d.mkdir(exist_ok=True)
    lock = open(ROOT / ".run.lock", "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # BlockingIOError on Linux/macOS, PermissionError on Windows
        sys.exit("another run.py is already running; try again when it finishes")
    db = sqlite3.connect(DB)  # opens existing db, never recreates it
    cols = ", ".join(f"{f} {'REAL' if f == 'confidence' else 'INTEGER' if f == 'is_back_side' else 'TEXT'}" for f in FIELDS)
    db.executescript(f"""
        CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY, image_path TEXT UNIQUE, source_photo TEXT, {cols},
            qr_text TEXT, raw_json TEXT, needs_review INTEGER, duplicate_of INTEGER, company_key TEXT,
            created_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS photos (name TEXT PRIMARY KEY, cards_found INTEGER, background TEXT, processed_at TEXT);
        CREATE TABLE IF NOT EXISTS companies (key TEXT PRIMARY KEY, name TEXT, website TEXT, about TEXT, what_they_do TEXT,
            industry TEXT, hq TEXT, socials TEXT, sources TEXT, enriched_at TEXT);""")
    have = {r[1] for r in db.execute("PRAGMA table_info(cards)")}
    for col in ("link_info TEXT", "links_checked_at TEXT", "rotation INTEGER", "oriented_at TEXT"):  # added after the first cards.db existed
        if col.split()[0] not in have:
            db.execute(f"ALTER TABLE cards ADD COLUMN {col}")

    print("Cropping new photos...")
    n_photos, n_cards = crop_new_photos(db)
    orient_new(db)  # before extraction: the model reads upright text better too
    todo = db.execute("SELECT id, image_path, qr_text FROM cards WHERE raw_json IS NULL ORDER BY id").fetchall()
    print(f"Extracting {len(todo)} cards with {MODEL}...")
    failed, new_ids = 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(extract, p, q): (cid, p, q) for cid, p, q in todo}
        for fut in as_completed(futs):
            cid, p, q = futs[fut]
            try:
                save(db, cid, merge_qr(fut.result(), q))
                new_ids.append(cid)
            except Exception as e:  # noqa: BLE001 - row stays unextracted; next run retries it
                failed += 1
                print(f"  ! {e}")
    follow_all_links(db)
    dups = flag_duplicates(db)
    group_companies(db)
    enriched, enrich_failed = enrich_companies(db)
    export(db)

    total, review = db.execute("SELECT COUNT(*), SUM(needs_review) FROM cards WHERE raw_json IS NOT NULL").fetchone()
    n_co = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"\nSUMMARY: photos processed {n_photos} | cards found {n_cards} new, {total} extracted total | "
          f"needs_review {review or 0} | duplicates flagged {dups} | companies {n_co} ({enriched} newly enriched)"
          + (f" | FAILED {failed} cards, {enrich_failed} companies (re-run to retry)" if failed or enrich_failed else ""))
    for cid in sorted(new_ids):
        name, company, title, rv, dup, back = db.execute(
            "SELECT full_name, company, job_title, needs_review, duplicate_of, is_back_side FROM cards WHERE id=?", (cid,)).fetchone()
        tags = " ".join(t for t, on in (("[review]", rv), (f"[dup of #{dup}]", dup), ("[back]", back)) if on)
        print(f"  #{cid} {name or '?'} - {', '.join(x for x in (title, company) if x) or '-'} {tags}".rstrip())
    print(f"Export: {EXPORT}/cards.csv, needs_review.csv, companies.csv, review.html")


if __name__ == "__main__":
    main()
