# WireGuard relay: public VPS → udp2raw → home server

A ready-to-run, two-machine setup that lets WireGuard clients reach **only your
Mattermost server at home**, through your VPS, without those clients being able
to touch anything on the VPS — and without disturbing the services that already
run on either machine.

* **Yes, it is possible**, and nothing has to be stopped or reconfigured on the
  VPS (it keeps its 2 WireGuard servers, 2 Telegram bots, udp2raw tunnel and
  Mattermost) or at home (Mattermost, Caddy, AdGuard LXCs).
* Everything is added **next to** what already exists, with its own interface
  names, ports, subnets, systemd units and its own nftables table.
* Configuring that by hand is a lot of error-prone work, so this folder contains
  two scripts that do it (plus one config file).

```
infra/wireguard-relay/
├── home-setup.sh          <- run this on the home machine (Mattermost side)
├── vps-setup.sh           <- run this on the VPS
├── wg-relay-setup.sh      <- the engine (both wrappers call it, --role home|vps)
├── relay.conf.example     <- copy to relay.conf and adjust
└── README.md
```

---

## 1. The picture

```
            phone / laptop / tablet
                     │
                     │  WireGuard,  UDP  5.75.198.81:<client port>
                     ▼
┌───────────────────────────── VPS 5.75.198.81 ──────────────────────────────┐
│                                                                            │
│   wgvpn0  10.66.0.1/24        ← the new WireGuard server, peers are        │
│      │                          managed by the wireguard-dashboard         │
│      │  forward:  only  dst ∈ HOME_DESTS  and  out via wgrelay0            │
│      │  anything else  →  DROP   (your existing Mattermost, SSH, the       │
│      │                            bots, the other WG servers … unreachable)│
│   wgrelay0  10.67.0.2/32      ← uplink, no listening port at all           │
│      │  endpoint 127.0.0.1:51830                                           │
│   udp2raw (client)            ← fake-TCP stream towards home               │
│                                                                            │
│   (untouched: 2× wg server, 2× telegram bot, 1× udp2raw, Mattermost)       │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  fake-TCP (looks like a TCP session)
                                    │  port 45731, both directions
┌───────────────────────────────────▼────  home 94.183.52.210 ───────────────┐
│   udp2raw (server)  0.0.0.0:45731  →  127.0.0.1:51840                      │
│   wgrelay0  10.67.0.1/32      ← no public WireGuard port (nft drops it)    │
│      │  forward: only dst ∈ HOME_DESTS, only the ports you listed          │
│   Mattermost 10.10.0.x   ·   AdGuard 10.10.0.53   ·   Caddy               │
└────────────────────────────────────────────────────────────────────────────┘
```

Traffic path for a client:

```
client → wgvpn0 (VPS) → route 10.10.0.0/24 dev wgrelay0 → udp2raw → home
       → wgrelay0 (home) → [SNAT] → Mattermost → back the same way
```

No NAT on the VPS, no full-tunnel routing, no default-route changes.

---

## 2. What is created (and what is not touched)

| object | VPS | home | note |
|---|---|---|---|
| interface `wgvpn0` | ✅ | – | clients, managed by the dashboard |
| interface `wgrelay0` | ✅ | ✅ | the tunnel between the two machines |
| `udp2raw-relay.service` | ✅ client | ✅ server | one extra instance, own ports |
| `wg-relay-firewall.service` | ✅ | ✅ | applies `table inet wgrelay` |
| `table inet wgrelay` (nftables) | ✅ | ✅ | **own table**, policy `accept`, only matches our own interfaces |
| dashboard (`/opt/wireguard-dashboard`) | ✅ | – | your repo, its own install script |
| routes | `10.67.0.1/32` + `HOME_DESTS` via `wgrelay0` | `10.66.0.0/24` via `wgrelay0` | only those, nothing else |
| `net.ipv4.ip_forward` | set to 1 *if it was 0* | set to 1 *if it was 0* | it is already 1 on a machine that runs WireGuard |

**Never done by the scripts:** no `iptables -F`, no `nft flush ruleset`, no
restart of a unit it did not create, no change of `wg0`/`wg1`/existing configs,
no change of the default route, no change of DNS on the host, no port reuse
(every port is checked first and must be free), no deletion of existing peers.

The iptables shim (`wg-relay-fw`) only inserts ACCEPT rules that match
`-i/-o wgvpn0` and `-i/-o wgrelay0`. If your VPS has a `FORWARD DROP` policy
(ufw, docker, firewalld) those two rules are what makes the relay work — they
cannot affect traffic of any other service.

---

## 3. Requirements

1. Home router: forward **TCP *and* UDP** on `RELAY_PORT` to the home machine.
   udp2raw's fake-TCP transport is neither a real TCP connection nor plain UDP,
   so both must arrive. (WireGuard itself is never exposed: it listens on
   `127.0.0.1` only and nftables drops it on any other interface.)
