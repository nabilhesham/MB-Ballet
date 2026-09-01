#!/usr/bin/env bash
# MB Ballet Academy — setup and start, in one command.
#
#   ./start.sh              normal start
#   ./start.sh --seed       wipe and rebuild from the sheets/ workbooks
#
# Safe to run repeatedly: each step is skipped if already done.

set -euo pipefail
cd "$(dirname "$0")"

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; grn=$'\033[32m'; off=$'\033[0m'
step(){ printf "  %s%s%s\n" "$dim" "$1" "$off"; }
ok(){   printf "  %s✓%s %s\n" "$grn" "$off" "$1"; }
die(){  printf "  %s✗ %s%s\n" "$red" "$1" "$off"; exit 1; }

printf "\n  %sMB BALLET ACADEMY%s\n  %s──────────────────%s\n\n" "$bold" "$off" "$dim" "$off"

# A virtual environment holds compiled, platform-specific binaries, so a single
# .venv shared between Windows and Linux destroys itself every time the other
# platform runs. Each platform gets its own; the database, .env, cards and
# photos are plain files and stay shared.
case "$(uname -s)" in
  Linux*)                 VENVDIR=".venv-linux" ;;
  Darwin*)                VENVDIR=".venv-macos" ;;
  MINGW*|MSYS*|CYGWIN*)   VENVDIR=".venv-windows" ;;
  *)                      VENVDIR=".venv-$(uname -s | tr 'A-Z' 'a-z')" ;;
esac

# Git Bash on Windows builds a venv with Scripts/ where Linux uses bin/.
venv_python() {
  if   [ -x "$VENVDIR/bin/python" ];        then echo "$VENVDIR/bin/python"
  elif [ -x "$VENVDIR/Scripts/python.exe" ]; then echo "$VENVDIR/Scripts/python.exe"
  else echo ""; fi
}

if [ -d ".venv" ]; then
  printf "  %sAn old shared .venv folder is present and no longer used.%s\n" "$dim" "$off"
  printf "  %sYou can delete it to save space.%s\n\n" "$dim" "$off"
fi

# ---------------------------------------------------------------- python
PY=""
for c in python3 python py; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || die "Python 3 not found. Install it from python.org, then run this again."

VER=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MAJOR=${VER%%.*}; MINOR=${VER##*.}
[ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ] || die "Python 3.10 or newer required (found $VER)."
ok "Python $VER"

# ---------------------------------------------------------------- venv
VPY="$(venv_python)"
if [ -n "$VPY" ] && ! "$VPY" -c "import sys" >/dev/null 2>&1; then
  step "Environment is broken, rebuilding…"
  rm -rf "$VENVDIR"
  VPY=""
fi
if [ -z "$VPY" ]; then
  step "Creating $VENVDIR…"
  "$PY" -m venv "$VENVDIR" || die "Could not create $VENVDIR"
  VPY="$(venv_python)"
fi
[ -n "$VPY" ] || die "Could not find the interpreter inside $VENVDIR"
ok "Environment ready ($VENVDIR)"

# ---------------------------------------------------------------- deps
if ! "$VPY" -c "import fastapi, uvicorn, qrcode, PIL, multipart" >/dev/null 2>&1; then
  step "Installing packages (first run takes a minute)…"
  "$VPY" -m pip install --upgrade pip --quiet
  "$VPY" -m pip install -r requirements.txt --quiet || die "Package install failed"
fi
ok "Packages installed"

# ---------------------------------------------------------------- secret
if [ ! -f ".env" ]; then
  step "Generating signing secret…"
  "$VPY" -c "import secrets; open('.env','w').write('ENTRY_SECRET='+secrets.token_urlsafe(32)+'\n')"
  printf "  %sSaved to .env — back this file up. Losing it invalidates every card.%s\n" "$dim" "$off"
fi
set -a; . ./.env; set +a
ok "Signing secret loaded"

# ---------------------------------------------------------------- flags
for arg in "$@"; do
  case "$arg" in
    --seed)
      step "Rebuilding the database from sheets/…"
      "$VPY" seed.py --force
      ;;
  esac
done

# ---------------------------------------------------------------- database
if [ ! -f "academy.db" ]; then
  printf "\n  No database yet.\n"
  printf "  Load it from the workbooks in sheets/? %s[Y/n]%s " "$dim" "$off"
  read -r reply || reply="y"
  case "${reply:-y}" in
    [Nn]*) "$VPY" -c "import db; db.init(); print('  Empty database created.')" ;;
    *)     "$VPY" seed.py --force ;;
  esac
fi
ok "Database ready"

# ---------------------------------------------------------------- go
printf "\n  %sStarting on http://127.0.0.1:8000%s\n" "$bold" "$off"
printf "  %sPress Ctrl+C to stop.%s\n\n" "$dim" "$off"

( sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8000 >/dev/null 2>&1
  elif command -v open   >/dev/null 2>&1; then open http://127.0.0.1:8000 >/dev/null 2>&1
  fi ) &

exec "$VPY" server.py
