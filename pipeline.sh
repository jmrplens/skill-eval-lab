#!/usr/bin/env bash
# Run trigger, scenarios, judge and report in one detached container, so a
# run survives the shell that started it. Same key handling as lab.sh.
#
#   OUT=<dir> [SUITE=<dir>] [SKILLS=<dir>] [CAP=25] [REPS=1] [MODELS=ids] [MODELS_FILE=<file>] pipeline.sh
#
# MODELS_FILE, if set, is a user catalog (JSON, or YAML) merged over the
# built-in lab/models.json; it is mounted read-only and PyYAML is added to the
# container only for a .yaml/.yml file.
set -euo pipefail

LAB="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$LAB/.env}"
SUITE="${SUITE:-$LAB/suites/example}"
SKILLS="${SKILLS:-$LAB/skills}"
OUT="${OUT:?set OUT to the run directory}"
CAP="${CAP:-25}"
REPS="${REPS:-1}"
MODELS="${MODELS:-}"
MODELS_FILE="${MODELS_FILE:-}"
NAME="${NAME:-skill-lab-pipeline-$(basename "$OUT")}"

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
common="--out /out --cap $CAP"
[ -n "$MODELS" ] && common="$common --models $MODELS"

mounts=(-v "$LAB/lab:/lab:ro" -v "$SKILLS:/skills:ro" -v "$SUITE/inputs:/inputs:ro" -v "$SUITE/key:/key:ro" -v "$OUT:/out")
pyyaml_install=""
mfile=""
if [ -n "$MODELS_FILE" ]; then
  mounts+=(-v "$MODELS_FILE:/models-file:ro")
  mfile="--models-file /models-file"
  common="$common $mfile"
  case "$MODELS_FILE" in
    *.yaml | *.yml) pyyaml_install="pip -q install pyyaml >/dev/null 2>&1 || true" ;;
  esac
fi

docker run -d --rm --name "$NAME" --memory 2g \
  -e ANTHROPIC_API_KEY -e OPENAI_API_KEY -e GOOGLE_API_KEY -e QWEN_API_KEY -e PYTHONUNBUFFERED=1 \
  "${mounts[@]}" \
  python:3.13-slim sh -c "
    set -e
    $pyyaml_install
    python -I /lab/run.py trigger $common --reps $REPS >> /out/lab.log 2>&1
    python -I /lab/run.py scenarios $common --reps $REPS >> /out/lab.log 2>&1
    python -I /lab/run.py judge --out /out --cap $CAP $mfile >> /out/lab.log 2>&1
    python -I /lab/run.py report --out /out >> /out/lab.log 2>&1
    echo PIPELINE-DONE >> /out/lab.log
  "
