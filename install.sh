#!/usr/bin/env bash
# BCCRM installer: curl -fsSL https://raw.githubusercontent.com/muninnravenbot-ux/bccrm/main/install.sh | bash
# Installs into ~/bccrm (or $BCCRM_DIR). Safe to run again: it updates the code and keeps your cards.
set -euo pipefail
DIR="${BCCRM_DIR:-$HOME/bccrm}"
REPO="${BCCRM_REPO:-https://github.com/muninnravenbot-ux/bccrm}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "BCCRM needs $1. $2"; exit 1; }; }
need git "Install it from https://git-scm.com and run this again."
need python3 "Install Python 3.10 or newer from https://www.python.org and run this again."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "BCCRM needs Python 3.10 or newer (you have $(python3 -V))."; exit 1; }

if [ -d "$DIR/.git" ]; then
  say "Updating BCCRM in $DIR"
  git -C "$DIR" pull --ff-only -q
else
  say "Downloading BCCRM to $DIR"
  git clone -q "$REPO" "$DIR"
fi
cd "$DIR"
say "Installing Python packages (a minute or two the first time)"
[ -d .venv ] || python3 -m venv .venv || { echo "Could not create a virtual environment. On Ubuntu/Debian: sudo apt install python3-venv"; exit 1; }
.venv/bin/pip install -q --upgrade pip >/dev/null
.venv/bin/pip install -q -r requirements.txt
mkdir -p photos

ai=""
[ -n "${BC_API_KEY:-}${ZAI_API_KEY:-}" ] && ai="$ai API key,"
command -v claude >/dev/null 2>&1 && ai="$ai Claude Code,"
command -v codex >/dev/null 2>&1 && ai="$ai Codex,"
echo
say "BCCRM is installed."
if [ -n "$ai" ]; then
  echo "AI that will read your cards:${ai%,}"
else
  echo "One thing left: BCCRM needs an AI to read the cards. Pick one:"
  echo "  - Claude subscription:  npm install -g @anthropic-ai/claude-code   then run: claude   (log in once)"
  echo "  - ChatGPT subscription: npm install -g @openai/codex               then run: codex    (log in once)"
  echo "  - An API key:           export BC_API_KEY=...   (see README, AI options)"
fi
cat <<EOF

Next:
  1. Put your card photos in   $DIR/photos
  2. Read them:                cd $DIR && .venv/bin/python run.py
  3. Open your contact book:   .venv/bin/python crm.py   then visit http://localhost:8770

Made by Crowork.ai. Want a tool like this built for your team? https://crowork.ai
EOF
