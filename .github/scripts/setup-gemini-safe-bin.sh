#!/usr/bin/env sh
set -eu

# Create safe wrappers for the limited shell tools enabled in gemini-cli settings.
# These wrappers refuse large/binary payloads to prevent prompt/context explosions.

max_bytes="${GEMINI_MAX_READ_BYTES:-200000}"

mkdir -p .gemini/safe-bin

cat > .gemini/safe-bin/_validate_file.sh <<'SH'
#!/usr/bin/env sh
set -eu

max_bytes="${GEMINI_MAX_READ_BYTES:-200000}"

is_blocked_ext() {
  case "$1" in
    *.bin|*.ko|*.deb|*.tar|*.tar.gz|*.tgz|*.zip|*.gz|*.xz|*.7z|*.exe|*.so|*.a|*.o)
      return 0
      ;;
  esac
  return 1
}

is_text_file() {
  mime=$(file -b --mime-type "$1" 2>/dev/null || true)
  case "$mime" in
    text/*|application/json|application/xml)
      return 0
      ;;
  esac
  return 1
}

validate_file() {
  f="$1"

  if [ "$f" = "-" ]; then
    echo "ERROR: refusing to read from stdin" >&2
    exit 2
  fi

  if is_blocked_ext "$f"; then
    echo "ERROR: refusing to read blocked file type: $f" >&2
    exit 2
  fi

  if [ ! -f "$f" ]; then
    echo "ERROR: file not found or not a regular file: $f" >&2
    exit 2
  fi

  size=$(wc -c < "$f" | tr -d ' ')
  if [ "$size" -gt "$max_bytes" ]; then
    echo "ERROR: refusing to read large file ($size bytes): $f" >&2
    exit 2
  fi

  if ! is_text_file "$f"; then
    echo "ERROR: refusing to read non-text file: $f" >&2
    exit 2
  fi
}

# Validate any arg that is an existing file.
for arg in "$@"; do
  if [ -f "$arg" ]; then
    validate_file "$arg"
  fi
done
SH

chmod +x .gemini/safe-bin/_validate_file.sh

cat > .gemini/safe-bin/cat <<'SH'
#!/usr/bin/env sh
set -eu

"${0%/*}/_validate_file.sh" "$@"
exec /bin/cat "$@"
SH

cat > .gemini/safe-bin/head <<'SH'
#!/usr/bin/env sh
set -eu

"${0%/*}/_validate_file.sh" "$@"
exec /usr/bin/head "$@"
SH

cat > .gemini/safe-bin/tail <<'SH'
#!/usr/bin/env sh
set -eu

"${0%/*}/_validate_file.sh" "$@"
exec /usr/bin/tail "$@"
SH

cat > .gemini/safe-bin/grep <<'SH'
#!/usr/bin/env sh
set -eu

"${0%/*}/_validate_file.sh" "$@"
exec /usr/bin/grep "$@"
SH

chmod +x .gemini/safe-bin/cat .gemini/safe-bin/head .gemini/safe-bin/tail .gemini/safe-bin/grep

# Quick sanity check that wrappers are discoverable.
command -v cat >/dev/null 2>&1
command -v grep >/dev/null 2>&1
command -v head >/dev/null 2>&1
command -v tail >/dev/null 2>&1
