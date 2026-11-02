#!/usr/bin/env bash
# Read-only tablet diagnostics. Public-IP checks contact Cloudflare; DNS check contacts Google.
set -Eeuo pipefail
readonly SERIAL='5203c66b523ca357'
command -v adb >/dev/null 2>&1 || { echo 'STOP: adb is not installed on this Linux host.' >&2; exit 1; }
command -v timeout >/dev/null 2>&1 || { echo 'STOP: timeout is required on this Linux host.' >&2; exit 1; }
state=$(timeout 25 adb -s "$SERIAL" get-state 2>/dev/null | tr -d '\r') || { echo 'STOP: cannot reach the tablet over ADB.' >&2; exit 1; }
[[ "$state" == device ]] || { echo "STOP: tablet ADB state is $state; start and authorize Android." >&2; exit 1; }
model=$(timeout 25 adb -s "$SERIAL" shell getprop ro.product.model | tr -d '\r') || exit 1
[[ "$model" == SM-P585 ]] || { echo "STOP: unexpected tablet model: $model" >&2; exit 1; }
rootinfo=$(timeout 25 adb -s "$SERIAL" shell 'su -c id' | tr -d '\r') || { echo 'STOP: tablet root is unavailable.' >&2; exit 1; }
[[ "$rootinfo" == uid=0\(* ]] || { echo 'STOP: ADB shell does not have root on the tablet.' >&2; exit 1; }
echo 'Checking the tablet (not the Linux host). Cloudflare and Google will receive network queries.'
echo 'This is read-only. Network checks can take up to a minute.'
if ! result=$(timeout 140 adb -s "$SERIAL" shell 'su -c sh' 2>&1 <<'PHONE'
printf '\n== TIME AND REGION ==\n'
printf 'Timezone property: '; getprop persist.sys.timezone
printf 'Automatic timezone: '; settings --user 0 get global auto_time_zone
printf 'System time: '; date '+%Y-%m-%d %H:%M:%S %z %Z'
printf 'Locale: '; getprop persist.sys.locale

printf '\n== MOCK LOCATION ==\n'
dumpsys location 2>/dev/null | grep -E 'Mock Providers:|Location\[(gps|network).*mock' || echo 'No matching mock provider found.'
printf 'Location mode: '; settings --user 0 get secure location_mode

printf '\n== SIM AND CARRIER PROPERTIES ==\n'
for k in gsm.sim.state gsm.sim.operator.numeric gsm.sim.operator.iso-country gsm.sim.operator.alpha gsm.operator.numeric gsm.operator.iso-country gsm.operator.alpha; do
    printf '%s=' "$k"; getprop "$k"
done

printf '\n== SCANNING SETTINGS ==\n'
for k in wifi_scan_always_enabled ble_scan_always_enabled; do
    printf '%s=' "$k"; settings --user 0 get global "$k"
done

printf '\n== WATCHER ==\n'
mod=/data/adb/modules/p585_manama_privacy
pid=$(cat "$mod/worker.pid" 2>/dev/null || true)
case "$pid" in
    ''|*[!0-9]*) echo 'Privacy watcher: NOT VERIFIED' ;;
    *) if [ -r "/proc/$pid/cmdline" ] && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -F "$mod/service.sh" >/dev/null; then
           echo 'Privacy watcher: RUNNING'
       else echo 'Privacy watcher: NOT VERIFIED'; fi ;;
esac

printf '\n== WIFI ADDRESSES AND ROUTES ==\n'
ip addr show dev wlan0 2>/dev/null | grep -E 'inet |inet6 ' || echo 'No wlan0 address found.'
printf 'IPv4 default route: '; ip -4 route show default 2>/dev/null
printf 'IPv6 default route: '; ip -6 route show default 2>/dev/null

printf '\n== DNS AND VPN INDICATORS ==\n'
for k in net.dns1 net.dns2 net.dns3 net.dns4; do
    value=$(getprop "$k")
    [ -z "$value" ] || printf '%s=%s\n' "$k" "$value"
done
conn=$(dumpsys connectivity 2>/dev/null)
printf '%s\n' "$conn" | grep -Eo 'DnsAddresses: \[[^]]*\]' | sort -u
if printf '%s\n' "$conn" | grep -Eiq 'NetworkAgentInfo.*type: VPN|type: VPN.*CONNECTED|TRANSPORT_VPN'; then
    echo 'VPN-related network listed (may not apply to every app).'
else
    echo 'No VPN network found by this check (not conclusive).'
fi
proxy=$(settings --user 0 get global http_proxy 2>/dev/null)
case "$proxy" in ''|null|:0) echo 'Global HTTP proxy: none reported' ;; *) echo 'Global HTTP proxy: configured (details hidden)' ;; esac
printf 'Private DNS setting: '; settings --user 0 get global private_dns_mode

printf '\n== PUBLIC INTERNET IP / APPROXIMATE COUNTRY ==\n'
echo 'Testing from the tablet using Cloudflare (not the Linux host).'
url=https://www.cloudflare.com/cdn-cgi/trace
bb=/data/adb/magisk/busybox
found=0
if command -v curl >/dev/null 2>&1; then
    for flag in -4 -6; do
        trace=$(curl "$flag" -fsS --connect-timeout 8 --max-time 18 "$url" 2>/dev/null || true)
        if printf '%s\n' "$trace" | grep -q '^ip='; then
            printf 'Result %s:\n' "$flag"
            printf '%s\n' "$trace" | grep -E '^(ip|loc|warp)='
            found=1
        else
            printf 'Result %s: unavailable\n' "$flag"
        fi
    done
fi
if [ "$found" = 0 ] && [ -x "$bb" ]; then
    trace=$("$bb" timeout 20 "$bb" wget -q -O - -T 15 "$url" 2>/dev/null || true)
    if printf '%s\n' "$trace" | grep -q '^ip='; then
        echo 'BusyBox result (TLS certificate validation may be limited):'
        printf '%s\n' "$trace" | grep -E '^(ip|loc|warp)='
        found=1
    fi
fi
[ "$found" = 1 ] || echo 'Public IP could not be retrieved; no host IP was substituted.'

printf '\n== DNS RESOLVER OBSERVATION ==\n'
if [ -x "$bb" ]; then
    echo 'Google DNS diagnostic (one query; not a complete DNS leak test):'
    "$bb" timeout 15 "$bb" nslookup -type=txt o-o.myaddr.l.google.com 2>/dev/null || echo 'Resolver observation unavailable.'
else
    echo 'Resolver observation unavailable (BusyBox not found).'
fi
printf '\nFinished. IPs and DNS addresses above are sensitive.\n'
printf '%s\n' 'P585_DIAGNOSTIC_FINISHED'
PHONE
); then
    printf '%s\n' "$result" >&2
    echo 'STOP: tablet diagnostic timed out or ADB disconnected.' >&2
    exit 1
fi
result=${result//$'\r'/}
if [[ "$result" != *P585_DIAGNOSTIC_FINISHED* ]]; then
    printf '%s\n' "$result" >&2
    echo 'STOP: tablet did not confirm diagnostic completion.' >&2
    exit 1
fi
printf '%s\n' "${result%P585_DIAGNOSTIC_FINISHED*}"
