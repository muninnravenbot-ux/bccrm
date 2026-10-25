<p align="center"><img src="docs/img/logo.png" width="120" alt="BCCRM logo: a fanned stack of business cards"></p>
<h1 align="center">BCCRM</h1>
<p align="center"><b>Your pile of business cards, turned into a contact book you can search.</b><br>
Photograph the cards. BCCRM reads them, researches every company, and gives you a small CRM on your own computer.<br>
It runs on the AI subscription you already pay for (Claude or ChatGPT), so you don't need an API key.</p>
<p align="center">Made by <a href="https://crowork.ai"><b>Crowork.ai</b></a> · <a href="#built-by-croworkai">Want one built for your team?</a></p>

<p align="center">
  <img src="docs/img/card-stack.jpg" height="340" alt="A stack of 200+ business cards next to a small desk robot">
  &nbsp;
  <img src="docs/img/card-stack-and-app.jpg" height="340" alt="The same stack in front of a monitor showing BCCRM">
</p>
<p align="center"><em>200+ cards from a year of conferences: every person searchable, every company researched,
every card one click from email or Telegram.</em></p>

## Set it up in one line

**Easiest: ask your AI.** Paste this into Claude Code, Codex or any coding agent:

```text
Set up BCCRM for me from https://github.com/muninnravenbot-ux/bccrm and follow its AGENTS.md.
My business card photos are in ~/Pictures/cards.
```

It installs everything, reads your cards and opens your contact book. Change the folder to wherever your photos are.

**Or run the installer yourself** (macOS, Linux, or WSL on Windows):

```bash
curl -fsSL https://raw.githubusercontent.com/muninnravenbot-ux/bccrm/main/install.sh | bash
```

Then:

```bash
cp ~/Pictures/cards/*.jpg ~/bccrm/photos/
cd ~/bccrm && .venv/bin/python run.py     # reads the cards
.venv/bin/python crm.py                   # opens your contact book at http://localhost:8770
```

Took more photos? Drop them in `photos/` and run `run.py` again. It only reads the new ones, retries anything that
failed, and never overwrites or deletes a card.

## What you get

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/app-dark.png">
  <img src="docs/img/app-light.png" alt="BCCRM: the people list, a contact with the card photo, one-click actions, status, notes and details (demo data)">
</picture>
<p align="center"><sub>The contact book, shown with demo cards.</sub></p>

- **Every person, once,** with the photo of their card (front and back) next to their details.
- **One click to reach them:** email, Telegram, phone, WhatsApp, LinkedIn, their website, the link in their QR code.
- **Every company researched on the web:** who they are, what they do, their sector, HQ and social profiles, with
  the sources it used. Colleagues from the same company are grouped together.
- **A small CRM:** statuses (New, To contact, Contacted, Replied, Meeting, Not a fit), stars, tags and notes.
- **Search everything** and filter by status, sector, tag, or how you can reach someone. Export any list to CSV.
- **It all stays on your computer:** one SQLite file, no account, no cloud. Only the card images and web searches go to
  the AI you choose.

## Which AI reads the cards

BCCRM uses whatever you already have. It picks the first one available, and the others become backups for what the
first one can't do.

