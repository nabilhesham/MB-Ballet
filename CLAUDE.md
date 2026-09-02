# CLAUDE.md

Project memory for Claude Code. Read before making changes.

## What this is

**MB Ballet Academy** — a management system for an art academy in Alexandria,
Egypt. Classes run on a weekly timetable, clients buy session packs, and a QR
member card is scanned at reception to check them into whatever class is
running. Everything runs on one laptop.

Two surfaces:
- `/` — dashboard and all management screens
- `/reception` — kiosk check-in screen, fullscreen

**Phase 1 (current): laptop only, no door hardware.** Reception opens the door
manually after seeing the verdict. The maglock/Raspberry Pi build is deferred
until this has run with real clients. Do not add door-hardware code unless asked.

**Phase 2 (designed for, not built):** a Pi at the door calls the same
`/api/access/verify` and drives a relay. Keep that contract stable.

## Stack

Python 3, FastAPI, SQLite (WAL), uvicorn. Frontend is vanilla HTML/CSS/JS with
hash routing — **no build step, no npm, no framework**.

**Visual language.** The palette comes straight from the academy logo — the
purple `#87438E` of the dancer and the pink `#EAAECA` of the panel behind her.
The admin is light: paper background, purple accent, serif headings
(`--serif`) against a sans body and mono for numbers and identifiers. Staff
look at it all day in a bright room. The reception kiosk overrides the same
token names back to dark in its own `<style>` block, because it is read at a
glance from a distance. Never hardcode a colour in JS — use the CSS variables;
that is what makes the two themes share one set of views.

**Responsive.** Sidebar becomes an off-canvas drawer under 900px (`body.nav-open`,
`.topbar`, `.scrim`), tables scroll horizontally inside `.dt-scroll`, secondary
columns are dropped with `.hide-sm`, modal actions stack under 480px. There is
also a print stylesheet. This is deployed by
copying a folder to a laptop and running one command. Anything needing a build
pipeline is the wrong answer here.

**Tables sort and search without a library.** `enhanceTables()` in `app.js`
upgrades ordinary server-rendered `<table>` markup in place: click a header to
sort, and long tables get a search box and a capped scrolling body whose header
stays put. It is called from exactly two places — the end of `render()` and the
end of `openModal()` — so no view wires it up itself, and it is idempotent.

A table opts into the search box by carrying `data-search="placeholder"`; the
seven long ones do. A date cell carries `data-sort="<unix seconds>"` so WHEN
columns order chronologically rather than by the letter the month starts with.
The scroll cap itself is unconditional — every table gets it once it is long
enough, `data-search` or not — only the toolbar is opt-in.

The Clients list carries its own hand-built bar instead of `data-search`: it
already searches server-side through `/api/clients?q=`, and letting
`enhanceTables()` add a second, client-side box next to it would filter a
different set of rows than the one just fetched. The bar is styled to match —
same classes, same position above the table — so it reads as one system even
though the wiring underneath is different.

DataTables and jQuery were tried from a CDN and removed. Two reasons, both
fatal here: this folder is copied to a reception laptop that may have no
internet, so a CDN tag means the admin silently loses sorting and searching
exactly where the tables are longest; and the library ships its own CSS, which
fought the padding and type scale of everything above. Do not reintroduce
either.

## Files

Run `./cleanup.sh` if the folder has accumulated files from earlier versions
(`entry.db`, `run.bat`, `run.sh`, `issue_card.py`, `reissue.py`, `test_flow.py`,
`MB Ballet Academy.spec`, `__pycache__`, a shared `.venv`). It lists before it
deletes and never touches data.

