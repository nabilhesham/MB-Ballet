"""
The filter dialect the primitives speak.

Deliberately Mongo-shaped, because that is the weaker of the two backends:
anything expressible here compiles to SQL in about forty lines, while the
reverse — letting callers write SQL fragments — has no Mongo translation at
all and would put the query language back in the caller.

    {"client_id": 7}                      field equals value
    {"status": {"ne": "booked"}}          one operator
    {"starts_at": {"gte": a, "lt": b}}    several, ANDed
    {"name_en": {"like": "%dana%"}}       SQL LIKE / Mongo regex
    {"$or": [{...}, {...}]}               top level only

Everything at the top level is ANDed. There is no nesting beyond `$or`,
no field-to-field comparison and no computed values: a question that needs
one of those is a named method on a port, not a filter. That limit is the
point — it is what stops a caller building a query the other backend cannot
answer.

`sort` is a list of `(field, 1 | -1)` pairs.
"""

OPERATORS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "nin", "like")

# Every sort ends with the primary key. SQLite falls back to rowid order for
# ties and Mongo to natural order, so a sort on a non-unique column -- of
# which this app has several, "ORDER BY name_en" among them -- would
# otherwise return the same rows in a different order on each backend, and
# the parity tests would be comparing lists that legitimately disagree.
TIEBREAK = "id"


class FilterError(ValueError):
    """The filter is not expressible. Raised by both backends identically."""


def check_field(name: str) -> str:
    """
    Guard a field name.

    Checked here rather than in the SQLite compiler, even though only SQL
    interpolates it into a statement. A filter either works on both backends
    or is refused by both — an interface where one silently accepts what the
    other rejects is not one interface.
    """
    if not name.replace("_", "").isalnum():
        raise FilterError(f"{name!r} is not a field name")
    return name


def validate(flt: dict) -> dict:
    """Check a filter before either backend sees it, so both refuse alike."""
    if flt is None:
        return {}
    if not isinstance(flt, dict):
        raise FilterError(f"a filter is a dict, got {type(flt).__name__}")
    for field, cond in flt.items():
        if field == "$or":
            if not isinstance(cond, (list, tuple)) or not cond:
                raise FilterError("$or takes a non-empty list of filters")
            for sub in cond:
                validate(sub)
            continue
        if field.startswith("$"):
            raise FilterError(f"unknown top-level operator {field!r}")
        check_field(field)
        if isinstance(cond, dict):
            for op in cond:
                if op not in OPERATORS:
                    raise FilterError(
                        f"unknown operator {op!r} on {field!r}; "
                        f"expected one of {', '.join(OPERATORS)}")
    return flt


def normalise_sort(sort):
    """
    Append the tiebreak unless it is already named. See TIEBREAK above.
    """
    sort = list(sort or [])
    if not any(field == TIEBREAK for field, _ in sort):
        sort.append((TIEBREAK, 1))
    return sort
