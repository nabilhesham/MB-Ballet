#!/usr/bin/env bash
# repo.raw() is the temporary escape hatch from the SQLite-only port. The
# count only ever goes down: a new query belongs in a primitive or a port
# method, not in SQL. When it reaches zero, raw() is deleted from
# repo/base.py so it cannot come back without a deliberate re-add.
#
# Usage: .github/raw-count.sh [expected-maximum]
set -euo pipefail
cd "$(dirname "$0")/.."
count=$(grep -ho '\.raw(' access.py api/*.py | wc -l | tr -d ' ')
max=${1:-63}
echo "repo.raw() call sites: $count (ceiling $max)"
if [ "$count" -gt "$max" ]; then
  echo "FAIL: the escape hatch grew. A new query belongs in a port method." >&2
  exit 1
fi
