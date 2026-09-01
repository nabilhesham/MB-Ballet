# sheets/

The academy's own Excel workbooks. `seed.py` reads these to build `academy.db`.

They hold real client names and mobile numbers, so **they are not in git** —
same reasoning as `academy.db`, `photos/` and `cards/`. Copy them in by hand on
the machine you are seeding.

## What goes here

| file | what it is |
|---|---|
| `ballet.xlsx` | the Wednesday ballet roster: one block per class group |
| `flexibility.xlsx` | evening and morning flexibility rosters |
| `August_Salaries_2026.xlsx` | the month's instructor hours |

The file names are not magic — the list `seed.py` actually reads is `SHEETS` at
the top of that script. Add a term's workbook there and it is imported.

## The shape the reader expects

**Roster sheets** are read block by block. A block is:

```
Wednesday At 3:30Pm With Captain Karma primary      <- title, alone in column A
                    1ST MONTH                       <- merged band, ignored
PAID DATE | NAME | CELL PHONE | DOB | AGE | month | PAID | school | 1ST | 2ND | ...
2026-08-05 | jamila karim | 1005646919 | | 5 | 1 | yes | gems | 2026-08-12 | 26/8 absent
```

The reader works from the header row, not from column positions, so a block
that has no `school` column or leaves `NAME` unlabelled still imports. What it
looks for:

- **the title** — the weekday, the time and the captain. `level 8`, `grade 6`
  and `primary` are pulled out as the class level. A title with no day or time
  (`EVENING FLEXIBILITY`) is scheduled from the students' own `days` column.
- **`PAID DATE` / `date`** — when they paid, which becomes the join date.
- **`month`** (ballet) or **`sessions`** (flexibility) — how big the plan is.
  Ballet sells months at one class a week; flexibility sells session packs.
- **`PAID`** — a number is money and is recorded as the plan price. `package`,
  `free` and `yes` are kept as a note, and the plan stays **unpriced** rather
  than being recorded as zero. The dashboard counts those separately.
- **`1ST` … `4TH`** — the attendance columns, however many times they repeat.
  A date means she came; `26/8 absent` means she did not. Anything else
  (`app`, `out`, `Start 10/9`) is kept as a note on the client.

**Salary sheets** are instructors across the top, dates down column A, hours in
the cells. The two unlabelled rows under the grid are read as total hours and
total pay, and the hourly rate is their ratio — so a rate change next month
arrives on its own.

## Before you seed

`seed.py --force --dry-run` parses everything and prints what it found without
writing to the database. Read the warnings it prints: they are every place the
sheets were ambiguous and the reader had to choose — a year that looked like a
typo, a date written into two columns, an instructor who teaches but is not on
the salary sheet.
