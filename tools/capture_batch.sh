#!/usr/bin/env bash
# usage: capture_batch.sh OUTDIR sample... ; one guarded process per sample (a hung Wine session must not stall the batch)
out="$1"; shift
for s in "$@"; do
  timeout 400 uv run python -u "$(dirname "$0")/capture_sdf.py" "$out" "$s" || echo "FAILED/TIMEOUT: $s"
  pkill -f "bwrap.*prefix-work" 2>/dev/null; sleep 2
done
echo BATCH_DONE