| You have | What BCCRM uses | API key? |
|---|---|---|
| A Claude subscription (Pro, Max) | the [Claude Code](https://claude.com/claude-code) CLI: `npm install -g @anthropic-ai/claude-code`, then run `claude` once to log in | no |
| A ChatGPT subscription (Plus, Pro) | the [Codex](https://github.com/openai/codex) CLI: `npm install -g @openai/codex`, then run `codex` once to log in | no |
| Any API with vision models | any OpenAI-compatible endpoint: OpenAI, OpenRouter, Z.AI, Gemini, a local server like LM Studio or Ollama | yes |

Want a specific one? Set `BC_BACKEND=claude`, `codex` or `api`. All three were tested on the same real card, a rotated
copy, a QR page and a company lookup, and gave the same answers. Codex was the slowest at company research (about
80 seconds a company, against 10–15).

**Backups.** If an AI refuses a card (Z.AI's content filter blocked some gambling-industry cards) or reads a
right-to-left script backwards (a small vision model reversed Hebrew names), the next available AI reads it. If none
can, the card is kept and marked for you to fill in from the photo.

## Company research

After the cards are read, BCCRM groups people by company and looks each company up on the web:

- **What it finds:** a short "who they are", what they do, industry, headquarters, and the company's LinkedIn, X,
  Telegram, Instagram, Facebook and YouTube.
- **Where it shows:** under each person, and in the Companies tab with everyone you met there. Each profile lists
  the pages it came from and the date it was researched.
- **How it searches:** Claude and Codex use their own web search. With an API key, it uses the endpoint's search
  tool if it has one (Z.AI's `web_search_prime` is set up automatically) and keeps only links that were actually in
  the search results.
- **What it skips:** companies known only by a name off a logo, with no website or email domain. A name alone matches
  too many companies ("MLB" came back as Major League Baseball).

On a real stack of 218 cards it profiled 185 of 196 companies. Set `BC_ENRICH=off` to skip it.

## Taking good photos

- **Use the original photo from your phone.** WhatsApp and Telegram shrink photos, and the AI starts misreading
  digits. Sending "as file" is fine. BCCRM warns you about small photos.
- **Light cards on a dark surface, dark cards on a white sheet,** with gaps between them and even light. 8–20 cards
  per photo works well.
- **An overview plus close-ups is best.** Cards cut off by the photo edge are skipped, and repeat photos of the same
  card are merged into one person.
- **One card in your hand works too:** if no card edges are found, the whole photo is read as one card.

Each photo gets `debug/<photo>_debug.jpg` with numbered boxes, so you can see if a card was missed.

## How it works

`run.py` takes every new photo in `photos/` through these steps:

1. **Crop.** OpenCV finds each card (several passes, so dark cards on dark mats are found too), straightens it and
   saves it to `cards/`. Your photos are never changed.
2. **Turn upright.** The AI sees the card's four rotations and picks the one that reads normally (two votes and a
   tie-break), so vertical cards stay vertical and upside-down ones are fixed.
3. **QR codes.** `zxing-cpp` decodes them, including coloured codes and codes with a logo in the middle.
4. **Read.** The AI reads each card into name, title, company, emails, phones (international format), Telegram,
   website, address, LinkedIn and language, keeping the original spelling and script. When no website is printed,
   it is taken from the company email domain. Skype IDs are dropped.
5. **Follow links.** Every QR link is opened and the page is read for contact details, so a QR-only card becomes a
   real contact. Telegram handles are checked on `t.me`. These only fill gaps; printed data always wins.
6. **Duplicates.** Copies of a card (same email, phone, Telegram, or name with company or title) are linked to one
   person, never deleted. Logo-only backs attach to their front. Two photos that disagree are marked for review.
7. **Companies.** People are grouped by company, and each company is researched on the web (above).
8. **Export.** `export/cards.csv`, `companies.csv`, `needs_review.csv` and a static `review.html`.

Everything lives in `cards.db` (SQLite). The contact book adds its own `crm` and `notes` tables, which `run.py`
never touches.

## The contact book

`crm.py` serves the app on `127.0.0.1` only, and never serves the database or your original photos. The interface
is built with [OpenUI](https://www.openui.com)'s React components, with light and dark themes. The built app is
committed in `web/dist/`, so running it needs only Python. To change it: `cd web && npm install && npm run dev`, then
`npm run build`. Without `web/dist`, `crm.py` falls back to `crm.html`, a single page with the same features.

To keep it running in the background on Linux or WSL, create `~/.config/systemd/user/bccrm.service`:

```ini
[Service]
WorkingDirectory=%h/bccrm
ExecStart=%h/bccrm/.venv/bin/python %h/bccrm/crm.py
Restart=on-failure
[Install]
WantedBy=default.target
```

and run `systemctl --user enable --now bccrm`.

## Settings

All optional. Set them as environment variables.

| Variable | Default | What it does |
|---|---|---|
| `BC_BACKEND` | auto | `claude`, `codex` or `api`: which AI goes first |
| `BC_FALLBACK` | on | `off`: use only the first AI, no backups |
| `BC_CLAUDE_MODEL` | `sonnet` | model for the Claude Code CLI |
| `BC_CODEX_MODEL` | Codex's default | model for the Codex CLI |
| `BC_API_KEY` | none | key for the `api` backend (`ZAI_API_KEY` also works) |
| `BC_CHAT_URL` | Z.AI Coding Plan | any OpenAI-compatible `/chat/completions` URL |
| `BC_VISION_MODEL` | `glm-5.3-flash` | `api` model that reads card images |
| `BC_TEXT_MODEL` | `glm-5.3` | `api` model that reads QR pages and search results |
| `BC_SEARCH_MCP` | Z.AI search, on Z.AI | web search MCP for the `api` backend |
| `BC_ENRICH` | on | `off`: skip company research |
| `BC_PORT` | `8770` | port of the contact book |

Using Z.AI's Coding Plan? Keep the default `/api/coding/` URL: the general `/api/paas/v4/` endpoint bills your API
balance instead of the plan.

## How accurate is it

Numbers from one real stack: 118 photos, 521 card crops, 218 unique cards, 196 companies. Every card was afterwards
checked against its photo.

- **Photo resolution matters most.** WhatsApp-compressed photos gave wrong digits and invented emails; the phone
  originals read correctly.
- **Small vision models make small mistakes.** `glm-5.3-flash` was fully right on about 3 of 4 cards. Typical errors:
  a dropped digit, an email domain one letter off, Skype IDs filed as Telegram. Unsure cards are marked for review,
  and fixing them in the contact book takes seconds.
- **The cropper's limit:** black rounded cards on a black mat, and strong shadows. Reshoot those on a white sheet.
- **Glare on metal cards** hides text from any model.

## Problems it already solves for you

Every one of these happened on a real stack and is handled in the code, so you don't have to:

| What went wrong | What BCCRM does |
|---|---|
| Chat apps compress photos and digits get misread | warns about photos under 2400 px |
| Dark cards on a dark mat, light cards on the same mat | several detection passes, merged |
| Cards cut off by the photo edge | dropped when the photo has other cards |
| A keyboard detected as a card | boxes far bigger than the typical card are dropped |
| A card held in the hand, no mat | the whole photo is read as one card |
| Cards sideways or upside down | turned upright before reading |
| The browser keeps showing the old, unrotated image | image links change when a crop changes |
| Coloured QR codes, or a logo in the middle | `zxing-cpp` reads about twice as many as OpenCV |
| QR-only cards with no printed details | the QR's contact card and web page fill the fields |
| Skype IDs (Skype is dead) | dropped |
| Telegram handles that may not exist | checked on `t.me`; a hidden profile counts as unconfirmed, not wrong |
| Two photos of the same card read differently | marked for review |
| The same person on several cards | linked into one person |
| Logo-only card backs | attached to the matching front |
| A name-only company researched as someone else | only companies with a website or email domain are researched |
| Link-in-bio sites (linktr.ee) mistaken for the company | never used to identify a company |
| The AI provider's content filter refuses a card | the next AI reads it; if none can, it's kept and marked |
| Hebrew and Arabic names read backwards | re-read by a stronger AI |
| Cards with no text (logo backs) | left as photographed, not guessed |
| Dark mode with unreadable menus and dim text | themed controls, readable contrast in both themes |
| Two runs at the same time | a lock makes them take turns |

## Feeding it from a chat bot

`run.py` reads whatever is in `photos/` and can be called from anywhere (a lock makes parallel runs wait). A Telegram
or WhatsApp bot can save each incoming card photo into `photos/` (as a file, not a compressed photo), run `run.py`,
and reply with the SUMMARY line it prints.

## Built by Crowork.ai

BCCRM was built by [Crowork.ai](https://crowork.ai), an AI company that builds tools like this: practical automations
that take a messy, manual job and make it a one-command one, running on AI subscriptions you already have.

**Want something like this for your team?** A custom CRM, a pipeline for your documents or invoices, or an AI
assistant wired into your tools: [tell us about it at crowork.ai](https://crowork.ai).

## License

MIT. Use it, change it, share it.
