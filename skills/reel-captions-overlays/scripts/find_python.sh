#!/bin/sh
# Print the Python interpreter the scripts in this skill should use.
# Order: $REELKIT_PYTHON, then $REELKIT_HOME/.venv/bin/python3, then a .venv/bin/python3
# at the root of the repo this skill lives in (skills/<name>/scripts/ -> repo root, symlinks
# resolved), then python3 on PATH. Usage: PY=$("$SK/find_python.sh")
if [ -n "$REELKIT_PYTHON" ]; then echo "$REELKIT_PYTHON"; exit 0; fi
if [ -n "$REELKIT_HOME" ] && [ -x "$REELKIT_HOME/.venv/bin/python3" ]; then
  echo "$REELKIT_HOME/.venv/bin/python3"; exit 0
fi
self="$0"
while [ -L "$self" ]; do
  link=$(readlink "$self")
  case "$link" in /*) self="$link" ;; *) self="$(dirname "$self")/$link" ;; esac
done
dir=$(cd "$(dirname "$self")" && pwd -P)
root=$(cd "$dir/../../.." 2>/dev/null && pwd -P)
if [ -n "$root" ] && [ -x "$root/.venv/bin/python3" ]; then echo "$root/.venv/bin/python3"; exit 0; fi
command -v python3 || echo python3
