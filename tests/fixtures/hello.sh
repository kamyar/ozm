#!/usr/bin/env bash
set -euo pipefail

NAME="${USER:-world}"
for i in 1 2 3; do
    echo "hello ${NAME}! (${i})"
done