```
db.py             Schema + connection helpers. All tables live here.
tokens.py         Signed token issue/parse. HMAC-SHA256. No I/O.
access.py         Access rules: verify / check_in / undo / manual_check_in.
cards.py          Member card PNG generation.
server.py         FastAPI routes. Thin — logic belongs in access.py.
sheets.py         Readers for the academy's Excel workbooks. Parsing only, no I/O.
seed.py           Wipes the DB and rebuilds it from sheets/. --force required.
static/index.html Admin shell + sidebar.
static/app.js     SPA: router, all views, all modals.
static/style.css  Design tokens and components.
static/reception.html  Kiosk check-in screen (standalone, own JS).
START.bat         Windows double-click launcher.
start.sh          Same thing for terminal / Mac / Linux.
Check Setup.bat   Reports what the launcher can see. For diagnosing setup.
BUILD_EXE.bat     Run once on Windows to produce the standalone .exe.
academy.spec      PyInstaller build definition. Hidden imports live here.
run_app.py        Entry point for the packaged build.
cleanup.sh        Removes leftovers from earlier versions.
                  (no settings page: the no-show rules it configured are gone)
sheets/           The academy's own workbooks — the seed reads these.
                  Real names and numbers, so not in git. See sheets/README.md.
static/scanner-test.html   Scanner timing diagnostic, for tuning GAP_MS.
cards/  photos/   Generated assets. Not in git.
academy.db        The database. Not in git. This IS the business record.
.env              ENTRY_SECRET. Not in git, ever.
```

## Commands

```bash
./start.sh                  # Mac/Linux, or Git Bash on Windows
./start.sh --seed           # wipe and rebuild from the sheets/ workbooks
./start.sh --reset-admin    # forget all accounts, regenerate a bootstrap admin
START.bat                   # Windows: double-click, or run from cmd
BUILD_EXE.bat               # Windows, run once: produces a standalone .exe
"Create Desktop Shortcut.bat"
```

### The reception laptop has nothing installed

`START.bat` is written for a machine with no Python and a user who cannot be
asked to install any. It tries, in order:

1. a `runtime\` folder bundled beside it
2. a Python already on the machine — **skipping any path containing
   `WindowsApps`**, because that is the Microsoft Store stub that opens the
   Store instead of running
3. `winget install Python.Python.3.12`
4. downloading the **embeddable** Python zip into `runtime\`. No installer, no
   admin rights, nothing written outside the folder; deleting the folder removes
   it. The embeddable build ships with site-packages disabled, so the script
   uncomments `import site` in `python*._pth` or pip installs would be invisible.

Failures print what to do in plain language, never a stack trace, and
`:diagnostics` dumps what was found. `Check Setup.bat` runs the same detection
standalone.

**The batch trap that already bit once.** cmd.exe parses an entire
parenthesised block before executing any of it, so a `)` or `>` inside a
command in that block terminates it early. An earlier version inlined

```bat
python -c "sys.exit(0 if sys.version_info>=(3,10) else 1)"
```

inside an `if` block. The `(3,10)` closed the block and `>=` read as a
redirect, so a machine with Python 3.11 was told Python was missing. Every
command containing parentheses, pipes or redirects now lives in its own
subroutine, and version comparison parses the plain text of `python -V`
instead of running Python code. **Keep it that way.**

Detection also scans the standard install folders and the registry, because
installing Python with "Add to PATH" unticked is common and makes `where`
useless.

**Dependencies go into a per-platform environment beside the script**, never
into the machine's own Python.

| platform | folder |
|---|---|
| Windows (`START.bat`, Git Bash) | `.venv-windows` |
| Linux | `.venv-linux` |
| macOS | `.venv-macos` |

A single shared `.venv` does **not** work: it holds compiled, platform-specific
binaries, so running the Linux script clobbers the Windows one and vice versa —
which is exactly what happened when the folder is on a USB stick, a synced
drive, or a dual-boot machine. Everything else in the folder (`academy.db`,
`.env`, `cards/`, `photos/`) is plain data and stays shared, which is the point.

`start.sh` also handles Git Bash on Windows, where `venv` builds `Scripts/`
rather than `bin/` — `venv_python()` checks both. Installing system-wide often needs admin rights on a
locked-down reception laptop, and a local folder means uninstalling is just
deleting the folder. The base interpreter is used for exactly one thing —
`python -m venv .venv` — and every command after that runs `%VPY%`
(`.venv\Scripts\python.exe`). `start.sh` does the same on Mac and Linux.

Two cases the script handles:

- **A stale `.venv`.** A folder left behind by an uninstalled or upgraded
  Python still exists but cannot run, so `:check_venv` executes it rather than
  trusting `if exist`, and rebuilds if it fails.
- **The embeddable fallback Python has no `venv` module.** It is already
  private to the folder, so packages install into it directly and the venv step
  is skipped. That is what the `EMBEDDED` flag is for.

`BUILD_EXE.bat` is the stronger option: run once on any Windows machine with
Python and it produces a single `MB Ballet Academy.exe` needing nothing at all.
**PyInstaller cannot cross-compile — a Windows exe must be built on Windows.**

The entry point is `run_app.py`, not `server.py`. A double-clicked exe closes
its console the moment the process dies, so an unhandled exception is invisible
— the window "opens and shuts" with nothing to go on. `run_app.py` therefore:

- writes any traceback to `error.log` beside the exe and holds the window open
- forces line-buffered stdout, or the banner and the first-run admin password
  would only appear after the program exits
- checks the port **before** uvicorn touches it. "Address already in use" is the
  most common cause of an instant exit and is usually not an error at all: if
  the thing on the port answers as ours, it just reopens the browser; if it is
  something else, it moves to the next free port
- calls `multiprocessing.freeze_support()`, since onefile builds re-exec

Hidden imports live in `academy.spec`, not on the command line — uvicorn and
starlette load modules by string name that static analysis cannot find, and a
missing one produces exactly the silent-crash symptom above.

**Bug worth remembering:** `app.mount("/photos", StaticFiles(directory="photos"))`
executes at *import* time and raises if the folder is absent. Creating the
folders in the startup event was too late, so a fresh install died during
import. `server.py` now makes `photos/` and `cards/` at module level, before the
mounts. Any new mount needs the same treatment.

`server.py` is freeze-safe for this: when `sys.frozen` is set, static assets are
read from `sys._MEIPASS` (wiped on exit) while the working directory is the
folder containing the exe, so `academy.db`, `.env`, `photos/` and `cards/`
persist. Getting this backwards silently destroys the database on every close.

The server also **provisions its own `ENTRY_SECRET`** into `.env` if none
exists, since a double-clicked exe has no shell wrapper to export one.

Manual, when working on the code:

```bash
set -a; source .env; set +a   # Windows: $env:ENTRY_SECRET="..."
python seed.py --force        # rebuild from sheets/ — WIPES the database
python seed.py --force --dry-run   # parse and report, write nothing
python server.py
```

`START.bat` opens the browser at `/reception`, not the dashboard — that is the
screen the laptop exists for, and the sidebar links back.

## Data model

```
instructors ─< sessions >─ classes
     │            │
     │            └──< bookings >── clients ──< subscriptions
     │                                  └──< credentials (one per class)
     └──< instructor_hours
