#!/usr/bin/env bash
# Copyright (C) 2026 HusainAlhajHasan1 — SPDX-License-Identifier: AGPL-3.0-or-later
# =============================================================================
#  vps-setup.sh — run this ON THE PUBLIC VPS
#
#  Thin wrapper around wg-relay-setup.sh, so nobody has to remember --role.
#  It adds a new WireGuard server (+ dashboard) next to the services that are
#  already running there and leaves every existing service untouched.
#
#     sudo ./vps-setup.sh --peer-pubkey <HOME_KEY>
#     sudo ./vps-setup.sh --peer-ssh root@HOME.IP   # does both sides
#     sudo ./vps-setup.sh --check                         # dry run
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/wg-relay-setup.sh" --role vps "$@"
