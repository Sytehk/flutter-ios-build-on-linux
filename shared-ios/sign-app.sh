#!/usr/bin/env bash
set -euo pipefail
umask 077

tools=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
private=$(realpath -e -- "$1")
output=$(realpath -e -- "$2")
shift 2

if [[ $private == "$output" || $output != "$private/"* ]]; then
  echo "Use a separate output directory inside the project's signing folder." >&2
  exit 1
fi

docker --config "$tools/docker-config" run --rm --network none \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$tools/signing-tools/rcodesign,target=/usr/local/bin/rcodesign,readonly" \
  --mount "type=bind,source=$private,target=$private,readonly" \
  --mount "type=bind,source=$output,target=$output" \
  --entrypoint /usr/local/bin/rcodesign sweetpad-local:0.1.29 \
  sign -C /dev/null --timestamp-url none "$@"
