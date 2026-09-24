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

Python 3, FastAPI, SQLite (WAL), uvicorn on the backend. The admin frontend is
React (Vite, plain JavaScript + `.jsx`, no TypeScript) with `react-router-dom`'s
`HashRouter`, which is what keeps every `#/clients`, `#/client/17` URL working
byte for byte across the rewrite. Source lives in `frontend/src/`.

**No build step on the reception laptop — that promise survives the rewrite,
it just moves.** `npm run build` runs once, on a developer's own machine,
producing a folder of hashed, minified JS/CSS in `static/app/`. That output is
committed to git like any other file. `START.bat`, `start.sh`, and the
packaged `.exe` never invoke npm — they serve whatever is already sitting in
`static/app/`. Node and npm are developer-only tools, exactly the way a
compiler is a developer-only tool for a language that ships compiled: nothing
about running the app requires them.

**The cost of that, written down where the next person hits it:** a developer
who changes anything under `frontend/src/` must run `npm run build` and
commit the refreshed `static/app/` in the same commit. Nothing rebuilds it
automatically — a source change with no matching `static/app/` change ships
as a silent no-op.

**Replacing the folder on the laptop is enough — the browser is told not to
cache the app.** It did not used to be: copying a new build over the old one
left the receptionist looking at last month's screens until someone pressed
ctrl-shift-R on every page in turn. Nothing was cached deliberately. A
response with *no* `Cache-Control` at all lets a browser invent its own
freshness, and the usual heuristic — a tenth of the file's age — means an
`index.html` that has sat on disk for a month is treated as fresh for days;
it then keeps asking for the hashed bundle that stale copy names, which it
also still holds, so the new folder is invisible.

`server.py`'s `cache_policy` middleware ends that by saying what every
response is, and there are only two kinds:

| what | header | why |
|---|---|---|
| `/static/app/assets/*` | `max-age=31536000, immutable` | the build hashes these names, so a new build is a new URL and this one can never be stale — that is the entire point of hashing them |
| everything else | `no-store` | the entry HTML, `style.css`, `reception.html`, the API, a reissued card: all keep their names across builds, so a cached copy is a stale copy |

The cost is refetching a few small files over localhost, which is not
measurable. Do not "optimise" the second row — losing this means a
receptionist being shown an old build with no way to know it.

The three launchers also open the browser at `…?v=<a number that differs
every launch>` (`app_url()` in `run_app.py`, `%RANDOM%%RANDOM%` in
`START.bat`, `date +%s` in `start.sh`). That is not a second mechanism doing
the same job: it covers the one case the header cannot, a page a browser
cached *before* the header existed, which it will go on serving for days
without asking. A URL it has never seen cannot come out of that cache.

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
also a print stylesheet. Deployed by copying a folder to a laptop and running
one command — see the Stack section above for how a React build stays
compatible with that on the machine that matters, the reception laptop.

**A row of filter inputs and the buttons acting on them uses `.filterbar`**,
which pins both to the same 42px height and bottom-aligns them. A default
button is a few pixels shorter than a date field and reads as stuck on the
end rather than part of the same control; the instructor page's from/to
range is the first user. Such a range applies on a button, never on change —
a date input fires on every edit, so binding a request straight to it
reloads the view for a half-typed year.

The dashboard's intake period is the second user, and is **whole months**
(`<input type="month">`, `month_from`/`month_to` on `/api/dashboard`),
because that is the granularity both figures it governs actually mean:
"joined in September" is an answer, and "joined between the 8th and the
23rd" is not a question anyone asks of an academy that bills by the month.
It sits directly above the two cards it moves — new clients and what they
paid — and nothing else on that page is about a period at all, so putting it
anywhere else would imply it governs the rest. `new_clients_prev` compares
against the same span immediately before (one month back for a month, three
for a quarter), so the comparison stays like for like however wide the
window is opened.

**The two intake figures are scoped differently, and must stay that way.**
"Earned from them" is filtered by *who* — every plan belonging to a client
who joined in the period, whenever they bought it — while `revenue` is
filtered by *when*, every plan sold inside the window whoever bought it. The
first used to carry both filters at once, and a client who joined on 14
August whose plan started on 2 September then fell through both months: out
of range in August, not a new client in September. August read "4 new
clients, 0 EGP" while three of those four had paid 4,100 between them, and
because two ANDed date windows do not add up across sub-periods, August plus
September came to more than either month showed — which is how it was
noticed. Scoping it to the people is what keeps the two cards describing the
same clients; the price is that a past month's figure grows as its intake
renews later, which is what "earned from *them*" means. Do not put the
second filter back to make it add up — `revenue` is the number that is
period-bound, and the card says "new or returning" under it for exactly that
contrast.

**Tables sort and search via `<DataTable>`** (`frontend/src/components/DataTable.jsx`),
a controlled component that replaced app.js's old `enhanceTables()`. Click a
column header to sort; a long table gets a capped scrolling body whose header
stays put. The behaviour is the same as before — the mechanism had to change,
because the old one worked by mutating already-rendered DOM after the fact
(wrapper divs, rows physically reordered by `appendChild`), which is exactly
what fights React owning that same subtree.

A table opts into the search box with a `search="placeholder"` prop; most
long tables pass one. Sorting has no separate opt-in switch: a column sorts
if its definition carries a `sortValue: row => value` function, which is why
every column doesn't need a `sortable: false` — no `sortValue` already means
unsortable. The scroll cap applies to every table regardless of whether it
has a search box, and is recomputed against the *filtered* row count on every
render, so a table searched down to a handful of rows drops its cap rather
than keeping the one computed before the search started.

The Clients list carries its own hand-built search bar instead of
`<DataTable>`'s `search` prop: it already searches server-side through
`/api/clients?q=`, and a second, client-side filter next to it would filter a
different set of rows than the one just fetched. It still renders its table
through `<DataTable>` with no `search` prop, so sorting still works — the old
`enhanceTables()` made every table sortable regardless of whether it opted
into search, and this is the one list that needs that distinction preserved.
The bar is styled to match — same classes, same position above the table —
so it reads as one system even though the wiring underneath is different.

DataTables and jQuery were tried from a CDN in the old vanilla frontend and
removed — the library's own CSS fought the padding and type scale of
everything else, and back when there was no build step at all, a CDN tag
meant the admin could silently lose sorting on a reception laptop with no
internet. That specific risk is gone now that the whole interface is one
bundled, committed file, but the CSS fight is still a real reason: do not
reintroduce either.

## Files

Run `./cleanup.sh` if the folder has accumulated files from earlier versions
(`entry.db`, `run.bat`, `run.sh`, `issue_card.py`, `reissue.py`, `test_flow.py`,
`MB Ballet Academy.spec`, `__pycache__`, a shared `.venv`). It lists before it
deletes and never touches data.

```
config.py         Where the files live and which database to talk to. Every
                  value is a function, never a module constant -- see the
                  Configuration section below. Must be imported and
                  load_env()'d before `import db`.
repo/             The data-access interface and its two implementations.
                  Nothing outside repo/sqlite/ writes SQL -- see the
                  Repository section below.
  base.py          The twelve primitives, the transaction boundary, and the
                   admin methods. An ABC: a backend missing one fails at
                   construction rather than at reception.
  ports.py         Tier two -- the named business questions that join,
                   aggregate, or compare-and-swap.
  filters.py       The filter dialect both backends speak.
  sqlite/          SQLite: the only place in the app that contains SQL.
  mongo/           MongoDB: schema.py declares every field (which is what
                   makes null-vs-missing a non-issue), ids.py mints the
                   integer ids, filters.py adds the null guard.
db.py             Schema + connection helpers + db.tx(). All tables live
                  here, and the indexes live in a *second* string applied
                  after migrate() -- see the note under Files below.
identity.py       What makes two client records the same person: the mobile
                  number AND the name. MIN_DIGITS, the last ten digits
                  (phone_key) and how a name is folded (name_key). Its own
                  module -- it was phones.py until the name became half the
                  rule -- because access.py, both repository backends and
                  seed.py all need the same answers and a second copy would
                  drift. No I/O, imports nothing, so it sits under all of
                  them.
tokens.py         Signed token issue/parse. HMAC-SHA256. No I/O.
access.py         Access rules: verify / check_in / undo / swap_and_check_in.
cards.py          Member card PNG generation, and cards.issue() --
                  credential + PNG + stamped URL in one step, shared by
                  the profile's Issue/Reissue and the desk renewal.
server.py         FastAPI app, paths, the lifespan handler, cache policy,
                  static mounts.
                  Thin — routes
                  live in api/, business rules live in access.py.
api/              One router module per resource, wired into server.py with
                  ordinary static imports (clients.py, plans.py,
                  classes.py, instructors.py, sessions.py, access_routes.py,
                  dashboard.py, images.py, appointments.py).
sheets.py         Readers for the academy's Excel workbooks. Parsing only, no I/O.
seed.py           Wipes academy.db and rebuilds it from sheets/. --force
                  required. Always SQLite, whatever MB_DB_BACKEND says —
                  migrate_to_mongo.py is what moves it to Mongo.
frontend/         React admin source (Vite, plain JS + .jsx). See Stack above
                  for the build-once-commit-the-output model.
  src/views/       One file per route: Dashboard, Calendar, Classes,
                   ClassDetail, Instructors, InstructorDetail, Cards, Sessions,
                   SessionDetail, Clients, ClientDetail, Appointments.
  src/modals/      Every modal, one file each, imported by the view(s) that
                   open it. There is no AddStudents: booking is done from
                   the client's profile, not the session — see below.
  src/components/  Shell (sidebar/topbar/drawer), DataTable, Modal/ConfirmModal,
                   Toast, Avatar, Pill, Empty, ClassPick (the searchable
                   class list both plan pickers choose from).
  src/lib/         format.js (timestamp -> what a receptionist reads, and
                   fmtISODay/fmtISODayTime for a calendar-day column) and
                   planSessions.js (the window a plan's slots are filled
                   from, the ENDS ON cap, and useAutoPick — see the
                   three-weeks-back rule and the start-day rule below).
static/app/       Committed build output of frontend/ — what server.py
                  actually serves at `/`. Regenerate with `npm run build`
                  after any `frontend/src/` change; see Stack above.
static/fonts/     The card's typefaces (DejaVu Serif/Serif-Bold/SansMono)
                  plus their licence. Committed on purpose — cards.py sets
                  the card from these rather than from whatever the machine
                  has installed, so one card looks the same everywhere.
static/logo.png   The academy mark, on the printed card and the kiosk's idle
                  screen. **Transparent background** — keep it that way; see
                  the note in the card section below.
static/style.css  Design tokens and components. Shared by the React admin,
                  reception.html and scanner-test.html — all three load it
                  by the same `/static/style.css` URL, so it is never bundled
                  into `static/app/`.
static/reception.html  Kiosk check-in screen (standalone, own JS, untouched
                  by the React rewrite — see the note below).
.gitattributes    Line endings: LF everywhere, CRLF for .bat, binary
                  left alone. Committed because it beats whatever Git each
                  machine was installed with -- see the line-ending trap
                  below.
START.bat         Windows double-click launcher.
start.sh          Same thing for terminal / Mac / Linux.
                  (Both open the browser with a per-launch ?v= — see the
                  cache note in Stack above.)
Check Setup.bat   Reports what the launcher can see. For diagnosing setup.
BUILD_EXE.bat     Run once on Windows to produce the standalone .exe. Rebuilds
                  the frontend first if Node is present; uses the committed
                  static/app/ build as-is otherwise.
build_mac.sh      Same thing, run once on a Mac, for a macOS binary. Refuses
                  to run on anything that isn't Darwin — see the WSL note
                  below for why that guard exists.
build_linux.sh    Same thing, run once on Linux, for a Linux binary. Same
                  host guard, symmetrically (refuses on anything but Linux).
.github/workflows/
  build-macos.yml Manually-triggered CI (Actions tab -> Run workflow, or
                  `gh workflow run build-macos.yml`) that builds the macOS
                  binary on a real GitHub-hosted Mac, for anyone who needs a
                  ready-to-run Mac build without access to a Mac. Download
                  the result from the finished run's Artifacts, or
                  `gh run download`. Builds **both** architectures --
                  macos-15 (arm64) and macos-15-intel (x86_64) -- because
                  x86_64 needs Rosetta 2 to be *already installed* on
                  Apple Silicon and it is not there by default; see the
                  runner note below before touching either label. It also
                  smoke-tests the binary it built, and carries a second,
                  cheap `canary` job on a monthly cron -- see the
                  deprecation note below.
  dependabot.yml  Opens one grouped pull request when a GitHub Action
                  publishes a new version. github-actions only: Python and
                  npm are deliberately excluded -- see the file.
academy.spec      PyInstaller build definition, shared by all three build
                  scripts above. Hidden imports live here, and so does
                  the step that bakes this folder's .env into the
                  binary -- it refuses to build without one.
run_app.py        Entry point for the packaged build.
migrate_to_mongo.py  Copies an existing academy.db into MongoDB, preserving
                  every integer id. --dry-run counts first.
cleanup.sh        Removes leftovers from earlier versions.
                  (no settings page: the no-show rules it configured are gone)
sheets/           The academy's own workbooks — the seed reads these.
                  Real names and numbers, so not in git. See sheets/README.md.
static/scanner-test.html   Scanner timing diagnostic, for tuning GAP_MS.
images.py         Every picture the app holds — client and instructor
                  photos and the printed cards — stored in the database as
                  base64. Also carries photos/ and cards/ from an older
                  install in on first start. See the image section below.
cards/  photos/   Only on an install older than images.py: the files those
                  pictures used to be, left where they are once they have
                  been read in. Not in git. `cleanup.sh` removes them.
academy.db        The database, pictures included. Not in git. This IS the
                  business record.
.env              ENTRY_SECRET and the MB_ settings. Not in git, ever.
                  There is exactly ONE of these, here, in the source
                  folder -- a build bakes its values into the binary
                  rather than the app growing a second copy beside
                  the exe. See Configuration below.
```

`static/reception.html` and `static/scanner-test.html` are deliberately
untouched by the React rewrite: the kiosk is the one latency-sensitive
surface in the app (USB-HID scanner keystroke timing, camera barcode
scanning, audio beeps), has no tables, no router, no modals, and nothing to
gain from a re-render model. It stays self-contained, inline `<style>`,
inline `<script>`, vanilla — exactly as before. Its two panels that look
like modals (the manual swap, and the desk renewal below) are inline
blocks for that reason, and the renewal's dates are chosen server-side
precisely so the kiosk never needs a session picker.

