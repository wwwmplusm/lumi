#!/bin/sh
# Install from a local checkout; uv keeps Python dependencies isolated.
set -eu
LUMI_SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v uv >/dev/null 2>&1; then
    echo 'Нужен uv: https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 1
fi
uv tool install --force "$LUMI_SOURCE_DIR"
LUMI_BIN_DIR=$(uv tool dir --bin)
printf '\nLumi установлен: %s/lumi\n' "$LUMI_BIN_DIR"
case ":$PATH:" in
    *":$LUMI_BIN_DIR:"*) ;;
    *) echo "Добавьте $LUMI_BIN_DIR в PATH (или выполните uv tool update-shell)." ;;
esac
exec "$LUMI_BIN_DIR/lumi" setup "$@"
