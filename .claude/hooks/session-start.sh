#!/bin/bash
# Claude Code on the web: prepare a Python environment so tests and linters run.
# Idempotent; the container is cached after this hook completes.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

# The system Python's pip cannot replace Debian-managed packages (e.g. blinker),
# so the desk's dependencies live in the project venv (gitignored, like on Windows).
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt pytest pytest-timeout pyflakes

# Tests import app.py; never start its scanner threads during a session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/.venv\""
    echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
    echo "export TOMAHAWK_NO_BG=1"
  } >> "$CLAUDE_ENV_FILE"
fi
