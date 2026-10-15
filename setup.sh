#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m pip install --disable-pip-version-check --upgrade --target "$ROOT/vendor" -r "$ROOT/requirements.txt"
