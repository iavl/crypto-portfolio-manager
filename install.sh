#!/bin/sh
set -eu

skill_name=crypto-portfolio-manager
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
codex_home=${CODEX_HOME:-${HOME:?HOME must be set when CODEX_HOME is unset}}
skills_dir="$codex_home/skills"
destination="$skills_dir/$skill_name"
temporary_dir=

payload_items="SKILL.md README.md README.zh-CN.md docs config references schemas crypto_portfolio scripts"

fail() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$temporary_dir" ] && [ -d "$temporary_dir" ]; then
        rm -rf "$temporary_dir"
    fi
}

trap cleanup EXIT
trap 'exit 1' HUP INT TERM

[ -f "$script_dir/SKILL.md" ] || fail "SKILL.md not found beside install.sh"

for item in $payload_items; do
    [ -e "$script_dir/$item" ] || fail "required payload is missing: $item"
done

if [ -e "$destination" ] || [ -L "$destination" ]; then
    fail "destination already exists: $destination"
fi

mkdir -p "$skills_dir"
temporary_dir=$(mktemp -d "$skills_dir/.$skill_name.tmp.XXXXXX")

for item in $payload_items; do
    cp -R "$script_dir/$item" "$temporary_dir/$item"
done

# Keep local caches out of the installed payload even when they exist inside a
# copied Python package or script directory.
find "$temporary_dir" \( \
    -name '.DS_Store' -o \
    -name '__pycache__' -o \
    -name '*.pyc' -o \
    -name '*.pyo' \
\) -exec rm -rf {} +

mv "$temporary_dir" "$destination"

printf 'Installed %s to %s\n' "$skill_name" "$destination"
printf 'Reload or restart Codex if the Skill is not immediately available.\n'
