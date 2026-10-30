#!/usr/bin/env bash
# =============================================================================
#  home-setup.sh — run this ON THE MACHINE THAT HOSTS MATTERMOST
#                  (the one behind the static IP 94.183.52.210)
#
#  Thin wrapper around wg-relay-setup.sh, so nobody has to remember --role.
#
#     sudo ./home-setup.sh                       # first run
#     sudo ./home-setup.sh --peer-pubkey <VPS>   # after the VPS run
#     sudo ./home-setup.sh --check               # dry run, changes nothing
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/wg-relay-setup.sh" --role home "$@"
