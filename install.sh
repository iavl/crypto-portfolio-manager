#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: ./install.sh [--target TARGET]

Create user-level symlinks to the repository Skill.

TARGET:
  all      Install for Codex, Claude Code, and ZCode (default)
  codex    Install for Codex at ~/.agents/skills/
  claude   Install for Claude Code at ~/.claude/skills/
  zcode    Install for ZCode at ~/.zcode/skills/

Existing files, directories, and unknown symlinks are never overwritten.
The old Codex symlink pointing to this checkout root is repaired to the
canonical Skill directory.
EOF
}

fail() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

target=all
while [ "$#" -gt 0 ]; do
    case "$1" in
        --target)
            [ "$#" -ge 2 ] || fail "--target requires codex, claude, zcode, or all"
            target=$2
            shift 2
            ;;
        --target=*)
            target=${1#--target=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

case "$target" in
    all|codex|claude|zcode) ;;
    *) fail "invalid target: $target" ;;
esac

home_dir=${HOME:?HOME must be set}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null) \
    || fail "run this script from a Git checkout"
skill_dir="$repo_root/.agents/skills/crypto-portfolio-manager"
[ -f "$skill_dir/SKILL.md" ] || fail "canonical Skill not found: $skill_dir/SKILL.md"
legacy_codex_destination="$home_dir/.agents/skills/crypto-portfolio-manager"

install_link() {
    destination=$1
    parent=$(dirname -- "$destination")
    mkdir -p "$parent"

    if [ -L "$destination" ]; then
        current_target=$(readlink "$destination")
        if [ "$current_target" = "$skill_dir" ]; then
            printf 'already linked: %s -> %s\n' "$destination" "$skill_dir"
            return
        fi
        if [ "$destination" = "$legacy_codex_destination" ] && [ "$current_target" = "$repo_root" ]; then
            rm "$destination"
            ln -s "$skill_dir" "$destination"
            printf 'repaired legacy link: %s -> %s\n' "$destination" "$skill_dir"
            return
        fi
        fail "refusing to replace existing symlink: $destination"
    fi
    [ ! -e "$destination" ] || fail "refusing to overwrite existing path: $destination"

    ln -s "$skill_dir" "$destination"
    printf 'linked: %s -> %s\n' "$destination" "$skill_dir"
}

case "$target" in
    all|codex)
        install_link "$home_dir/.agents/skills/crypto-portfolio-manager"
        ;;
esac
case "$target" in
    all|claude)
        install_link "$home_dir/.claude/skills/crypto-portfolio-manager"
        ;;
esac
case "$target" in
    all|zcode)
        install_link "$home_dir/.zcode/skills/crypto-portfolio-manager"
        ;;
esac
