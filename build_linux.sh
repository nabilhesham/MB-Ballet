#!/usr/bin/env bash
# ===========================================================================
#  Build a standalone MB Ballet Academy binary — Linux
#
#  Run this ONCE, on a Linux machine that has Python, when you want to hand
#  the reception laptop something with nothing to install at all. It
#  produces dist/MB Ballet Academy — copy that single file to the reception
#  laptop and run it. No Python, no packages, no internet needed.
#
#  The database, photos, cards and .env are created next to it, so keep it
#  in its own folder rather than loose on the desktop.
#
#  PyInstaller cannot cross-compile: a Linux binary must be built on Linux.
#  It is also tied to the glibc version of the machine that built it — built
#  here, it runs on this distro and newer, not older. If the reception
#  laptop runs an older Linux than this one, build there instead, or on
#  something conservatively old.
# ===========================================================================

set -euo pipefail
cd "$(dirname "$0")"

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; grn=$'\033[32m'; off=$'\033[0m'
step(){ printf "  %s%s%s\n" "$dim" "$1" "$off"; }
ok(){   printf "  %s✓%s %s\n" "$grn" "$off" "$1"; }
die(){  printf "  %s✗ %s%s\n" "$red" "$1" "$off"; exit 1; }

# PyInstaller cannot cross-compile — whatever OS this script runs ON is the
# OS the binary targets, regardless of the script's name. Running this on a
# Mac would silently produce a macOS binary that "succeeds" here and then
# fails to launch at all on the reception laptop. Catch that up front,
# before any of the slow steps below (see build_mac.sh for the symmetric
# guard — this project has already been bitten by the WSL/uname version of
# this mistake once).
case "$(uname -s)" in
  Linux*) ;;
  *) die "This produces a Linux build and has to run on Linux (WSL counts — it reports itself as Linux). This machine is $(uname -s). Use build_mac.sh on an actual Mac, or BUILD_EXE.bat on Windows." ;;
esac

printf "\n  %sBuild MB Ballet Academy — Linux%s\n  %s────────────────────────────────%s\n\n" \
  "$bold" "$off" "$dim" "$off"
printf "  Building a standalone program file. This takes a few minutes.\n\n"

[ -f "academy.spec" ] || die "academy.spec is missing. Run this from the program folder."

# The app needs Python 3.10 or newer: it uses `int | None` annotations, which
# 3.9 evaluates at runtime and rejects. START.bat has always enforced this;
# the build scripts did not, so building on a Mac whose `python3` is the
# system 3.9 produced a binary that died at import with a TypeError about
# `|` — a build that succeeds and a program that cannot start.
#
# PATH alone is not enough to find one, for the same reason START.bat scans
# the registry and the standard folders on Windows: a perfectly good
# interpreter is often installed somewhere the current shell does not look.
# On a Mac that is usually Homebrew's, with /opt/homebrew/bin missing from a
# PATH that still has /usr/bin and its 3.9 in it.
MIN_MAJOR=3
MIN_MINOR=10

version_number() {
  # "3.14" -> 314, so versions compare as integers. Empty if it will not run.
  "$1" -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>/dev/null
}