**`db.init()` is three steps, in this order: tables, `migrate()`, indexes.**
`CREATE TABLE IF NOT EXISTS` is a no-op on a database that already has the
table, so a column added to `db.py` later does not reach an existing database
until `migrate()` ALTERs it in. An index over that column sitting in the same
`executescript` therefore ran *first*, failed with "no such column", and took
the whole script down -- `migrate()` then never ran at all. That is why
`INDEXES` is a separate string: the academy's own database predates
`sessions.ends_at`, and `ix_sess_ends` made the app unable to open it.
`tests/test_sqlite_schema.py` opens a hand-built pre-`ends_at` database to
keep that true.

## Commands

```bash
./start.sh                  # Mac/Linux, or Git Bash on Windows
./start.sh --seed           # wipe and rebuild from the sheets/ workbooks
START.bat                   # Windows: double-click, or run from cmd
BUILD_EXE.bat               # Windows, run once: produces a standalone .exe
./build_mac.sh              # macOS, run once: produces a standalone binary
./build_linux.sh            # Linux, run once: produces a standalone binary
"Create Desktop Shortcut.bat"

gh workflow run build-macos.yml   # or the Actions tab -> Run workflow:
                             #   builds the macOS binary on a real Mac in CI,
                             #   for when there's no Mac to build on locally.
                             #   Fetch the result with `gh run download` or
                             #   from the run's page -> Artifacts.

pip install -r requirements-dev.txt   # pytest + httpx, developer-only
pytest                      # the suite. Throwaway database per test; the
                             #   real academy.db is never touched.

cd frontend && npm install  # once, to work on the React admin at all
npm run dev                 # Vite dev server, proxies /api etc. to a real
                             #   backend on :8000 (run ./start.sh in a second
                             #   terminal) — for frontend iteration only. Open
                             #   the URL Vite prints, e.g. http://localhost:
                             #   5173/static/app/ — NOT bare :5173/ — since
                             #   the app's `base` in vite.config.js is
                             #   /static/app/, matching production's URL
npm run build                # regenerates static/app/ — required before
                              #   committing any change under frontend/src/
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

**The WSL trap that already bit once, too.** `uname -s` reports `Linux`
inside WSL — both WSL1 and WSL2 — not anything that names Windows. Someone
ran `bash build_mac.sh` inside WSL expecting a macOS build; PyInstaller
cannot cross-compile, so it silently built a genuine Linux binary while
printing macOS-flavoured success text, and the failure only surfaced later,
confusingly, as `zsh: exec format error` on the actual Mac — `chmod +x` and
clearing Gatekeeper are both irrelevant to that, since neither changes a
file's binary format. `build_mac.sh` and `build_linux.sh` now both refuse to
run on the wrong host (`case "$(uname -s)" in Darwin*)`/`Linux*)`), failing
fast with an explanation instead of producing a wrong-platform binary that
"succeeds" until someone actually tries to run it. **Keep those guards.**

**The line-ending trap, which bit repeatedly until `.gitattributes` existed.**
Git for Windows installs with `core.autocrlf=true`, so a clone made on Windows
rewrites every text file to CRLF on checkout. This project is then worked on
*from WSL, against that same checkout on a Windows drive* — and bash cannot run
a CRLF script at all:

```
start.sh: line 8: $'\r': command not found
: invalid option name: pipefail
```

There is no version of a `.sh` file that works both ways, and **no guard can be
written inside one either**: a CRLF script dies at its first `if` with exit 2,
because `then\r` is not `then`. Whatever the guard said would never run. The
only fix is for CRLF never to reach a shell script.

`.gitattributes` does that, and it is the right place because it beats
`core.autocrlf` and `core.eol` and it is committed — so it holds on every clone
on every machine with nobody configuring anything. `* text=auto eol=lf` for
everything, `*.bat`/`*.cmd` back to `eol=crlf` (cmd.exe mis-handles LF-only
files around labels and `goto`, and `START.bat` is built out of subroutines it
jumps between — see the batch trap above), and the shipped assets marked
`binary` so no conversion can ever touch `static/logo.png`'s alpha or the card's
typefaces.

**It only acts at checkout, so a checkout that is already CRLF needs one
command.** `git add --renormalize .` does *not* do it — git's clean filter
strips the CR on read, decides the file is unchanged, and rewrites nothing,
which looks like it worked. What works is re-checking-out every tracked file:

```bash
git rm --cached -rq . && git reset --hard
```

That discards uncommitted changes to tracked files, so commit first. It cannot
touch `academy.db`, `.env`, `photos/` or `cards/` — none of them are tracked.

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
`build_mac.sh`/`build_linux.sh` are the same idea for those platforms — same
`academy.spec`, same four-step flow (build tool, frontend refresh, package,
done). They differ from `BUILD_EXE.bat` only in shell (bash, not batch),
Python-discovery idiom, and the OS-specific caveat printed at the end (see
below) — nothing about the packaging step itself changes per OS.
**PyInstaller cannot cross-compile — a build has to run on the OS it targets.**
A Windows `.exe` needs a Windows machine, a macOS binary needs a Mac, a Linux
one needs Linux; there is no way to pick a target from one script on one
machine. The Linux binary is also tied to the glibc version of the machine
that built it (newer glibc, not older, runs it), and an unsigned macOS binary
copied to a different Mac needs one right-click-Open the first time to clear
Gatekeeper — both scripts print these caveats at the end of a successful build.
`build_mac.sh` and `build_linux.sh` also refuse outright to run on the wrong
host OS (see the WSL trap above) rather than silently producing a
wrong-platform binary — `BUILD_EXE.bat` gets no equivalent guard, since a
`.bat` file only runs under `cmd.exe`/PowerShell in practice, unlike a bash
script that WSL, Git Bash, a Linux box and a Mac terminal can all run
without complaint.

**No Mac to build on?** `.github/workflows/build-macos.yml` builds the macOS
binary on a real GitHub-hosted Mac instead — trigger it from the Actions tab
or `gh workflow run build-macos.yml` (both reachable from Windows/WSL), then
download the finished binary from the run's Artifacts or `gh run download`.
It is manually triggered only (`workflow_dispatch`), never on push, because
macOS runner minutes are billed at a 10x multiplier against the GitHub free
tier and this is an occasional "cut a release" action, not a per-commit one.

**It builds both architectures, and that is the fix for a real failure.** The
academy's Mac met an x86_64 binary and said:

```
zsh: bad CPU type in executable: /Users/…/MB Ballet Academy
```

That error names no cause anyone can act on, **and `error.log` cannot explain
it** — the process never starts, so `run_app.py` never runs. Worse, it has two
opposite causes: an arm64 binary on an Intel Mac (impossible, full stop), or an
x86_64 binary on Apple Silicon **without Rosetta 2** — which is not installed
by default, and is *not* offered when the binary is launched from Terminal the
way Finder launches a Unix executable. The written-down reasoning here used to
be "x86_64 runs everywhere via Rosetta 2", and that is the sentence this
disproved: it runs everywhere Rosetta 2 is *already there*.

So the matrix builds `macos-15` (arm64) and `macos-15-intel` (x86_64) and
names each artifact after its architecture. Download the one matching the
Mac's own `uname -m` and it runs natively, with no Rosetta and nothing to work
out. `fail-fast: false`, so a broken Intel build still hands over a working
Apple Silicon one. A `universal2` build would sidestep the question but needs
every wheel to be universal2, and pydantic-core and pymongo ship
per-architecture ones.

**Two guards, because this label has been wrong twice.** It was `macos-13`,
retired by GitHub in December 2025, and then `macos-14`, which is arm64 — that
bump silently inverted a comment still claiming Intel, and the wrong binary
only surfaced on the reception Mac. `academy.spec` sets `target_arch=None`, so
the runner label *is* the choice of target and GitHub may redefine it at any
time. Each job therefore checks `uname -m` against what the matrix asked for
before building, and `lipo -archs` on the finished binary after — the first
would have caught the macos-14 bug on the run that introduced it. x86_64 is
the row with a deadline: **GitHub drops it in August 2027**, which then just
removes a row.

**Every action is pinned to a major that runs on Node 24, and that is a
deadline rather than a preference.** GitHub removed Node 20 from the runner
images on **23 September 2026**. From 16 June 2026 runners had already been
forcing node20 actions onto Node 24 and printing a deprecation warning,
which was the only notice this workflow ever got; after that date an action
still declaring `runs.using: node20` does not warn, it fails to start.

**The majors do not line up, and assuming they do is the trap.**
`actions/upload-artifact@v5` is **still node20** -- it predates the move --
so bumping each action to "the next one" fixes two thirds of the problem and
looks finished. The pins are `checkout@v6`, `setup-python@v7`,
`upload-artifact@v7`; check `runs.using` in an action's own `action.yml`
before trusting any version number. Two things were checked before pinning
them: no input this workflow passes was removed (setup-python v7 dropped
`pip-install`, unused here), and upload-artifact v6+ needs Actions Runner
>= 2.327.1, which the hosted images have and a self-hosted one might not.
Note also that `upload-artifact@v7`'s new `archive: false` is **not** a
replacement for the tar step below -- a file mode can only travel inside an
archive that carries one.

**Two mechanisms now notice the next one of these, because three have
already been missed.** `macos-13` retired, `macos-14` silently meaning
arm64, and this Node removal all surfaced on the reception Mac rather than
in CI, and they share one cause: this workflow is `workflow_dispatch`-only,
correctly, so nothing exercises it between releases and the breakage always
lands on the day somebody urgently needs a binary.

- **`.github/dependabot.yml`** opens one grouped PR when an action
  publishes a new version -- months of warning for the Node 24 majors. It
  costs no Actions minutes, running on GitHub's own infrastructure.
- **The `canary` job** runs monthly (`0 7 1 * *`, UTC) and does only the
  fragile half: check out, prove the runner label still means the
  architecture the matrix claims, set Python up and confirm it is 3.11,
  upload something. No PyInstaller and no smoke test, so about a minute of
  wall clock -- roughly 20-40 billed minutes a month across two
  architectures at the 10x multiplier, one to two percent of the free-tier
  quota. `"0 7 1 */3 *"` makes it quarterly, at the price of a blind window
  three times as wide.

  It runs on the **real macOS labels** rather than a 1x ubuntu runner
  deliberately: a canary not using the same labels cannot catch a retired
  or redefined one, which is the failure that has actually happened twice.
  `build` carries `if: github.event_name != 'schedule'` so a cron can never
  trigger the expensive build -- which would also arrive with no `backend`
  input, since a scheduled event carries none. The canary's runner list is
  a second copy of `build`'s matrix, the same deliberate double as the
  `.env` parser: **keep the two in step.** If they drift the canary fails
  with "no runner matching the labels", which points at the drift rather
  than hiding it.

  GitHub disables scheduled workflows in a repository that has seen no
  activity for around 60 days, mailing the owner. This repository is quiet
  between terms, so if the canary goes silent that is the first thing to
  check -- it is re-enabled from the Actions tab.

**The artifact is a `.tar.gz`, not the bare binary, and that is not
packaging taste.** `upload-artifact` builds the zip itself and its own docs
say file permissions are not maintained — everything arrives `644`. A 644
binary is not a broken download, it just refuses to run:

```
zsh: permission denied: ./MB Ballet Academy
```

which is a second unexplained error waiting behind the first. The workaround
the action recommends is to tar before uploading, and tar carries the mode, so
the file comes out already executable and there is no `chmod` to remember —
the kind of step that gets skipped exactly when it matters. macOS unarchives a
`.tar.gz` on a double-click, so it costs no Terminal either.

**Each job writes what it built to the run summary**, which is where whoever
downloads it actually looks: which architecture and how to check theirs, the
unpack-and-run lines, the Gatekeeper right-click, the Rosetta install on the
x86_64 job only, and the reminder that a CPU-type error leaves no `error.log`.
Every line of it is an error someone has already hit with nothing to go on.

`build_mac.sh` prints the same notes for a local build: which architecture it
produced, which Macs that runs on, the Rosetta command if it is x86_64, and —
correcting a promise it used to make — that a CPU-type error leaves no
`error.log` to read, because nothing ever started.

**It smoke-tests what it built**, because the failure this packaging step
actually produces is a missing hidden import — uvicorn and starlette load
modules by string name, static analysis cannot see them, and the symptom is a
window that "opens and shuts" with nothing to go on. The step starts the
binary, reads the port out of its own banner rather than assuming 8000 (a
taken port makes `run_app.py` move, and a hardcoded one would either miss the
binary or pass by talking to whatever else was listening), then asks for
`/reception`, `/` and `/api/dashboard` — the kiosk, the committed React build
and the database. It also fails the build when `static/app/index.html` is
missing, since there is no npm step here and a checkout without it packages a
binary that serves nothing at `/`. On failure both `run.log` and `error.log`
are uploaded as an artifact.

**The backend is a choice made when you press Run workflow**, and this is the
one thing about CI builds that has to be understood rather than remembered:
**the checkout has no `.env`.** It is gitignored and always will be — it holds
`ENTRY_SECRET` and an Atlas password — so "make the build use my `.env`" is
not a thing that can happen. CI has never seen that file. `academy.spec` bakes
whatever `.env` is in the tree at build time, and the workflow's own step is
what puts one there, from repository secrets.

That is how a build came out talking to SQLite while the developer's `.env`
said `mongo`. Nothing was broken: the settings were never given to CI, and
`backend()` fell back to its default in silence. Two things stop it
recurring. The `workflow_dispatch` input `backend` (`sqlite` | `mongo`,
default `sqlite`) is printed in the log and named in the run summary, so a run
always states which database its binary is for. And choosing `mongo` without
the `MB_MONGO_URI` secret **fails the build there**, rather than shipping a
binary that raises on the reception Mac — which it would, since
`MB_DB_BACKEND=mongo` with no URI is a deliberate refusal, not a fallback.

The backend is an input rather than a fourth secret because it is not one: it
is a decision, and one visible switch beats two sources of truth that can
disagree. `MB_MONGO_URI` and `MB_MONGO_DB` stay secrets, because the URI
carries the password.

**A `mongo` build has the Atlas credentials inside it**, which follows from
baking `.env` at all and is worth saying out loud where someone downloads one:
anyone who can fetch the artifact can read them. The run summary says so. Keep
the repository private, and scope the Atlas user and IP access list to what
reception actually needs.

`config.describe()` is the line on the startup banner that names the backend,
and it prints the host with any credentials stripped. It used to split on `@`
alone, so a URI with **no** credentials in it — the direct multi-host form
`.env.example` documents for networks that filter SRV lookups — kept the whole
string and the next split returned the *scheme*, printing `at mongodb:`.
`config.mongo_hosts()` does it properly now: drop the scheme, drop
`user:pass@` from the right (a password may itself contain `@`), then take the
host list.

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
executed at *import* time and raised if the folder was absent. Creating the
folders in the startup event was too late, so a fresh install died during
import; `server.py` made them at module level, before the mounts. Both mounts
are gone now that pictures are rows, so the folders are gone with them — but
**any new mount needs the same treatment**, which is why this is still here.

`server.py` is freeze-safe for this: when `sys.frozen` is set, static assets are
read from `sys._MEIPASS` (wiped on exit) while the working directory is the
folder containing the exe, so `academy.db` persists. Getting this backwards
silently destroys the database on every close. That used to be three things to
keep beside the binary and is now one.

**`.env` is deliberately not in that list.** The server used to provision its
own `ENTRY_SECRET` into a `.env` beside the exe when it found none, since a
double-clicked exe has no shell wrapper to export one. That quietly minted a
*second* signing key: `app_dir()` is the folder holding the executable, so a
packaged build never saw the project's `.env` at all, invented one, and every
card printed from the source tree stopped verifying against a build that
looked like it had worked perfectly.

So the values travel inside the binary instead. `academy.spec` reads the
project's own `.env` at build time, writes it into a generated `_baked_env`
module compiled into the bundle, and **refuses to build** when that file is
missing or its `ENTRY_SECRET` is empty — see Configuration below.

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
  roster sheet is one class. They carry a duration, a colour and a level.
  Splitting them this finely is what makes one card per class mean something
  — a Grade 6 card must not check someone into the Level 8 class two hours
  earlier.
- **sessions** are dated occurrences. Each carries its own `instructor_id`,
  because who teaches a given date changes often enough that a per-session
  field, not a class-level constant, is what has to be the source of truth.
  No capacity field. `ends_at` is **stored, not derived** —
  `starts_at + duration_hours*3600` wrapped the column in an expression, so
  neither `slot_conflict()` nor the absent sweep could use an index, and
  both run on nearly every request. `access.ends_at_of()` is its only
  writer; `create_session`, `edit_session`, `repeat_sessions` and `seed.py`
  are the four paths that must set it, and `db.migrate()` fills any row
  where it is NULL (targeted at the wrong rows rather than run once behind a
  marker, so it repairs a future mistake as well as migrating an old
  database). A NULL there is invisible to both callers — a wrong answer with
  nothing on screen to suggest it.
- **classes.instructor_id** is a *default*, not a substitute for the field
  above: what a new session for that class falls back to when none is named,
  and what every one of that class's upcoming (`status='scheduled'`,
  not yet started) sessions is overwritten to whenever it's set or changed —
  deliberately including a session someone had set to a different, specific
  instructor. `api/classes.py`'s `update_class` is the one place that
  cascade happens; `create_session`/`repeat_sessions` are the two places the
  fallback is read. Past and cancelled sessions are never touched by either.
- **clients** are identified by their **mobile number and their name
  together**, not by either alone -- see the rule below, and `identity.py`. They carry `age` as **REAL, not INTEGER** — the roster sheets hold
  "4.8" and "12.5" for the youngest children, and rounding a four-year-old up
  to five loses the distinction the class placement is made on. The whole
  path is float: `sheets.py` parses it with `number()` (deliberately without
  the `int()` that `months` and `sessions` get), `ClientIn.age` is
  `Optional[float]`, and the form's age input carries `step="any"`. Both of
  those last two matter — an `int` field *rejects* 3.5 outright rather than
  rounding it, and without `step` the browser refuses it before it is sent.
- **bookings** replaced `enrolments`, `attendance` and `session_roster` at once.
  Status is `booked` → `present` | `absent`. There is no third state: a slot is
  either used or it is not, and **both present and absent consume it**, because
  the place was reserved either way.
- **subscriptions** are plans. `sessions_used` is a column but is dead weight —
  nothing writes it and nothing reads it. Used slots are counted live from the
  bookings by `access.plan_state()` instead, so the two can never drift apart.
  `price` is what was paid, and it is **nullable on purpose**: the ballet sheet
  records "yes" rather than an amount, and a plan whose price nobody wrote down
  must not be reported as zero revenue. `payment_note` keeps the cell verbatim
  ("package", "free", "680") because "package" and a blank mean different
  things. `paid_on` answers *when* the money arrived, where those two answer
  what the sheet said about it — seeded from the roster's own PAID DATE
  column (68 of 70 rows have one), and **nullable on purpose**: NULL is
  read as unpaid and shown that way. It is set in the plan picker, edited
  from the plan's Edit button, and shows as a pill on the client profile,
  as the payment history's PAID column, and as a tag at reception. It is
  good for one session on trust and then blocks a check-in — see the
  unpaid rule below. The printed card
  deliberately omits it: that PNG is a snapshot nothing regenerates, so a
  card printed while unpaid would read UNPAID for the life of the card.
  `notes` is about *this purchase* — "paid half now, half in October" — and
  is deliberately a second, separate field from `clients.notes`, which is
  about the person. Both optional, both shown on the profile: the client's
  under the KPI row, the plan's on its own card. Editing a plan sends the
  notes as an ordinary field, so `""` clears it — unlike `paid_on`, which
  needs `?clear_paid_on=true` because the route drops None before
  `edit_plan()` ever sees it.
- **instructor_hours** is one row per instructor per working day, from the
  monthly salary sheet. Pay is `hours x hourly_rate` at read time, never
  stored, so correcting a rate re-prices the month instead of leaving a stale
  total behind. It is what payroll is actually paid on; "sessions taught" is
  the app's own count, and the instructor page shows both because a gap
  between them is worth seeing.
- **instructor_hour_adjustments** corrects **hours taught** — the timetable
  figure, not the salary sheet. An instructor who stayed for an extra
  rehearsal taught it whether or not a session row says so, and pay follows
  the corrected number (`totals.earned`). `access.taught_hours()` returns
  `scheduled` and `adjustment` separately as well as their sum, so the screen
  can show what was corrected rather than a total that silently disagrees
  with the sessions listed under it.

  **Only one of the two hour figures may carry the corrections**, or a single
  correction is counted twice; `access.logged_hours()` is therefore pure
  salary sheet now, and its card is read-only. (Adjustments used to be summed
  into it instead — any rows written before that change now move to hours
  taught.)

  **A correction belongs to one day.** `access.adjust_taught_hours()` takes a
  date, not a range: dated that way the deltas accumulate into a real daily
  history, and any wider range picks them up by summing, where a
  month-long correction would leave no trace of which day the extra hour
  was. That is why the Edit button appears only when the instructor page is
  showing a single day, and why the page defaults to today. It writes a new
  dated delta row rather than editing a session's duration or a salary-sheet
  row, so the timetable and the sheet still say what they always said.
  "Days worked" counts only real `instructor_hours` rows, since a correction
  is not a claim of an extra day worked.
- **credentials** carry a `class_id`. A client taking two classes holds two
  cards; scanning the Ballet card looks only for a Ballet session.
- **appointments** are enquiries, and are deliberately **not** clients and
  carry **no foreign key** onto them. Most are strangers who rang up, and
  the whole value of the list is the people who have *not* been written down
  as clients yet — so the row holds its own `name`, `phone` and `age`
  (`REAL`, for the same reason `clients.age` is), which is all reception has
  when the phone rings. If the person turns up and enrols, a client is
  created separately and this row stays as the record of the enquiry.

  **The day and the time of day are two columns, not one timestamp.**
  `on_date` is an ISO day like every other calendar day in this schema;
  `on_time` is `HH:MM` beside it, and **nullable on purpose** — "sometime
  Tuesday" is a real answer over the phone and midnight is not a truthful
  stand-in for it. Keeping them apart is what lets the list's date range
  need no end-of-day arithmetic (contrast the Sessions list, where the TO
  bound has to be pushed to 23:59 because the column is an epoch), and the
  filter is still on the day alone however precise the time against it. The
  two are joined for display only, by `lib/format.js`'s `fmtISODayTime`.

  **None of the client identity rules reach it**, and that is a decision
  rather than an omission: the mobile is optional and may repeat, because
  the same family rings twice about two children and the same person
  reschedules. There is no uniqueness rule here at all.

  **An enquiry is edited and deleted in place**, which is the one row in
  this app that is deleted outright rather than archived. The deletion
  policy below exists because losing who attended what is worse than a
  cluttered list — and an appointment has no attendance, no plan, no card
  and nothing pointing at it. A cancelled call kept for ever as a greyed
  row would make the list worse at the only job it has, which is showing
  who is expected. The edit offers every field, because an enquiry is a
  note taken over the phone and any part of it can be misheard; it is held
  to exactly the same refusals a create is (`_checked()` in
  api/appointments.py is the single copy of them), since refusing something
  at creation and allowing it a minute later leaves the state the refusal
  exists to prevent.

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

**A client is their mobile number *and* their name, together**, which makes
it two rules rather than one. `POST /api/clients` and `PUT
/api/clients/{id}` both refuse a client with **no** usable number (400,
`access.phone_required()`) and a **name-plus-number pair** another client
already holds (409, `access.duplicate_client()`) — in that order, because a
blank number has nothing to compare and checking the pair first would let it
straight through. Each is the single answer to its question, so the form and
the endpoint cannot drift: the same sentence the modal shows is the one the
endpoint returns, the way `can_freeze()` works.

**The pairing is the point: a shared mobile is not a duplicate.** A parent
enrols two children on one number, which at a children's ballet academy is
the ordinary case rather than the exception — so the number alone was the
wrong thing to make unique. (It was, briefly. The refusal now names what to
do about it: "a different person on the same number is fine — change the
name if this is a second client".)

What a duplicate still is: the same name on the same number, which is one
person entered twice. That is not an untidiness problem — their sessions,
plans and cards divide between the two records, so a card scans against a
balance that is half what they bought, and the missing half is invisible
because the other profile looks perfectly healthy. A client with no number
at all is the same failure one step earlier: nothing tells them apart from
the next client with no number, and `duplicate_client()` cannot help,
because two blanks are not duplicates of each other and never would be.

**Names are compared through `identity.name_key()`** — whitespace
collapsed, case folded, and deliberately nothing cleverer. "Dana Halim" and
"dana  halim" are one person typed twice; "Mohamed" and "Mohammed" are left
as two, because a rule that folded those would also fold two real cousins.
Reception can see both rows and decide.

**"Usable" is doing work in that first rule.** A required field that
accepts `n/a` is not required in any sense that matters: it is satisfied by
something carrying no identity, and several clients could hold the same
placeholder without any of them conflicting.
`identity.looks_like_a_number()` is the test and `identity.MIN_DIGITS` (8)
records where the line sits — low enough to admit any real number anywhere,
an Egyptian mobile being eleven digits and ten without its leading zero,
high enough to exclude a placeholder or a half-typed one. The field stays
`Optional[str]` on `ClientIn` **on purpose**: a required pydantic field
answers a missing key with a 422 whose `detail` is a list of dicts, and a
refusal here has to be a sentence a receptionist can read.

**The seed does not go through either rule**, and must not. `seed.py`
inserts clients directly, the roster sheets are the business record, and
refusing to import a student because nobody wrote her number down would
lose her. So a seeded database can legitimately hold a client with no
number — and editing that client from the profile is then the one moment
the missing number is actually askable, with them on the screen. The cost,
written where reception meets it: an unrelated edit to such a profile asks
for the number too.

**The number is compared as `identity.phone_key()` — the last ten digits —
and that is deliberately not what gets stored.** The academy's sheets hold one student
as `1129200365` (Excel ate the leading zero), `01129200365` and
`+201129200365`. No normalising reconciles the third with the other two,
because deleting a country code from what somebody wrote down is inventing
data; the last ten digits reconcile all three and leave the stored text
exactly as typed, which is what reception reads back and dials. This is the
same `identity.phone_key()` the seed merges the roster sheets on (see
"Clients are identified by phone" below) — though the seed stops there,
matching on the number alone, which is the divergence recorded under that
heading.

**Neither backend can express "the last ten digits of a column" as a
filter**, so `clients_by_phone_key()` is a port method and both
implementations compare in Python. It matches on the **phone only** and
returns every row sharing the number — several legitimately do now — and the
caller compares the names. That costs one query, charged only when a client
is created or their number edited, never on a page reception waits for.

**The lookup includes archived clients**, answering with "belongs to Karim
Nour, who is archived — restore them from the Archived list instead of
adding them again": a bare "already exists" would send reception looking
for somebody who is not on the list, and the only way out of that is a
second profile, which is the thing being prevented.

**Editing is covered as well as creating, and has to be, for both rules.**
Refusing a duplicate at creation and then allowing the number to be typed
over somebody else's a minute later leaves exactly the state the refusal
exists to prevent; demanding a number at creation and then allowing it to
be *cleared* a minute later gives back the client with no identity that
requiring it removed. `exclude_id` is what stops a client being a duplicate
of themselves.

The rule governs new writes only — **it does not retrofit.** Nothing sweeps
up a pair already in the database; merging two profiles means deciding which
plans, bookings and cards survive, which is a decision, not a migration.

**The seed still merges on the number alone, and the two rules now
disagree.** `seed.py`'s `_identity` has to: the roster sheets write one
student as "rodaina hesham" in one block and "rodina hesham" in another, and
matching on the name as well would split her back into two half-profiles,
which is the failure that key exists to prevent. The cost, now that a shared
mobile is legitimate, is the mirror image — **two real siblings on one
parent's number arrive from the sheets as one client.** That is
pre-existing rather than new, and neither answer is free. If the workbooks
ever start carrying siblings on one number, fix it there, with a reported
warning rather than a silent guess (see "Every guess is reported").

**A client stops being a student of a class one month after their last plan
for it ends.** Class membership is derived from bookings and bookings are
never deleted, so without a cutoff a client who stopped coming last year
stays on the class page for ever and the roster slowly stops describing who
actually attends. `access.lapsed_cutoff()` is that date — one *calendar*
month back, because "a month after it ran out" is what reception means and a
month is what the plans are sold in, with the day clamped so 31 March
answers 28 February rather than raising.

A plan's end is `access.plan_end()`: the **later** of its `expires_on` and
the last session it pays for. `refresh_expiry()` normally keeps those equal,
so this matters in exactly one case — an ENDS ON typed *earlier* than a
session the plan still funds. A plan cannot have finished before a session it
is paying for, so the session wins.

**It is a read filter and deletes nothing.** `class_students()` and
`classes_with_counts()` both take a `lapsed_before` date and leave it out of
the answer; every booking and every attendance mark stays exactly where it
is, so the client's own profile and payment history still show the class and
selling them another plan puts them straight back. Both port methods take
the same date and must: a Classes list saying 12 students beside a class
page showing 8 is worse than either number on its own.

The rule is "*every* plan of theirs in this class has lapsed", not "their
first one did" — a client who took the class two years ago, stopped, and came
back is a student. A booking with no plan behind it (older rows,
`subscription_id` NULL) falls back to its own session's date, the same
fallback `_decide()` makes. `tests/test_lapsed_students.py` holds the
boundary, both fallbacks, and that nothing is deleted.

**The date bound is passed in rather than read inside the port**, the same
shape `sessions_in_range(start, end)` and `settle_absences(now, …)` already
use. That is what keeps `LAPSED_GRACE_MONTHS` in `access.py` where the
business rules live, instead of a constant inside the data layer that
`repo/` would have to import `access` to reach.

**Every plan slot must be assigned to a real session before the plan saves.**
`POST /api/clients/{id}/plan` rejects a mismatch between `sessions_total` and
`session_ids`, and the picker keeps Save disabled until they match. A plan with
unassigned slots is a promise nobody has written down.

**Every screen that picks a session for a client offers the same window:
three weeks back plus everything ahead.** Reception writes a plan down, or
corrects an attendance, after the client has already been coming, so the
dates they actually attended have to be reachable. `lib/planSessions.js`
holds that window and **all six** places fetch through it — the four plan
pickers (`PlanPicker`, `EditPlan`, `AddSessionToPlan`, `AssignRemaining`)
via `fetchPlanSessions()` for one class, and the two cross-class corrections
(`EditAttendance`, `MoveBooking`) via `fetchAnyClassSessions()`. It also
drops cancelled sessions, which `/api/sessions` does not filter.

They used to disagree: three weeks in the plan pickers, one week in the
attendance correction, and no past at all in the move and in "add session to
plan". The same receptionist doing the same job on the same client got a
different list depending on which button she pressed, and the two that
started at today could not record "she came on Saturday instead" — the
commonest correction there is. All
three write paths (`add_plan`, `edit_plan`, `book`) already book a finished
session straight to `absent`, so the list marks past rows and says why, and
"auto-fill earliest" skips them: creating absences is a decision to make one
date at a time, never in bulk.

**Stating when a plan starts and how many sessions it buys is stating which
sessions those are.** `access.sessions_from_start()` answers that, and
`POST /api/plans/auto-sessions` is how the forms ask: changing STARTS ON or
NUMBER OF SESSIONS re-picks the ticks below and moves ENDS ON to the last
of them. Everything stays editable afterwards — this only saves the common
case being done twice by hand, and nothing is locked.

**The rule is one function on the server because three forms need it**, and
one of them — the kiosk's UPDATE PLAN panel — has no session list to work
it out from at all. Three hand-written copies would have agreed on "the
first four sessions from 1 October" and parted company on the edges, which
are the part that matters:

- **Already-attended sessions are kept whatever the start day says, and
  they count toward the total.** `edit_plan()` refuses any edit that drops
  one, so a rule that quietly excluded them would hand the form a set the
  server will not accept. `kept` comes back so a form can say "n sessions
  are already attended — the plan cannot go below that" instead of
  miscounting.
- **The plan's own upcoming slots are candidates again**, not obstacles.
  `not_booked_by` rightly hides dates the client already holds; without
  putting the plan's own back, changing 4 sessions to 5 would skip the four
  it already had and offer four *different* dates.
- **A day already gone is offered.** This is where it differs from
  "auto-fill earliest", which skips the past deliberately because creating
  absences in bulk is not a decision to take by accident. Here the start
  day was *typed*, and writing a plan down after the client started coming
  is exactly why the window reaches three weeks back. `past` counts what
  would *become* an absence — finished, and not already attendance — and
  the form says so before anything saves.
- **A short timetable is reported, not padded** (`short`), the same way a
  short timetable refuses a desk renewal.

**The forms narrow the answer to what they can actually show.** The list on
screen is a window and the rule is not, so a returned id with no row to
tick would be the "12 of 12 chosen" disagreement nobody can debug from the
screen. **And the request waits 400ms for a complete date**: a date input
fires on every edit, so "2" on the way to "2026" arrives as a real value —
the same problem the filter ranges solve with an Apply button, which a
re-pick meant to feel automatic cannot use.

**A typed ENDS ON narrows that window from the other end.** "Ends on the
20th" and "pays for a session on the 25th" cannot both be true, so once
reception states an end date the list stops offering dates past it —
`lib/planSessions.js`'s `withinEndDate()`, inclusive of the day itself
(a plan is valid *through* its last session, so a session on the end date is
the normal case).

**Only when the date was typed, never when it is the auto-filled one.**
`ENDS ON` follows the sessions picked until somebody overrides it, and
capping on the derived value would mean one pick removed every later session
from the list — after which the last pick could never be moved outwards
again. `endsTouched` is the switch, and the field's hint changes to say which
of the two is in force.

Two things it must not do. It **unticks** whatever it excludes rather than
leaving it counted-but-invisible, because "12 of 12 chosen" beside a list
that cannot show twelve is a disagreement nobody can debug from the screen.
And in `EditPlan` it **keeps every already-attended session** whatever the
date says (`withinEndDate`'s `keep` argument): those are attendance history,
the server refuses any edit that drops one, and hiding one would leave a row
counted in the total with nothing on screen to explain where it went.

**A session can only be booked against the plan that pays for its class.**
`access.book()` refuses when the client has no active plan in that session's
class, when the named plan belongs to another class, when it is frozen, or
when every one of its slots already has a date. The no-plan case used to
fall through and insert a booking with `subscription_id` NULL — a session
nobody paid for, in a class the client was never enrolled in.
**Booking someone in happens on their profile, never on the session.** The
session page took attendance *and* had an "Add student" button; that button,
its `AddStudents` modal and the `GET /api/sessions/{sid}/bookable` endpoint
that fed it are all gone. A booking spends a slot on one of that client's
plans, so the screen that creates one should be the screen showing what they
have left — "Add session" and "Assign them now" on the client profile. Two
ways in also meant two places to keep the class rule straight; now the
session page only marks people present or absent, and removes them. (The
endpoint existed solely to pre-answer `book()`'s four questions for that
picker, so it went with it rather than staying as an unreachable route.)

**Selling is class-locked; correcting afterwards is not.** The rule above
governs *buying* — `add_plan()`, `edit_plan()` and the plan pickers only ever
offer the plan's own class. But once a slot is sold, the three corrections on
the client profile — move an upcoming session, add a session to a plan, and
"they were actually present at this one" — may point that slot at **any**
class's session, by passing `allow_other_class` to `book()` or
`move_booking()`. The booking keeps the plan that paid for it; only the date
changes. This is what records "she missed Ballet on Tuesday and came to
Flexibility on Wednesday instead" without selling a second plan.

The card still works for it, because **the scan matches the booking's
*plan's* class, not the session's** — see `_decide()`, which joins
`subscriptions` for exactly this. A Ballet card finds the Ballet-funded slot
wherever it now sits; a Flexibility card still cannot spend Ballet credit, so
one card per class keeps meaning something. Bookings with no plan behind them
(older rows, `subscription_id` NULL) fall back to matching on the session's
class.

**The Sessions list shows the whole timetable, and must keep doing so.** It
is the one screen a session can be deleted from, so anything it cannot show
is a session nobody can remove. It used to fetch a hardcoded nine-week
window (`-21d`/`+42d`) while the calendar asked for whatever month you
turned to, and `/api/sessions` with no range quietly meant a *default*
window rather than no window at all. A session outside those nine weeks
therefore existed, showed in the calendar, and held its slot against
`slot_conflict()` — while the list gave no way to find or delete it, so
scheduling over it was refused by something invisible. Repeat weekly writes
twelve weeks at a time, so half of every batch landed out of reach the
moment it was created. `/api/sessions` with no `start`/`end` now means no
date bound, and the Sessions view asks for exactly that. Do not put a window
back on that screen without giving it a way to reach past the window.

It has a **FROM/TO date range** over the top of it instead, which is a filter
on what is already loaded rather than a narrower fetch — the whole timetable
still arrives, and "Show all" is one press away. `<DataTable>`'s search box
matches the row's own values and a session's date is an epoch integer in
there, so typing "12 Sep" found nothing: the one screen showing every session
was the one screen a session could not be found by date on. The text box
stays, for class, instructor and status. The range applies on a button and
not on change, and the TO bound is the end of its day — picking one date for
both would otherwise match nothing but midnight. See the `.filterbar` note
above.

**Deleting sessions in bulk keeps the same rule one-at-a-time deletion
has.** `POST /api/sessions/bulk-delete` refuses any session carrying
attendance unless `force`, and **names the ones it kept back rather than
failing the batch** — clearing a term with one taught week in the middle of
it should remove the other eleven and say why the twelfth stayed. Bookings
are deleted directly, so like `delete_session` it must refresh the expiry of
every plan that funded them.

**One session at a time, academy-wide.** A slot that is taken is taken,
whatever class wants it: `access.slot_conflict()` is the single answer to
"is this free?", and `create_session`, `edit_session`, `repeat_sessions` and
un-cancelling via `set_session_status` all ask it. Intervals are half-open,
so 15:01–16:01 and 16:01–17:00 are fine — back-to-back is how a timetable is
built, and only a real overlap is refused. A cancelled session occupies
nothing, so cancelling is how reception frees a slot up.

The rule applies to past datetimes too, deliberately: a slot that has been
and gone is still a slot. **`seed.py` does not call it.** The academy's own
roster sheets contain three real makeup classes that ran alongside another
class, and the sheets record what actually happened — dropping or moving one
to satisfy a scheduling rule would corrupt the business record. So a
re-seeded database legitimately holds three overlaps the UI would now refuse
to create. If that ever needs reconciling, fix the sheets, not the importer.

Repeat weekly **skips** a clash rather than failing the batch, and returns
`skipped` saying which dates and why — one taken evening in week 7 must not
cost the other eleven. It is the same treatment a date already holding that
class's own session has always had.

**An unpaid plan is good for one session, then it is not.**
`access.UNPAID_GRACE_SESSIONS` holds the number. A client who has genuinely
forgotten their wallet gets today's class and pays next time, because
turning a paying member away at the door over a payment reception could
take in a minute helps nobody. From the second session it is no longer a
forgotten wallet, and the refusal is what puts the payment in front of the
receptionist *while the client is standing there* — which is the only
moment it is easy to collect at all.

Three things about where the check sits, all of them decisions:

- **After a session of theirs has been found.** Somebody with nothing on
  today is told that, not chased for money on a day they were never due.
- **Before the `absent_today` branch**, whose MANUAL CHECK-IN spends a slot
  exactly as a scan does — letting that through would be letting them in
  unpaid by another door.
- **After the frozen check.** A frozen plan needs unfreezing, and being
  told to pay instead sends reception after the wrong thing.

It counts this plan's own used slots from the bookings already in hand, and
only when the booking belongs to the card's *live* plan: a slot left over
from an already-renewed plan is finished business, not something reception
would be collecting for. The refusal carries `code="unpaid_plan"`, which is
what puts **UPDATE PLAN** on the kiosk — see below.

**One check-in per day.** A second scan the same day is refused with the time of
the first, and nothing is deducted.

**Arriving early still checks you in.** The scan matches any of today's booked
sessions for that card's class, not just one starting imminently — making
reception wait for the exact start time helps nobody.

**Past sessions settle themselves.** `access.settle_past_sessions()` marks any
still-`booked` slot absent once its session has ended, and runs on startup,
hourly, and before every read that touches attendance. Nothing on screen is
stale.

**It skips itself when it provably has nothing to do, which is not the same
as throttling it.** What the sweep acts on is wall-clock time crossing a
session's `ends_at` — both halves, `booked`→`absent` and
`scheduled`→`completed` — or a date boundary, which is what
`lift_expired_freezes()` compares `frozen_until` against. So once it has run,
it cannot do anything again before the earlier of the next session end and
the next local midnight. `access._sweep_deadline` is that moment, from
`repo.next_sweep_deadline()`, and before it the sweep costs **zero round
trips** rather than four.

That distinction is the whole point and a throttle was declined for it: a
throttle trades the invariant above for speed, where skipping until a moment
nothing can have happened before loses nothing at all. It ran at the top of
nine read endpoints and was a fifth to a third of every page in the admin
against Atlas — about 420ms.

The one thing a deadline cannot see is a **write**: a session created in the
past, an edited start time, a new freeze. `server.py`'s `cache_policy`
middleware calls `access.sweep_invalidate()` on any non-GET request, which is
the single place in the app where "something may have changed" is knowable,
so a write endpoint added later is covered without anyone remembering to. A
rejected write invalidates too; that costs one extra sweep and nothing else,
which is the right way round for a guess to be wrong.

The cache is module state, so `tests/conftest.py`'s `repo` fixture clears it —
a deadline computed from the previous test's sessions would make this one's
sweep skip itself, and every attendance assertion would pass or fail on what
the test before it happened to contain.

**A plan is valid through the last session it pays for, kept current by
writing it, not by deriving it at read time.** `subscriptions.expires_on` is
the answer `plan_state()` returns, verbatim — no floor, no read-time raise.
What keeps it honest is `access.refresh_expiry()`, which rewrites it to
`access.last_session_date()` (the max `starts_at` among the plan's bookings),
called from **every** path that changes which sessions a plan's slots point
at: `book()`, `unbook()`, `move_booking()`, `edit_plan()`, and the bulk
booking-deletes in `delete_session`/`delete_class` that bypass `unbook()`.
Assigning a later session pushes the date out; removing one pulls it back —
both directions, automatically, with nothing edited by hand. A date typed by
reception (`PlanPicker`'s and `EditPlan`'s `ENDS ON` field both auto-fill from
the sessions picked, but stay overridable) is written as given and holds
right up until the plan's sessions change again, at which point it recomputes
— it is a courtesy override, not a permanent one.

**`freeze_plan()` is the one deliberate exception** and must never call
`refresh_expiry()`: it deletes future bookings on purpose, and
`unfreeze_plan()`'s existing day-shift needs the expiry to still be sitting
where it was, not collapsed back to an earlier remaining session, so it has
something real to shift from. If a future change adds another path that
touches a plan's bookings, it needs this same refresh — forgetting it is
exactly the kind of bug that only shows up as a card printing the wrong date
weeks later.

The printed member card shows whatever `plan_state()` returned at issue
time — it does **not** update itself if the plan's validity changes
afterward, grown or shrunk, edited or unfrozen; reissuing is what refreshes
it. The card's `SESSIONS` field is `sessions_total` (the total bought), not a
remaining count — remaining goes stale the moment they check in, and the PNG
is a print snapshot nothing regenerates on its own.

**A reissued card is served as `…png?v={issued_at}`.** `card_path()` returns
one stable filename per client per class — deliberately, so the download and
print links can be derived rather than looked up — which means reissuing
overwrites the same URL and the browser goes on showing the picture it
already cached. An edited end date was right in the file and wrong on the
screen. Both places that hand out a card URL (`get_client` and `issue_card`
in `api/clients.py`) stamp it with the issue time, which is exactly when the
image changes. It is the same class of bug the photo upload hit, fixed the
other way round: a photo has no derivable name to protect, so it gets a
fresh filename instead.

**Editing a plan** (`PUT /api/plans/{pid}`, `access.edit_plan()`) changes its
name, its session count, its sessions, and its end date after it has been
sold — the **Edit** button next to Freeze/Renew on the client profile.
Refused outright on a frozen plan (unfreeze first — editing underneath a
freeze would fight the exception above). Changing the session count reopens
the same session picker `PlanPicker` uses and requires the picker's full,
matching set of session ids — a count changed without saying which sessions
is refused, the same contract `add_plan()` uses. A session already marked
present or absent is attendance history and can never be dropped from a plan,
whatever the new count is.

**It can also move the plan to another class** — `class_id`, the searchable
class list at the top of the modal. That is a correction of "this was written
down against the wrong class", so it takes the plan's slots with it: the new
class's dates are picked in the same save (`session_ids` is required, the same
contract as changing the count), the old class's *upcoming* ones are dropped,
and the card for the class it left is revoked unless another live plan still
stands behind that class.

**What a move never touches is attendance.** A session already present or
absent stays on the plan exactly as it was, still on its own date in the
class it happened in — protected by the same rule that stops any edit
dropping one, so only what is still ahead of the client changes. A client
who attended four Grade 6 sessions before the plan was corrected keeps those
four. This works because a card proves the booking's *plan's* class, not the
session's (see `_decide()`), which is the same property the cross-class
corrections rest on: the new class's card finds those older slots too. The
modal keeps them listed, locked and ticked across the class change, and says
so.

The one refusal left is a client who already has a live plan in the class it
is moving to — one plan per class per client is what makes `active_plan()`
answer at all, so renew that one rather than ending up with two.

**Saving an edit reissues the card.** The card prints the end date and the
session count of the plan it was made for, and nothing regenerates it — so
every edit left an out-of-date card in the client's hand until someone
remembered to press Reissue. `EditPlan` now issues one itself, the same thing
`PlanPicker` already does on a renewal; the endpoint revokes the previous
card for that class as it goes, and a class change needs the new class's card
anyway.

## Pictures live in the database

Client photos, instructor photos and the printed member cards were files —
`photos/client_00060_1788711822.png`, `cards/client_00060_ballet.png` — with
the path written into a column. They are rows in `images` now, and `images.py`
owns all of it.

**Why.** They were the one part of the academy's record a backup of
`academy.db` did not contain, so the nightly backup this project still owes
itself would have restored an academy with no faces on it. On the hosted
backend it was worse: the data lived on Atlas and the pictures lived on
whichever laptop had done the uploading, which is not a deployment, it is two
half-deployments.

**Base64 text, not a BLOB.** The same rows have to live in MongoDB, and
base64 is the one encoding SQLite, BSON and the repository's plain dicts all
carry with no per-backend special case. It costs a third more space than raw
bytes, which for a few hundred photos is nothing next to having them in the
backup.

**What is stored is not what is served.** A row holds the bytes; the column
holds a short URL — `/api/images/client_photo/60?v=1789387980` — that
`api/images.py` exchanges for them. A `data:` URI in `photo_path` would have
been fewer moving parts and much worse: that column is returned in the
clients list, the dashboard's attention list and every kiosk scan, so a
megabyte of base64 would ride along with every row of every list that
mentions a person.

**The `?v=` is the same fix the card file needed, for the same reason.** The
URL is derived from *who* the picture belongs to, so it does not change when
the picture does and the browser goes on showing the one it already has — an
edited end date was right in the database and wrong on the screen. Stamping
it with the update time changes the URL exactly when the bytes change. There
is no second mechanism: a fresh filename per upload was the old answer and it
is gone, because a row has no filename to make fresh.

**A photo is shrunk to 900px on the way in** (`images.shrink()`, EXIF
orientation applied first — a phone stores the sensor's idea of up and a tag
saying how to turn it). The largest anything is displayed is the kiosk's
150px square, so this is already generous; and a raw phone upload genuinely
threatens MongoDB's 16MB document limit. Anything Pillow cannot decode comes
back untouched rather than failing the upload — the route has already checked
the extension.

**An install older than this keeps its pictures.** `images.import_legacy_files()`
runs once at startup, reads whatever is in `photos/` and `cards/` beside the
database, and rewrites the `photo_path` columns. It is idempotent by
construction — a file is read only when there is no row for that owner yet —
so it moves everything on the first start after the upgrade and costs a
directory listing on every start after that. The files are left where they
are; `cleanup.sh` is where deleting them belongs, once someone has seen the
photos still on screen. Shipping this change without that step would have
blanked the largest element on the kiosk, which is the one real control
against a screenshotted card being passed between friends.

**Two legacy filename shapes, not one.** The timestamp went into the name to
stop the browser serving a cached old picture, so anything uploaded before
that is a plain `client_00001.jpg`. The academy's own database has two of
each, and the importer's pattern accepts both (`client_00001[._]`). Where an
owner has one of each, the timestamped one wins -- `.` sorts before `_`, so
the newer scheme is always last.

**A credential can outlive its picture, and the profile says so.**
`get_client` hands back `card_url: null` when no image is stored, and the
client page shows "no image stored" beside **Reissue** instead of a Download
and a Print that 404. It happens to every card issued before this change on
an install whose `cards/` folder was not beside `academy.db` at first start,
and to any card carried across a backend move without its images.
Regenerating the PNG on demand would be worse, not better: the card is a
print snapshot of what `plan_state()` said at issue time, so a silently
redrawn one would carry today's figures under the old issue date. The
variants that *do* have a picture are fetched in one query for the whole
profile, not an `exists()` per card -- `tests/test_query_budget.py` is what
holds that.

**`<Avatar>` falls back to initials on a failed load, not only on no photo.**
`photo_path` is a URL this app answers and it can point at something gone --
a legacy `/photos/...` path on a database opened before its folder was read
in, a row lost in a move. A bare `<img>` on a 404 shows the browser's
broken-image glyph, which reads as a fault in the app rather than as a client
with no picture.

There is no `/photos` or `/cards` mount any more, and nothing writes to the
disk on an upload, a reissue or a seed.

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
two half-profiles. `identity.phone_key()` is that comparison.

**The admin no longer agrees with it**, and the difference is written down
under "A client is their mobile number *and* their name" above: a shared
mobile is legitimate there, so the seed merging on the number alone turns two
real siblings into one client. Neither rule can have it both ways — matching
the name here would split "rodaina"/"rodina" — so the divergence stands until
the sheets force the question.

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
a cluttered list.

**Appointments are the one exception, and it is not a loophole.** `DELETE
/api/appointments/{id}` removes an enquiry for good, because an enquiry has
no attendance, no plan, no card and nothing pointing at it — the rule above
is protecting a record that does not exist here, and a list of cancelled
calls is worse at the only job the list has. See the appointments note in
the data model.

Archiving is currently **one-way**: there is no restore endpoint and no
"Admin → Archive" screen. An `admin_routes.py` once existed with a restore
route and a manual-balance-adjustment endpoint, but it was never mounted into
`server.py` — dead code from the pre-authentication version of the app,
removed rather than wired in (see Known gaps). If either is wanted, build it
fresh against the current model rather than reviving that file.

**A plan can be deleted for good, and it is the one deletion that takes
attendance with it.** `DELETE /api/plans/{pid}` — the **Delete** button beside
Freeze/Renew — removes the plan and every booking it paid for, which drops the
client off the upcoming sessions it had them down for. It is the exception to
the rule above because a plan's bookings *are* its attendance: there is no
version of removing the plan that keeps the record. So the confirm dialog
counts what goes (upcoming sessions, attended ones) before it goes rather
than after, the endpoint returns the same counts, and the card for that class
is revoked unless another live plan holds the class up. Renewing or freezing
is what almost every case actually wants; this is for a plan entered by
mistake. It deliberately does **not** call `refresh_expiry()` — the plan whose
expiry would be recomputed is itself gone.

**Deleting the last live plan for a class also takes the client out of that
class**, which is what a receptionist means by deleting a plan.
`access.release_from_class()` is that step: membership is derived from
bookings, so a slot of theirs still sitting on a date next week left them on
the class page's student list and on next week's roster for a class they had
just been removed from. The leftover is normally an older, already-renewed
plan's booking, which is why those slots *are* given back — each goes to
whichever plan paid for it, as `unassigned`, with `refresh_expiry()` on that
plan (these bookings are deleted directly rather than through `unbook()`, the
same rule `delete_session` and `delete_class` follow). It runs only when no
other live plan of theirs stands behind the class — the same condition that
decides whether the card is revoked, and for the same reason: one plan per
class per client is what makes `active_plan()` answer at all.

**Only what is still ahead of them, and this is the line.** A booking already
marked present or absent is the record of a session that happened and belongs
to the plan that paid for it — visible in the payment history right below.
This deletion takes attendance, but it takes *this plan's*, because a plan's
bookings are its attendance; it does not reach into another plan's. So a
client with attended sessions in the class funded by an earlier plan still
appears on that class's student list afterwards, correctly: they did attend
it. If that ever needs to change, it is a decision about erasing history, not
a bug in this path.

**Archiving a class also releases its upcoming sessions.** A class that
stops being offered has nothing left to happen for, so `delete_class`'s soft
path deletes its `status='scheduled'`, not-yet-started sessions and their
bookings (not just flips `active=0` and leaves them dangling) — the clients
booked into them get the slot back as `unassigned` on their plan, same as any
other booking removal, via `access.refresh_expiry()`. Past sessions and their
attendance are never touched. This is the one archive path that cascades a
delete into another table on its own; client and instructor archiving do not
touch sessions this way.

**Instructors, classes and clients all have an archived list with a restore
path.** `GET /api/{resource}?status=archived` and
`POST /api/{resource}/{id}/unarchive` back an "Archived" view for each,
reachable from a plain button beside "New instructor" / "New class" / "New
client" — never the sidebar, which is the convention any future
archived-list screen should follow.

`/api/clients`'s `status` is the one that carries two jobs: `"archived"`
picks which half of the list to read, while `"attention"` filters *within*
the active half, after each row has been enriched with its plan state. They
are not three values of one switch, and the SQL only knows about the first.

What a restore does not bring back is per-resource. A class comes back with
its past sessions and attendance but not the upcoming sessions the archive
released — those were deleted for good. A client comes back with their
history but not their card: archiving revokes it, and credentials are
revoked rather than deleted (see below), so a restored client needs a new
one issued.

**Archiving a client is refused while they have upcoming sessions.** Not
released, refused — `delete_client` counts bookings whose session is in the
future and not cancelled, using the same predicate `get_client` builds the
profile's `upcoming` list from, so the number in the error is the number on
the screen the receptionist is looking at. This replaces the older behaviour
of silently deleting those bookings. Unused slots with no dates on them do
*not* block it; the confirm dialog just says how many are being given up,
since a lapsed plan holding slots nobody will book must not make a client
permanently un-archivable. The button raises the error without a round-trip
and the endpoint refuses independently — the same split `can_freeze` uses.

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
would have been charged twice. The guard is at the top of `access._decide()`.

**"Today" there means the session's day, never `checked_in_at`.** The two are
not the same: `set_status()` stamps `checked_in_at` with the instant someone
presses **Present**, so marking a client present this evening for yesterday's
class writes today's timestamp onto yesterday's booking. The guard used to
filter on that timestamp, and a client with nothing on today was turned away
with "already checked in today for Adult Ballet Monday" — naming a class that
ran the day before, and withholding the manual check-in that "no session
booked today" is supposed to offer. It now joins the session and asks whether
*that* falls in today, and prints the time only when the stamp is itself from
today (a booking marked present in advance carries an earlier day's).
`_client_payload()`'s `last_visit` reads the session's date for the same
reason — it sat one panel away from the recent-attendance chips, which have
always used the session's own date, and disagreed with them.

**A refusal has two temperatures.** `_deny()` carries a `severity`: `"stop"`
is the default and reads red, `"warn"` reads amber and is what the
second-scan-today case returns. Scanning twice is the ordinary thing a
client does when they are not sure the first one took, and answering it with
the same red STOP as a revoked card told the whole room she had done
something wrong. Nothing is deducted either way — only the temperature
differs. Reach for `"warn"` whenever a refusal means "nothing to do here"
rather than "something is wrong".

**verify() and check_in() are still separate calls, but the kiosk now presses
the button itself.** verify() stays read-only and returns an `event_id`;
check_in() is what spends the slot, and the 60-second `undo()` is unchanged.
What changed is who calls it: a scan that matched one of today's sessions
checks itself in, because the card already proved everything the button was
confirming. Undo, not a second confirmation, is the safety net. Any granted
scan with **no** matched session keeps a manual button — see the next point
for why that path is currently unreachable, and why the branch stays anyway.

**Session matching is a hard deny today, and the docs used to claim
otherwise.** The intent written down here was that verify() returns
`granted: true` with `no_session: true` and the kiosk turns amber with
"CHECK IN ANYWAY" — inform, don't block. That is **not** what the code does:
`access._decide()` returns "No session booked today" as a refusal, and
`no_session` appears nowhere in the codebase. Whichever way this is settled,
settle both sides together; the kiosk's manual-check-in branch is written
and commented for the softer behaviour so it can be restored without
touching the auto path.

**Session spend is guarded before the insert.** `access.book()` counts the
plan's existing bookings and refuses once that count reaches
`sessions_total`, then inserts the new booking. There is no atomic
single-statement spend here — `sessions_used` (see above) was that model's
column, and it is dead. The count-then-insert happens inside one connection
under SQLite's own locking, which is enough for this app's single-writer,
one-laptop reality; it is not a claim of correctness under real concurrent
writers.

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

**Four input sources**, chosen with a segmented control in the sidebar:

- **Scanner** — the default. Passive keystroke capture, so it keeps listening
  even while the camera is on; switching modes only changes what the screen
  shows.
- **Camera** — `BarcodeDetector` where available, jsQR from CDN otherwise.
- **Number** — the member number typed in, for an unplugged scanner or a card
  the camera will not focus on.
- **Name** — search the client list and pick one, for the client who left the
  card at home and does not know their number, which is most of them.

**The last two are ways of finding the client, not different rules.** Both end
in `lookupClient()` -> `POST /api/access/lookup` -> `access.verify_by_client()`,
which runs the same `_decide()` a scan does — so the same verdict screen, the
same automatic check-in, the same 60-second Undo, the same notes, and the same
MANUAL CHECK-IN swap when nothing of theirs is on today or they were already
swept absent. Nothing in `reception.html` re-implements any part of that
decision; a fifth method should be a fifth way of naming a client and no more.

Name search goes through the admin's own `/api/clients?q=` rather than an
endpoint of its own, so what reception finds at the kiosk is what they would
find on the Clients page — a second search with its own idea of a match
would eventually disagree with it. It waits for two characters, debounces
180ms, ignores an answer newer keystrokes have overtaken, and shows at most
eight rows: a two-letter search matches half the academy, and a list nobody
reads to the end of is not a shortlist. Enter takes the first row.

The browser cannot enumerate HID keyboards, so "scanner connected" is *inferred*
rather than detected: the indicator turns green the first time a burst of
scanner-speed keystrokes arrives. Until then it reads "listening for scanner"
and the idle text suggests switching to Camera. Do not claim detection the
browser cannot actually do.

The old dev panel (client dropdown, paste box) has been removed. Test without
hardware using Camera mode and a card PNG on a phone screen.



### One result at a time, cleared by hand

**Nothing on the kiosk dismisses itself.** A verdict stays up until someone
presses Esc. The old ten- and nine-second timers meant a queue of clients
could roll the screen past a receptionist who was still reading it, and
there was no way to get the last one back.

**While a result is up, the reader is deaf.** A second card, a second face
in the camera, a second member number — none of them replace what is
showing; `locked()` gates `scan()`, `lookupById()` and the camera's `pump()`
alike, because two people scanning in quick succession is exactly how one
client's verdict used to be swapped out before it was read. A scan that is
ignored **says so** — an amber line and a soft beep — since a scanner that
silently does nothing reads as broken hardware. The camera is the one that
stays quiet rather than nudging: a card sitting in front of the lens would
re-trigger every few seconds, so `pump()` skips without remembering, and the
card still in frame reads the instant Esc clears the screen.

**Esc is handled before every other guard** in the keydown listener, so it
works from the manual-entry box and from a focused button, not just from the
bare page. It was previously unreachable in Number mode — which the
clear-by-hand rule now depends on.

**Two refusals offer a way out instead of only saying no.** They carry
`code="no_session_today"` (nothing of theirs runs today) and
`code="absent_today"` (their session has been and gone and they were swept
absent, but they have turned up anyway). The code is the only thing the
kiosk branches on, and either one puts a **MANUAL CHECK-IN** button on the
screen; every other refusal gets none, because a revoked card or a frozen
plan is not fixed by moving a session. Pressing
it shows two lists: every session running today across all classes, and every
slot of the client's own that could be given up — a date still ahead, or one
they were already marked absent for, each tagged. Pick one from each and they
are checked in to today's session. The session they just missed appears in
the second list as a slot to spend, never in the first as somewhere to go.

`access.swap_and_check_in()` is deliberately built out of the ordinary
pieces: `move_booking(allow_other_class=True)` to change the date, then the
same `_log()`/`check_in()` pair a scan goes through. That is what makes the
60-second Undo work here exactly as it does for a scan, and keeps the day's
count honest. The slot keeps the plan that paid for it, so the card still
works afterwards. **Esc at any point checks nobody in** — the swap only
happens on the confirm button.

### Renewing at the desk

**A plan that has just run out can be replaced from the kiosk**, which is
the second way in to a renewal — the client profile keeps the first, with
its full session picker. This one exists for the moment it is actually
needed: somebody is standing at the counter, their plan is spent, and the
alternative is the receptionist leaving the kiosk for the admin screen with
a queue behind them.

**RENEW PLAN appears beside MANUAL CHECK-IN on one condition**, so it can
never show up for a reason nobody can name: they have a plan for a class,
it has **nothing left**, and it is not frozen (a frozen plan is unfrozen,
not replaced). A client with no plan at all has `sessions_remaining` of
`null` rather than `0` and is deliberately not offered one — selling a
*first* plan is still the profile's job.

That covers both arrivals, which are the same situation a beat apart:

- **nothing left on the way in** — the scan is refused, `sessions_remaining`
  is 0 on the refusal, and the button is there with the verdict;
- **one session left on the way in** — the scan checks them in as usual and
  spends it, and `doCheckIn()` re-tests the offer against the balance the
  check-in *returned*. `current.sessions_remaining` is updated there first,
  or the test would key off the pre-check-in figure and a client who walked
  in with exactly one session would be sent away without being offered the
  next plan.

**`access.renew_at_desk()` chooses the dates rather than offering them.**
Every slot must be assigned to a real session before a plan saves, and a
receptionist with a queue cannot tick twelve dates on a kiosk that has no
tables and no modals — so the terms come from the desk and the dates come
from the rule: the earliest sessions of that class the client could still
attend, which is what `PlanPicker`'s "auto-fill earliest" already means on
the admin side. Reception corrects any of them afterwards from the profile.
A short timetable is refused as the thing to do about it ("Only 2 Ballet
Level 8 sessions are scheduled — schedule more, or sell a shorter plan")
rather than as a rule that was broken, and nothing is sold.

**"Could still attend" means not finished, not "starts in the future."** The
session somebody is standing at the desk for has usually already started by
the time they scan. Filling only from sessions ahead would hand back a plan
that cannot let them into the class they came for — which is the commonest
renewal there is. Cancelled sessions and ones they already hold a slot in
are both dropped.

**It issues the card, and it checks nobody in.**

- **The card is replaced, both ways in.** The printed card carries the
  session count and the end date of the plan it was made for, so a renewal
  makes both of them wrong — the profile's `PlanPicker` has always issued a
  fresh one on a renewal, and the desk does too. `POST /api/access/renew`
  is where that happens rather than `access.renew_at_desk()`, because
  drawing a PNG is presentation; `cards.issue()` is the one copy of the
  three steps (credential, PNG, stamped URL) that both the profile's
  Issue/Reissue button and this route go through.

  **The cost is real and the kiosk says it out loud.** Issuing **revokes
  the previous credential**, and the credential being revoked is the card
  in the client's hand — between the sale and the printout, that card does
  not scan. The screen therefore reads "New card issued — print it from
  their profile. The old card no longer works", and the form says the same
  thing before SAVE. The trade is a wrong number on a card that works
  against a right one that has to be printed, and it is reception's to
  manage with the client in front of them. (This reverses an earlier
  decision that the desk should leave the card alone; if it is ever
  reversed back, the sentence under the form and the line under the verdict
  are the two places that have to move with it.)

  **A card that could not be drawn does not undo the sale.** The route
  reports the failure beside the verdict instead of raising: unselling a
  plan somebody has just paid for is not a press of a button, and a 500
  over a picture would leave the kiosk claiming nothing happened with the
  money already in the till.
- **It checks nobody in.** Selling a plan and spending one of its sessions
  are separate decisions, and a sale that consumed the first slot would be
  the app making the second. So the client who had nothing left scans again
  afterwards to use one; the client who had one session is already in for
  today and simply leaves with a fresh plan. The screen says which of those
  two it is, because that is the only difference in what reception does
  next.

**The refresh afterwards re-reads the client rather than patching the
panel**, so what is on screen comes from the same `verify()` a scan goes
through and cannot drift from what the next scan will say. It renders with
`show(r, {noAuto: true})` — the one caller of that flag — because the
automatic check-in a granted verdict normally triggers would spend a slot
of the plan just sold. Which is also why the button under it reads
**CHECK IN** there rather than CHECK IN ANYWAY: that wording is for a scan
that matched nothing and is being let in regardless, and this one has a
session and a slot to spend on it.

### Taking the payment at the desk

**UPDATE PLAN appears on exactly one refusal**, the unpaid one above, for
the same reason RENEW PLAN appears on exactly one condition: a button that
can show up for a reason nobody can name is worse than no button. A revoked
card or a frozen plan is not fixed by a payment, so neither gets it.

It opens the plan the scanned card just proved. Every field the profile's
Edit offers is there except two the kiosk cannot answer: **the class**,
which is the card's own and whose correction belongs on the profile, and
**the session ticks**, because the dates follow from STARTS ON and SESSIONS
through `access.sessions_from_start()` — the same rule the profile's forms
use. That is what keeps the kiosk pickerless, which is the whole reason the
renewal above chooses dates rather than offering them.

**Putting a date in PAID ON checks them in, and the kiosk does that by
asking to be scanned again.** `doPlanUpdate()` saves, then calls
`/api/access/lookup` and renders the answer through the ordinary `show()`
path — so the verdict, the automatic check-in, the deduction, the
60-second Undo and the day's count are the real ones rather than a second
implementation of them. `POST /api/access/plan-update` deliberately checks
nobody in for that reason; saving a payment and spending a session stay
separate, exactly as selling a plan and spending one do.

**By client id, not by the token just scanned.** A change to the session
count or the end date reissues the card, which revokes the token in their
hand — re-verifying with it would answer "revoked card" to somebody who has
just paid.

**Saving still unpaid is a real answer**, not a half-finished save: the
plan stays unpaid, nobody is checked in, and the screen says so rather than
pretending otherwise. The next scan refuses them again for the same reason.

**The card is left alone when only the money changed**, which is the common
case here. Issuing revokes the card in the client's hand and there is no
reason to do that to somebody who has just paid; the card prints the
session count and the end date, a payment changes neither, and `paid_on` is
deliberately not on the card at all. A change that *does* move those two
reissues it, and the kiosk says so.

**"Next class" is scoped to the card being held.** `_client_payload()` takes
the credential's `class_id` and filters the lookup by it. A client taking
Ballet and Flexibility was shown whichever came first across both, so the
Ballet card could answer with a Flexibility date — true, but not the
question asked. A member-number lookup names no class and still spans
everything.

### One card per class

`credentials.class_id` decides which *plan* a scan spends. A client holding a
Ballet card and a Flexibility card gets the right session either way, and
presenting the wrong card for today returns "No session booked today for
Flexibility" rather than silently checking them into the other class.

Note it is the plan's class, not the session's: a slot moved to another
class's session (see the correction rule above) is still found by the card of
the plan that paid for it.

The printed card **spells the month** (`11 Sep 2026`), converted by
`cards._card_date()` at the moment of drawing. Everywhere else — the database,
the API, every screen — the date stays ISO, which sorts and cannot be misread;
the card is the one place a date leaves the system on paper, to be read by a
person. Do not push the conversion any further back than the card, and do not
put a numbered month back on it: `11-09-2026` reads as September to half the
world and November to the other half, and the card is exactly the artefact
handed to someone who does not know which convention printed it. The month is
abbreviated rather than written out because the card sets it beside the
session count at the same size, and `11 September 2026` only fits by shrinking
until the two columns stop matching.

The member number and the two figures under the QR (session count, end date)
are deliberately set large: reception types the number in when the scanner
and camera are both unavailable, and the other two are what a client asks
about while standing at the desk.

**The session count is drawn as a number plus a smaller word**, not as one
auto-fitted `"12 SESSIONS"` string. Fitted, it was the only value on the card
that shrank — and it shrank by a different amount on every machine, since the
serif is DejaVu on a developer's Linux box and Georgia on the reception
laptop. The figure a client actually asks about came out visibly smaller than
the date beside it. Splitting them lets the number keep its size whatever the
font, with the word taking the strain instead. The footer's two columns are
unequal for the same reason: a count is three characters and a spelled date
is eleven, so the rule between them sits at 36% rather than halfway, which
leaves the date enough room to stay at its full size on a wider font instead
of shrinking back out of step.

**The card carries its own fonts** (`static/fonts/`, committed, bundled into
the packaged builds by `academy.spec`'s existing `datas` entry). Each role
used to fall back through a list of Linux, Windows and macOS paths, so the
same client's card came out in DejaVu on a developer's Linux box, Georgia on
Windows and Georgia or Menlo on a Mac — three different cards for one
academy, with every typographic decision here (what fits a column, what has
to shrink, how the two footer figures line up) silently retuned by whichever
machine printed it. The system paths are still listed underneath as a safety
net for a checkout missing the folder, not as a choice. DejaVu because the
design was drawn against it and its licence allows redistribution.

**One typeface does the lettering, one does the member number.** Everything
made of letters — the card's labels, the token, the footer line — is set in
the same serif as the client's name, so the card reads as one printed piece
rather than a form. The member number keeps its mono face and the accent
colour: it is the one value on the card that gets *typed in* (see above), and
a face where 0 and O cannot be confused is worth the break in the family. The
two figures under the QR are letter-spaced the way that number is — same
treatment, not the same font or colour — so the card's figures read as
belonging together.

**The logo must stay a transparent PNG.** `build_card()` composites it with
its own alpha so the card's paper shows through, and the kiosk's idle screen
shows the same file. `static/logo.png` shipped as opaque RGB, so what the
card actually printed was a white rectangle on off-white paper, edged top and
bottom by the thin black scan lines in the original file. The background was
knocked out on the asset by flood-filling the *colourless* pixels inward from
the border — every part of the artwork is saturated (the purple spreads 75
points across its channels), everything that is not is a pure neutral, and
the flood is what spares the white "MB Ballet Academy" lettering inside the
purple panel, which a blanket knockout would have punched through to the
paper. Replace the logo only with a PNG that already has alpha.

Cards are rows, not files — `build_card()` returns the PNG's bytes and
`images.store()` keeps them under the class slug, which is what lets two
cards coexist for one client. `cards.class_slug()` derives that slug and is
the only place that does: the client profile builds the download and print
links from it, and a second copy of the rule drifting from this one is a dead
link on the one screen that has to work.

### What a scan shows

`access._client_payload()` supplies the context, and it is deliberately
generous — the receptionist has a few seconds with a person in front of her,
and that is the only moment "expired last week" or "three absences" is worth
anything. Looking it up afterwards never happens.

The **photo is the largest element on the screen**. It is the real control
against a screenshotted card being passed between friends; a signature check
cannot tell you the person holding the phone is the member.

Denials carry the profile too, where the client is known — reception needs to
know *who* was refused and why, not just that something failed. `verify()`
looks the client up **before** the revoked-card check for exactly this
reason: every reissue leaves an older card in circulation, and answering one
with a blank panel headed "Unknown card" told reception the person in front
of them was a stranger rather than someone holding last month's card.

**Both kinds of note are on the screen** — the client's own (about the
person) and the one on the plan being spent (about that purchase), shown as
two labelled blocks rather than run together. Same reasoning as everything
else here: the note is worth something in the few seconds the client is
standing at the desk, and worth nothing at all afterwards, because nobody
looks one up.

## Hardware status

Not yet purchased. Requirement: 2D **imager** (never "laser" — a laser scanner
physically cannot read QR), USB HID keyboard mode, must read a phone screen at
~60% brightness at an angle.

- U-POS UP-888 Pro — preferred. Presentation style, large window.
- U-POS UP-868 — cheaper, narrower aperture.
- Honeywell MS7820 Solaris — **rejected: 1D laser, cannot read QR.**
- Upgrade path if screen reading disappoints: Zebra DS9308.

## Known gaps / next up

- [ ] No manual balance-adjustment endpoint. Archive-restore now exists for
      all three of instructors, classes and clients — see the Deletion
      policy section above. An `admin_routes.py` once had both a restore
      route and the balance adjustment, but it was never mounted into
      `server.py` — dead, unreachable code from the pre-authentication
      version of the app, deleted rather than wired in. Building the
      balance adjustment for real is separate feature work against the
      current model.
- [ ] Ballet prices. The ballet roster's PAID column only ever says "yes", so
      those plans import unpriced and the month's revenue figure counts
      flexibility alone. `month_intake()` still returns `mo_unpriced` — the
      count of plans with no price — but the dashboard no longer shows it,
      so that figure is now reported nowhere and the month's revenue reads
      as a clean total while half its plans carry no amount at all. Either
      the sheet starts recording the amount, or the fee goes on the class,
      or the count comes back onto the screen.
- [ ] The unpaid rule is one session of trust for everyone. There is no
      per-client exception and no way to extend it from a screen —
      `access.UNPAID_GRACE_SESSIONS` is a constant. If the academy ever
      wants "this family always pays at the end of the month", that is a
      field on the client, not a bigger number here.
- [ ] Rotating phone tokens: `access.py` has the `kind='phone'` path with a 90s
      freshness window, but nothing generates them client-side.
- [ ] `settle_past_sessions` runs in-process. If the laptop is off overnight it
      catches up on next start, which is fine — but there is no record of *when*
      a slot was marked absent versus when the session ran.
- [ ] No migration from the pre-bookings schema. Three tables were replaced at
      once, so an old `academy.db` must be re-seeded rather than upgraded.
- [ ] Nightly SQLite backup + a *tested* restore.
- [ ] The MongoDB backend has never run on the reception laptop, only
      against Atlas from a developer machine. `MB_DB_BACKEND` stays
      `sqlite` there until someone has a reason to change it and has
      thought about what happens when the internet drops.
- [ ] `migrate_to_mongo.py` has been run against a synthetic database, not
      against the academy's real one. Do that on a *copy*, and check a
      previously printed card still scans before trusting it.
- [ ] The Mongo half of the suite takes eight minutes or more against Atlas,
      almost all of it round-trip latency -- measured at a 1,344ms median
      from Alexandria, not the ~100ms this once assumed. It is opt-in
      (`MB_TEST_MONGO_URI`) for that reason, and note that a URI sitting in
      `.env` opts *every* `pytest` run in, since conftest reads `.env` the
      way the app does. `pytest -k sqlite` is the fast half.
      A local replica set would be faster if this starts getting run often.
- [ ] **The latency itself is the unfixed problem.** Cutting round trips got
      a reception scan from 2.1s to 1.1s, the client profile and the class
      page from ~1.5-1.75s to ~0.5s, the timetable from 1.0s to 0.25s and
      the dashboard from 2.2s to 1.4s -- but each trip still costs on the
      order of 100ms on this link and has been measured over a second on a
      bad one, so a screen needing a dozen of them is still slow in a way no
      further query change fixes. The two things that would actually fix it
      are moving reception back to `sqlite` (what this document already
      prescribes, and what keeps it working with no internet) or moving the
      cluster to a region near Alexandria. Neither is a query change. The
      one structural lever left is running a page's independent reads
      concurrently, which is deliberately not taken -- see the end of "The
      admin screens, same finding" under "Round trips are the unit of cost".
- [ ] Dated deadlines this repository is carrying, so they are in one
      place: **GitHub drops the x86_64 macOS runner in August 2027**, which
      just removes a row from `build-macos.yml`'s matrix (and ends Intel Mac
      support with it). Node 20 was removed from the runners on 23 September
      2026 and is already handled -- see the deprecation note in the Files
      section. `.github/dependabot.yml` plus the monthly canary job are what
      should surface the next one without it being on this list first.
- [ ] Auto-start on boot, and disable laptop sleep / lid-close suspend.
- [ ] Key rotation: single secret. Changing it kills every printed card at once.
      Needs an accepted-keys list with an overlap window.

## Transactions

**`isolation_level=None`, and every write names its own boundary with
`db.tx(conn)`.** sqlite3 used to manage transactions itself — one opened at
the first write and closed at whatever `commit()` came next — so a boundary
was wherever a commit happened to sit rather than where anyone had decided
it should be. There were 46 `conn.commit()` calls and no `rollback()`
anywhere: a failed multi-statement write was undone only because closing a
connection discards an open transaction. That worked, but by accident.

```python
with db.tx(conn):
    conn.execute(...)
    conn.execute(...)
