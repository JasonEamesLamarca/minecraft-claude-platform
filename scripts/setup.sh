#!/usr/bin/env bash
# Minecraft AI Platform - setup wizard.
#
# Creates .env and secrets/ (both git-ignored), asks you to accept the Minecraft EULA, configures the
# owner account and AI authentication, validates the Compose file and optionally starts the stack.
# Safe to re-run: existing settings and secrets are kept unless you change them.
#
# Interactive:      scripts/setup.sh
# Non-interactive:  scripts/setup.sh --non-interactive --accept-eula --owner Steve [--port 25565]
#                     [--memory 4G] [--ai-auth none|api-key|subscription] [--start]
#                   (with --ai-auth api-key|subscription the credential is read from stdin)
# Other:            --check   only run prerequisite checks
#                   --help
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
SECRETS="$ROOT/secrets"
EULA_URL="https://www.minecraft.net/eula"

NONINTERACTIVE=false; ACCEPT_EULA=false; START=false; CHECK_ONLY=false
OWNER=""; PORT=""; MEMORY=""; AI_AUTH=""

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_bld=$'\e[1m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_red=""; c_grn=""; c_ylw=""; c_bld=""; c_off=""; }
info() { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%s✘ %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) NONINTERACTIVE=true ;;
    --accept-eula) ACCEPT_EULA=true ;;
    --owner) OWNER="${2:?--owner needs a value}"; shift ;;
    --port) PORT="${2:?--port needs a value}"; shift ;;
    --memory) MEMORY="${2:?--memory needs a value}"; shift ;;
    --ai-auth) AI_AUTH="${2:?--ai-auth needs a value}"; shift ;;
    --start) START=true ;;
    --check) CHECK_ONLY=true ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done

# ---------- .env helpers (never source .env: values are data, not code) ----------
env_get() {  # env_get KEY -> value or empty
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true
}
env_set() {  # env_set KEY VALUE  (VALUE must not contain newlines)
  local key="$1" val="$2" tmp
  [[ "$val" != *$'\n'* ]] || die "internal: newline in value for $key"
  tmp="$(mktemp "$ROOT/.env.XXXXXX")"
  if [[ -f "$ENV_FILE" ]]; then grep -vE "^$key=" "$ENV_FILE" > "$tmp" || true; fi
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}
ask() {  # ask "Prompt" default -> answer
  local prompt="$1" def="${2:-}" ans
  if $NONINTERACTIVE; then printf '%s' "$def"; return; fi
  read -r -p "$prompt${def:+ [$def]}: " ans </dev/tty
  printf '%s' "${ans:-$def}"
}
gen_secret() { head -c 32 /dev/urandom | base64 | tr -d '\n=+/' | cut -c1-40; }

# ---------- 1. prerequisites ----------
check_prereqs() {
  info "${c_bld}Checking prerequisites${c_off}"
  command -v docker >/dev/null || die "Docker is not installed. See docs/installation/README.md#install-docker"
  docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon. Is it running, and may your user use it? (Linux: add yourself to the 'docker' group, then log out and in.)"
  ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) reachable"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin missing ('docker compose'). Install docker-compose-plugin."
  local cv; cv="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  if [[ "$(printf '%s\n2.24.0\n' "$cv" | sort -V | head -1)" != "2.24.0" ]]; then
    die "Docker Compose $cv is too old; 2.24.0 or newer is required."
  fi
  ok "Docker Compose $cv"
  local arch; arch="$(docker info --format '{{.Architecture}}' 2>/dev/null)"
  case "$arch" in x86_64|amd64|aarch64|arm64) ok "Architecture $arch supported" ;;
    *) warn "Architecture '$arch' is not tested; images are published for amd64 and arm64 only." ;; esac
  local free_kb; free_kb="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
  if (( free_kb < 5*1024*1024 )); then warn "Less than 5 GB free disk space at $ROOT."; else ok "Disk space OK"; fi
  command -v base64 >/dev/null || die "'base64' is required."
}

check_prereqs
$CHECK_ONLY && exit 0

umask 077
mkdir -p "$SECRETS"; chmod 700 "$SECRETS"
[[ -f "$ENV_FILE" ]] || { cp "$ROOT/.env.example" "$ENV_FILE"; chmod 600 "$ENV_FILE"; ok "Created .env from .env.example"; }

# ---------- 2. EULA ----------
info ""; info "${c_bld}Minecraft EULA${c_off}"
if [[ "$(env_get MC_EULA)" == "TRUE" ]]; then
  ok "EULA already accepted"
else
  info "The Minecraft server software is downloaded at first start. To run it you must agree to the"
  info "Minecraft End User License Agreement: $EULA_URL"
  if $NONINTERACTIVE; then
    $ACCEPT_EULA || die "You must accept the Minecraft EULA: re-run with --accept-eula after reading $EULA_URL"
  else
    ans="$(ask "Have you read and do you accept the Minecraft EULA? Type 'yes' to accept" "")"
    [[ "$ans" == "yes" ]] || die "EULA not accepted - nothing was started. Re-run when you are ready."
  fi
  env_set MC_EULA TRUE; ok "EULA accepted"
fi

# ---------- 3. host user (containers run as you, not root) ----------
env_set PUID "$(id -u)"; env_set PGID "$(id -g)"

