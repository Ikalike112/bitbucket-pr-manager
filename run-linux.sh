#!/bin/sh
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. See README.md for installation." >&2
  exit 1
fi
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  echo "Tkinter was not found. See README.md for installation." >&2
  exit 1
fi

exec python3 app/bitbucket_pr_ui.py
