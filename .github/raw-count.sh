#!/usr/bin/env bash
# repo.raw() was the temporary escape hatch from the SQLite-only port: every
# query moved onto the repository object first, and the ones not yet
# expressible as a primitive or a port method kept running through it.
#
# It reached zero, so the method is gone from repo/base.py. This check keeps
# it gone. A new query belongs in a primitive or a port method — SQL in a
# caller has no MongoDB translation, which is the whole reason the interface
# exists.
set -euo pipefail
cd "$(dirname "$0")/.."
# grep exits 1 when it finds nothing, which is the success case here, so
# both counts are taken with `|| true` under `set -e`.
count=$( { grep -ho '\.raw(' access.py api/*.py tests/*.py || true; } | wc -l | tr -d ' ')
defined=$( { grep -ho 'def raw(' repo/base.py repo/sqlite/__init__.py || true; } | wc -l | tr -d ' ')
echo "repo.raw() call sites: $count   definitions: $defined"
if [ "$count" -ne 0 ] || [ "$defined" -ne 0 ]; then
  echo "FAIL: the escape hatch is back. Add a port method instead." >&2
  exit 1
fi