# ---------- 4. Minecraft settings ----------
info ""; info "${c_bld}Minecraft server${c_off}"
OWNER="${OWNER:-$(ask "Your Minecraft Java username (server owner; whitelisted + operator)" "$(env_get MC_OWNER)")}"
[[ "$OWNER" =~ ^[A-Za-z0-9_]{3,16}$ ]] || die "'$OWNER' is not a valid Minecraft username (3-16 letters, digits, underscore)."
env_set MC_OWNER "$OWNER"
PORT="${PORT:-$(ask "Game port" "$(env_get MC_PORT)")}"; PORT="${PORT:-25565}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then die "Port must be a number between 1024 and 65535."; fi
env_set MC_PORT "$PORT"
MEMORY="${MEMORY:-$(ask "Java heap for the server (e.g. 2G, 4G, 8G)" "$(env_get MC_MEMORY)")}"; MEMORY="${MEMORY:-4G}"
[[ "$MEMORY" =~ ^([1-9][0-9]?)G$ ]] || die "Memory must look like 4G (whole gigabytes, 1G-99G)."
heap_g="${BASH_REMATCH[1]}"
env_set MC_MEMORY "$MEMORY"
env_set MC_MEM_LIMIT "$((heap_g + heap_g / 2 + 1))g"   # heap + ~50% for JVM overhead
ok "Owner $OWNER, port $PORT, heap $MEMORY (container limit $(env_get MC_MEM_LIMIT))"

# ---------- 5. internal secrets ----------
info ""; info "${c_bld}Secrets${c_off}"
for s in rcon_password gateway_mod_token; do
  if [[ -s "$SECRETS/$s" ]]; then ok "secrets/$s exists (kept)"
  else gen_secret > "$SECRETS/$s"; chmod 600 "$SECRETS/$s"; ok "Generated secrets/$s"; fi
done

# ---------- 6. AI authentication ----------
info ""; info "${c_bld}AI authentication${c_off} (used from Phase 2 on; see docs/architecture/adr/0003-ai-authentication.md)"
current="$(env_get AI_AUTH_METHOD)"
if [[ -z "$AI_AUTH" ]]; then
  if $NONINTERACTIVE; then AI_AUTH="${current:-none}"
  else
    info "  1) Anthropic API key - pay per use; recommended for unattended use"
    info "  2) Claude subscription token from 'claude setup-token' - you are responsible for your plan's terms"
    info "  3) Skip for now"
    case "$current" in api-key) def=1 ;; subscription) def=2 ;; *) def=3 ;; esac
    case "$(ask "Choose 1, 2 or 3" "$def")" in
      1) AI_AUTH=api-key ;; 2) AI_AUTH=subscription ;; 3) AI_AUTH=none ;; *) die "Please choose 1, 2 or 3." ;;
    esac
  fi
fi
case "$AI_AUTH" in
  none) ok "AI authentication skipped" ;;
  api-key|subscription)
    file="$SECRETS/anthropic_api_key"; prefix="sk-ant-api"
    [[ "$AI_AUTH" == subscription ]] && { file="$SECRETS/claude_oauth_token"; prefix="sk-ant-oat"; }
    if [[ -s "$file" && "$AI_AUTH" == "$current" ]] && ! $NONINTERACTIVE; then
      [[ "$(ask "A credential is already stored. Replace it? (y/N)" "n")" =~ ^[yY] ]] || { ok "Kept existing credential"; AI_SKIP_READ=1; }
    fi
    if [[ -z "${AI_SKIP_READ:-}" ]]; then
      if $NONINTERACTIVE; then IFS= read -r cred || true
      else read -r -s -p "Paste the credential (input hidden): " cred </dev/tty; echo; fi
      [[ -n "${cred:-}" ]] || die "No credential provided."
      [[ "$cred" == "$prefix"* ]] || warn "That doesn't look like a $AI_AUTH credential (expected it to start with $prefix). Stored anyway."
      printf '%s' "$cred" > "$file"; chmod 600 "$file"; unset cred
      # only one method is active: remove the other credential
      if [[ "$AI_AUTH" == api-key ]]; then rm -f "$SECRETS/claude_oauth_token"; else rm -f "$SECRETS/anthropic_api_key"; fi
      ok "Stored in ${file#"$ROOT"/} (mode 600, git-ignored)"
    fi ;;
  *) die "--ai-auth must be none, api-key or subscription" ;;
esac
env_set AI_AUTH_METHOD "$AI_AUTH"

# ---------- 7. SSH administration ----------
info ""; info "${c_bld}SSH administration${c_off}: not available yet (arrives in Phase 4)."

# ---------- 8. validate ----------
mkdir -p "$ROOT/data/minecraft" "$ROOT/backups"
(cd "$ROOT" && docker compose config --quiet) || die "docker compose config failed - see the error above."
ok "compose.yaml validates with your settings"
if grep -qE '^(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN)=.+' "$ENV_FILE"; then
  warn ".env contains an AI credential. Move it to secrets/ by re-running this wizard, then delete it from .env."
fi

# ---------- 9. start ----------
if ! $START && ! $NONINTERACTIVE; then
  [[ "$(ask "Start the server now? (Y/n)" "y")" =~ ^[nN] ]] || START=true
fi
if $START; then
  info ""; info "${c_bld}Starting${c_off} (first start downloads Minecraft + mods; this can take several minutes)"
  (cd "$ROOT" && docker compose up -d)
  "$ROOT/scripts/wait-healthy.sh" minecraft 900
  ok "Minecraft is up. Connect your Minecraft Java $(env_get MC_VERSION) client to <this-host>:$PORT"
else
  info ""; info "Next: ${c_bld}docker compose up -d${c_off}, then ${c_bld}scripts/doctor.sh${c_off}"
fi
