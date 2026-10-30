#!/usr/bin/env bash
# =============================================================================
#  vps-setup.sh — run this ON THE PUBLIC VPS (5.75.198.81)
#
#  Thin wrapper around wg-relay-setup.sh, so nobody has to remember --role.
#  It adds a new WireGuard server (+ dashboard) next to the services that are
#  already running there and leaves every existing service untouched.
#
#     sudo ./vps-setup.sh --peer-pubkey <HOME_KEY>
#     sudo ./vps-setup.sh --peer-ssh root@94.183.52.210   # does both sides
#     sudo ./vps-setup.sh --check                         # dry run
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/wg-relay-setup.sh" --role vps "$@"