```

**It is re-entrant**, because the calls genuinely nest —
`swap_and_check_in()` calls `move_booking()` and `check_in()`, and
`settle_past_sessions()` calls `lift_expired_freezes()`, which calls
`unfreeze_plan()` once per due row. The outermost block owns the
transaction; inner ones are no-ops. Nesting is detected through sqlite3's
own `in_transaction`, since a `Connection` cannot carry attributes.

**There are no savepoints, deliberately.** An inner block that raises rolls
the whole outermost transaction back. Nothing here half-succeeds on purpose.

**`BEGIN IMMEDIATE`, not `BEGIN`.** It takes the write lock at the top of
the block rather than at the first write, which is what serialises a
read-then-write: `book()` counts a plan's bookings before inserting one, and
`create_session`, `edit_session` and un-cancelling all check the slot is
free before writing into it.

A single statement outside a block autocommits, which is what a lone read or
a one-row update wants anyway. **But a bulk insert outside one is a separate
fsync per row** — that is why `seed.py`'s phases and the test fixture are
each wrapped.

`access.refresh_expiry()` still does not commit. That used to be an
invariant held by a comment; it is now structural, since it is always called
inside a caller's block.

## Configuration

`config.py` owns both "where do the files live" and "which database".
**Nothing in it is a module constant** — every value is a function, read
when asked for. `db.py` used to bind `DB_PATH` at import time and
`server.py` read `.env` in its FastAPI *startup event*, which runs long
after `import db`; harmless while the value was a literal, and fatal once an
environment variable decides which implementation gets constructed.

`import config; config.load_env()` is the **first statement** of `server.py`
and `run_app.py`, above `import db`. It also does the `chdir`.

| | default | |
|---|---|---|
| `MB_DB_BACKEND` | `sqlite` | `sqlite` or `mongo` |
| `MB_SQLITE_PATH` | `academy.db` | relative to the app folder |
| `MB_MONGO_URI` | — | required when the backend is mongo |
| `MB_MONGO_DB` | `mb_ballet` | |

All `MB_`-prefixed, because `load_env()` sets any `KEY=VALUE` it finds and
must not collide with something already on the machine. Real environment
variables beat the file. See `.env.example`.

**`mongo` with no URI raises at startup — it never falls back to SQLite.** A
silent fallback means reception writing a day of attendance into a local
file nobody looks at again.

Two bugs this replaced, both of which would have bitten the moment a second
key lived in `.env`: the file was only read when `ENTRY_SECRET` was unset
(so on a machine where the secret is exported in the shell — which is what
this document tells you to do — every other setting was ignored), and
provisioning a generated secret opened it with mode `"w"`, truncating it.

### There is one `.env`, in the source folder

A packaged build does not read one and cannot write one. `academy.spec`
parses this folder's `.env` at build time and writes the key/values into a
generated `build/baked/_baked_env.py`, which is compiled into the bundle;
`config._baked()` is its only reader, and `load_env()` applies it —
`setdefault`, so a real environment variable still wins — **instead of**
opening a file when `sys.frozen` is set.

Three refusals hold that shape in place, all of them structural rather than
remembered:

| where | what it refuses |
|---|---|
| `academy.spec` | a build with no `.env`, or an empty `ENTRY_SECRET` |
| `config.set_env_value()` | any write to `.env` from a frozen process |
| `server.py`'s startup | provisioning a secret when `sys.frozen` is set |

**Why it had to stop being a file beside the exe.** `app_dir()` is the folder
holding the executable, so a build looked for `.env` in `dist/`, found none,
and `server.py` generated a fresh random `ENTRY_SECRET` into a second file
nobody knew existed. That build then signed cards with a key the source tree
had never seen, and rejected every card the source tree had already printed —
with nothing on screen to suggest it, because provisioning is what a healthy
first run does too.

The consequences, both deliberate: the `.env` parser now exists twice (in
`config.load_env()` and `academy.spec::_read_env`, since the spec runs before
anything of the app is importable — **keep the two in step**), and every
setting in this folder's `.env` is embedded in any binary built from it, the
Mongo URI included. A generated module rather than a `datas` entry because
`datas` unpacks to `sys._MEIPASS`, a real directory on disk while the app
runs.

`.github/workflows/build-macos.yml` builds from a checkout, which has no
`.env`, so it writes one from an `ENTRY_SECRET` **repository secret** before
packaging and fails loudly when that is unset. It must match the local one,
or a CI binary rejects every existing card.

`START.bat` and `start.sh` still generate a `.env` when there is none — in
the repo root, which is the one this section is about.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/` replaced three root-level scripts that ran against the live
`academy.db` **and wrote to it**. They also looked their subjects up in it —
"a client holding two cards", "a plan of 12+ sessions not already frozen" —
which made them unrunnable without the academy's own workbooks and meant a
test could pass or fail depending on which term had last been seeded.

