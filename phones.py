"""
What counts as the same mobile number.

The phone is the client's identity — not the name. The academy's own roster
sheets write one student as "rodaina hesham" in one block and "rodina
hesham" in another, and the seed has always merged those two rows on the
number rather than the spelling. Creating a client through the admin now
answers to the same rule.

`key()` is the comparison, and it is deliberately not the same thing as the
number that gets stored. The sheets hold one person as 1129200365 (Excel ate
the leading zero), as 01129200365, and as +201129200365 — three strings, one
mobile. No amount of normalising reconciles the third with the other two,
because deleting a country code from what somebody wrote down is inventing
data. The last ten digits reconcile all three and leave the stored text
exactly as it was typed, which is what a receptionist reads back and dials.

It lives in its own module, under everything, because four places across
three layers need the same answer — `access.py` (the rule), both repository
backends (the lookup) and `seed.py` (the merge) — and a second copy of "the
last ten digits" would eventually disagree with the first. Nothing is
imported here but `re`, so it can sit beneath all of them with no cycle.
"""

import re

# A required mobile number has to actually be a number, or the requirement is
# empty: "n/a" and "-" would satisfy it while carrying no identity at all,
# and two clients could both hold "n/a" without either being a duplicate of
# the other, since key() returns nothing to compare. Eight is chosen to be
# low enough to admit any real number anywhere -- an Egyptian mobile is
# eleven digits, ten without its leading zero -- and high enough to exclude
# a placeholder or a half-typed one.
MIN_DIGITS = 8


def digits(phone) -> str:
    """Just the digits, which is all any of the rules here look at."""
    return re.sub(r"\D", "", str(phone)) if phone else ""


def looks_like_a_number(phone) -> bool:
    """Enough of a number to identify somebody by. See MIN_DIGITS."""
    return len(digits(phone)) >= MIN_DIGITS


def key(phone) -> str | None:
    """
    The identity of a phone number: its last ten digits, nothing else.

    Ten, because that is an Egyptian mobile with neither its leading zero
    nor its +20 — the part that is the same however the number was written
    down. Anything shorter is not a mobile and is compared as written, which
    is the conservative answer: a short string can fail to match, but it can
    never match the wrong person.

    Returns None for a number with no digits in it at all, so a caller can
    tell "nothing to compare" from "compared, and nobody has it".
    """
    d = digits(phone)
    if not d:
        return None
    return d[-10:] if len(d) >= 10 else d
