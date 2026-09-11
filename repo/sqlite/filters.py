"""
Compile the filter dialect to a WHERE clause.

The dialect is defined in repo/filters.py. Everything here is mechanical;
the only judgement is that field names are checked against a whitelist
rather than interpolated freely, since they reach the SQL as text.
"""

from ..filters import FilterError, validate

_OPS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
        "like": "LIKE"}


def _column(name: str) -> str:
    """
    Guard a field name before it is interpolated.

    Values are always bound as parameters, but a column name cannot be —
    so the names are restricted to what a column can actually be called.
    Every field in this app's schema is lowercase ASCII with underscores.
    """
    if not name.replace("_", "").isalnum():
        raise FilterError(f"{name!r} is not a column name")
    return name


def compile_where(flt: dict):
    """Returns (sql_fragment, params). An empty filter gives ("", [])."""
    validate(flt)
    clauses, params = [], []

    for field, cond in (flt or {}).items():
        if field == "$or":
            parts = []
            for sub in cond:
                frag, sub_params = compile_where(sub)
                parts.append(f"({frag})" if frag else "1=1")
                params.extend(sub_params)
            clauses.append("(" + " OR ".join(parts) + ")")
            continue

        col = _column(field)
        if not isinstance(cond, dict):
            # `None` means IS NULL, not `= NULL`, which is never true.
            if cond is None:
                clauses.append(f"{col} IS NULL")
            else:
                clauses.append(f"{col} = ?")
                params.append(cond)
            continue

        for op, value in cond.items():
            if op in ("in", "nin"):
                values = list(value)
                if not values:
                    # "in nothing" matches nothing; "not in nothing" matches
                    # everything. An empty IN () is a syntax error in SQLite,
                    # and `x NOT IN (NULL)` is NULL rather than true, so both
                    # are answered here instead of being emitted.
                    clauses.append("0=1" if op == "in" else "1=1")
                    continue
                marks = ",".join("?" * len(values))
                clauses.append(f"{col} {'NOT ' if op == 'nin' else ''}IN ({marks})")
                params.extend(values)
                continue
            if value is None and op in ("eq", "ne"):
                clauses.append(f"{col} IS {'NOT ' if op == 'ne' else ''}NULL")
                continue
            clauses.append(f"{col} {_OPS[op]} ?")
            params.append(value)

    return " AND ".join(clauses), params


def compile_order(sort) -> str:
    if not sort:
        return ""
    parts = [f"{_column(f)} {'DESC' if d < 0 else 'ASC'}" for f, d in sort]
    return " ORDER BY " + ", ".join(parts)