2. Both machines: Linux with systemd, `python3`, and the WireGuard kernel module.
   *Proxmox LXC:* the module must be loaded on the Proxmox **host**
   (`modprobe wireguard`), the container needs `NET_ADMIN` + `NET_RAW`
   (unprivileged containers have both by default) and it must be allowed to set
   `net.ipv4.ip_forward` — if it is not, add to `/etc/pve/lxc/<id>.conf`:
   `lxc.sysctl.net.ipv4.ip_forward = 1` and restart the container once.
3. Home side: the machine must be able to reach Mattermost (same LAN/LXC bridge).
4. VPS: a free UDP port for clients, a free TCP port for the dashboard
   (both are picked automatically and checked first).
5. Take a snapshot of the VPS first if your provider offers it. Not because the
   script is destructive (it is not), but because it costs nothing.

---

## 4. How to run it (3 commands + 1 test)

Copy this folder to both machines (e.g. `scp -r infra/wireguard-relay root@HOST:/root/`).

### Step 1 — home machine

```bash
cd /root/wireguard-relay
cp relay.conf.example relay.conf
nano relay.conf            # set HOME_DESTS (your home LAN) and HOME_PUBLIC_IP
sudo ./home-setup.sh --config relay.conf
```

It prints (and writes into `/etc/wireguard-relay/`):

```
home public key : xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx=
udp2raw key     : ...            (already stored in /etc/wireguard-relay/relay.conf)
relay port      : 45731
```

### Step 2 — VPS

Copy the **master** config (it now contains the udp2raw key and the relay port):

```bash
scp root@94.183.52.210:/etc/wireguard-relay/relay.conf ./relay.conf
sudo ./vps-setup.sh --config relay.conf --peer-pubkey '<home public key>'
```

The script installs `github.com/HusainAlhajhasan1/wireguard-dashboard`, points it
at `wgvpn0`, sets the client defaults to *split tunnel to `HOME_DESTS`*, and
prints:

```
vps public key  : yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy=
dashboard       : http://127.0.0.1:8080   (ssh -L 8080:127.0.0.1:8080 root@5.75.198.81)
login           : admin / <generated>     (cat /etc/wireguard-relay/dashboard-admin.txt)
```

### Step 3 — home machine again (close the key exchange)

```bash
sudo ./home-setup.sh --peer-pubkey '<vps public key>'
```

*Or let the VPS do it for you* — if you can `ssh root@94.183.52.210` from the VPS:

```bash
sudo ./vps-setup.sh --config relay.conf --peer-ssh root@94.183.52.210
```

The VPS then fetches / pushes both keys by itself (home script must have been run
once before).

### Step 4 — create a client

Dashboard → *Peers → Add peer* (or `wgdash add phone`). The generated config
already contains:

```
AllowedIPs = 10.10.0.0/24          # = HOME_DESTS, nothing else
Endpoint   = 5.75.198.81:51820     # whatever port the script picked
DNS        = 10.10.0.53            # only if you set HOME_DNS
MTU        = 1280
```

---

## 5. Prove that it works — and that the VPS is protected

On the VPS:

```bash
wg show wgrelay0            # "latest handshake" must be recent
wg show wgvpn0              # your phone must appear with a handshake
systemctl status udp2raw-relay --no-pager
nft list table inet wgrelay
```

On the client:

```bash
ping 10.10.0.5                       # Mattermost host     -> must work
curl -k https://10.10.0.5            # Mattermost UI       -> must work
ping 10.10.0.53                      # AdGuard (if enabled)-> must work

ping 5.75.198.81                     # the VPS itself      -> MUST FAIL
curl -k https://5.75.198.81          # VPS Mattermost      -> MUST FAIL
ssh root@5.75.198.81                 # -> MUST FAIL / time out
```

Those last three are the whole isolation guarantee:

* the client's `AllowedIPs` does not contain the VPS address, so nothing is even
  sent to it through the tunnel;
* if a client crafts such traffic anyway, the packet arrives on `wgvpn0` and the
  nftables `input` chain drops it: `iifname "wgvpn0" drop`;
* traffic to the internet does not work either (`iifname "wgvpn0" drop` in
  `forward`), so the VPS is not an exit node for the clients.

---

## 6. How the isolation is implemented

Only one small nftables table is added; it matches **only** packets that come
from / go to the two new interfaces:

```nft
table inet wgrelay {

  chain input {                       # clients must not reach the VPS itself
        type filter hook input priority filter; policy accept;
        iifname "wgvpn0" drop
  }

  chain forward {                     # clients may only reach home_nets, via wgrelay0
        type filter hook forward priority filter; policy accept;
        iifname "wgvpn0" oifname "wgrelay0" ip daddr @home_nets accept
        iifname "wgvpn0" drop
        iifname "wgrelay0" oifname "wgvpn0" accept
        iifname "wgrelay0" drop
  }

  chain mss { ... tcp option maxseg size set 1240 ... }
}
```

`policy accept` plus interface-scoped matches is the key: every other packet on
the host is evaluated exactly as before, so your existing WireGuard servers,
Telegram bots and Mattermost keep working. (An `accept` in a separate nft table
cannot override a `drop` somewhere else either — which is why the helper also
inserts the two interface-scoped iptables ACCEPTs for ufw/docker style setups.)