`tests/fixtures.py::build_academy()` constructs those shapes on purpose
instead, into a throwaway database per test. It writes with plain SQL rather
than through `access.py`: a fixture built out of the functions under test
cannot fail independently of them.

The `conn` fixture points **config** at the temp file rather than passing a
path around. That is what makes the `api/` layer testable at all — route
handlers call a bare `db.connect()`, so before `config.py` any test touching
one reached for the real business record.

Two things the fixture guards, because both have already bitten:
- neither recurring session series may land on today, or a scan matches
  whichever session is nearer the clock and the suite passes or fails by the
  hour it runs at;
- `sessions.ends_at` must never disagree with the columns it is derived
  from.

`@pytest.mark.sqlite_only` marks the tests that introspect `PRAGMA
table_info` and `sqlite_master`. They describe the backend rather than the
app and do not port to a document store.

## The repository

**The backend is a configuration choice.** `MB_DB_BACKEND=sqlite` (the
default, and what the reception laptop runs) or `mongo`. Nothing outside
`repo/sqlite/` contains SQL -- `access.py`, every router, `seed.py` and the
tests all speak the interface.

Two tiers, with a boundary that is structural rather than a matter of
discipline:

**Tier one, twelve primitives** (`repo/base.py`): `get`, `find`, `find_one`,
`count`, `exists`, `distinct`, `insert`, `insert_many`, `insert_ignore`,
`update`, `update_where`, `delete`, `delete_where`. Single-collection only.
The signatures make a join *impossible to express*, so "anything harder
belongs in a named method" does not depend on anyone remembering it. About
half the app's queries are this shape and need no method of their own.