```

**A booking is one paid slot.** Buying a 12-session plan creates 12 bookings
immediately, each pointing at a specific session. This is the centre of the
system and everything else follows from it:

- **classes** are the offering, at the granularity the roster sheets use:
  "Ballet Level 8", "Ballet Grade 6", "Evening Flexibility". One block of a
  roster sheet is one class. They carry a duration, a colour and a level, and
  they do **not** carry an instructor. Splitting them this finely is what
  makes one card per class mean something — a Grade 6 card must not check
  someone into the Level 8 class two hours earlier.
- **sessions** are dated occurrences. The instructor lives here, per session,
  because who teaches a given date changes often enough that a class-level
  default was more misleading than useful. No capacity field.
- **bookings** replaced `enrolments`, `attendance` and `session_roster` at once.
  Status is `booked` → `present` | `absent`. There is no third state: a slot is
  either used or it is not, and **both present and absent consume it**, because
  the place was reserved either way.
- **subscriptions** are plans. `sessions_used` is *not* stored — it is counted
  from the bookings by `access.plan_state()`, so the two can never drift apart.
  `price` is what was paid, and it is **nullable on purpose**: the ballet sheet
  records "yes" rather than an amount, and a plan whose price nobody wrote down
  must not be reported as zero revenue. `payment_note` keeps the cell verbatim
  ("package", "free", "680") because "package" and a blank mean different
  things.
- **instructor_hours** is one row per instructor per working day, from the
  monthly salary sheet. Pay is `hours x hourly_rate` at read time, never
  stored, so correcting a rate re-prices the month instead of leaving a stale
  total behind. It is what payroll is actually paid on; "sessions taught" is
  the app's own count, and the instructor page shows both because a gap
  between them is worth seeing.
- **credentials** carry a `class_id`. A client taking two classes holds two
  cards; scanning the Ballet card looks only for a Ballet session.

**The class is the spine.** A plan is bought for one class, may only be
assigned to that class's sessions, and is proved by that class's card:

```
class ──< sessions ──< bookings >── subscription (one class) ──< credential (same class)
```

Every link is enforced server-side, not just in the UI:

- `POST /api/clients/{id}/plan` takes a `class_id` and rejects any chosen
  session belonging to another class. Renewing deactivates only the previous
  plan **for that class**, so a client's other class keeps running.
- `POST /api/clients/{id}/card` requires a `class_id` and refuses when the
  client has no active plan in it. Issuing a Flexibility card to someone who
  only takes Ballet would mint a credential that can never check anyone in,
  and reads at reception as a system fault.
- `access.active_plan(conn, client_id, class_id)` returns that class's plan or
  **nothing** — it never falls back to another class. Falling back is worse
  than "no plan": it lets one card spend another class's balance, which is the
  exact confusion one card per class exists to prevent.

The plan picker in the UI leads with the class and fetches sessions with
`?class_id=`, so the wrong session is never on screen to be chosen.

Class membership is derived from bookings. There is no enrolment list, which is
why the class page shows "students with a booking" rather than a roster.

### Rules the model enforces

**Every plan slot must be assigned to a real session before the plan saves.**
`POST /api/clients/{id}/plan` rejects a mismatch between `sessions_total` and
`session_ids`, and the picker keeps Save disabled until they match. A plan with
unassigned slots is a promise nobody has written down.

**One check-in per day.** A second scan the same day is refused with the time of
the first, and nothing is deducted.

**Arriving early still checks you in.** The scan matches any of today's booked
sessions for that card's class, not just one starting imminently — making
reception wait for the exact start time helps nobody.

**Past sessions settle themselves.** `access.settle_past_sessions()` marks any
still-`booked` slot absent once its session has ended, and runs on startup,
hourly, and before every read that touches attendance. Nothing on screen is
stale.

## Seeding from the academy's spreadsheets

Reception has run this academy out of Excel for years and will keep doing so.
So the workbooks are the seed's input, not a throwaway import format:
`sheets.py` parses them into plain dataclasses and `seed.py` inserts. Add a
term by adding a line to `SHEETS` at the top of `seed.py`.

`sheets.py` never touches the database and `seed.py` holds no parsing. Keep
that split — it is what lets the reader be reasoned about against a real
workbook without a database in the loop.

**The reader works from the header row, never from column numbers.** Blocks in
the same file disagree about whether they have a `school` column and whether
`NAME` is labelled at all. Attendance columns are found by their `1ST`/`2ND`
headings, which is what lets the ballet and flexibility sheets share one
reader. Do not reintroduce fixed column indices.

**Every guess is reported, never applied quietly.** One row is dated 2028 and
another 2019 in a sheet whose every other date is 2026; one student has the
same date written into two columns. The reader repairs these and appends a line
to `warnings`, which `seed.py` prints at the end of the run. `--dry-run` parses
and prints without writing. If a repair ever becomes silent, the sheet stops
being auditable.

**Money the sheet does not state is not zero.** The flexibility sheet writes an
amount; the ballet sheet writes "yes". Unpriced plans are counted and shown as
their own figure on the dashboard.

**Clients are identified by phone, not by name.** The same student is "rodaina
hesham" on one sheet and "rodina hesham" on another. Merging on the last ten
digits of the mobile is what gives her one profile and two cards rather than
two half-profiles.

**Attendance on a day the group does not normally meet still creates a
session.** Those are makeup classes and they really happened. The weekly grid
is generated from the block's own weekdays, then any stray attendance date is
added to it.

## No authentication

Deliberately removed. This runs on one laptop, on one desk, physically behind
the reception counter — a login screen there is a daily obstacle protecting
against nothing, since anyone who can reach the keyboard could just as easily
read the screen over the receptionist's shoulder.

Consequences to keep in mind:

- The server binds to `127.0.0.1` only. **Do not expose it on the LAN** without
  putting authentication back first. That is the whole security model.
- There is no admin/staff split, no users table, no audit log. Every screen is
  available to whoever is at the machine.
- Editing happens in the view that owns the thing: classes on the class page,
  sessions on the session page, clients on their profile. There is no separate
  admin console, and re-adding one would just duplicate what those pages do.

## Deletion policy

Anything with history is **archived** (`active=0`), never deleted. Permanent
deletion is a separate `?hard=true` call, and the server refuses it when
attendance records exist. Losing the record of who attended what is worse than
a cluttered list. Archived records are restorable from the Admin → Archive tab.

Manual balance adjustment exists (`PATCH /api/admin/subscriptions/{id}`) and
requires a reason, because the alternative is staff inventing workarounds.

## Design decisions — do not undo these without asking

**The QR says WHO, not WHETHER.** The token carries a client ID and a
signature. All permission comes from the database. Never bake balances,
expiry, or class membership into the token.

**Token format: 40 base32 chars, uppercase A–Z and 2–7 only.** No punctuation.
Reception runs Windows with an Arabic keyboard layout active, and an HID scanner
emits *keystrokes* — punctuation gets remapped and corrupts the payload. Do not
switch to base64, UUIDs, or JSON payloads.

**One check-in per day.** A client who scans again on the same calendar day is
refused with "Already checked in today at HH:MM" and **nothing is deducted**.
The earlier rolling ten-minute window was wrong: someone returning after lunch
would have been charged twice. `access._todays_checkin()` owns this.

**verify() and check_in() are separate calls.** verify() is read-only and
returns an `event_id`. Nothing is deducted until reception presses the button. A
scan that doesn't become a check-in must not cost a session. There is a
60-second `undo()`.

**Session matching is a suggestion, not a gate.** verify() finds sessions the
client is enrolled in within roughly ±45 min. If none match it still returns
`granted: true` with `no_session: true`, and the kiosk turns amber with "CHECK IN
ANYWAY". Reception has judgement; the software should inform, not block. Do not
turn this into a hard deny.

**Session spend is atomic via the WHERE clause:**
```sql
UPDATE subscriptions SET sessions_used = sessions_used + 1
 WHERE id=? AND sessions_used < sessions_total
