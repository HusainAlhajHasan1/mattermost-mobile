#!/usr/bin/env bash
# Copyright (C) 2026 HusainAlhajHasan1 — SPDX-License-Identifier: AGPL-3.0-or-later
# =============================================================================
#  wg-relay-setup.sh
#  "WireGuard entry VPS  ->  udp2raw  ->  home server" relay, fully scripted.
#
#  ROLE "home"  (the machine behind the public IP that runs the service)
#     * wgrelay0                : WireGuard endpoint, reachable via udp2raw only
#     * udp2raw (server)        : listens on 0.0.0.0:$RELAY_PORT (fake-TCP)
#     * nftables table `wgrelay`: clients may only reach what you allow, and
#                                 the WG port itself is not exposed directly
#
#  ROLE "vps"   (the public VPS; its other services keep running untouched)
#     * wgvpn0                  : client-facing WireGuard server, managed by
#                                 (default: HusainAlhajhasan1/wireguard-dashboard,
#                                  override with DASH_REPO=...)
#     * wgrelay0                : uplink towards the home server (no listen port)
#     * udp2raw (client)        : 127.0.0.1:$UDP2RAW_LOCAL_PORT -> home:$RELAY_PORT
#     * nftables table `wgrelay`: clients can ONLY reach HOME_DESTS through
#                                 wgrelay0; everything else - including every
#                                 service on the VPS - is dropped
#
#  The script is idempotent and purely additive: it never edits, restarts or
#  removes an object it did not create. Everything it owns is namespaced:
#     interfaces  wgvpn0 / wgrelay0
#     nft table   inet wgrelay
#     units       udp2raw-relay.service / wg-relay-firewall.service
#     ports       picked automatically, only free ports are used
#
#  Usage:  ./wg-relay-setup.sh --role home|vps [options]
#  Help :  ./wg-relay-setup.sh --help
# =============================================================================
set -Eeuo pipefail

VERSION="1.0.0"
PROG="${0##*/}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/${BASH_SOURCE[0]##*/}"

# ----------------------------------------------------------------------------
# defaults — every value can be overridden in relay.conf or on the command line
# ----------------------------------------------------------------------------
ROLE=""

# tunnel
HOME_PUBLIC_IP=""                  # public IP / hostname of the home side
VPS_PUBLIC_IP=""                   # auto-detected when it is empty
HOME_DESTS=""                      # what clients may reach through the tunnel
HOME_DNS=""                        # optional DNS pushed to clients (home AdGuard)
RELAY_PORT=0                       # public fake-TCP port on the home side (0=auto)
UDP2RAW_MODE="faketcp"             # faketcp | udp | icmp
UDP2RAW_KEY=""                     # shared secret, generated on first run
UDP2RAW_BIN=""                     # path to the udp2raw binary (auto)
UDP2RAW_EXTRA_ARGS=""              # e.g. "--fix-gro"
UDP2RAW_LOCAL_PORT=0               # VPS: 127.0.0.1 port the uplink sends to
WG_RELAY_LOCAL_PORT=0              # home: local port wgrelay0 listens on
RELAY_MTU=1280
CLIENT_MTU=1280
MSS_CLAMP=1240

# interfaces / addressing
CLIENT_IFACE="wgvpn0"
RELAY_IFACE="wgrelay0"
CLIENT_NET="10.66.0.0/24"
RELAY_VPS_IP="10.67.0.2"
RELAY_HOME_IP="10.67.0.1"
CLIENT_LISTEN_PORT=0               # VPS: public UDP port for clients (0=auto)

# dashboard (VPS only)
DASH_REPO="https://github.com/HusainAlhajhasan1/wireguard-dashboard"  # any compatible dashboard
DASH_BRANCH="main"
DASH_SRC_DIR="/opt/wireguard-dashboard-src"
DASH_PORT=8080
DASH_BIND="127.0.0.1"
DASH_DOMAIN=""
DASH_EMAIL=""
DASH_USER="admin"
DASH_PASS=""
DASH_ALLOW_FROM=""                 # e.g. "203.0.113.7" when DASH_BIND=0.0.0.0
DASH_TELEGRAM_TOKEN=""
DASH_TELEGRAM_ADMIN=""

# home side policy
HOME_SNAT="yes"                    # masquerade clients towards the home LAN
HOME_ALLOW_TCP="80 443"             # local TCP ports reachable on the relay host
HOME_ALLOW_UDP="53"                # local UDP ports reachable on the relay host
HOME_LAN_IFACE=""                  # auto

# fixed paths
RELAY_DIR="/etc/wireguard-relay"
STATE_FILE="$RELAY_DIR/state.env"
FW_FILE="$RELAY_DIR/firewall.nft"
UDP2RAW_CONF="$RELAY_DIR/udp2raw.conf"
FW_HELPER="/usr/local/sbin/wg-relay-fw"
UNIT_UDP2RAW="udp2raw-relay.service"
UNIT_FW="wg-relay-firewall.service"
BACKUP_ROOT="/var/backups/wg-relay"
SNAP_DIR="$RELAY_DIR/baseline"      # where --snapshot stores its baseline

# runtime flags
DRY_RUN=0
ASSUME_YES=0
CHECK_ONLY=0
UNINSTALL=0
FORCE=0
STATUS_ONLY=0
SNAPSHOT_ONLY=0
DIFF_ONLY=0
PEER_PUBKEY=""
PEER_SSH=""
CONFIG_FILE=""
declare -A OVERRIDES=()
UDP2RAW=""

# ----------------------------------------------------------------------------
# output helpers
# ----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_R=$'\033[0m'; C_G=$'\033[1;32m'; C_Y=$'\033[1;33m'; C_RD=$'\033[1;31m'
    C_C=$'\033[1;36m'; C_B=$'\033[1m'
else
    C_R=""; C_G=""; C_Y=""; C_RD=""; C_C=""; C_B=""
fi

log()  { printf '%s==>%s %s\n' "$C_G" "$C_R" "$*"; }
step() { printf '\n%s━━ %s%s\n' "$C_C" "$*" "$C_R"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '%s[✓]%s %s\n' "$C_G" "$C_R" "$*"; }
warn() { printf '%s[!]%s %s\n' "$C_Y" "$C_R" "$*" >&2; }
die()  { printf '%s[✗]%s %s\n' "$C_RD" "$C_R" "$*" >&2; exit 1; }

trap 'rc=$?; printf "\n%s[✗]%s %s line %s failed (exit %s)\n" "$C_RD" "$C_R" "${BASH_SOURCE[0]##*/}" "$LINENO" "$rc" >&2' ERR

usage() {
    cat <<EOF
${C_B}wg-relay-setup.sh $VERSION${C_R}
WireGuard entry-VPS + home-server relay over udp2raw, with the
wireguard-dashboard on the VPS.

${C_B}USAGE${C_R}
  sudo ./wg-relay-setup.sh --role home [--config relay.conf] [options]
  sudo ./wg-relay-setup.sh --role vps  [--config relay.conf] [options]

${C_B}ROLES${C_R}
  home   run on the machine that runs the service (behind the public IP).
         Creates wgrelay0 + udp2raw server + isolation firewall.
  vps    run on the public VPS. Creates wgvpn0 (dashboard) + wgrelay0 uplink
         + udp2raw client + isolation firewall + the dashboard itself.

${C_B}OPTIONS${C_R}
  --role home|vps           which side to configure (required)
  --config FILE             config file (default: ./relay.conf, then
                            /etc/wireguard-relay/relay.conf)
  --peer-pubkey KEY         WireGuard public key of the other side
  --peer-ssh USER@HOST      (role vps) log in to the home server over ssh and
                            exchange keys automatically (run after the first
                            run on the home side)
  --set KEY=VALUE           override any config variable, repeatable
  --check                   run every validation, change nothing
  --status                  show what is configured / running right now
  --snapshot                record the current state (ports, routes, firewall,
                            services) so you can prove later what changed
  --diff                    compare the current state with that snapshot
  --uninstall               remove everything this script created
  --purge                   ... including the dashboard and wgvpn0 (VPS)
  --dry-run                 print what would be done
  -y, --yes                 do not ask for confirmation
  --force                   continue even if a check fails (be careful)
  --show-defaults           print every variable with its current value
  -h, --help                this help

${C_B}CONVENIENCE FLAGS${C_R}
  --home-public-ip IP    --vps-public-ip IP    --home-dests LIST
  --home-dns IP          --relay-port PORT     --mode faketcp|udp|icmp
  --home-allow-tcp LIST  --home-allow-udp LIST  (ports the home side may use)
  --udp2raw-key KEY      --client-net CIDR     --client-listen-port PORT
  --dash-domain DOMAIN   --dash-email MAIL     --dash-port PORT
  --dash-bind ADDR       --dash-user USER      --dash-pass PASS
  --dash-telegram-token TOKEN                  --dash-telegram-admin ID
EOF
}

show_defaults() {
    cat <<EOF
# ---- relay.conf ----------------------------------------------------------
HOME_PUBLIC_IP="$HOME_PUBLIC_IP"
VPS_PUBLIC_IP="$VPS_PUBLIC_IP"
HOME_DESTS="$HOME_DESTS"
HOME_DNS="$HOME_DNS"
RELAY_PORT=$RELAY_PORT
UDP2RAW_MODE="$UDP2RAW_MODE"
UDP2RAW_KEY="$UDP2RAW_KEY"
UDP2RAW_BIN="$UDP2RAW_BIN"
UDP2RAW_EXTRA_ARGS="$UDP2RAW_EXTRA_ARGS"
UDP2RAW_LOCAL_PORT=$UDP2RAW_LOCAL_PORT
WG_RELAY_LOCAL_PORT=$WG_RELAY_LOCAL_PORT
CLIENT_IFACE="$CLIENT_IFACE"
RELAY_IFACE="$RELAY_IFACE"
CLIENT_NET="$CLIENT_NET"
RELAY_VPS_IP="$RELAY_VPS_IP"
RELAY_HOME_IP="$RELAY_HOME_IP"
CLIENT_LISTEN_PORT=$CLIENT_LISTEN_PORT
RELAY_MTU=$RELAY_MTU
CLIENT_MTU=$CLIENT_MTU
MSS_CLAMP=$MSS_CLAMP
DASH_PORT=$DASH_PORT
DASH_BIND="$DASH_BIND"
DASH_DOMAIN="$DASH_DOMAIN"
DASH_EMAIL="$DASH_EMAIL"
DASH_USER="$DASH_USER"
DASH_PASS="$DASH_PASS"
DASH_ALLOW_FROM="$DASH_ALLOW_FROM"
DASH_TELEGRAM_TOKEN="$DASH_TELEGRAM_TOKEN"
DASH_TELEGRAM_ADMIN="$DASH_TELEGRAM_ADMIN"
HOME_SNAT="$HOME_SNAT"
HOME_ALLOW_TCP="$HOME_ALLOW_TCP"
HOME_ALLOW_UDP="$HOME_ALLOW_UDP"
HOME_LAN_IFACE="$HOME_LAN_IFACE"
EOF
}