**Tier two, named business questions** (`repo/ports.py`): everything that
joins, aggregates, has a computed predicate, or is a compare-and-swap, named
for the question the app actually asks. `repo/sqlite/ports.py` answers them
with joins; `repo/mongo/ports.py` answers the same questions with a few
`$in` fetches joined in Python. **The contract is the dict that comes back,
not the shape of the query** -- `tests/test_parity.py` is what enforces that.

> **The governing rule: the interface is written in the weaker backend's
> vocabulary, and SQLite implements downward into it.** MongoDB cannot do
> `ORDER BY ABS(x - ?)` or a five-table join without becoming unreadable;
> SQLite can do everything MongoDB can. Nothing in the interface quotes SQL.

The filter dialect is deliberately Mongo-shaped: `{"f": v}`,
`{"f": {"lt": x}}`, a top-level `"$or"`. No nesting beyond that, no
field-to-field comparison, no computed values. A question needing one of
those is a port method. That limit is the point -- it is what stops a caller
building a query the other backend cannot answer.

### Three things that are not negotiable

**Integer ids, on both backends.** `tokens.py` packs `client_id` as a
**uint32** into every card already in a client's hands; the client id *is*
the printed member number reception types in when the scanner is down; and
it is in filenames on disk. MongoDB `_id` therefore holds the integer
itself, minted from a `_counters` collection with `$inc`. SQLite's
`AUTOINCREMENT` and that counter agree **only because neither reuses a
number** -- drop `AUTOINCREMENT` and a hard-deleted client's id is reissued
to the next person, and a printed card in a drawer belongs to somebody else.
`tests/test_sqlite_schema.py` asserts the keyword is still there.

