#!/usr/bin/env bash
# End-to-end demo used for the video: setup, tests, failing baseline, agent run.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m harness prepare                        # clone target @ pinned commit, build sandbox image
python3 -m unittest discover -s tests -t .        # core tests (scripted model, no API key)
python3 -m harness baseline                       # acceptance FAILS on the starting code
python3 -m harness run "$@"                       # real model (Ollama), then independent verification