On the home side the same table restricts what the clients may reach:

```nft
iifname "wgrelay0" icmp type { echo-request, destination-unreachable, time-exceeded } accept
iifname "wgrelay0" tcp dport { 80, 443, 8065 } accept     # HOME_ALLOW_TCP
iifname "wgrelay0" udp dport { 53 } accept                # HOME_ALLOW_UDP
iifname "wgrelay0" drop
iifname != "lo" udp dport 51840 drop                      # WireGuard only via udp2raw

chain forward { ... iifname "wgrelay0" ip daddr @home_nets accept; iifname "wgrelay0" drop ... }
chain postrouting { ip saddr @client_nets ip daddr @home_nets masquerade }   # HOME_SNAT
```

### Why SNAT at home?

If Mattermost runs in its **own** LXC (different IP than the relay host), its
replies would go to the LXC's default gateway (your router), which knows nothing
about `10.66.0.0/24` — the connection would hang. With `HOME_SNAT="yes"` the
clients appear to the LAN as the relay host, replies come back there and are
forwarded into the tunnel. That works for **any** LAN topology without touching
the Mattermost LXC.

Trade-off: Mattermost sees the relay host's IP instead of the client IP. If you
prefer real client IPs, set `HOME_SNAT="no"` and give every destination host a
route `10.66.0.0/24 via 10.67.0.1` (the relay LXC's LAN address), or simply put
the relay on the same LXC as Mattermost.

---

## 7. Troubleshooting

| symptom | check |
|---|---|
| `wg show wgrelay0` never shows a handshake | `journalctl -u udp2raw-relay -f`. On the VPS you must see "connected"; if not, the router at home is not forwarding **TCP+UDP** on `RELAY_PORT`. |
| udp2raw logs `connection reset` / `RST` | faketcp needs `-a` (already in the generated `udp2raw.conf`) and `iptables` installed. Inside an LXC without iptables, run `udp2raw --conf-file … -g`, add the printed rules to the LXC config, or use `--mode udp`. |
| handshake ok, but Mattermost is slow / hangs on big pages | MTU/MSS. Keep `RELAY_MTU=1280`, `CLIENT_MTU=1280`, `MSS_CLAMP=1240`; add `UDP2RAW_EXTRA_ARGS="--fix-gro"` on the receiving side if you see packet loss on cloud NICs. |
| a service on the VPS broke | run `sudo ./vps-setup.sh --status` and `nft list table inet wgrelay`; the only global change is `net.ipv4.ip_forward` (and the two interface-scoped ACCEPT rules). Undo with `sudo ./vps-setup.sh --uninstall`. |
| dashboard unreachable | it binds `127.0.0.1` by default: `ssh -L 8080:127.0.0.1:8080 root@5.75.198.81`, then <http://localhost:8080>. Or set `DASH_DOMAIN` (Caddy must already be installed on the VPS). |
| `Cannot create a WireGuard interface` at home | unprivileged LXC without the module on the Proxmox host — see *Requirements*. |
| tunnel port blocked by the ISP | try `--mode udp` (still obfuscated, just plain UDP on the wire) or another `RELAY_PORT`. |

Useful one-liners:

```bash
sudo ./vps-setup.sh --status        # interfaces, units, nft table, routes
sudo ./vps-setup.sh --check         # re-run all validations, change nothing
sudo ./vps-setup.sh --uninstall     # remove the relay (keeps dashboard + wgvpn0)
sudo ./vps-setup.sh --uninstall --purge   # remove those too
```

---

## 8. FAQ

**Why udp2raw at all?** Your existing setup uses it, and it hides WireGuard from
ISP-level filtering. If your ISP does not block WireGuard you can drop udp2raw
later: put `Endpoint = 94.183.52.210:<port>` directly on `wgrelay0` (VPS) and
publish the home WireGuard port.

**Do I need a second WireGuard interface on the VPS?** Yes — and it is deliberate.
The dashboard rewrites its own config file whenever a peer is added; keeping the
uplink on a separate interface (`wgrelay0`) means the dashboard can never break
or show the tunnel peer as a "client".

**Can the clients use the VPS as a normal VPN?** No, by design: only `HOME_DESTS`
is routed and everything else is dropped. If you want a normal VPN as well, keep
using your existing WireGuard servers on the VPS.

**IPv6?** Everything here is IPv4-only. IPv6 traffic from clients simply stays
outside the tunnel (split tunnel), so it behaves like it did before.

**What if I already use `10.66.0.0/24` or `10.67.0.0/24` somewhere?** Change
`CLIENT_NET` / `RELAY_VPS_IP` / `RELAY_HOME_IP` in `relay.conf` — the pre-flight
check refuses to continue on a clash instead of quietly breaking routing.

**Is the dashboard exposed to the internet?** Only if you ask for it. Default is
`127.0.0.1:8080` (use an SSH tunnel). `DASH_DOMAIN` puts it behind Caddy with
HTTPS and validates + rolls back the Caddyfile if Caddy refuses the new config.
