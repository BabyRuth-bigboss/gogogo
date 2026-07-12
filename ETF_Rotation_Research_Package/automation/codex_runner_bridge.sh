#!/bin/zsh
set -euo pipefail

if [[ -z "${CODEX_PROMPT:-}" ]]; then
  print -u2 "CODEX_PROMPT is not set"
  exit 1
fi

prompt=$(<"$CODEX_PROMPT")
exec codex exec --full-auto "$prompt"