PY=""
PY_BEST=0
FOUND=""
for candidate in \
    python3.14 python3.13 python3.12 python3.11 python3.10 python3 python \
    /opt/homebrew/bin/python3.* /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.* /usr/local/bin/python3 \
    /opt/homebrew/opt/python@3*/bin/python3.* \
    /Library/Frameworks/Python.framework/Versions/*/bin/python3 \
    /usr/bin/python3
do
  case "$candidate" in
    /*) [ -x "$candidate" ] || continue ;;
    *)  command -v "$candidate" >/dev/null 2>&1 || continue ;;
  esac
  n=$(version_number "$candidate") || continue
  [ -n "$n" ] || continue
  case "$FOUND" in
    *"$candidate "*) continue ;;
  esac
  FOUND="$FOUND$candidate ($((n / 100)).$((n % 100))) "
  # Highest wins, so the answer does not depend on PATH order.
  if [ "$n" -ge "$((MIN_MAJOR * 100 + MIN_MINOR))" ] && [ "$n" -gt "$PY_BEST" ]; then
    PY="$candidate"
    PY_BEST="$n"
  fi
done
if [ -z "$PY" ]; then
  if [ -n "$FOUND" ]; then
    die "Python $MIN_MAJOR.$MIN_MINOR or newer is needed to build this. Found: $FOUND. Install a newer one — 'brew install python@3.12' or python.org — then run this again."
  fi
  die "Python 3 is needed to BUILD this — the finished program won't need it. Install it from python.org, then run this again."
fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])') — $(command -v "$PY" 2>/dev/null || echo "$PY")"

step "[1/4] Setting up the build environment…"
# Everything is installed into a virtualenv beside this script rather than
# into the chosen Python.
#
# Not tidiness: Debian, Ubuntu and Fedora all mark their system Python
# externally managed (PEP 668) and refuse `pip install` into it outright,
# and Homebrew does the same on macOS. Installing into the machine's own
# Python was never a good idea anyway — the same reasoning START.bat gives
# for .venv-windows.
#
# PyInstaller bundles what it can see, so the app's dependencies go in here
# too, not just the build tool. On Debian and Ubuntu the venv module is a
# separate package (python3-venv); the error below says so.
BUILD_VENV=".venv-build"
if [ -x "$BUILD_VENV/bin/python" ] && "$BUILD_VENV/bin/python" -c "" 2>/dev/null; then
  :
else
  # A venv left behind by an uninstalled or upgraded Python still exists but
  # cannot run, so it is executed rather than trusted -- the same check
  # START.bat makes.
  rm -rf "$BUILD_VENV"
  "$PY" -m venv "$BUILD_VENV" || die "Could not create the build environment in $BUILD_VENV. On Debian or Ubuntu, install python3-venv and run this again."
fi
BPY="$BUILD_VENV/bin/python"
"$BPY" -m pip install --upgrade pip --quiet 2>/dev/null || true
"$BPY" -m pip install --upgrade pyinstaller --quiet || die "Could not install the build tool"
"$BPY" -m pip install -r requirements.txt --quiet || die "Could not install the app's own dependencies"

step "[2/4] Refreshing the web interface…"
# static/app/ (the built React interface) is already committed to the
# repository, so this is a freshness check, not a requirement — a machine
# with Python but no Node.js still produces a working binary, just with
# whatever interface build was last committed. Only a developer who edited
# frontend/src needs this to actually do anything.
if command -v npm >/dev/null 2>&1; then
  if (cd frontend && npm ci && npm run build); then
    :
  else
    printf "  %sRefreshing the web interface failed. Using the build already%s\n" "$dim" "$off"
    printf "  %scommitted in static/app instead.%s\n" "$dim" "$off"
  fi
else
  printf "  %sNode.js is not installed on this machine.%s\n" "$dim" "$off"
  printf "  %sUsing the interface build already in static/app.%s\n" "$dim" "$off"
fi

step "[3/4] Packaging…"
# The hidden imports live in academy.spec rather than on this line: uvicorn
# loads several modules by string name at runtime, PyInstaller cannot see
# them, and any that are missing produce a program that opens and closes
# instantly. Keeping them in a file makes them reviewable.
"$BPY" -m PyInstaller academy.spec --clean --noconfirm || die "The build failed — see the output above."

step "[4/4] Done."
echo
printf "  ------------------------------------------------------------\n"
printf "    Your program is here:\n\n"
printf "      dist/MB Ballet Academy\n\n"
printf "    Copy that file into an EMPTY FOLDER on the reception\n"
printf "    laptop and run it: ./\"MB Ballet Academy\" from a terminal,\n"
printf "    or double-click it if the file manager allows running it.\n\n"
printf "    Put it in its own folder, not loose on the desktop: it\n"
printf "    creates academy.db, .env, photos and cards beside itself.\n"
printf "    Back up that whole folder, not just the file.\n\n"
printf "    Test it here first. If it exits immediately, an error.log\n"
printf "    file will be sitting next to it explaining why.\n"
printf "  ------------------------------------------------------------\n\n"
