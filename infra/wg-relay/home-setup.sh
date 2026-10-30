#!/usr/bin/env bash
# Copyright (C) 2026 HusainAlhajHasan1 — SPDX-License-Identifier: AGPL-3.0-or-later
# =============================================================================
#  home-setup.sh — run this ON THE MACHINE THAT RUNS THE SERVICE
#                  (the one behind the public IP)
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