**Dates stay exactly as they are.** Epoch integers for moments, ISO
`YYYY-MM-DD` strings for calendar days -- in the documents, on the wire,
everywhere. Not BSON `Date`: this app has no timezone concept (`date.today()`,
`datetime.combine(d, time.min).timestamp()`), and BSON dates are UTC-anchored,
so a freeze entered at 23:30 Cairo would land on the wrong day.

**Null and missing are the same thing in SQLite and different in MongoDB**,
and the differences are silent:

```
{"f": None}                   matches explicit-null AND missing
{"f": {"$ne": None}}          matches neither
{"f": {"$lt": "2026-01-01"}}  MATCHES null      <- no SQL counterpart
```

That last one sits one line from `lift_expired_freezes()`'s
`frozen_until <= today`: on MongoDB it would unfreeze plans that were never
frozen, on one backend only, invisibly. Two things guard it, both structural
rather than remembered:

- `repo/mongo/schema.py` declares every field, and **every insert writes all
  of them, even when null** -- so "missing" is a state that cannot occur. It
  is also the MongoDB counterpart of `db.migrate()`'s `ALTER TABLE ... ADD
  COLUMN`: SQLite backfills a new column at write time, this backfills at
  read time.
- `repo/mongo/filters.py` adds `$ne: None` to any range comparison on a
  nullable field, automatically.

Aggregates always return `0`, never `None`: SQL's `SUM` over no rows is NULL
and MongoDB's is no group at all.

Every sort ends with the primary key as a tiebreak, because SQLite falls back
to rowid order and MongoDB to natural order, and two backends returning equal
rows in different orders would make the parity tests compare lists that were
never promised to match.

### Round trips are the unit of cost

On a local SQLite file an N+1 is invisible. Against Atlas each round trip is
about **100ms measured from a good connection -- but measure it, do not
assume it.** From Alexandria to the eu-central-1 (Frankfurt) cluster the
median was **1,344ms**, with a 16-second connect and a 20-second worst case;
ICMP to the same host averaged 392ms with a 525ms standard deviation. At that
figure a page costing twenty round trips is half a minute, and the only
number that helps is the count.

`access.plan_states()` answers for many plans in **one** query via
`repo.plan_rows()` -- the subscription, its class and its booking counts
together -- and `plan_state()` is a one-element call into it so the two
cannot drift. It was three, which is three round trips even for the single
plan the reception scan path asks about with a client standing at the desk.
`plan_counts_bulk`, `attendance_counts`, `card_counts_bulk`,
`taught_totals_bulk`, `active_plans_for` and `last_session_ts_bulk` exist for
the same reason; `access.refresh_expiries()` is the bulk counterpart of
`refresh_expiry()`, for the paths that delete bookings for many plans at once.
**`tests/test_query_budget.py` asserts that the dashboard, the clients list,
the classes list, a class page, a client profile, an instructor profile, a
reception scan, repeating a term and a bulk delete do not grow a query per
row** -- it is the only thing that stops this regressing. It also holds the
sweep's deadline: that a second `settle_past_sessions()` costs nothing, and
that an invalidation makes it run again. Its `Counted.excluding()` exists because SQLite's `insert_many`
is documented as N separate INSERTs while MongoDB's is genuinely one round
trip; counting the decomposed inserts would make a batched write look
unbatched on SQLite alone.

`insert_many()` is not a convenience either: it allocates a block of ids with
a single increment, so selling a plan (twelve bookings) or repeating a term
(up to ninety-six sessions) costs one round trip rather than one each.

**The scan path is the one with a person waiting for it**, so it is measured
in whole seconds rather than in round trips. A member-number lookup against
the Frankfurt cluster took **2.1 seconds**; it is **1.1** now, and nothing
about what it decides changed. Four things were wrong with it, and three of
them are the same mistake:

- It asked four port methods -- `client_day_bookings`, `recent_attendance`,
  `next_booked_session` and `client_totals` -- and every one of them re-read
  the same client's bookings and re-joined the same sessions behind them.
  Thirteen round trips to answer four questions about rows already in hand.
  `repo.client_bookings()` is the one read now and `access.py` decides all
  four from the list in Python; on MongoDB it is a single `$lookup`
  pipeline, written the way `plan_rows()` is.
- `active_plan()` was called twice for one scan -- once to build the
  payload, once in `_decide()` to check the plan was not frozen, with the
  same client and the same class both times. `_decide()` is handed the plan
  its caller already has.
- `settle_past_sessions()` read the frozen plans, then `lift_expired_freezes()`
  read them again, narrowed. One read, passed in.
- `_log()` wrapped its single insert in a transaction, and on a document
  store a commit is its own round trip. A lone statement autocommits, which
  is what this section already says it wants; inside `swap_and_check_in()`'s
  block it still joins that block, because `begin()` is re-entrant.

MongoDB's `settle_absences()` was also reading the id of **every finished
session in the academy** and sending the list back as an `$in`, on a query
that runs before every read that touches attendance -- a payload growing
with the whole history, on the path a client waits through. It is driven
from the still-`booked` bookings now, which is what is left to settle plus
what is yet to happen, and never what has already been settled once.

What was left after that was `settle_past_sessions()` itself, about a third
of the remaining time, and it is now free on all but the first read after a
write -- **not** by throttling it, which was declined, but by skipping it
until the moment it could possibly have work. See "Past sessions settle
themselves" above for the deadline and where it is invalidated.

### The admin screens, same finding

The scan path was the first place this was measured and not the only place it
was true. Every admin screen paid the same two costs: the sweep at the top of
nine read endpoints, and `repo/mongo/ports.py` fetching one `$in` per table
it joins to. Measured against the Frankfurt cluster, route functions called
directly:

| screen | was | now |
|---|---|---|
| `/api/dashboard` | 2194ms / 20 trips | 1399ms / 12 |
| `/api/classes/{id}` | 1751ms / 16 | 508ms / 7 |
| `/api/clients/{id}` | 1490ms / 18 | 529ms / 7 |
| `/api/clients` | 1189ms / 8 | 809ms / 4 |
| `/api/sessions` | 1010ms / 8 | 265ms / 1 |
| `/api/classes` | 920ms / 6 | 837ms / 6 |
| `/api/instructors/{id}` | 906ms / 11 | 526ms / 7 |
| `/api/instructors` | 582ms / 6 | 172ms / 2 |
| `/api/sessions/{id}` | 743ms / 10 | 328ms / 6 |

A reception scan is 1.2s when it follows a write (the check-in before it
invalidated the sweep) and 0.58s when it does not, from the 1.1s it was.

**Pagination was considered and is the wrong tool.** The academy holds 254
clients, 402 sessions and 1,144 bookings; a page that fetches 402 rows in one
trip is already optimal, and one that fetches 40 in eight trips is not. The
unit of cost is the round trip, which is what this whole section is about.

What changed, beyond the sweep:

- **The client profile read the same client's bookings twice.**
  `client_upcoming` and `client_history` were separate port methods, each a
  fetch of the bookings plus an `$in` per table behind them — eight round
  trips to ask twice about one list. `api/clients.py`'s `get_client` reads
  `repo.client_bookings()` once and derives both, the same split `access.py`
  makes for the scan payload. `client_history` had no other caller and is
  gone; `client_upcoming` stays for `access.delete_client()`.
- **`_decorate_sessions` became `_sessions_decorated`**, a pipeline rather
  than three `$in`s on top of a fetch. It is charged to the timetable, the
  class page and the calendar, and `/api/sessions` is one round trip because
  of it. `not_booked_by` reuses the bookings that pipeline already joins.
- **`_plan_ends` has two ways in on purpose.** `classes_with_counts()` holds
  every session and booking by the time it asks, so the last session per plan
  is arithmetic; `class_students()` holds only one class's, so it gets a
  nested `$lookup` **bounded to the plans that fund that class**. Unbounded
  it walks every plan in the academy, which measured 295ms — most of three
  round trips to answer a question about one class.
- **`recent_events`, `active_plans_with_clients`, `day_attendance_totals`
  and `client_cards`** are each one round trip now instead of two to four.
- **`event_totals` and `joined_counts`** replace pairs of counts over one
  collection — two of the dashboard's figures came from reading the same
  window twice, which is a round trip spent on arithmetic.
- **`_projected()`** is for the whole-collection reads. `classes_with_counts`
  carries every session and every booking to work out who still studies what,
  and at ~220ms a collection most of that was columns no loop there reads. It
  deliberately does *not* go through `schema.normalise()`: that fills in every
  declared field with its default, so a projected document would hand back a
  plausible-looking zero for a field that was never fetched. A field nobody
  asked for is absent, and reading it raises.

`/api/classes` is the one that barely moved and is now the slowest list: it
reads two whole collections to derive class membership from bookings, and
collapsing that into an aggregation needs the nested plan-end walk over every
plan — the 295ms shape above. Left alone on purpose.

**The next lever is concurrency, and it has not been taken.** The reads on a
page are mostly independent, so a thread-pool fan-out would roughly halve
these again. It would also add concurrency to a single-threaded
business-record app, `sqlite3` connections are not thread-safe, and it buys
nothing on SQLite — which is what reception runs. Decide it on purpose.

**Two write paths were the worst offenders and are now batched.**
`access.repeat_sessions()` checks a whole term against the duplicate and
overlap rules in three round trips instead of three per date -- see its
docstring for the three things the per-date loop got for free and it has to
do deliberately, the subtlest being that **sessions created in one batch must
conflict with each other** (each insert used to be visible to the next
`slot_conflict()`). `access.delete_sessions()` is a fixed number of round
trips rather than seven-plus per session, and still refuses a session
carrying attendance by naming it in `blocked` rather than failing the batch.

### MongoDB deployment

**Transactions need a replica set.** Atlas is one; a standalone `mongod` is
not, and `start_transaction` against one fails. The commit is retried only on
`UnknownTransactionCommitResult`, where the outcome is genuinely unknown and
the retry is idempotent. A `TransientTransactionError` is deliberately *not*
retried -- this is a single-user app, so the real causes are network blips,
and silently re-running a POST is worse than telling reception to press the
button again.

**A packaged build carries its own CA bundle, and must.** The Mac binary
died at startup against Atlas with

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
unable to get local issuer certificate (_ssl.c:1006)
```