# ----------------------------------------------------------------------------
# argument parsing
# ----------------------------------------------------------------------------
need_val() { [[ $# -ge 2 && -n ${2:-} ]] || die "option $1 needs a value"; }

while (( $# )); do
    case "$1" in
        --role)                  need_val "$@"; ROLE="$2"; shift 2 ;;
        --role=*)                ROLE="${1#*=}"; shift ;;
        --config)                need_val "$@"; CONFIG_FILE="$2"; shift 2 ;;
        --config=*)              CONFIG_FILE="${1#*=}"; shift ;;
        --peer-pubkey)           need_val "$@"; PEER_PUBKEY="$2"; shift 2 ;;
        --peer-pubkey=*)         PEER_PUBKEY="${1#*=}"; shift ;;
        --peer-ssh)              need_val "$@"; PEER_SSH="$2"; shift 2 ;;
        --peer-ssh=*)            PEER_SSH="${1#*=}"; shift ;;
        --set)                   need_val "$@"
                                 [[ $2 == *=* ]] || die "--set needs KEY=VALUE"
                                 OVERRIDES["${2%%=*}"]="${2#*=}"; shift 2 ;;
        --set=*)                 _kv="${1#*=}"
                                 [[ $_kv == *=* ]] || die "--set needs KEY=VALUE"
                                 OVERRIDES["${_kv%%=*}"]="${_kv#*=}"; shift ;;
        --check)                 CHECK_ONLY=1; shift ;;
        --status)                STATUS_ONLY=1; shift ;;
        --snapshot)              SNAPSHOT_ONLY=1; shift ;;
        --diff)                  DIFF_ONLY=1; shift ;;
        --uninstall)             UNINSTALL=1; shift ;;
        --purge)                 UNINSTALL=2; shift ;;
        --dry-run)               DRY_RUN=1; shift ;;
        -y|--yes)                ASSUME_YES=1; shift ;;
        --force)                 FORCE=1; shift ;;
        --home-public-ip)        need_val "$@"; OVERRIDES[HOME_PUBLIC_IP]="$2"; shift 2 ;;
        --vps-public-ip)         need_val "$@"; OVERRIDES[VPS_PUBLIC_IP]="$2"; shift 2 ;;
        --home-dests)            need_val "$@"; OVERRIDES[HOME_DESTS]="$2"; shift 2 ;;
        --home-dns)              need_val "$@"; OVERRIDES[HOME_DNS]="$2"; shift 2 ;;
        --relay-port)            need_val "$@"; OVERRIDES[RELAY_PORT]="$2"; shift 2 ;;
        --mode)                  need_val "$@"; OVERRIDES[UDP2RAW_MODE]="$2"; shift 2 ;;
        --udp2raw-key)           need_val "$@"; OVERRIDES[UDP2RAW_KEY]="$2"; shift 2 ;;
        --client-net)            need_val "$@"; OVERRIDES[CLIENT_NET]="$2"; shift 2 ;;
        --client-listen-port)    need_val "$@"; OVERRIDES[CLIENT_LISTEN_PORT]="$2"; shift 2 ;;
        --dash-domain)           need_val "$@"; OVERRIDES[DASH_DOMAIN]="$2"; shift 2 ;;
        --dash-email)            need_val "$@"; OVERRIDES[DASH_EMAIL]="$2"; shift 2 ;;
        --dash-port)             need_val "$@"; OVERRIDES[DASH_PORT]="$2"; shift 2 ;;
        --home-allow-tcp)        need_val "$@"; OVERRIDES[HOME_ALLOW_TCP]="$2"; shift 2 ;;
        --home-allow-udp)        need_val "$@"; OVERRIDES[HOME_ALLOW_UDP]="$2"; shift 2 ;;
        --dash-bind)             need_val "$@"; OVERRIDES[DASH_BIND]="$2"; shift 2 ;;
        --dash-user)             need_val "$@"; OVERRIDES[DASH_USER]="$2"; shift 2 ;;
        --dash-pass)             need_val "$@"; OVERRIDES[DASH_PASS]="$2"; shift 2 ;;
        --dash-telegram-token)   need_val "$@"; OVERRIDES[DASH_TELEGRAM_TOKEN]="$2"; shift 2 ;;
        --dash-telegram-admin)   need_val "$@"; OVERRIDES[DASH_TELEGRAM_ADMIN]="$2"; shift 2 ;;
        --show-defaults)         show_defaults; exit 0 ;;
        -h|--help)               usage; exit 0 ;;
        -V|--version)            echo "$VERSION"; exit 0 ;;
        *)                       die "unknown option: $1 (try --help)" ;;
    esac
done

# ----------------------------------------------------------------------------
# config file: values from the file are the defaults, CLI overrides win
# ----------------------------------------------------------------------------
if [[ -z $CONFIG_FILE ]]; then
    if [[ -r ./relay.conf ]]; then
        CONFIG_FILE="$PWD/relay.conf"
    else
        CONFIG_FILE="$RELAY_DIR/relay.conf"
    fi
fi

# The master copy always lives at $RELAY_DIR/relay.conf, because the ssh
# key-exchange (--peer-ssh) and later re-runs read it from there. A file given
# with --config is adopted as the master copy on the first run.
ADOPTED_CONFIG=""
if [[ $CONFIG_FILE != "$RELAY_DIR/relay.conf" && -r $CONFIG_FILE && ! -e $RELAY_DIR/relay.conf ]]; then
    if (( ! CHECK_ONLY && ! DRY_RUN )); then
        mkdir -p "$RELAY_DIR" 2>/dev/null || true
        chmod 700 "$RELAY_DIR" 2>/dev/null || true
        if cp -a "$CONFIG_FILE" "$RELAY_DIR/relay.conf" 2>/dev/null; then
            ADOPTED_CONFIG="$CONFIG_FILE"
        fi
    fi
fi
if [[ -r $RELAY_DIR/relay.conf ]]; then
    CONFIG_FILE="$RELAY_DIR/relay.conf"
    if [[ -n $ADOPTED_CONFIG ]]; then
        info "config adopted: $ADOPTED_CONFIG -> $RELAY_DIR/relay.conf (master copy)"
    fi
elif [[ ! -r $CONFIG_FILE ]]; then
    CONFIG_FILE="$RELAY_DIR/relay.conf"
fi

if [[ -r $CONFIG_FILE ]]; then
    set -a
    # shellcheck disable=SC1090,SC1091
    . "$CONFIG_FILE"
    set +a
fi

for _k in "${!OVERRIDES[@]}"; do
    if [[ ! $_k =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        die "--set: '$_k' is not a valid variable name"
    fi
    case $_k in
        ROLE|CONFIG_FILE|SELF|PROG|RELAY_DIR|STATE_FILE|FW_FILE|UDP2RAW_CONF|FW_HELPER)
            die "--set $_k is not allowed" ;;
    esac
    printf -v "$_k" '%s' "${OVERRIDES[$_k]}"
done
unset _k 2>/dev/null || true
unset _kv 2>/dev/null || true

# ----------------------------------------------------------------------------
# generic helpers
# ----------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

run() {
    if (( DRY_RUN )); then
        printf '    [dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

run_sh() {
    if (( DRY_RUN )); then
        printf '    [dry-run] sh -c %q\n' "$*"
        return 0
    fi
    bash -c "$*"
}

confirm() {
    (( ASSUME_YES )) && return 0
    (( DRY_RUN )) && return 0
    local a=""
    if [[ -r /dev/tty ]]; then
        printf '%s [y/N]: ' "$1"
        read -r a </dev/tty || true
    else
        die "$1 (no tty, use --yes)"
    fi
    [[ ${a,,} == y* ]]
}

rand_alnum() {
    local n="${1:-24}" out
    out=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$n" || true)
    printf '%s' "$out"
}

require_root() {
    if (( CHECK_ONLY )); then
        if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
            warn "running --check as a normal user: some checks (interfaces, routes) may be incomplete"
        fi
        return 0
    fi
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "run me as root: sudo $PROG --role ${ROLE:-home} ..."
}

require_linux() {
    [[ $(uname -s) == Linux ]] || die "this script is for Linux only"
}

backup_file() {
    local f=$1
    [[ -e $f ]] || return 0
    local d
    d="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
    run mkdir -p "$d"
    run cp -a "$f" "$d/" || warn "could not back up $f"
    info "backup: $d/$(basename "$f")"
}

# ----------------------------------------------------------------------------
# CIDR helpers (python3 keeps the maths honest)
# ----------------------------------------------------------------------------
py3() { have python3 || die "python3 is required"; python3 - "$@"; }

norm_cidrs() {   # "a,b c" -> "a, b, c" (canonicalised); dies on invalid input
    py3 "$@" <<'PY'
import ipaddress, re, sys
out = []
for chunk in sys.argv[1:]:
    for tok in re.split(r"[,\s]+", chunk or ""):
        if not tok:
            continue
        try:
            out.append(str(ipaddress.ip_network(tok, strict=False)))
        except ValueError:
            sys.stderr.write("invalid CIDR: %s\n" % tok)
            sys.exit(2)
print(", ".join(out))
PY
}

cidrs_overlap() {   # rc 0 when the two networks overlap
    py3 "$1" "$2" <<'PY'
import ipaddress, sys
a = ipaddress.ip_network(sys.argv[1], strict=False)
b = ipaddress.ip_network(sys.argv[2], strict=False)
sys.exit(0 if a.version == b.version and a.overlaps(b) else 1)
PY
}

cidr_holds_ip() {
    py3 "$1" "$2" <<'PY'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=False)
sys.exit(0 if ipaddress.ip_address(sys.argv[2]) in net else 1)
PY
}

cidr_gateway() {   # "10.66.0.0/24" -> "10.66.0.1/24"
    py3 "$1" <<'PY'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=False)
first = next(net.hosts(), None)
if first is None:
    sys.exit(1)
print("%s/%d" % (first, net.prefixlen))
PY
}

cidrs_hold_ip() {   # $1 = "a/24, b/32"   $2 = ip
    py3 "$1" "$2" <<'PY'
import ipaddress, re, sys
try:
    ip = ipaddress.ip_address(sys.argv[2])
except ValueError:
    sys.exit(2)
for tok in re.split(r"[,\s]+", sys.argv[1]):
    if not tok:
        continue
    try:
        if ip in ipaddress.ip_network(tok, strict=False):
            sys.exit(0)
    except ValueError:
        continue
sys.exit(1)
PY
}

# filter stdin lines: keep every "network ..." entry that overlaps $1
# (uses -c so that stdin stays connected to the pipe)
overlap_routes() {
    have python3 || die "python3 is required"
    python3 -c '
import ipaddress, sys
target = ipaddress.ip_network(sys.argv[1], strict=False)
for line in sys.stdin:
    tok = line.split()
    if not tok:
        continue
    try:
        net = ipaddress.ip_network(tok[0], strict=False)
    except ValueError:
        continue
    if net.version != target.version or net.prefixlen == 0:
        continue
    if net.overlaps(target):
        print(line.rstrip())
' "$1"
}

# ----------------------------------------------------------------------------
# ports
# ----------------------------------------------------------------------------
udp_ports() { ss -H -lun 2>/dev/null | awk '{print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un; }
tcp_ports() { ss -H -ltn 2>/dev/null | awk '{print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un; }

# ports already handed out during this run (never hand out one twice)
RESERVED_PORTS=""

# a port that belongs to this very setup (state file, generated configs, the
# dashboard config) — those may be "busy" simply because our own services use
# them, and re-runs must keep using the same numbers.
port_is_ours() {
    local p=$1
    if [[ -r $STATE_FILE ]] && grep -qE "=${p}\$" "$STATE_FILE"; then return 0; fi
    if [[ -r /etc/wireguard/${RELAY_IFACE}.conf ]] \
       && grep -qE "^[[:space:]]*ListenPort[[:space:]]*=[[:space:]]*${p}[[:space:]]*\$" "/etc/wireguard/${RELAY_IFACE}.conf"; then
        return 0
    fi
    if [[ -r /etc/wireguard/${CLIENT_IFACE}.conf ]] \
       && grep -qE "^[[:space:]]*ListenPort[[:space:]]*=[[:space:]]*${p}[[:space:]]*\$" "/etc/wireguard/${CLIENT_IFACE}.conf"; then
        return 0
    fi
    if [[ -r $UDP2RAW_CONF ]] && grep -qE "^--?[lr][[:space:]]+[^[:space:]]*:${p}\$" "$UDP2RAW_CONF"; then
        return 0
    fi
    if [[ -r /etc/wireguard-dashboard/config.json ]] \
       && grep -qE "\"listen_port\"[[:space:]]*:[[:space:]]*${p}" /etc/wireguard-dashboard/config.json; then
        return 0
    fi
    return 1
}

port_busy() {   # $1 port  $2 udp|tcp
    local list
    case " $RESERVED_PORTS " in
        *" $1 "*) return 0 ;;
    esac
    if [[ $2 == udp ]]; then list=$(udp_ports); else list=$(tcp_ports); fi
    grep -qx "$1" <<<"$list"
}

pick_port() {   # $1 udp|tcp  $2 first  $3 last
    local p
    for (( p=$2; p<=$3; p++ )); do
        if ! port_busy "$p" "$1"; then printf '%s' "$p"; return 0; fi
    done
    return 1
}

# resolve_port VARNAME udp|tcp "description"
# keeps the value in the caller's shell so no port is handed out twice
resolve_port() {
    local var=$1 proto=$2 what=$3 val
    eval "val=\${$var:-0}"
    if [[ $val =~ ^[0-9]+$ ]] && (( val > 0 )); then
        if [[ " $RESERVED_PORTS " == *" $val "* ]]; then
            die "$what: port $val is already used by another part of this setup"
        fi
        if port_busy "$val" "$proto"; then
            if port_is_ours "$val"; then
                info "$what: reusing port $val (already belongs to this setup)"
            elif (( ! FORCE )); then
                die "$what: port $val/$proto is already in use on this machine (choose another one or --force)"
            fi
        fi
        RESERVED_PORTS+=" $val"
        printf -v "$var" '%s' "$val"
        return 0
    fi
    val=$(pick_port "$proto" 20000 60999) || die "no free $proto port found"
    RESERVED_PORTS+=" $val"
    printf -v "$var" '%s' "$val"
    return 0
}

# ----------------------------------------------------------------------------
# packages / system
# ----------------------------------------------------------------------------
PKG=""
detect_pkg() {
    if have apt-get; then PKG=apt
    elif have dnf; then PKG=dnf
    elif have yum; then PKG=yum
    elif have pacman; then PKG=pacman
    else die "unsupported distribution (need apt, dnf, yum or pacman)"
    fi
}

pkg_refreshed=0
pkg_refresh() {
    (( pkg_refreshed )) && return 0
    pkg_refreshed=1
    (( DRY_RUN )) && return 0
    case $PKG in
        apt) apt-get update -qq || warn "apt-get update failed (continuing)" ;;
        dnf|yum) "$PKG" -q makecache || true ;;
        pacman) pacman -Sy --noconfirm >/dev/null || true ;;
    esac
}