```
A second call matches zero rows. Never replace with read-then-write.

**Credentials are revoked, never deleted.** `revoked_at` timestamp. The log must
keep pointing at the credential actually used. Issuing a card auto-revokes the
previous one.

**Tolerant of scanner quirks, strict about tampering.** `parse()` accepts
lowercase and surrounding whitespace — scanners emit stray characters and shift
states, and rejecting a paying client over that is a weekly support call. One
flipped character fails the signature. Keep this asymmetry.

**`hmac.compare_digest` for signatures.** Never `==`.

**Only plans of 12 sessions or more can be frozen.** `access.FREEZE_MIN_SESSIONS`
holds the number and `access.can_freeze()` is the single answer to "may this be
paused?" — the endpoint refuses and the button greys out for the same reason,
carrying the same sentence. Short packs are meant to be used inside their
window; freezing a 4-session pack for two months makes the expiry meaningless.
`plan_state()` returns `can_freeze` and `freeze_blocked_because` so the UI never
has to re-derive the rule and drift from it.

**Manual check-in deducts a session too.** Marking someone present from the
session page is the same transaction as a scan, because a client who forgot
their card still attended. Undoing refunds it.

## Scanner input handling

The scanner is a USB HID device — it *is* a keyboard to the OS. No driver, no
serial port, no SDK.

`reception.html` separates scans from typing with four stacked layers:
1. Ignore keystrokes when focus is in an input/textarea/select
2. Timing: `GAP_MS = 80`, characters further apart reset the buffer
3. Shape: minimum 10 chars, terminated by Enter
4. Signature check server-side — the real backstop

**Tune `GAP_MS` once hardware arrives:** open `static/scanner-test.html` in the browser, note the max
gap, set it to roughly 3x that.

**Planned, not built:** configure the scanner to emit a prefix character (`~`)
and gate capture on it. Makes detection deterministic rather than heuristic and
fixes the key-autorepeat false positive. Blocked on having the hardware.

## The reception kiosk

**Exactly two input sources**, chosen with a segmented control in the sidebar:

- **Scanner** — the default. Passive keystroke capture, so it keeps listening
  even while the camera is on; switching modes only changes what the screen
  shows.
- **Camera** — `BarcodeDetector` where available, jsQR from CDN otherwise.

The browser cannot enumerate HID keyboards, so "scanner connected" is *inferred*
rather than detected: the indicator turns green the first time a burst of
scanner-speed keystrokes arrives. Until then it reads "listening for scanner"
and the idle text suggests switching to Camera. Do not claim detection the
browser cannot actually do.

The old dev panel (client dropdown, paste box) has been removed. Test without
hardware using Camera mode and a card PNG on a phone screen.

### One card per class

`credentials.class_id` decides which session a scan looks for. A client holding
a Ballet card and a Flexibility card gets the right session either way, and
presenting the wrong card for today returns "No session booked today for
Flexibility" rather than silently checking them into the other class.

Cards are written to `cards/client_00001_ballet.png` — the class slug is part of
the filename so two cards coexist. `cards.card_path()` derives that name and is
the only place that does: the client profile offers the file for download and
print, and a second copy of the rule drifting from this one is a dead link on
the one screen that has to work.

### What a scan shows

`access.client_profile()` supplies the context, and it is deliberately
generous — the receptionist has a few seconds with a person in front of her,
and that is the only moment "expired last week" or "three absences" is worth
anything. Looking it up afterwards never happens.

The **photo is the largest element on the screen**. It is the real control
against a screenshotted card being passed between friends; a signature check
cannot tell you the person holding the phone is the member.

Denials carry the profile too, where the client is known — reception needs to
know *who* was refused and why, not just that something failed.

## Hardware status

Not yet purchased. Requirement: 2D **imager** (never "laser" — a laser scanner
physically cannot read QR), USB HID keyboard mode, must read a phone screen at
~60% brightness at an angle.

- U-POS UP-888 Pro — preferred. Presentation style, large window.
- U-POS UP-868 — cheaper, narrower aperture.
- Honeywell MS7820 Solaris — **rejected: 1D laser, cannot read QR.**
- Upgrade path if screen reading disappoints: Zebra DS9308.

## Known gaps / next up

- [ ] Rate limiting on `/api/auth/login`. Currently unlimited attempts; fine on
      a LAN-less laptop, not fine if this is ever exposed.
- [ ] Ballet prices. The ballet roster's PAID column only ever says "yes", so
      those plans import unpriced and the month's revenue figure counts
      flexibility alone. The dashboard says how many plans carry no price
      rather than quietly reporting them as zero. Either the sheet starts
      recording the amount or the fee goes on the class.
- [ ] Rotating phone tokens: `access.py` has the `kind='phone'` path with a 90s
      freshness window, but nothing generates them client-side.
- [ ] `settle_past_sessions` runs in-process. If the laptop is off overnight it
      catches up on next start, which is fine — but there is no record of *when*
      a slot was marked absent versus when the session ran.
- [ ] No migration from the pre-bookings schema. Three tables were replaced at
      once, so an old `academy.db` must be re-seeded rather than upgraded.
- [ ] Nightly SQLite backup + a *tested* restore.
- [ ] Auto-start on boot, and disable laptop sleep / lid-close suspend.
- [ ] Key rotation: single secret. Changing it kills every printed card at once.
      Needs an accepted-keys list with an overlap window.
- [ ] The test scripts (`test_model.py`, `test_freeze.py`, `test_plan_class.py`)
      run against the live `academy.db` and mutate it. Re-seed before and
      between runs. They resolve their subjects from whatever was seeded rather
      than naming clients or classes, so they survive a new term's workbooks.

## Conventions

- Business rules in `access.py`, never in `server.py` or the frontend.
- Deny messages are written for a receptionist to read aloud in plain language
  ("Subscription expired 6 days ago"), not error codes. Technical detail goes in
  `detail`, which the UI does not show.
- All user text passes through `esc()` in the frontend. No exceptions.
- Comments explain *why*, not *what*. Most existing ones record a decision.
- **English only.** There are no Arabic fields anywhere — no `name_ar`, no
  second name input, no RTL block. The academy works in English and a
  half-filled translation column was worse than none.
- Colours come from CSS variables in `style.css`. Never hardcode a hex in a view.
- No emoji in code or UI.

## Security notes

- `ENTRY_SECRET` lives in `.env`. Never commit, hardcode, or log it. The server
  refuses to start without it.
- Binds to `127.0.0.1` only. Do not expose on the LAN without adding auth first.
- `academy.db` is the entire business record. Back it up.