PyInstaller bundles its own Python, and macOS keeps its root certificates in
the Keychain rather than at the OpenSSL paths `ssl` falls back to — which is
why a normal macOS Python install ships an "Install Certificates.command".
There is no such step for a binary someone double-clicks, so the build works
on the machine that made it and fails on every other Mac. **pymongo does not
depend on certifi** (only dnspython), so nothing pulled a bundle in by
accident. `repo/mongo/client.py`'s `ca_file()` passes `certifi.where()` as
`tlsCAFile`, `certifi` is in `requirements.txt` *and* in `academy.spec`'s
hiddenimports (the second is what makes PyInstaller's hook collect
`cacert.pem`), and a URI that names its own bundle still wins — a keyword
argument would otherwise override a deliberate choice.
`tests/test_mongo_tls.py` holds all of that, including that both files still
declare it.

**A failed first connection says what to do, not what happened.** The same
failure arrives from pymongo as several hundred characters of
`ServerDescription` objects, one per replica-set member, each repeating the
same underlying error — and startup is exactly where a dump is worst, because
the window closes and `error.log` is all anyone gets.
`server.py`'s `_connect_or_explain()` names the three real causes separately,
because they need different actions from different people: a certificate that
cannot be checked is a **fault in the build**; a name that cannot be looked up
is the SRV filtering below; and no answer at all is either no internet or an
IP missing from **Atlas → Network Access**, which is per-network, so a machine
that works in one place stops working in another.

