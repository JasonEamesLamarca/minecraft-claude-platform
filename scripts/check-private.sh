#!/usr/bin/env bash
# Fail if any file in the repo contains a private identifier from the maintainer's local deny-list.
# The deny-list holds the real names, hostnames and addresses that must never be published,
# so it lives OUTSIDE the repository and is never committed:
#   ${MCAI_DENYLIST:-$HOME/.config/minecraft-claude-platform/denylist.txt}
# One case-insensitive fixed string per line; blank lines and lines starting with # are ignored.
# Usage: scripts/check-private.sh [--history]   (--history also scans every commit)
set -euo pipefail
list="${MCAI_DENYLIST:-$HOME/.config/minecraft-claude-platform/denylist.txt}"
cd "$(git rev-parse --show-toplevel)"

if [[ ! -f "$list" ]]; then
  echo "check-private: no deny-list at $list - skipped (create one to enable this check)" >&2
  exit 0
fi
patterns=$(mktemp); trap 'rm -f "$patterns"' EXIT
grep -vE '^\s*(#|$)' "$list" > "$patterns" || true
[[ -s "$patterns" ]] || { echo "check-private: deny-list is empty - skipped" >&2; exit 0; }

# Public identifiers that legitimately contain a denied string (e.g. the maintainer's GitHub handle)
# are listed in .private-allow (committed) and removed from a line before it is re-checked.
allow=.private-allow
strip_allowed() {
  if [[ -f "$allow" ]]; then
    local sed_expr=""
    while IFS= read -r a; do
      [[ -z "$a" || "$a" == \#* ]] && continue
      sed_expr+="s/$(printf '%s' "$a" | sed 's/[][\\/.*^$]/\\&/g')//Ig;"
    done < "$allow"
    sed -e "$sed_expr"
  else
    cat
  fi
}

status=0
# Tracked + staged + untracked-but-not-ignored files in the working tree.
hits=$(git ls-files -z --cached --others --exclude-standard \
       | xargs -0 -r grep -I -n -i -F -f "$patterns" -- 2>/dev/null | strip_allowed \
       | grep -i -F -f "$patterns" || true)
if [[ -n "$hits" ]]; then
  printf '%s\n' "$hits"
  echo "check-private: private identifiers found in the working tree (see above)" >&2
  status=1
fi
if [[ "${1:-}" == "--history" ]]; then
  for c in $(git rev-list --all); do
    hits=$(git grep -I -n -i -F -f "$patterns" "$c" -- 2>/dev/null | strip_allowed \
           | grep -i -F -f "$patterns" || true)
    if [[ -n "$hits" ]]; then printf '%s\n' "$hits"; status=1; fi
  done
  [[ $status -eq 0 ]] || echo "check-private: private identifiers found in history" >&2
fi
[[ $status -eq 0 ]] && echo "check-private: clean ($(wc -l < "$patterns") patterns)"
exit $status
