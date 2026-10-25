# Instructions for AI agents (Claude Code, Codex, and others)

Your user wants their paper business cards turned into a searchable contact book. Do it for them, step by step,
and keep it simple: they may not be technical.

## Set up

1. Install: `curl -fsSL https://raw.githubusercontent.com/muninnravenbot-ux/bccrm/main/install.sh | bash`
   (installs into `~/bccrm`; on Windows run it inside WSL). If you are already inside a clone, run `bash install.sh`.
2. BCCRM reads the cards with an AI it finds on this machine, in this order: an API key in `BC_API_KEY`, the
   `claude` CLI, the `codex` CLI. If you are Claude Code or Codex, you are probably that AI already: check that
   `claude -p "say ok"` or `codex exec "say ok"` answers. Nothing else needs configuring.

## Read the cards

1. Ask the user where their card photos are (phone originals, not WhatsApp/Telegram copies: compressed photos get
   digits wrong). Copy them into `~/bccrm/photos/`. Never move or edit their originals.
2. Run `cd ~/bccrm && .venv/bin/python run.py`. It can take a few minutes for a big stack. It prints a SUMMARY line.
3. If it warns about low-resolution photos or `debug/<photo>_debug.jpg` shows cards without a box, tell the user
   which photos to reshoot (dark cards on a white sheet, light cards on a dark surface, gaps between cards).
4. Start the contact book: `.venv/bin/python crm.py` and tell the user to open http://localhost:8770.
   Cards marked for review are listed in `export/needs_review.csv`: walk the user through them, or fix them in
   the contact book with Edit.

Running `run.py` again only processes new photos. It never deletes or overwrites a card, so it is always safe.

## Rules

- Report the real SUMMARY numbers. Never claim cards were read if the run failed.
- `cards.db`, `photos/`, `cards/` and `export/` hold the user's contacts: never commit, upload or share them.
- Do not change the code to "fix" a single misread card: correct it in the contact book.