**`mongodb+srv://` needs a DNS SRV lookup that some networks filter.** This
has already bitten: it fails with a DNS timeout that looks nothing like a
configuration problem. The direct form names the hosts instead and is
documented in `.env.example`:

```
mongodb://h1:27017,h2:27017,h3:27017/?replicaSet=...&tls=true&authSource=admin
```

**`seed.py` writes SQLite, always — `MB_DB_BACKEND` does not apply to it.**
It is an importer: it reads the academy's workbooks and rebuilds the database
from them, and its first act is `drop_all()`. Asked through `repo.connect()`
that would be a wipe of the live hosted record with nothing to undo it from,
triggered by an environment variable set for a different reason. So it builds
its own `SqliteRepo` over `config.sqlite_path()` (`seed.py`'s `sqlite_repo()`),
and `migrate_to_mongo.py` is the one thing that writes to Mongo — reading
exactly what the seed produced. The two steps are `python seed.py --force`
then `python migrate_to_mongo.py`.

It calls `config.load_env()` as its first statement, above `import db`, like
every other entry point. `start.sh` deliberately does not export `.env` into
the shell — a Mongo URI's `&` does not survive sourcing — and relies on each
process reading the file itself. This was the one that did not, so
`./start.sh --seed` arrived with no `ENTRY_SECRET` and stopped with
"ENTRY_SECRET is not set" against a `.env` that had one in it: the seed
silently did nothing.

**`migrate_to_mongo.py` brings the source's schema up to date before reading
it.** `db.init()` is additive and idempotent -- it is what the app runs on
every start -- and without it the tool cannot read a database older than its
own `ORDER` list. The academy's own file predates the `images` table, so
counting the rows to move died on `no such table: images` before anything had
moved. This was once written down as "start the app once first", which made a
manual step load-bearing; an instruction like that gets skipped exactly when
it matters.

**It also reads the pictures in itself, first, for the same reason.** Rows are
the only thing that travels, so a `photo_path` of `/photos/client_00001.jpg`
would arrive on Atlas as a path to a file on a laptop nobody will ever query
it from. `images.pending_legacy_files()` counts the files, the import runs
before the rows are counted (or the `images` total is the one from before it),
and `--dry-run` reports what it would take in without touching the disk. So
the whole thing is one command:

```bash
python migrate_to_mongo.py --dry-run          # counts, writes nothing to Mongo
MB_DB_BACKEND=mongo python migrate_to_mongo.py
```

**It copies by id, replacing, and never deletes.** `insert_many()` would mint
fresh ids from the counters and the ids are the whole point, so the documents
go in with the ones they have -- but a plain insert then fails on the second
run with a duplicate key, half way through, leaving a database that is neither
the old one nor the new one. A migration is something people run more than
once while they get it right, so `copy()` uses `ReplaceOne(upsert=True)` per
id and converges: a re-run ends with MongoDB holding exactly what SQLite
holds. A document under an id the source does not have is **left alone and
counted** -- this tool is not the authority on what else is in that database.
`--force` is therefore "overwrite what shares an id with this source", not
"add to it", and the refusal without it stands so nobody overwrites a database
by accident.

**`settings` is deliberately not in `ORDER`.** It is SQLite bookkeeping -- one
row, `expiry_backfilled`, recording that `db.migrate()`'s one-shot repair has
run -- and `db.migrate()` never runs against MongoDB, so the marker would mean
nothing there. Both backends' `is_empty()` already exclude it for that reason;
having it in the list contradicted them. It is also the one table keyed by
`key` rather than `id`, so `find()` appending its `id` tiebreak made the very
first collection copied fail with `no such column: id` -- on a real run only,
since `--dry-run` returns before `copy()` is ever reached.
`tests/test_migrate_copy.py` drives `copy()` and `main()` against a recording
stand-in for Atlas, which is what now covers that whole path without a server.

**What is left over is a warning, not a refusal**, and the difference matters.
After the import, what can remain is a picture that exists *nowhere*: a photo
never taken, a card whose PNG was deleted or never drawn. No amount of
importing produces those -- only reissuing the card or uploading the photo
does -- so refusing would be a refusal with no way to clear it, which is worse
than a faceless profile. It says which of the two, how many, and what fixes
one; a card with no picture still scans, because the token is in the database
and only the printed image is missing.

**`config.legacy_media_dir()` is the folder holding the SQLite file, and is
deliberately not a function of `MB_DB_BACKEND`.** Those folders only ever sat
beside `academy.db` -- they predate there being a second backend -- and the
migration reads SQLite whatever the backend says. Keying it on the backend was
wrong in the one place it mattered: with `MB_DB_BACKEND=mongo` it answered
`app_dir()` while the pictures sat beside the source file, so the migration
found nothing to read in and carried the paths across instead of the pictures.
Silently. In an ordinary install the two answers are the same.

**`drop_all()` refuses a database whose name does not start with `mbtest_`**
unless `MB_MONGO_ALLOW_DROP` is set. On SQLite it unlinks a local file; on
Atlas it can be a shared remote database.

**Reception should stay on SQLite.** It is the only backend that keeps
working without internet. `MB_DB_BACKEND=mongo` with no `MB_MONGO_URI`
raises at startup rather than falling back, because a silent fallback means a
day of attendance written into a local file nobody looks at again. Do not
build offline queueing or dual-write: reconciling two writers that allocate
from independent integer counters is a genuinely hard distributed-systems
problem, and half of it is worse than none.

## Conventions

- Business rules in `access.py`, never in `server.py` or the frontend.
- Deny messages are written for a receptionist to read aloud in plain language
  ("No session booked today for Flexibility"), not error codes. Technical
  detail goes in `detail`, which the UI does not show.
- **Never use `dangerouslySetInnerHTML`.** JSX escapes interpolated text by
  default — that is what replaced the old `esc()` helper. There is no
  legitimate reason to render raw HTML anywhere in this app.
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