pkg_install() {
    (( $# )) || return 0
    case $PKG in
        apt)  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "$@" ;;
        dnf|yum) run "$PKG" install -y -q "$@" ;;
        pacman) run pacman -S --noconfirm --needed "$@" ;;
    esac
}

ensure_cmd() {   # $1 package, $2 command (default $1)
    local pkg=$1 cmd=${2:-$1}
    if have "$cmd"; then
        (( CHECK_ONLY )) && info "ok       : $cmd"
        return 0
    fi
    if (( CHECK_ONLY )); then
        info "missing  : $cmd  (would install package '$pkg')"
        MISSING_PKGS+=("$pkg")
        return 0
    fi
    log "installing $pkg"
    pkg_refresh
    pkg_install "$pkg"
    have "$cmd" || die "package '$pkg' did not provide the command '$cmd'"
}

ensure_ip_forward() {
    local cur
    cur=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)
    if [[ $cur == 1 ]]; then
        info "net.ipv4.ip_forward = 1 (already)"
        return 0
    fi
    log "enabling net.ipv4.ip_forward"
    if ! run_ok sysctl -w net.ipv4.ip_forward=1; then
        cat >&2 <<EOF

${C_Y}[!]${C_R} This machine does not allow changing net.ipv4.ip_forward
    (typical for an unprivileged Proxmox LXC). Fix it from the Proxmox host:

      # inside the LXC config (/etc/pve/lxc/<id>.conf) add:
      lxc.sysctl.net.ipv4.ip_forward = 1

      # then: pct stop <id> && pct start <id>
      # or on the Proxmox host:  sysctl -w net.ipv4.ip_forward=1

EOF
        (( FORCE )) || die "ip_forward could not be enabled"
    fi
    run mkdir -p /etc/sysctl.d
    if ! grep -qs '^net.ipv4.ip_forward *= *1' /etc/sysctl.d/90-wg-relay.conf 2>/dev/null; then
        if (( ! DRY_RUN )); then
            printf '# wg-relay\nnet.ipv4.ip_forward=1\n' > /etc/sysctl.d/90-wg-relay.conf
        fi
    fi
}

run_ok() {   # like run(), but reports success/failure
    (( DRY_RUN )) && return 0
    "$@" >/dev/null 2>&1
}

systemd_enable_now() {
    run systemctl enable "$1" >/dev/null 2>&1 || true
    run systemctl restart "$1"
}

systemd_restart_if_exists() {
    systemctl list-unit-files "$1" >/dev/null 2>&1 || return 0
    run systemctl restart "$1"
}

check_wireguard_kernel() {
    modprobe wireguard >/dev/null 2>&1 || true
    if (( DRY_RUN )); then
        info "wireguard kernel support: not tested (dry-run)"
        return 0
    fi
    if ip link add wg-relay-probe0 type wireguard 2>/dev/null; then
        ip link del wg-relay-probe0 2>/dev/null || true
        info "wireguard kernel support: ok"
        return 0
    fi
    cat >&2 <<EOF

${C_Y}[!]${C_R} Cannot create a WireGuard interface on this machine.
    * Proxmox LXC: the WireGuard module must be loaded on the Proxmox HOST
      (modprobe wireguard) and the container needs NET_ADMIN. In
      /etc/pve/lxc/<id>.conf use an unprivileged container with:
          features: nesting=1
      and make sure 'net_admin' is not dropped from its capability set.
    * Kernel older than 5.6 without the wireguard module: install
      wireguard-dkms, or run this machine as a VM instead of a container.

EOF
    (( FORCE )) || die "WireGuard is not usable here"
}

# ----------------------------------------------------------------------------
# state + config persistence
# ----------------------------------------------------------------------------
state_get() {   # $1 = variable name inside $STATE_FILE
    local name=$1
    [[ -r $STATE_FILE ]] || return 1
    ( set +u
      # shellcheck disable=SC1090
      . "$STATE_FILE" >/dev/null 2>&1 || true
      eval "printf '%s' \"\${$name-}\"" )
}

state_write() {
    [[ $DRY_RUN == 1 ]] && { info "[dry-run] would write $STATE_FILE"; return 0; }
    run mkdir -p "$RELAY_DIR"
    run chmod 700 "$RELAY_DIR"
    local tmp; tmp=$(mktemp)
    {
        echo "# generated by $PROG $VERSION on $(date -Is) — do not edit"
        echo "ROLE=$(printf '%q' "$ROLE")"
        echo "HOME_DESTS=$(printf '%q' "$HOME_DESTS_LIST")"
        echo "CLIENT_NET=$(printf '%q' "$CLIENT_NET")"
        echo "CLIENT_IFACE=$(printf '%q' "$CLIENT_IFACE")"
        echo "RELAY_IFACE=$(printf '%q' "$RELAY_IFACE")"
        echo "RELAY_VPS_IP=$(printf '%q' "$RELAY_VPS_IP")"
        echo "RELAY_HOME_IP=$(printf '%q' "$RELAY_HOME_IP")"
        echo "RELAY_PORT=$(printf '%q' "$RELAY_PORT")"
        echo "UDP2RAW_MODE=$(printf '%q' "$UDP2RAW_MODE")"
        echo "UDP2RAW_KEY=$(printf '%q' "$UDP2RAW_KEY")"
        echo "UDP2RAW_LOCAL_PORT=$(printf '%q' "$UDP2RAW_LOCAL_PORT")"
        echo "WG_RELAY_LOCAL_PORT=$(printf '%q' "$WG_RELAY_LOCAL_PORT")"
        echo "CLIENT_LISTEN_PORT=$(printf '%q' "$CLIENT_LISTEN_PORT")"
        echo "HOME_PUBKEY=$(printf '%q' "${HOME_PUBKEY:-}")"
        echo "VPS_PUBKEY=$(printf '%q' "${VPS_PUBKEY:-}")"
        echo "PEER_PUBKEY=$(printf '%q' "${PEER_PUBKEY:-}")"
        echo "HOME_PUBLIC_IP=$(printf '%q' "$HOME_PUBLIC_IP")"
        echo "VPS_PUBLIC_IP=$(printf '%q' "$VPS_PUBLIC_IP")"
    } >"$tmp"
    mv "$tmp" "$STATE_FILE"
    chmod 600 "$STATE_FILE"
}

# One installation per machine.
# The unit names (udp2raw-relay.service / wg-relay-firewall.service), the state
# directory (/etc/wireguard-relay) and the nft table (inet wgrelay) are fixed, so
# a second, differently addressed installation on the same host would silently
# take over the first one. Refuse instead of breaking a working tunnel.
assert_single_instance() {
    [[ -r $STATE_FILE ]] || return 0
    local p_role p_net p_ciface p_rface p_vps p_home
    p_role=$(state_get ROLE)        || p_role=""
    p_net=$(state_get CLIENT_NET)   || p_net=""
    p_ciface=$(state_get CLIENT_IFACE) || p_ciface=""
    p_rface=$(state_get RELAY_IFACE)   || p_rface=""
    p_vps=$(state_get RELAY_VPS_IP)    || p_vps=""
    p_home=$(state_get RELAY_HOME_IP)  || p_home=""

    local -a differs=()
    if [[ -n $p_net    && $p_net    != "$CLIENT_NET"    ]]; then differs+=("CLIENT_NET      $p_net  vs  $CLIENT_NET"); fi
    if [[ -n $p_ciface && $p_ciface != "$CLIENT_IFACE"  ]]; then differs+=("CLIENT_IFACE    $p_ciface  vs  $CLIENT_IFACE"); fi
    if [[ -n $p_rface  && $p_rface  != "$RELAY_IFACE"   ]]; then differs+=("RELAY_IFACE     $p_rface  vs  $RELAY_IFACE"); fi
    if [[ -n $p_vps    && $p_vps    != "$RELAY_VPS_IP"  ]]; then differs+=("RELAY_VPS_IP    $p_vps  vs  $RELAY_VPS_IP"); fi
    if [[ -n $p_home   && $p_home   != "$RELAY_HOME_IP" ]]; then differs+=("RELAY_HOME_IP   $p_home  vs  $RELAY_HOME_IP"); fi
    if [[ ${#differs[@]} -eq 0 ]]; then return 0; fi

    local msg="a different wg-relay installation already exists on this machine (role ${p_role:-?}).
    The unit names, $RELAY_DIR and the nft table are fixed, so two installations
    on one machine would overwrite each other."
    if (( CHECK_ONLY )); then
        warn "${msg//$'\n'/$'\n    '}"
        printf '    - %s\n' "${differs[@]}"
        warn "the run would stop here — use the existing values, --uninstall first, or another machine"
        return 0
    fi
    printf '\n' >&2
    printf '%s[✗]%s %s\n' "$C_RD" "$C_R" "$msg" >&2
    printf '    - %s\n' "${differs[@]}" >&2
    printf '\n' >&2
    printf '    Options:\n' >&2
    printf '      1. keep the values that are already configured (re-run without --set)\n' >&2
    printf '      2. sudo %s --uninstall   (removes this installation, then re-run)\n' "$PROG" >&2
    printf '      3. run the second tunnel on a different machine\n' >&2
    printf '      4. --force to overwrite anyway (the other tunnel will stop working)\n\n' >&2
    if (( FORCE )); then
        warn "--force given: continuing, the existing installation will be replaced"
        return 0
    fi
    exit 1
}

# update (or append) KEY=VALUE inside the config file, so the file can simply
# be copied to the other machine
config_upsert() {
    local key=$1 val=$2 file=$CONFIG_FILE
    (( DRY_RUN )) && { info "[dry-run] would set $key in $file"; return 0; }
    [[ -n $file ]] || return 0
    mkdir -p "$(dirname "$file")"
    [[ -f $file ]] || printf '# wg-relay configuration — generated/updated by %s\n' "$PROG" >"$file"
    local tmp; tmp=$(mktemp)
    grep -v -E "^[[:space:]]*${key}=" "$file" >"$tmp" || true
    printf '%s="%s"\n' "$key" "$val" >>"$tmp"
    cat "$tmp" >"$file"
    rm -f "$tmp"
    chmod 600 "$file"
}

# ----------------------------------------------------------------------------
# WireGuard keys
# ----------------------------------------------------------------------------
gen_keypair() {   # prints "priv pub"
    local priv pub
    priv=$(wg genkey)
    pub=$(printf '%s\n' "$priv" | wg pubkey)
    printf '%s %s\n' "$priv" "$pub"
}

conf_privkey() {
    [[ -r $1 ]] || return 0
    # NB: never split on '=' — a WireGuard key is base64 and the padding '='
    # belongs to it (43 chars instead of 44 => "Key is not the correct length").
    awk '/^[[:space:]]*[Pp]rivate[[:space:]]*[Kk]ey[[:space:]]*=/ {
             sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]\r]+$/, ""); print; exit }' \
        "$1" 2>/dev/null || true
}

conf_listen_port() {
    [[ -r $1 ]] || return 0
    awk -F' *= *' 'tolower($1)=="listenport" {print $2; exit}' "$1" 2>/dev/null || true
}

dash_listen_port() {
    [[ -r /etc/wireguard-dashboard/config.json ]] || return 0
    python3 -c 'import json;print(json.load(open("/etc/wireguard-dashboard/config.json")).get("listen_port", 0))' 2>/dev/null || true
}

persist_values() {   # keep every chosen port in the config file
    config_upsert RELAY_PORT "$RELAY_PORT"
    config_upsert UDP2RAW_MODE "$UDP2RAW_MODE"
    config_upsert HOME_DESTS "$HOME_DESTS_LIST"
    config_upsert CLIENT_NET "$CLIENT_NET"
    if [[ -n $UDP2RAW_KEY ]]; then config_upsert UDP2RAW_KEY "$UDP2RAW_KEY"; fi
    if [[ -n $HOME_PUBLIC_IP ]]; then config_upsert HOME_PUBLIC_IP "$HOME_PUBLIC_IP"; fi
    if [[ -n $VPS_PUBLIC_IP ]]; then config_upsert VPS_PUBLIC_IP "$VPS_PUBLIC_IP"; fi
    if [[ $ROLE == home ]]; then
        if (( WG_RELAY_LOCAL_PORT > 0 )); then config_upsert WG_RELAY_LOCAL_PORT "$WG_RELAY_LOCAL_PORT"; fi
    else
        if (( UDP2RAW_LOCAL_PORT > 0 )); then config_upsert UDP2RAW_LOCAL_PORT "$UDP2RAW_LOCAL_PORT"; fi
        if (( CLIENT_LISTEN_PORT > 0 )); then config_upsert CLIENT_LISTEN_PORT "$CLIENT_LISTEN_PORT"; fi
        if (( DASH_PORT > 0 )); then config_upsert DASH_PORT "$DASH_PORT"; fi
    fi
}

