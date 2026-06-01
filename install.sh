#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"

mkdir -p "$BIN_DIR"
cp "$ROOT/bin/yoyo" "$BIN_DIR/yoyo"
chmod +x "$BIN_DIR/yoyo"

YOYO_SKILL_SOURCE="$ROOT/skills/yoyo" "$BIN_DIR/yoyo" install-skill

printf 'installed yoyo: %s\n' "$BIN_DIR/yoyo"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'note: add %s to PATH if yoyo is not found\n' "$BIN_DIR" ;;
esac
