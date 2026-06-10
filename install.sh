#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
CONFIG_DIR="${YOYO_CONFIG_DIR:-"$HOME/.config/yoyo"}"

mkdir -p "$BIN_DIR"
cp "$ROOT/bin/yoyo" "$BIN_DIR/yoyo"
chmod +x "$BIN_DIR/yoyo"

mkdir -p "$CONFIG_DIR"
printf '%s\n' "$ROOT" > "$CONFIG_DIR/source"

if [ -d "$ROOT/workflows" ]; then
  mkdir -p "$CONFIG_DIR/workflows"
  cp "$ROOT"/workflows/*.json "$CONFIG_DIR/workflows/"
fi

YOYO_SKILL_SOURCE="$ROOT/skills" "$BIN_DIR/yoyo" install-skill

printf 'installed yoyo: %s\n' "$BIN_DIR/yoyo"
printf 'recorded source: %s\n' "$ROOT"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'note: add %s to PATH if yoyo is not found\n' "$BIN_DIR" ;;
esac
