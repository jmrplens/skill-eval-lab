#!/usr/bin/env bash
# Run the skill lab in a python:3.13-slim container.
#
#   OUT=<dir> [SUITE=<dir>] [SKILLS=<dir>] [MODELS_FILE=<file>] [DETACH=1] [NAME=<name>] lab.sh <command> [run.py options]
#
# The four provider keys are read from ENV_FILE into this process's
# environment and handed to the container by name (-e VAR), so no value ever
# appears on a command line or in output. Only the judge gets the answer key.
# MODELS_FILE, if set, is a user catalog (JSON, or YAML) merged over the
# built-in lab/models.json; it is mounted read-only and PyYAML is added to the
# container only for a .yaml/.yml file.
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: OUT=<dir> [SUITE=] [SKILLS=] [MODELS_FILE=] $(basename "$0") <command> [run.py options]" >&2; exit 2; }

LAB="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$LAB/.env}"
SUITE="${SUITE:-$LAB/suites/example}"
SKILLS="${SKILLS:-$LAB/skills}"
OUT="${OUT:?set OUT to the run directory}"
NAME="${NAME:-skill-lab-$1-$$}"
IMAGE="${IMAGE:-python:3.13-slim}"

get() {
  local v
  v="$(grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
ANTHROPIC_API_KEY="$(get ANTHROPIC_API_KEY)"
OPENAI_API_KEY="$(get OPENAI_API_KEY)"
GOOGLE_API_KEY="$(get GOOGLE_API_KEY)"
QWEN_API_KEY="$(get QWEN_API_KEY)"
export ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY QWEN_API_KEY

mkdir -p "$OUT"
mounts=(-v "$LAB/lab:/lab:ro" -v "$SKILLS:/skills:ro" -v "$SUITE/inputs:/inputs:ro" -v "$OUT:/out")
if [ "$1" = judge ]; then
  mounts+=(-v "$SUITE/key:/key:ro")
fi

extra_args=()
pyyaml_install=""
if [ -n "${MODELS_FILE:-}" ]; then
  mounts+=(-v "$MODELS_FILE:/models-file:ro")
  extra_args=(--models-file /models-file)
  case "$MODELS_FILE" in
    *.yaml | *.yml) pyyaml_install="pip -q install pyyaml >/dev/null 2>&1 || true; " ;;
  esac
fi

run_flags=(--rm --name "$NAME" --memory 2g
  -e ANTHROPIC_API_KEY -e OPENAI_API_KEY -e GOOGLE_API_KEY -e QWEN_API_KEY -e PYTHONUNBUFFERED=1)
if [ "${DETACH:-0}" = 1 ]; then
  run_flags+=(-d)
fi

# The inner sh runs run.py with the lab command and options as its own
# positional parameters ("$@"), with extra_args (the --models-file flag, or
# nothing) appended after them, and pyyaml_install prepended for a YAML file.
inner="${pyyaml_install}"'python -I /lab/run.py "$@" --out /out 2>&1 | tee -a /out/lab.log'
exec docker run "${run_flags[@]}" "${mounts[@]}" "$IMAGE" \
  sh -c "$inner" sh "$@" ${extra_args[@]+"${extra_args[@]}"}