conf_peer_pubkey() {   # first PublicKey of the first [Peer] block
    [[ -r $1 ]] || return 0
    awk 'BEGIN{inpeer=0}
         /^[[:space:]]*\[/{inpeer=($0 ~ /^[[:space:]]*\[Peer\]/); next}
         inpeer && $0 ~ /^[[:space:]]*PublicKey/ { sub(/^[^=]*=[[:space:]]*/, ""); print; exit }' \
        "$1" 2>/dev/null || true
}

iface_valid() {
    [[ $1 =~ ^[A-Za-z0-9_=+.-]{1,15}$ ]] || die "'$1' is not a valid interface name (max 15 chars)"
    [[ $1 != lo ]] || die "interface name 'lo' is not allowed"
}

# ----------------------------------------------------------------------------
# firewall: nftables table `inet wgrelay` + iptables compatibility shims
# ----------------------------------------------------------------------------
write_fw_helper() {
    (( DRY_RUN )) && { info "[dry-run] would write $FW_HELPER"; return 0; }
    cat >"$FW_HELPER" <<'HELPER'
#!/bin/sh
# Generated by wg-relay-setup.sh — overwritten on every run, do not edit.
# Applies the wg-relay isolation rules (nftables) plus iptables ACCEPT shims
# for hosts whose FORWARD chain has a DROP policy (ufw, docker, firewalld).
set -u
FW=/etc/wireguard-relay/firewall.nft
STATE=/etc/wireguard-relay/state.env
CLIENT_IFACE=""
RELAY_IFACE=""
[ -r "$STATE" ] && . "$STATE"

apply_nft() {
    [ -r "$FW" ] || return 0
    command -v nft >/dev/null 2>&1 || return 0
    nft -f "$FW"
}

ipt_add() {
    command -v iptables >/dev/null 2>&1 || return 0
    iptables -C FORWARD "$@" >/dev/null 2>&1 && return 0
    iptables -I FORWARD 1 "$@" >/dev/null 2>&1 || return 0
}

ipt_del() {
    command -v iptables >/dev/null 2>&1 || return 0
    while iptables -C FORWARD "$@" >/dev/null 2>&1; do
        iptables -D FORWARD "$@" >/dev/null 2>&1 || break
    done
}

apply_ipt() {
    if [ -n "$CLIENT_IFACE" ]; then
        ipt_add -i "$CLIENT_IFACE" -m comment --comment wg-relay -j ACCEPT
        ipt_add -o "$CLIENT_IFACE" -m comment --comment wg-relay -j ACCEPT
    fi
    if [ -n "$RELAY_IFACE" ]; then
        ipt_add -i "$RELAY_IFACE" -m comment --comment wg-relay -j ACCEPT
        ipt_add -o "$RELAY_IFACE" -m comment --comment wg-relay -j ACCEPT
    fi
}

flush_ipt() {
    if [ -n "$CLIENT_IFACE" ]; then
        ipt_del -i "$CLIENT_IFACE" -m comment --comment wg-relay -j ACCEPT
        ipt_del -o "$CLIENT_IFACE" -m comment --comment wg-relay -j ACCEPT
    fi
    if [ -n "$RELAY_IFACE" ]; then
        ipt_del -i "$RELAY_IFACE" -m comment --comment wg-relay -j ACCEPT
        ipt_del -o "$RELAY_IFACE" -m comment --comment wg-relay -j ACCEPT
    fi
}

case "${1:-apply}" in
    apply) apply_nft; apply_ipt ;;
    flush) command -v nft >/dev/null 2>&1 && nft delete table inet wgrelay 2>/dev/null; flush_ipt ;;
    *) echo "usage: $0 apply|flush" >&2; exit 2 ;;
esac
HELPER
    chmod 755 "$FW_HELPER"
}

write_fw_unit() {
    (( DRY_RUN )) && { info "[dry-run] would write /etc/systemd/system/$UNIT_FW"; return 0; }
    cat >"/etc/systemd/system/$UNIT_FW" <<EOF
[Unit]
Description=wg-relay isolation firewall ($FW_FILE)
Documentation=file://$RELAY_DIR
After=network-pre.target
Wants=network-pre.target
Before=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$FW_HELPER apply
ExecReload=$FW_HELPER apply
ExecStop=$FW_HELPER flush

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "/etc/systemd/system/$UNIT_FW"
}

# --- ruleset for the VPS side -------------------------------------------------
write_fw_rules_vps() {
    local elems; elems=$(printf '%s' "$HOME_DESTS_LIST")   # "10.10.0.0/24, 10.10.0.5/32"
    (( DRY_RUN )) && { info "[dry-run] would write $FW_FILE (vps)"; return 0; }
    cat >"$FW_FILE" <<EOF
#!/usr/sbin/nft -f
# Generated by $PROG $VERSION — overwritten on every run, do not edit.
# Only traffic of THIS relay is matched; every other packet on this host is
# left completely alone (policy accept, no other table is touched).
add table inet wgrelay
delete table inet wgrelay
table inet wgrelay {
	comment "wg-relay: entry VPS isolation"

	set home_nets {
		type ipv4_addr
		flags interval
		elements = { $elems }
	}

	# -- what clients may forward ------------------------------------------
	chain forward {
		type filter hook forward priority filter; policy accept;

		iifname "$CLIENT_IFACE" oifname "$RELAY_IFACE" ip daddr @home_nets accept
		iifname "$CLIENT_IFACE" drop comment "clients may only reach home_nets via $RELAY_IFACE"
		iifname "$RELAY_IFACE" oifname "$CLIENT_IFACE" accept comment "return traffic from home"
		iifname "$RELAY_IFACE" drop comment "the relay link must not reach the VPS network"
	}

	# -- clients get nothing from this VPS itself ---------------------------
	chain input {
		type filter hook input priority filter; policy accept;
		iifname "$CLIENT_IFACE" drop comment "no access to any service on this VPS"
	}

	# -- keep TCP segments small enough for the tunnel ----------------------
	chain mss {
		type filter hook forward priority mangle; policy accept;
		iifname "$CLIENT_IFACE" oifname "$RELAY_IFACE" tcp flags syn tcp option maxseg size set $MSS_CLAMP
		iifname "$RELAY_IFACE" oifname "$CLIENT_IFACE" tcp flags syn tcp option maxseg size set $MSS_CLAMP
	}
}
EOF
    chmod 600 "$FW_FILE"
}

