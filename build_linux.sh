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

PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || die "Python 3 is needed to BUILD this — the finished program won't need it. Install Python, then run this again."
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])')"

step "[1/4] Installing the build tool…"
# Upgrading pip itself is best-effort: several Linux distros ship a
# distro-managed pip that refuses to upgrade itself this way (Debian/Ubuntu's
# apt-installed pip is a common one) — that's not a reason to stop, since the
# pip already on the machine still works fine.
"$PY" -m pip install --upgrade pip --quiet 2>/dev/null || true
"$PY" -m pip install --upgrade pyinstaller --quiet || die "Could not install the build tool"
"$PY" -m pip install -r requirements.txt --quiet || die "Could not install the app's own dependencies"

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
"$PY" -m PyInstaller academy.spec --clean --noconfirm || die "The build failed — see the output above."

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