# --- ruleset for the HOME side ------------------------------------------------
write_fw_rules_home() {
    local elems client_elems tcp_rule="" udp_rule=""
    elems=$(printf '%s' "$HOME_DESTS_LIST")
    client_elems=$(norm_cidrs "$CLIENT_NET")

    if [[ -n ${HOME_ALLOW_TCP// /} ]]; then
        local p
        for p in $HOME_ALLOW_TCP; do tcp_rule+="${tcp_rule:+, }$p"; done
        tcp_rule="		iifname \"$RELAY_IFACE\" tcp dport { $tcp_rule } accept
"
    fi
    if [[ -n ${HOME_ALLOW_UDP// /} ]]; then
        local p
        for p in $HOME_ALLOW_UDP; do udp_rule+="${udp_rule:+, }$p"; done
        udp_rule="		iifname \"$RELAY_IFACE\" udp dport { $udp_rule } accept
"
    fi

    (( DRY_RUN )) && { info "[dry-run] would write $FW_FILE (home)"; return 0; }
    cat >"$FW_FILE" <<EOF
#!/usr/sbin/nft -f
# Generated by $PROG $VERSION — overwritten on every run, do not edit.
# Only traffic of THIS relay is matched; every other packet on this host is
# left completely alone (policy accept, no other table is touched).
add table inet wgrelay
delete table inet wgrelay
table inet wgrelay {
	comment "wg-relay: home side"

	set home_nets {
		type ipv4_addr
		flags interval
		elements = { $elems }
	}

	set client_nets {
		type ipv4_addr
		flags interval
		elements = { $client_elems }
	}

	# -- services running on the relay host itself -------------------------
	chain input {
		type filter hook input priority filter; policy accept;

		iifname "$RELAY_IFACE" icmp type { echo-request, destination-unreachable, time-exceeded } accept
${tcp_rule}${udp_rule}		iifname "$RELAY_IFACE" drop comment "tunnel clients may only use the ports listed above"
		iifname != "lo" udp dport $WG_RELAY_LOCAL_PORT drop comment "WireGuard is only reachable through udp2raw"
	}

	# -- what tunnel clients may reach on the LAN --------------------------
	chain forward {
		type filter hook forward priority filter; policy accept;

		iifname "$RELAY_IFACE" ip daddr @home_nets accept
		iifname "$RELAY_IFACE" drop comment "tunnel clients may only reach home_nets"
		oifname "$RELAY_IFACE" accept comment "return traffic to the tunnel"
	}

	# -- keep TCP segments small enough for the tunnel ----------------------
	chain mss {
		type filter hook forward priority mangle; policy accept;
		iifname "$RELAY_IFACE" tcp flags syn tcp option maxseg size set $MSS_CLAMP
		oifname "$RELAY_IFACE" tcp flags syn tcp option maxseg size set $MSS_CLAMP
	}
EOF

    if [[ ${HOME_SNAT,,} == yes || ${HOME_SNAT,,} == true || $HOME_SNAT == 1 ]]; then
        cat >>"$FW_FILE" <<EOF

	# -- clients appear to the LAN as this host -----------------------------
	# Needed whenever the service lives on a *different* machine than
	# this relay host: their replies then go back to this host instead of
	# being lost at the LAN gateway. Set HOME_SNAT="no" once every destination
	# host has a route for $client_elems via $RELAY_HOME_IP.
	chain postrouting {
		# The chain type must be nat: masquerade is a NAT statement and nft
		# rejects it inside a filter chain ("Operation not supported").
		type nat hook postrouting priority srcnat; policy accept;
		ip saddr @client_nets ip daddr @home_nets masquerade
	}
EOF
    fi

    cat >>"$FW_FILE" <<EOF
}
EOF
    chmod 600 "$FW_FILE"
}

# Can this network namespace do nftables NAT at all?
# Unprivileged LXC containers inherit the host kernel: if the Proxmox host has
# no nf_nat / nft_chain_nat loaded, "masquerade" fails with
# "Operation not supported" — after the tunnel was already built.
nat_probe() {   # prints the nft error on failure, returns 1
    have nft || { printf 'nft is not installed'; return 1; }
    if (( DRY_RUN && ! CHECK_ONLY )); then return 0; fi
    local t=wgrelay_natprobe err=""
    nft delete table inet $t 2>/dev/null || true
    if ! err=$( { nft add table inet $t &&
                  nft "add chain inet $t c { type nat hook postrouting priority srcnat; }" &&
                  nft "add rule inet $t c oifname \"wgrelay-probe0\" masquerade"; } 2>&1 ); then
        nft delete table inet $t 2>/dev/null || true
        printf '%s' "$err"
        return 1
    fi
    nft delete table inet $t 2>/dev/null || true
    return 0
}

nat_hint() {
    cat <<EOF
    nftables NAT (masquerade) is not available in this container${1:+: $1}

    Proxmox/Debian LXC: this is a HOST kernel module problem, nothing in the
    container is broken. On the Proxmox host run:

        modprobe nf_nat nft_chain_nat
        printf 'nf_nat\nnft_chain_nat\n' > /etc/modules-load.d/netfilter-nat.conf

    then re-run this script (it continues where it stopped).

    If the service you want to reach runs on THIS machine (not on another LAN
    host), NAT is not needed at all: add --set HOME_SNAT=no
EOF
}

apply_fw() {
    (( DRY_RUN )) && { info "[dry-run] would apply $FW_FILE"; return 0; }
    if have nft; then
        local out=""
        if ! out=$(nft -f "$FW_FILE" 2>&1); then
            printf '%s\n' "$out" >&2
            if grep -qi "operation not supported" <<<"$out" && grep -q masquerade "$FW_FILE"; then
                # only claim a host-module problem if NAT really is unavailable;
                # otherwise the ruleset itself is wrong
                local nat_out=""
                if ! nat_out=$(nat_probe); then
                    die "$(nat_hint "$nat_out")"
                fi
                die "nftables rejected the masquerade rule although NAT works here — please report this ruleset:
    $FW_FILE"
            fi
            die "nftables rejected $FW_FILE (see message above)"
        fi
    else
        warn "nft is not installed — isolation rules were NOT applied"
        return 0
    fi
    "$FW_HELPER" apply || die "could not insert the iptables compatibility rules"
}


# ----------------------------------------------------------------------------
# udp2raw
# ----------------------------------------------------------------------------
find_udp2raw() {
    local c
    if [[ -n $UDP2RAW_BIN && -x $UDP2RAW_BIN ]]; then
        printf '%s' "$UDP2RAW_BIN"; return 0
    fi
    for c in /usr/local/bin/udp2raw /usr/bin/udp2raw \
             /usr/local/bin/udp2raw_amd64 /usr/local/bin/udp2raw_arm64 \
             /opt/udp2raw/udp2raw /opt/udp2raw/udp2raw_amd64 \
             /root/udp2raw/udp2raw_amd64 /root/udp2raw-tunnel/udp2raw_amd64; do
        [[ -x $c ]] && { printf '%s' "$c"; return 0; }
    done
    # any binary an existing udp2raw unit already uses
    for c in /etc/systemd/system/*udp2raw*.service /lib/systemd/system/*udp2raw*.service; do
        [[ -r $c ]] || continue
        c=$(awk -F'=' '/^ExecStart=/{print $2; exit}' "$c" | awk '{print $1}')
        [[ -x ${c:-} ]] && { printf '%s' "$c"; return 0; }
    done
    c=$(command -v udp2raw 2>/dev/null || true)
    [[ -n $c ]] && { printf '%s' "$c"; return 0; }
    return 1
}

install_udp2raw() {
    local arch url tmp bin
    case $(uname -m) in
        x86_64|amd64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        armv7l|armv6l) arch="arm" ;;
        *) die "unsupported CPU architecture for udp2raw: $(uname -m) — set UDP2RAW_BIN" ;;
    esac
    url="https://github.com/wangyu-/udp2raw/releases/download/20230206.0/udp2raw_binaries.tar.gz"
    tmp=$(mktemp -d)
    log "downloading udp2raw"
    if ! curl -fL --retry 2 --max-time 120 -o "$tmp/u2r.tar.gz" "$url"; then
        rm -rf "$tmp"
        die "download failed. Get udp2raw_binaries.tar.gz yourself, unpack it and run again with --set UDP2RAW_BIN=/path/to/udp2raw_$arch"
    fi
    tar -xzf "$tmp/u2r.tar.gz" -C "$tmp" || die "could not unpack udp2raw"
    bin=$(find "$tmp" -type f -name "udp2raw_$arch*" ! -name "*hw_aes*" | head -n1)
    [[ -n $bin ]] || bin=$(find "$tmp" -type f -name "udp2raw_$arch*" | head -n1)
    [[ -n $bin ]] || { rm -rf "$tmp"; die "no udp2raw_$arch binary inside the archive"; }
    run install -m 755 "$bin" /usr/local/bin/udp2raw
    rm -rf "$tmp"
    UDP2RAW=/usr/local/bin/udp2raw
}

ensure_udp2raw() {
    if UDP2RAW=$(find_udp2raw); then
        info "udp2raw: $UDP2RAW (existing installation reused)"
        return 0
    fi
    (( DRY_RUN )) && { info "[dry-run] would download udp2raw"; UDP2RAW=/usr/local/bin/udp2raw; return 0; }
    install_udp2raw
    info "udp2raw: $UDP2RAW"
}

port_from_udp2raw_conf() {   # the -l port of an existing udp2raw.conf
    [[ -r $UDP2RAW_CONF ]] || return 0
    awk '$1=="-l" {print $2; exit}' "$UDP2RAW_CONF" | sed -E 's/.*:([0-9]+)$/\1/'
}

write_udp2raw_conf() {
    local role=$1 laddr raddr
    if [[ $role == vps ]]; then
        laddr="127.0.0.1:$UDP2RAW_LOCAL_PORT"
        raddr="$HOME_PUBLIC_IP:$RELAY_PORT"
    else
        laddr="0.0.0.0:$RELAY_PORT"
        raddr="127.0.0.1:$WG_RELAY_LOCAL_PORT"
    fi

    (( DRY_RUN )) && { info "[dry-run] would write $UDP2RAW_CONF"; return 0; }
    {
        echo "# Generated by $PROG $VERSION — overwritten on every run."
        echo "# role: $role  (udp2raw config file format: one option per line)"
        [[ $role == vps ]] && echo "-c" || echo "-s"
        echo "-l $laddr"
        echo "-r $raddr"
        echo "-k $UDP2RAW_KEY"
        echo "--raw-mode $UDP2RAW_MODE"
        echo "--mtu-warn 1420"
        echo "--log-level 4"
        # -a installs (and removes) the iptables rules that keep the kernel
        # from answering the fake TCP packets with RST.
        if [[ $UDP2RAW_MODE == faketcp || $UDP2RAW_MODE == easy-faketcp ]]; then
            echo "-a"
        fi
        if [[ -n $UDP2RAW_EXTRA_ARGS ]]; then
            printf '%s\n' "$UDP2RAW_EXTRA_ARGS"
        else
            echo "--retry-on-error"
        fi
    } >"$UDP2RAW_CONF"
    chmod 600 "$UDP2RAW_CONF"
}

write_udp2raw_unit() {
    (( DRY_RUN )) && { info "[dry-run] would write /etc/systemd/system/$UNIT_UDP2RAW"; return 0; }
    cat >"/etc/systemd/system/$UNIT_UDP2RAW" <<EOF
[Unit]
Description=udp2raw relay tunnel ($ROLE side, $UDP2RAW_MODE)
Documentation=file://$RELAY_DIR
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=$UDP2RAW --conf-file $UDP2RAW_CONF
Restart=always
RestartSec=5
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "/etc/systemd/system/$UNIT_UDP2RAW"
}

# ----------------------------------------------------------------------------
# WireGuard configuration files
# ----------------------------------------------------------------------------
write_wg_home() {
    local priv=$1
    local f="/etc/wireguard/${RELAY_IFACE}.conf"
    (( DRY_RUN )) && { info "[dry-run] would write $f"; return 0; }
    backup_file "$f"
    {
        echo "[Interface]"
        echo "# Generated by $PROG $VERSION (role home)"
        echo "Address = $RELAY_HOME_IP/32"
        echo "ListenPort = $WG_RELAY_LOCAL_PORT"
        echo "PrivateKey = $priv"
        echo "MTU = $RELAY_MTU"
        echo "SaveConfig = false"
        echo "PostUp = $FW_HELPER apply"
        echo "PostDown = /bin/true"
        if [[ -n ${PEER_PUBKEY:-} ]]; then
            echo
            echo "[Peer]"
            echo "# the entry VPS (uplink). No Endpoint on purpose: udp2raw"
            echo "# connects from 127.0.0.1 and WireGuard learns the address."
            echo "PublicKey = $PEER_PUBKEY"
            echo "AllowedIPs = $RELAY_VPS_IP/32, $CLIENT_NET"
            echo "PersistentKeepalive = 25"
        fi
    } >"$f"
    chmod 600 "$f"
}

write_wg_vps_relay() {
    local priv=$1 cidr post_up post_down
    local f="/etc/wireguard/${RELAY_IFACE}.conf"

    post_up="ip route replace $RELAY_HOME_IP/32 dev %i"
    post_down="ip route del $RELAY_HOME_IP/32 dev %i || true"
    local c
    for c in ${HOME_DESTS_LIST//,/ }; do
        post_up+="; ip route replace $c dev %i"
        post_down+="; ip route del $c dev %i || true"
    done

    (( DRY_RUN )) && { info "[dry-run] would write $f"; return 0; }
    backup_file "$f"
    {
        echo "[Interface]"
        echo "# Generated by $PROG $VERSION (role vps, uplink to the home server)"
        echo "Address = $RELAY_VPS_IP/32"
        echo "# no ListenPort: this interface never accepts connections"
        echo "PrivateKey = $priv"
        echo "MTU = $RELAY_MTU"
        echo "Table = off"
        echo "SaveConfig = false"
        echo "PostUp = $FW_HELPER apply; $post_up"
        echo "PostDown = $post_down"
        if [[ -n ${PEER_PUBKEY:-} ]]; then
            echo
            echo "[Peer]"
            echo "# home server, reached through the udp2raw client below"
            echo "PublicKey = $PEER_PUBKEY"
            echo "Endpoint = 127.0.0.1:$UDP2RAW_LOCAL_PORT"
            echo "AllowedIPs = $RELAY_HOME_IP/32, $HOME_DESTS_LIST"
            echo "PersistentKeepalive = 25"
        fi
    } >"$f"
    chmod 600 "$f"
}

write_wg_vps_clients() {
    local priv=$1 gateway=$2
    local f="/etc/wireguard/${CLIENT_IFACE}.conf"
    if [[ -s $f ]]; then
        # never rewrite a config that already holds clients
        info "$f already exists — kept as is ($(grep -c '^\[Peer\]' "$f" 2>/dev/null || echo 0) peers)"
        if ! grep -q '^PostUp' "$f"; then
            if (( ! DRY_RUN )); then
                sed -i "/^\[Interface\]/a PostUp = $FW_HELPER apply" "$f"
            fi
            info "added the firewall hook to $f"
        fi
        return 0
    fi
    (( DRY_RUN )) && { info "[dry-run] would write $f"; return 0; }
    {
        echo "[Interface]"
        echo "# Generated by $PROG $VERSION (role vps, client-facing — the dashboard manages the peers)"
        echo "Address = $gateway"
        echo "ListenPort = $CLIENT_LISTEN_PORT"
        echo "PrivateKey = $priv"
        echo "MTU = 1420"
        echo "SaveConfig = false"
        echo "PostUp = $FW_HELPER apply"
        echo "PostDown = /bin/true"
    } >"$f"
    chmod 600 "$f"
}

wg_up() {   # $1 iface
    run systemctl enable "wg-quick@$1.service" >/dev/null 2>&1 || true
    if (( DRY_RUN )); then
        info "[dry-run] would start wg-quick@$1"
        return 0
    fi
    if ! systemctl restart "wg-quick@$1.service"; then
        journalctl -u "wg-quick@$1.service" -n 20 --no-pager 2>/dev/null || true
        die "wg-quick@$1 failed to start"
    fi
    ok "wg-quick@$1 is up"
}

# ----------------------------------------------------------------------------
# opening the client port (only additive, never touches other rules)
# ----------------------------------------------------------------------------
open_udp_port() {   # $1 port
    local p=$1
    if have ufw && ufw status 2>/dev/null | grep -q "Status: active"; then
        run ufw allow "$p"/udp >/dev/null 2>&1 || warn "ufw allow $p/udp failed"
        info "ufw: allowed $p/udp"
    fi
    if have firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
        run firewall-cmd --permanent --add-port="$p"/udp >/dev/null 2>&1 || true
        run firewall-cmd --reload >/dev/null 2>&1 || true
        info "firewalld: allowed $p/udp"
    fi
}

open_tcp_from() {   # $1 port $2 source ip
    local p=$1 src=$2
    have ufw || return 0
    ufw status 2>/dev/null | grep -q "Status: active" || return 0
    run ufw allow from "$src" to any port "$p" proto tcp >/dev/null 2>&1 || warn "ufw rule failed"
    info "ufw: allowed tcp/$p from $src"
}

open_tcp_port() {   # $1 port
    local p=$1
    have ufw || return 0
    ufw status 2>/dev/null | grep -q "Status: active" || return 0
    run ufw allow "$p"/tcp >/dev/null 2>&1 || warn "ufw rule failed"
    info "ufw: allowed tcp/$p"
}

# udp2raw reads its packets at layer 2 (AF_PACKET), so a host firewall cannot
# break the tunnel — these rules are here only so that a "deny everything"
# firewall does not surprise anybody, and they are scoped to the one port.
open_relay_port() {
    local p=$RELAY_PORT
    if have ufw && ufw status 2>/dev/null | grep -q "Status: active"; then
        run ufw allow "$p"/udp >/dev/null 2>&1 || warn "ufw allow $p/udp failed"
        run ufw allow "$p"/tcp >/dev/null 2>&1 || warn "ufw allow $p/tcp failed"
        info "ufw: allowed $p/udp and $p/tcp"
    fi
    if have firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
        run firewall-cmd --permanent --add-port="$p"/udp >/dev/null 2>&1 || true
        run firewall-cmd --permanent --add-port="$p"/tcp >/dev/null 2>&1 || true
        run firewall-cmd --reload >/dev/null 2>&1 || true
        info "firewalld: allowed $p/udp and $p/tcp"
    fi
    info "router: forward TCP+UDP $p to this host, otherwise nothing gets through"
}

# ----------------------------------------------------------------------------
# the dashboard
# ----------------------------------------------------------------------------
dashboard_installed() { [[ -x /opt/wireguard-dashboard/venv/bin/python && -r /etc/wireguard-dashboard/config.json ]]; }

dash_cli() { /usr/local/bin/wgdash "$@"; }

install_dashboard() {
    step "WireGuard dashboard (client-facing UI)"

    ensure_cmd git git
    ensure_cmd python3 python3
    if ! python3 -c 'import venv' >/dev/null 2>&1; then
        log "installing python3-venv"
        pkg_refresh
        case $PKG in
            apt) pkg_install python3-venv ;;
            *)   pkg_install python3-virtualenv || true ;;
        esac
    fi
    ensure_cmd curl curl
    ensure_cmd nftables nft
    ensure_cmd iptables iptables || warn "iptables missing — udp2raw's faketcp rules cannot be installed"

    if (( DRY_RUN )); then
        info "[dry-run] would clone $DASH_REPO and run its install.sh"
        return 0
    fi

    if [[ -d $DASH_SRC_DIR/.git ]]; then
        info "updating dashboard source in $DASH_SRC_DIR"
        git -C "$DASH_SRC_DIR" fetch --depth 1 origin "$DASH_BRANCH" >/dev/null 2>&1 \
            && git -C "$DASH_SRC_DIR" reset --hard FETCH_HEAD >/dev/null 2>&1 \
            || warn "could not update the dashboard source (using the local copy)"
    else
        log "cloning the dashboard"
        mkdir -p "$(dirname "$DASH_SRC_DIR")"
        git clone --depth 1 --branch "$DASH_BRANCH" "$DASH_REPO" "$DASH_SRC_DIR" \
            || die "git clone $DASH_REPO failed"
    fi

    local fresh=0
    if dashboard_installed && (( ! FORCE )); then
        info "dashboard already installed — settings and service are refreshed"
        info "existing login: cat /etc/wireguard-relay/dashboard-admin.txt"
    else
        fresh=1
        [[ $DASH_PASS =~ ^.{10,}$ ]] || DASH_PASS=$(rand_alnum 24)
        log "running the dashboard installer (unattended)"
        local args=(--unattended --no-caddy
                    --wg-interface "$CLIENT_IFACE"
                    --admin-user "$DASH_USER"
                    --admin-pass "$DASH_PASS"
                    --port "$DASH_PORT"
                    --bind "$DASH_BIND"
                    --endpoint "$VPS_PUBLIC_IP:$CLIENT_LISTEN_PORT")
        if [[ -n $DASH_TELEGRAM_TOKEN ]]; then args+=(--telegram-token "$DASH_TELEGRAM_TOKEN"); fi
        if [[ -n $DASH_TELEGRAM_ADMIN ]]; then args+=(--telegram-admin "$DASH_TELEGRAM_ADMIN"); fi
        bash "$DASH_SRC_DIR/install.sh" "${args[@]}" \
            || die "the dashboard installer failed"
    fi

    # ---- client defaults: split tunnel towards HOME_DESTS only ------------
    log "configuring dashboard defaults (split tunnel to $HOME_DESTS_LIST)"
    dash_cli set-setting default_allowed_ips "$HOME_DESTS_LIST" >/dev/null || warn "could not set default_allowed_ips"
    dash_cli set-setting default_full_tunnel true >/dev/null || true
    dash_cli set-setting default_mtu "$CLIENT_MTU" >/dev/null || true
    dash_cli set-setting default_keepalive 25 >/dev/null || true
    dash_cli set-setting default_dns "$HOME_DNS" >/dev/null || true
    dash_cli set-setting endpoint "$VPS_PUBLIC_IP:$CLIENT_LISTEN_PORT" >/dev/null || true

    if (( fresh )); then
        mkdir -p /etc/wireguard-relay
        ( umask 077; printf 'dashboard URL : %s\nusername      : %s\npassword      : %s\n' \
            "$( if [[ -n $DASH_DOMAIN ]]; then echo "https://$DASH_DOMAIN"; else echo "http://$DASH_BIND:$DASH_PORT (ssh -L $DASH_PORT:127.0.0.1:$DASH_PORT root@$VPS_PUBLIC_IP)"; fi )" \
            "$DASH_USER" "$DASH_PASS" >/etc/wireguard-relay/dashboard-admin.txt )
        chmod 600 /etc/wireguard-relay/dashboard-admin.txt
    fi

    systemd_enable_now wireguard-dashboard.service || warn "dashboard service did not start (journalctl -u wireguard-dashboard -n 50)"
}

install_caddy_site() {
    [[ -n $DASH_DOMAIN ]] || return 0
    if ! have caddy; then
        warn "DASH_DOMAIN is set but Caddy is not installed — dashboard stays on $DASH_BIND:$DASH_PORT"
        return 0
    fi
    step "Caddy vhost for https://$DASH_DOMAIN"
    local main=/etc/caddy/Caddyfile snippet=/etc/caddy/conf.d/wireguard-dashboard.caddy
    backup_file "$main"
    (( DRY_RUN )) || mkdir -p /etc/caddy/conf.d /var/log/caddy
    if (( DRY_RUN )); then
        info "[dry-run] would write $snippet and reload Caddy"
        return 0
    fi
    cat >"$snippet" <<EOF
# managed by $PROG — wg-relay dashboard
$DASH_DOMAIN {
	encode gzip
	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		-X-Powered-By
	}
	$(if [[ -n $DASH_EMAIL ]]; then echo "tls $DASH_EMAIL"; fi)
	reverse_proxy 127.0.0.1:$DASH_PORT {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Forwarded-For {remote}
	}
}
EOF
    if [[ ! -f $main ]]; then
        printf 'import /etc/caddy/conf.d/*.caddy\n' >"$main"
    elif ! grep -q 'conf.d' "$main"; then
        printf '\n# wg-relay dashboard\nimport /etc/caddy/conf.d/*.caddy\n' >>"$main"
    fi
    if ! caddy validate --config "$main" >/dev/null 2>&1; then
        warn "Caddy rejected the new config — restoring the previous Caddyfile"
        local latest
        latest=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n1)
        [[ -n $latest && -f $latest/Caddyfile ]] && cp -a "$latest/Caddyfile" "$main"
        rm -f "$snippet"
        warn "dashboard stays on http://$DASH_BIND:$DASH_PORT"
        return 0
    fi
    systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || warn "could not reload Caddy"
    ok "https://$DASH_DOMAIN -> 127.0.0.1:$DASH_PORT"
}

# ----------------------------------------------------------------------------
# preflight
# ----------------------------------------------------------------------------
declare -a PROBLEMS=()
declare -a MISSING_PKGS=()
problem() { PROBLEMS+=("$*"); warn "$*"; }

check_iface_free() {
    local i=$1
    iface_valid "$i"
    if ip link show dev "$i" >/dev/null 2>&1; then
        if [[ -r /etc/wireguard/$i.conf ]]; then
            info "interface $i already exists and belongs to wg-relay (re-run)"
        elif (( FORCE )); then
            warn "interface $i exists but $i.conf is missing — continuing because of --force"
        else
            problem "interface $i already exists and is not managed by this script"
        fi
    fi
}

# make sure a CIDR (or a comma separated list of them) does not clash with a
# route or an address that is already present on this machine. Routes that
# belong to our own interfaces are ignored, so re-runs stay clean.
check_no_clash() {
    local list=$1 what=$2 cidr hits
    local -a elems=()
    IFS=',' read -r -a elems <<<"$list" || true
    for cidr in "${elems[@]}"; do
        cidr=${cidr// /}
        [[ -n $cidr ]] || continue
        hits=$(ip -4 route show 2>/dev/null | overlap_routes "$cidr" 2>/dev/null \
               | grep -v -E "dev ($RELAY_IFACE|$CLIENT_IFACE)( |$)" || true)
        if [[ -n $hits ]]; then
            problem "$what ($cidr) overlaps an existing route: $(printf '%s' "$hits" | head -n1)"
        fi
        hits=$(ip -4 -o addr show 2>/dev/null \
               | awk -v a="$RELAY_IFACE" -v b="$CLIENT_IFACE" '$2!=a && $2!=b {print $4}' \
               | overlap_routes "$cidr" 2>/dev/null || true)
        if [[ -n $hits ]]; then
            problem "$what ($cidr) overlaps an address of this machine: $(printf '%s' "$hits" | head -n1)"
        fi
    done
}

# ----------------------------------------------------------------------------
# ROLE: home
# ----------------------------------------------------------------------------
role_home() {
    if [[ ! -d $SNAP_DIR/latest ]]; then
        info "tip: run 'sudo $PROG --snapshot' first, then '--diff' proves what changed"
    fi
    step "Pre-flight checks (home side)"
    detect_pkg
    ensure_cmd wireguard-tools wg
    ensure_cmd nftables nft
    ensure_cmd iptables iptables || warn "iptables not available — udp2raw faketcp needs it"
    ensure_cmd curl curl
    check_wireguard_kernel

    iface_valid "$RELAY_IFACE"
    check_iface_free "$RELAY_IFACE"

    # LAN detection
    if [[ -z $HOME_LAN_IFACE || -z $HOME_DESTS ]]; then
        local ifc cidr
        ifc=$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')
        [[ -z $ifc ]] && ifc=$(ip -4 -o link show up 2>/dev/null | awk -F': ' '$2!="lo"{print $2; exit}')
        HOME_LAN_IFACE=${HOME_LAN_IFACE:-$ifc}
        if [[ -z $HOME_DESTS && -n $HOME_LAN_IFACE ]]; then
            cidr=$(ip -4 -o addr show dev "$HOME_LAN_IFACE" scope global 2>/dev/null | awk '{print $4; exit}')
            [[ -n $cidr ]] && HOME_DESTS="$cidr"
        fi
        info "LAN interface: ${HOME_LAN_IFACE:-unknown}   LAN: ${HOME_DESTS:-unknown}"
    fi
    [[ -n $HOME_DESTS ]] || die "cannot detect the LAN — set HOME_DESTS=\"10.x.y.0/24\" in the config"

    HOME_DESTS_LIST=$(norm_cidrs "$HOME_DESTS") || die "HOME_DESTS is not valid"
    [[ -n $HOME_DESTS_LIST ]] || die "HOME_DESTS is empty"
    case ", $HOME_DESTS_LIST," in
        *", 0.0.0.0/0,"*) die "HOME_DESTS must not be 0.0.0.0/0" ;;
    esac

    check_no_clash "$CLIENT_NET"       "CLIENT_NET"
    check_no_clash "$RELAY_HOME_IP/32" "RELAY_HOME_IP"
    check_no_clash "$RELAY_VPS_IP/32"  "RELAY_VPS_IP"

    # NAT is what lets clients reach a *different* machine in the LAN
    case "${HOME_SNAT,,}" in
        yes|true|1)
            local nat_err=""
            if nat_err=$(nat_probe); then
                info "nftables NAT (masquerade): ok"
            else
                warn "$(nat_hint "$nat_err")"
                problem "nftables NAT (masquerade) unavailable${nat_err:+: $nat_err}"
            fi
            ;;
    esac

    # ports — an existing installation always wins, so re-runs keep working
    if [[ $RELAY_PORT -le 0 ]]; then
        RELAY_PORT=$(port_from_udp2raw_conf)
        RELAY_PORT=${RELAY_PORT:-0}
    fi
    if [[ $WG_RELAY_LOCAL_PORT -le 0 ]]; then
        WG_RELAY_LOCAL_PORT=$(conf_listen_port "/etc/wireguard/${RELAY_IFACE}.conf")
        WG_RELAY_LOCAL_PORT=${WG_RELAY_LOCAL_PORT:-0}
    fi
    resolve_port RELAY_PORT udp "relay (fake-TCP)"
    resolve_port WG_RELAY_LOCAL_PORT udp "local WireGuard"

    if (( CHECK_ONLY )); then
        info "relay port: $RELAY_PORT   local WG port: $WG_RELAY_LOCAL_PORT"
        info "home LAN iface: ${HOME_LAN_IFACE:-auto}   HOME_DESTS: $HOME_DESTS_LIST"
        return 0
    fi

    [[ ${#PROBLEMS[@]} -eq 0 ]] || (( FORCE )) || die "pre-flight found ${#PROBLEMS[@]} problem(s) — fix them or use --force"

    # udp2raw key
    if [[ -z $UDP2RAW_KEY ]]; then
        UDP2RAW_KEY=$(rand_alnum 32)
        info "generated a new udp2raw key (it is stored in $CONFIG_FILE)"
    fi

    step "udp2raw (server)"
    ensure_udp2raw
    write_udp2raw_conf home
    write_udp2raw_unit

    step "WireGuard endpoint ($RELAY_IFACE)"
    run mkdir -p /etc/wireguard
    run chmod 700 /etc/wireguard
    run mkdir -p "$RELAY_DIR"
    run chmod 700 "$RELAY_DIR"
    local priv pub existing
    existing=$(conf_privkey "/etc/wireguard/${RELAY_IFACE}.conf")
    if [[ -n $existing ]]; then
        if pub=$(printf '%s\n' "$existing" | wg pubkey 2>/dev/null); then
            priv=$existing
            info "reusing the existing $RELAY_IFACE private key"
        else
            warn "the private key in /etc/wireguard/${RELAY_IFACE}.conf is unusable — generating a new one"
            existing=""
        fi
    fi
    if [[ -z $existing ]]; then
        read -r priv pub <<<"$(gen_keypair)"
        [[ -n $priv && -n $pub ]] || die "wg genkey failed"
    fi
    HOME_PUBKEY=$pub
    # never lose a peer key that was configured earlier
    if [[ -z $PEER_PUBKEY ]]; then
        PEER_PUBKEY=$(state_get PEER_PUBKEY 2>/dev/null || true)
    fi
    if [[ -z $PEER_PUBKEY ]]; then
        PEER_PUBKEY=$(conf_peer_pubkey "/etc/wireguard/${RELAY_IFACE}.conf")
    fi
    write_wg_home "$priv"
    if (( ! DRY_RUN )); then
        ( umask 077; printf '%s\n' "$pub" >"$RELAY_DIR/home-pubkey" )
    fi

    step "Isolation firewall"
    write_fw_helper
    write_fw_unit
    write_fw_rules_home
    ensure_ip_forward
    run systemctl daemon-reload
    systemd_enable_now "$UNIT_FW" || warn "firewall unit failed"
    apply_fw

    step "Opening the relay port on this host's own firewall (if any)"
    open_relay_port

    step "Starting services"
    systemd_enable_now "$UNIT_UDP2RAW"
    wg_up "$RELAY_IFACE"

    # persist what the other side needs
    persist_values
    state_write
    if (( ! DRY_RUN )); then
        "$FW_HELPER" apply || warn "could not re-apply the firewall rules"
    fi

    print_home_summary
}

# ----------------------------------------------------------------------------
# ROLE: vps
# ----------------------------------------------------------------------------
role_vps() {
    if [[ ! -d $SNAP_DIR/latest ]]; then
        info "tip: run 'sudo $PROG --snapshot' first, then '--diff' proves what changed"
    fi
    step "Pre-flight checks (VPS side)"
    detect_pkg
    ensure_cmd wireguard-tools wg
    ensure_cmd nftables nft
    ensure_cmd iptables iptables || warn "iptables missing — udp2raw faketcp needs it"
    ensure_cmd curl curl
    check_wireguard_kernel

    iface_valid "$CLIENT_IFACE"; iface_valid "$RELAY_IFACE"
    [[ $CLIENT_IFACE != "$RELAY_IFACE" ]] || die "CLIENT_IFACE and RELAY_IFACE must differ"
    check_iface_free "$CLIENT_IFACE"
    check_iface_free "$RELAY_IFACE"

    HOME_DESTS_LIST=$(norm_cidrs "$HOME_DESTS" 2>/dev/null) || HOME_DESTS_LIST=""
    [[ -n $HOME_DESTS_LIST ]] || die "set HOME_DESTS (e.g. HOME_DESTS=\"10.10.0.0/24\") in $CONFIG_FILE or with --home-dests"
    case ", $HOME_DESTS_LIST," in
        *", 0.0.0.0/0,"*) die "HOME_DESTS must not be 0.0.0.0/0 (it would hijack this VPS's routing)" ;;
    esac
    if [[ -n $HOME_DNS ]] && ! cidrs_hold_ip "$HOME_DESTS_LIST" "$HOME_DNS" 2>/dev/null; then
        warn "HOME_DNS ($HOME_DNS) is not inside HOME_DESTS — clients will not be able to query it"
    fi

    check_no_clash "$HOME_DESTS_LIST" "HOME_DESTS"
    check_no_clash "$CLIENT_NET"      "CLIENT_NET"
    check_no_clash "$RELAY_VPS_IP/32" "RELAY_VPS_IP"

    [[ -n $HOME_PUBLIC_IP ]] || die "set HOME_PUBLIC_IP (the home server's public IP) in the config"
    if [[ -z $VPS_PUBLIC_IP ]]; then
        VPS_PUBLIC_IP=$(detect_public_ip || true)
        if [[ -z $VPS_PUBLIC_IP ]]; then
            if (( CHECK_ONLY )); then
                VPS_PUBLIC_IP="<not detected>"
                warn "could not detect this VPS's public IP (set VPS_PUBLIC_IP if the clients' endpoint matters)"
            else
                die "could not detect this VPS's public IP — set VPS_PUBLIC_IP"
            fi
        fi
    fi

    if [[ $CLIENT_LISTEN_PORT -le 0 ]]; then
        CLIENT_LISTEN_PORT=$(conf_listen_port "/etc/wireguard/${CLIENT_IFACE}.conf")
        CLIENT_LISTEN_PORT=${CLIENT_LISTEN_PORT:-0}
    fi
    if [[ $UDP2RAW_LOCAL_PORT -le 0 ]]; then
        UDP2RAW_LOCAL_PORT=$(port_from_udp2raw_conf)
        UDP2RAW_LOCAL_PORT=${UDP2RAW_LOCAL_PORT:-0}
    fi
    if [[ $DASH_PORT -le 0 ]]; then
        DASH_PORT=$(dash_listen_port)
        DASH_PORT=${DASH_PORT:-0}
    fi
    resolve_port CLIENT_LISTEN_PORT udp "WireGuard clients"
    resolve_port UDP2RAW_LOCAL_PORT udp "udp2raw local"
    resolve_port DASH_PORT tcp "dashboard"

    if [[ -z $PEER_PUBKEY ]]; then
        PEER_PUBKEY=$(state_get PEER_PUBKEY 2>/dev/null || true)
    fi
    if [[ -z $PEER_PUBKEY ]]; then
        PEER_PUBKEY=$(conf_peer_pubkey "/etc/wireguard/${RELAY_IFACE}.conf")
    fi
    if [[ -z $PEER_PUBKEY && -n $PEER_SSH ]]; then
        log "fetching the home server's public key over ssh ($PEER_SSH)"
        PEER_PUBKEY=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PEER_SSH" 'cat /etc/wireguard-relay/home-pubkey' 2>/dev/null || true)
        [[ -n $PEER_PUBKEY ]] || warn "could not read /etc/wireguard-relay/home-pubkey on $PEER_SSH — run the home script first"
    fi

    if (( CHECK_ONLY )); then
        info "client iface: $CLIENT_IFACE   listen: $CLIENT_LISTEN_PORT/udp"
        info "relay iface : $RELAY_IFACE   udp2raw local port: $UDP2RAW_LOCAL_PORT"
        info "home        : $HOME_PUBLIC_IP:$RELAY_PORT ($UDP2RAW_MODE)"
        info "reachable   : $HOME_DESTS_LIST"
        info "dashboard   : $DASH_BIND:$DASH_PORT"
        info "peer key    : ${PEER_PUBKEY:-MISSING — use --peer-pubkey or --peer-ssh}"
        [[ -n $PEER_PUBKEY ]] || problem "peer public key of the home server is missing"
        return 0
    fi

    [[ ${#PROBLEMS[@]} -eq 0 ]] || (( FORCE )) || die "pre-flight found ${#PROBLEMS[@]} problem(s) — fix them or use --force"

    [[ -n $PEER_PUBKEY ]] || warn "no peer key yet: the tunnel stays down until you re-run with --peer-pubkey/--peer-ssh"
    [[ ${#UDP2RAW_KEY} -ge 8 ]] || die "UDP2RAW_KEY is missing — copy the relay.conf produced by the home run, or set it with --udp2raw-key"

    setup_vps_relay
    setup_vps_clients
    setup_vps_firewall

    step "Dashboard"
    if [[ -n $DASH_DOMAIN ]]; then
        DASH_BIND="127.0.0.1"
    fi
    install_dashboard
    install_caddy_site
    if [[ $DASH_BIND == 0.0.0.0 || $DASH_BIND == :: ]]; then
        if [[ -n $DASH_ALLOW_FROM ]]; then
            open_tcp_from "$DASH_PORT" "$DASH_ALLOW_FROM"
        else
            warn "the dashboard listens on $DASH_BIND:$DASH_PORT without TLS and without a source restriction"
        fi
    fi
    open_udp_port "$CLIENT_LISTEN_PORT"

    step "Publishing the VPS key to the home server"
    VPS_PUBKEY=$(cat "$RELAY_DIR/vps-pubkey" 2>/dev/null || true)
    if [[ -z $VPS_PUBKEY ]]; then
        VPS_PUBKEY=$(wg show "$RELAY_IFACE" public-key 2>/dev/null | head -n1 || true)
    fi
    if [[ -n $PEER_SSH ]]; then
        push_key_to_home
    fi

    persist_values
    state_write
    if (( ! DRY_RUN )); then
        "$FW_HELPER" apply || warn "could not re-apply the firewall rules"
    fi
    print_vps_summary
}

setup_vps_relay() {
    step "udp2raw (client) towards the home server"
    ensure_udp2raw
    write_udp2raw_conf vps
    write_udp2raw_unit

    step "WireGuard uplink ($RELAY_IFACE)"
    run mkdir -p /etc/wireguard
    run chmod 700 /etc/wireguard
    run mkdir -p "$RELAY_DIR"
    run chmod 700 "$RELAY_DIR"
    local priv pub existing
    existing=$(conf_privkey "/etc/wireguard/${RELAY_IFACE}.conf")
    if [[ -n $existing ]]; then
        if pub=$(printf '%s\n' "$existing" | wg pubkey 2>/dev/null); then
            priv=$existing
            info "reusing the existing $RELAY_IFACE private key"
        else
            warn "the private key in /etc/wireguard/${RELAY_IFACE}.conf is unusable — generating a new one"
            existing=""
        fi
    fi
    if [[ -z $existing ]]; then
        read -r priv pub <<<"$(gen_keypair)"
        [[ -n $priv && -n $pub ]] || die "wg genkey failed"
    fi
    VPS_PUBKEY=$pub
    write_wg_vps_relay "$priv"
    if (( ! DRY_RUN )); then
        ( umask 077; printf '%s\n' "$pub" >"$RELAY_DIR/vps-pubkey" )
    fi
}

setup_vps_clients() {
    step "WireGuard server for clients ($CLIENT_IFACE)"
    local gateway priv existing
    gateway=$(cidr_gateway "$CLIENT_NET") || die "CLIENT_NET ($CLIENT_NET) has no usable host address"
    existing=$(conf_privkey "/etc/wireguard/${CLIENT_IFACE}.conf")
    if [[ -n $existing ]]; then
        priv=$existing; info "reusing the existing $CLIENT_IFACE private key"
    else
        read -r priv _ <<<"$(gen_keypair)"
    fi
    write_wg_vps_clients "$priv" "$gateway"
}

setup_vps_firewall() {
    step "Isolation firewall"
    write_fw_helper
    write_fw_unit
    write_fw_rules_vps
    ensure_ip_forward
    run systemctl daemon-reload
    systemd_enable_now "$UNIT_FW" || warn "firewall unit failed"
    apply_fw
    systemd_enable_now "$UNIT_UDP2RAW"
    wg_up "$RELAY_IFACE"
    wg_up "$CLIENT_IFACE"
}

push_key_to_home() {
    [[ -n ${VPS_PUBKEY:-} ]] || return 0
    log "sending $VPS_PUBKEY to $PEER_SSH"
    if (( DRY_RUN )); then
        info "[dry-run] would push the key over ssh"
        return 0
    fi
    if ssh -o BatchMode=yes -o ConnectTimeout=10 "$PEER_SSH" \
        "bash -s -- --role home --peer-pubkey '$VPS_PUBKEY' --yes" <"$SELF" >/tmp/wg-relay-home.log 2>&1; then
        ok "home server updated (log: /tmp/wg-relay-home.log)"
    else
        warn "could not update the home server automatically. On the home machine run:"
        warn "  sudo ./wg-relay-setup.sh --role home --peer-pubkey $VPS_PUBKEY"
    fi
}

detect_public_ip() {
    local ip=""
    ip=$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null || true)
    [[ -z $ip ]] && ip=$(curl -fsS --max-time 6 https://ifconfig.me/ip 2>/dev/null || true)
    if [[ -z $ip ]]; then
        ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
    fi
    printf '%s' "$ip"
}

# ----------------------------------------------------------------------------
# summaries
# ----------------------------------------------------------------------------
print_home_summary() {
    cat <<EOF

${C_B}──────────────────────────────  HOME SIDE READY  ──────────────────────────────${C_R}

  WireGuard endpoint : $RELAY_IFACE  address $RELAY_HOME_IP/32  (udp 127.0.0.1:$WG_RELAY_LOCAL_PORT)
  udp2raw            : listens on 0.0.0.0:$RELAY_PORT/$UDP2RAW_MODE  key stored in $CONFIG_FILE
  clients may reach  : $HOME_DESTS_LIST
  SNAT towards LAN   : $HOME_SNAT
  home public key    : $HOME_PUBKEY
  config file        : $CONFIG_FILE

  ${C_B}1.${C_R} On the VPS, copy this config file over (it now contains RELAY_PORT and
     UDP2RAW_KEY) and run:  sudo ./wg-relay-setup.sh --role vps --config relay.conf
  ${C_B}2.${C_R} Then come back here with the VPS key:
     sudo ./wg-relay-setup.sh --role home --peer-pubkey <VPS_KEY>

  Router: forward UDP+TCP $RELAY_PORT from the internet to ${HOME_LAN_IFACE:-this host}.
  (udp2raw "$UDP2RAW_MODE" needs both, because it speaks a fake TCP stream.)

  Checks:
     systemctl status $UNIT_UDP2RAW --no-pager
     wg show $RELAY_IFACE
     nft list table inet wgrelay
     journalctl -u $UNIT_UDP2RAW -n 30 --no-pager

EOF
}

admin_cred() {   # $1 = field name in dashboard-admin.txt
    [[ -r /etc/wireguard-relay/dashboard-admin.txt ]] || return 0
    awk -v f="$1" '$1==f {print $3; exit}' /etc/wireguard-relay/dashboard-admin.txt 2>/dev/null || true
}

print_vps_summary() {
    local dash_url dash_user dash_pass
    dash_url="http://$DASH_BIND:$DASH_PORT"
    dash_user=$(admin_cred username)
    dash_pass=$(admin_cred password)
    if [[ -n $DASH_DOMAIN ]]; then dash_url="https://$DASH_DOMAIN"; fi
    if [[ -z $dash_user ]]; then
        dash_user="(see dashboard-admin.txt)"
        dash_pass="(see dashboard-admin.txt)"
    fi
    cat <<EOF

${C_B}──────────────────────────────  VPS SIDE READY  ───────────────────────────────${C_R}

  clients       : WireGuard endpoint ${VPS_PUBLIC_IP}:$CLIENT_LISTEN_PORT/udp
  clients get   : $HOME_DESTS_LIST  (through $RELAY_IFACE -> udp2raw -> $HOME_PUBLIC_IP)
  isolation     : everything else is dropped — clients cannot reach this VPS
  vps public key: ${VPS_PUBKEY:-unknown}
  dashboard     : $dash_url
  login         : $dash_user / $dash_pass
  details       : cat /etc/wireguard-relay/dashboard-admin.txt

  ${C_B}If the tunnel is not up yet${C_R}, finish the key exchange:
     # on the VPS:
     sudo ./wg-relay-setup.sh --role vps --peer-pubkey <HOME_KEY>
     # or let it do both sides over ssh:
     sudo ./wg-relay-setup.sh --role vps --peer-ssh root@$HOME_PUBLIC_IP

  ${C_B}Reach the dashboard without exposing it${C_R}:
     ssh -L $DASH_PORT:127.0.0.1:$DASH_PORT root@$VPS_PUBLIC_IP
     then open http://localhost:$DASH_PORT

  ${C_B}Verify${C_R}:
     wg show $RELAY_IFACE          # "latest handshake" must appear
     wg show $CLIENT_IFACE
     systemctl status $UNIT_UDP2RAW --no-pager
     nft list table inet wgrelay
     journalctl -u $UNIT_UDP2RAW -n 30 --no-pager

  ${C_B}From a client${C_R} (a peer created in the dashboard):
     ping 10.10.0.5            # the host you want to reach  -> must work
     curl -k https://10.10.0.5 # must work
     ping $VPS_PUBLIC_IP       # must FAIL (isolation works)
     curl -k https://$VPS_PUBLIC_IP  # must FAIL

EOF
}

# ----------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------
print_status() {
    echo "${C_B}wg-relay status${C_R}"
    if [[ -r $STATE_FILE ]]; then
        echo "  state file : $STATE_FILE"
        sed 's/^/  /' "$STATE_FILE" | grep -v '^  #' | sed -E 's/(UDP2RAW_KEY=).*/\1<hidden>/'
    else
        echo "  state file : (none — not installed yet)"
    fi
    echo
    for i in "$RELAY_IFACE" "$CLIENT_IFACE"; do
        if ip link show dev "$i" >/dev/null 2>&1; then
            echo "  $i: up"
            wg show "$i" 2>/dev/null | sed 's/^/    /' || true
        else
            echo "  $i: not present"
        fi
    done
    echo
    for u in "$UNIT_UDP2RAW" "$UNIT_FW" wireguard-dashboard.service "wg-quick@$RELAY_IFACE" "wg-quick@$CLIENT_IFACE"; do
        printf '  %-34s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
    done
    echo
    if have nft; then
        echo "  nft table inet wgrelay:"
        nft list table inet wgrelay 2>/dev/null | sed 's/^/    /' || echo "    (absent)"
    fi
    echo
    echo "  routes:"
    ip -4 route show 2>/dev/null | grep -E "$RELAY_IFACE|$CLIENT_IFACE" | sed 's/^/    /' || echo "    (none)"
}

# ----------------------------------------------------------------------------
# --snapshot / --diff : prove what changed on this machine
# ----------------------------------------------------------------------------
capture_state() {
    local out=$1
    mkdir -p "$out" 2>/dev/null || return 1
    { ss -H -lntu 2>/dev/null | awk '{print $1, $5}' | LC_ALL=C sort -u; } >"$out/sockets.txt" 2>/dev/null || true
    { ip -4 -o addr show 2>/dev/null | awk '{print $2, $4}'; } >"$out/addr4.txt" 2>/dev/null || true
    { ip -4 route show 2>/dev/null; } >"$out/route4.txt" 2>/dev/null || true
    { ip -6 route show 2>/dev/null | head -50; } >"$out/route6.txt" 2>/dev/null || true
    { iptables-save 2>/dev/null || true; } >"$out/iptables.txt" 2>/dev/null || true
    { nft list ruleset 2>/dev/null | sed -E 's/counter packets [0-9]+ bytes [0-9]+/counter/' || true; } >"$out/nftables.txt" 2>/dev/null || true
    { systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null | awk '{print $1, $3, $4}'; } >"$out/services.txt" 2>/dev/null || true
    { ls -1 /etc/wireguard 2>/dev/null; } >"$out/wireguard.txt" 2>/dev/null || true
    {
        echo "ip_forward=$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)"
        ufw status 2>/dev/null | head -1 || true
        firewall-cmd --state 2>/dev/null || true
    } >"$out/firewall-tools.txt" 2>/dev/null || true
    return 0
}

# everything this setup creates is explained instead of looking suspicious
OURS_RE='wgvpn0|wgrelay0|wg-relay|udp2raw-relay|wireguard-dashboard|inet wgrelay'
build_ours_re() {
    local v p
    for v in RELAY_PORT CLIENT_LISTEN_PORT UDP2RAW_LOCAL_PORT WG_RELAY_LOCAL_PORT DASH_PORT; do
        eval "p=\${$v:-}"
        if [[ $p =~ ^[0-9]+$ ]]; then
            OURS_RE+="|:$p( |$)"
        fi
    done
}

do_snapshot() {
    local ts out
    ts=$(date +%Y%m%d-%H%M%S)
    out="$SNAP_DIR/$ts"
    capture_state "$out" || die "could not write the snapshot to $out"
    ln -sfn "$out" "$SNAP_DIR/latest" 2>/dev/null || true
    find "$SNAP_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | LC_ALL=C sort | head -n -5         | while read -r d; do rm -rf "$d"; done
    ok "baseline recorded: $out"
    info "now install, then run:  sudo $PROG --diff"
}

declare -a GONE_LINES=()
ADDED_TOTAL=0

report_set() {   # $1 label, $2 baseline file, $3 current file, $4 max added lines to print
    local label=$1 bf=$2 cf=$3 max=${4:-10} added gone line n_add n_gone shown
    [[ -r $bf && -r $cf ]] || return 0
    added=$(LC_ALL=C comm -13 <(LC_ALL=C sort -u "$bf") <(LC_ALL=C sort -u "$cf") 2>/dev/null || true)
    gone=$(LC_ALL=C comm -23 <(LC_ALL=C sort -u "$bf") <(LC_ALL=C sort -u "$cf") 2>/dev/null || true)
    n_add=0
    n_gone=0
    if [[ -n $added ]]; then n_add=$(printf '%s\n' "$added" | wc -l); fi
    if [[ -n $gone ]]; then n_gone=$(printf '%s\n' "$gone" | wc -l); fi
    ADDED_TOTAL=$((ADDED_TOTAL + n_add))
    printf '  %-24s +%-3s -%-3s\n' "$label" "$n_add" "$n_gone"
    if (( n_add )); then
        shown=0
        while IFS= read -r line; do
            [[ -z $line ]] && continue
            if (( shown >= max )); then
                printf '      ... (%s more)\n' "$((n_add - shown))"
                break
            fi
            if [[ $line =~ $OURS_RE ]]; then
                printf '      + %s   <- wg-relay\n' "$line"
            else
                printf '      + %s\n' "$line"
            fi
            shown=$((shown + 1))
        done <<<"$added"
    fi
    if (( n_gone )); then
        while IFS= read -r line; do
            [[ -z $line ]] && continue
            if [[ $line =~ $OURS_RE ]]; then
                printf '      - %s   <- wg-relay (expected)\n' "$line"
            else
                printf '      - %s   <- WAS THERE BEFORE, NOW GONE\n' "$line"
                GONE_LINES+=("$label: $line")
            fi
        done <<<"$gone"
    fi
}

do_diff() {
    local base="$SNAP_DIR/latest" cur
    if [[ ! -d $base ]]; then
        die "no baseline found ($base).
Run 'sudo $PROG --snapshot' BEFORE installing, then '--diff' afterwards."
    fi
    cur=$(mktemp -d)
    capture_state "$cur" || die "could not capture the current state"
    build_ours_re
    GONE_LINES=()
    ADDED_TOTAL=0

    printf '\n%s== Changes since baseline %s%s\n\n' "$C_C" "$(basename "$(readlink -f "$base")")" "$C_R"
    report_set "listening sockets"   "$base/sockets.txt"        "$cur/sockets.txt" 12
    report_set "addresses"           "$base/addr4.txt"          "$cur/addr4.txt" 6
    report_set "ipv4 routes"         "$base/route4.txt"         "$cur/route4.txt" 6
    report_set "iptables rules"      "$base/iptables.txt"       "$cur/iptables.txt" 8
    report_set "nftables rules"      "$base/nftables.txt"       "$cur/nftables.txt" 8
    report_set "services"            "$base/services.txt"       "$cur/services.txt" 8
    report_set "/etc/wireguard"      "$base/wireguard.txt"      "$cur/wireguard.txt" 6
    report_set "forward / firewalls" "$base/firewall-tools.txt" "$cur/firewall-tools.txt" 4
    rm -rf "$cur"

    echo
    if (( ${#GONE_LINES[@]} )); then
        warn "${#GONE_LINES[@]} item(s) that existed BEFORE are gone now:"
        printf '      %s\n' "${GONE_LINES[@]}"
        cat <<EOM

  ${C_B}What to do${C_R}
    1. sudo $PROG --uninstall      # removes the relay, keeps everything else
    2. sudo $PROG --diff           # compare again
    3. still missing? restore the machine from your snapshot
       (Proxmox snapshot / VPS provider snapshot / your own backup)

EOM
        return 1
    fi
    cat <<EOM
  ${C_G}Nothing that existed before has disappeared.${C_R}
  $ADDED_TOTAL new item(s), all of them created by wg-relay (marked above).
  Your existing services, routes, firewall rules and ports are untouched.

EOM
    return 0
}

# ----------------------------------------------------------------------------
# uninstall
# ----------------------------------------------------------------------------
do_uninstall() {
    step "Removing everything this script created"
    confirm "Remove the wg-relay configuration on this machine?" || die "aborted"

    local role="${ROLE:-$(state_get ROLE || true)}"
    role=${role:-unknown}

    run systemctl stop "$UNIT_UDP2RAW" 2>/dev/null || true
    run systemctl disable "$UNIT_UDP2RAW" 2>/dev/null || true
    run systemctl stop "wg-quick@$RELAY_IFACE" 2>/dev/null || true
    run systemctl disable "wg-quick@$RELAY_IFACE" 2>/dev/null || true
    run "$FW_HELPER" flush 2>/dev/null || true
    run systemctl stop "$UNIT_FW" 2>/dev/null || true
    run systemctl disable "$UNIT_FW" 2>/dev/null || true

    run rm -f "/etc/systemd/system/$UNIT_UDP2RAW" "/etc/systemd/system/$UNIT_FW"
    run rm -f "/etc/wireguard/${RELAY_IFACE}.conf"
    run rm -f "$FW_HELPER" /etc/sysctl.d/90-wg-relay.conf
    run systemctl daemon-reload

    if [[ $role == vps ]] && (( UNINSTALL == 2 )); then
        run systemctl stop "wg-quick@$CLIENT_IFACE" 2>/dev/null || true
        run systemctl disable "wg-quick@$CLIENT_IFACE" 2>/dev/null || true
        run rm -f "/etc/wireguard/${CLIENT_IFACE}.conf"
        if [[ -x $DASH_SRC_DIR/uninstall.sh ]]; then
            run bash "$DASH_SRC_DIR/uninstall.sh" --yes || warn "dashboard uninstall returned an error"
        fi
    else
        info "the client interface ($CLIENT_IFACE) and the dashboard were left in place"
        info "(add --purge if you want them gone as well)"
    fi

    info "kept for reference: $RELAY_DIR (config, keys, dashboard password)"
    ok "done"
}

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
main() {
    require_linux
    require_root

    if (( STATUS_ONLY )); then
        print_status
        exit 0
    fi

    if (( SNAPSHOT_ONLY )); then
        do_snapshot
        exit 0
    fi

    if (( DIFF_ONLY )); then
        if do_diff; then exit 0; else exit 2; fi
    fi

    if (( UNINSTALL )); then
        do_uninstall
        exit 0
    fi

    [[ -n $ROLE ]] || die "use --role home or --role vps (see --help)"
    [[ $ROLE == home || $ROLE == vps ]] || die "--role must be 'home' or 'vps'"

    assert_single_instance

    if (( CHECK_ONLY )); then
        DRY_RUN=1
        log "check mode: nothing will be changed"
    fi

    umask 022
    case $ROLE in
        home) role_home ;;
        vps)  role_vps ;;
    esac

    if (( ${#PROBLEMS[@]} )); then
        warn "${#PROBLEMS[@]} problem(s) were reported:"
        printf '    - %s\n' "${PROBLEMS[@]}"
    fi
    return 0
}

if [[ ${WGR_SOURCE_ONLY:-0} == 1 ]]; then
    return 0 2>/dev/null || exit 0
fi

main "$@"
