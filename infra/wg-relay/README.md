# wg-relay

**WireGuard clients → your VPS → an obfuscated udp2raw tunnel → one machine in
your LAN.**

Use it when you want to reach a service at home (Mattermost, Nextcloud, Home
Assistant, a NAS, a camera, an SSH jump host, a game server …) over a clean VPN,
but:

* your home connection has **no usable public IP** (CGNAT, dynamic IP, or you
  simply don't want to publish it),
* the entry point must be a **VPS you already run**, and the clients must
  **not** be able to touch anything on that VPS,
* the link between VPS and home must survive **DPI / traffic shaping** on your
  ISP (that is what udp2raw's fake-TCP mode is for),
* and **nothing that already runs on either machine may be disturbed**.

Two scripts, one config file. Both sides get their own interface names, ports,
subnets, systemd units and their own nftables table — everything is *added*
next to what already exists, never in place of it.

```
.
├── home-setup.sh          run on the machine in your LAN (the service side)
├── vps-setup.sh           run on the public VPS
├── wg-relay-setup.sh      the engine (both wrappers call it, --role home|vps)
├── relay.conf.example     copy to relay.conf and adjust
├── LICENSE                AGPL-3.0 (verbatim)
├── NOTICE                 copyright + how to apply it
└── README.md
```

---

## 1. The picture

```
            phone / laptop / tablet
                     │
                     │  WireGuard, UDP  VPS.IP:<client port>
                     ▼
┌─────────────────────────────── VPS ────────────────────────────────────────┐
│  wgvpn0  10.66.0.1/24   ← client WireGuard server (peers via the dashboard)│
│     │  forward:  only  dst ∈ HOME_DESTS  and  out via wgrelay0             │
│     │  anything else  →  DROP  (SSH, the web server, other VPNs … stay      │
│     │                            unreachable from the tunnel)              │
│  wgrelay0 10.67.0.2/32  ← uplink, no listening (WireGuard) port at all     │
│  udp2raw (client)       ← fake-TCP stream towards home                     │
│  (untouched: every service that was already on the VPS)                    │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  fake-TCP (looks like a TCP session)
                                    │  port 45731, both directions
┌───────────────────────────────────▼────── home public IP ──────────────────┐
│  udp2raw (server)  0.0.0.0:45731  →  127.0.0.1:51840                       │
│  wgrelay0  10.67.0.1/32  ← WireGuard bound to loopback, nft drops the rest │
│     │  forward: only dst ∈ HOME_DESTS, only the ports you listed           │
│  your service 10.10.0.5:8065  ·  other hosts in the LAN                    │
└────────────────────────────────────────────────────────────────────────────┘
```

A client's packet:

```
client → wgvpn0 (VPS) → route HOME_DESTS dev wgrelay0 → udp2raw → home
       → wgrelay0 (home) → SNAT → your service → back the same way
```

No NAT on the VPS, no full-tunnel routing, no default-route change, and the
home side exposes **no WireGuard port** to the internet — only the udp2raw port,
which to a scanner looks like an ordinary TCP service.

---

## 2. What is created (and what is never touched)

| object | VPS | home | note |
|---|---|---|---|
| `wgvpn0` | ✅ | – | clients, manageable from the dashboard |
| `wgrelay0` | ✅ | ✅ | the tunnel between the two machines |
| `udp2raw-relay.service` | ✅ client | ✅ server | one extra instance, own ports |
| `wg-relay-firewall.service` | ✅ | ✅ | applies `table inet wgrelay` |
| `table inet wgrelay` (nftables) | ✅ | ✅ | own table, policy `accept`, matches only our interfaces |
| routes | `10.67.0.1/32` + `HOME_DESTS` via `wgrelay0` | `10.66.0.0/24` via `wgrelay0` | only those |
| `net.ipv4.ip_forward` | set to 1 *if it was 0* | set to 1 *if it was 0* | already 1 on any WireGuard host |
| dashboard | ✅ | – | optional, see §7 |

**Never done:** no `iptables -F`, no `nft flush ruleset`, no restart of a unit it
did not create, no change to existing `wg*.conf`, no default-route change, no
DNS change on the host, no port reuse (every port is checked and must be free),
no deletion of existing peers.

Ports: nothing standard is used. `RELAY_PORT`, `CLIENT_LISTEN_PORT` and the two
loopback ports are picked automatically from 20000–60999 and verified free —
there is no 51820 anywhere unless you ask for it.

---

## 3. Requirements

1. **Home router**: forward **TCP *and* UDP** on `RELAY_PORT` to the home
   machine. udp2raw's fake-TCP is neither a real TCP connection nor plain UDP,
   so both must arrive.
2. Both machines: Linux, systemd, `python3`, `wireguard-tools`, and the
   WireGuard kernel module.
   *Proxmox LXC:* `modprobe wireguard` on the **host**, `NET_ADMIN` + `NET_RAW`
   in the container (default for unprivileged), and if the container may not
   set sysctls, add `lxc.sysctl.net.ipv4.ip_forward = 1` to
   `/etc/pve/lxc/<id>.conf` and restart it once. The **home side also needs
   NAT** in the container, i.e. `nf_nat` + `nft_chain_nat` loaded on the host:
   `modprobe nf_nat nft_chain_nat` (and put both names in
   `/etc/modules-load.d/netfilter-nat.conf` so it survives a reboot). The
   script probes this first and tells you before changing anything.
3. Home side: the machine must be able to reach the service (same LAN/bridge).
4. VPS: two free ports (client WireGuard, dashboard). Picked automatically.
5. Optional but free: a provider snapshot of the VPS before you start.

---

## 4. Quick start

> **No `sudo`?** A minimal Debian/Ubuntu container often ships without it. If you
> are already `root`, just drop the `sudo` from every command below — the scripts
> only need root privileges, not a particular way of obtaining them.

Copy this folder to both machines:

```bash
scp -r wg-relay root@HOME.IP:/root/ && scp -r wg-relay root@VPS.IP:/root/
```

### Step 1 — home machine

```bash
cd /root/wg-relay
cp relay.conf.example relay.conf
nano relay.conf          # set HOME_PUBLIC_IP and HOME_DESTS

sudo ./home-setup.sh --check        # validates everything, changes nothing
sudo ./home-setup.sh --snapshot     # optional: record the current state
sudo ./home-setup.sh                # installs; prints the relay port + home pubkey
```

Open `RELAY_PORT` (TCP **and** UDP) on the home router towards this machine.

### Step 2 — VPS

```bash
cd /root/wg-relay
scp root@HOME.IP:/etc/wireguard-relay/relay.conf .   # the master config
sudo ./vps-setup.sh --check
sudo ./vps-setup.sh --snapshot
sudo ./vps-setup.sh --peer-pubkey '<home public key from step 1>'
```

It installs the client WireGuard server, the udp2raw client, the isolation
firewall, and the dashboard (Flask + waitress behind `127.0.0.1`, admin
password in `/etc/wireguard-relay/dashboard-admin.txt`). It prints the VPS
public key.

### Step 3 — home again

```bash
sudo ./home-setup.sh --peer-pubkey '<vps public key from step 2>'
```

### Step 4 — test

Add a peer in the dashboard (`wg-quick`-style `[Peer]` blocks work too),
import the config into the WireGuard app, connect, then:

```bash
ping 10.10.0.5                 # the host you want to reach     → must work
ping 127.0.0.1                 # anything on the VPS            → must fail
curl -m 3 http://10.10.0.5:8065/   # your service
```

Or let the script do both machines over ssh:

```bash
sudo ./vps-setup.sh --peer-ssh root@HOME.IP
```

---

## 5. Prove that nothing broke

```bash
sudo ./vps-setup.sh --snapshot      # before
sudo ./vps-setup.sh --diff          # after
```

`--snapshot` records listening sockets, addresses, routes, iptables and nftables
rules (counters stripped), running services, `/etc/wireguard`, `ip_forward` and
the firewall daemon state into `/etc/wireguard-relay/baseline/` (last 5 kept).

`--diff` compares the live machine with that baseline and labels every line it
created with `← wg-relay`. Anything that existed before and is now **gone** is
printed in red and the command exits **2** — that is your alarm bell, and it
comes with the fix to apply.

```
  listening sockets        +3   -0
      + udp 0.0.0.0:51820   ← wg-relay
  ipv4 routes              +2   -0
  services                 +2   -0

  Nothing that existed before has disappeared.
```

---

## 6. How the isolation works

Two directions, two rules, both scoped to our own interfaces:

* **VPS**: `iifname "wgvpn0"` → only `10.67.0.2` (the tunnel) and `HOME_DESTS`
  are accepted; anything else — the VPS's own services, its other WireGuard
  servers, its SSH — is dropped. Clients cannot even see that the VPS has a
  web server.
* **Home**: `iifname "wgrelay0"` → only `HOME_DESTS` and only the ports in
  `HOME_ALLOW_TCP` / `HOME_ALLOW_UDP`; the rest of the LAN is unreachable.
  The home WireGuard port is additionally dropped on every interface except
  `lo`, so it is only reachable through udp2raw.
* **iptables shim** (`/usr/local/sbin/wg-relay-fw`): three `ACCEPT` rules that
  match `-i/-o wgvpn0` and `-i/-o wgrelay0`, for hosts whose `FORWARD` policy
  is `DROP` (ufw, docker, firewalld). They cannot match any other traffic.

The nftables table has `policy accept`, so a packet that does not match any of
our rules is left exactly as it was — that is what makes the whole thing safe
to add to a busy machine.

---

## 7. Recipes

All of these are the same install; only the destination and the allowed ports
change.

| you want to reach | `HOME_DESTS` | `HOME_ALLOW_TCP` |
|---|---|---|
| Mattermost | `10.10.0.5/32` | `8065` (plus `443` behind Caddy) |
| Nextcloud | `10.10.0.10/32` | `443` |
| Home Assistant | `10.10.0.20/32` | `8123` |
| NAS / file share | `10.10.0.30/32` | `443 445 5000 5001` |
| SSH jump host | `10.10.0.5/32` | `22` |
| Windows RDP | `10.10.0.40/32` | `3389` |
| IP camera | `10.10.0.60/32` | `554 80` |
| game server | `10.10.0.50/32` | `25565` |
| the whole LAN | `10.10.0.0/24` | whatever you need |

```bash
sudo ./home-setup.sh --home-dests 10.10.0.5/32 \
                     --home-allow-tcp "8065 443" --home-allow-udp "53"
```

DNS: set `HOME_DNS=10.10.0.53` so clients get names (the pushed DNS must be
inside `HOME_DESTS`).

---

## 8. More than one installation

**Different machines → just run it again.** Nothing is shared: each pair has
its own config, state and ports.

Give the second pair its own addresses so the two tunnels can never be confused
(important if the two home networks or the two VPSs are ever routed together):

```bash
# pair #2
CLIENT_NET=10.76.0.0/24
RELAY_VPS_IP=10.77.0.2
RELAY_HOME_IP=10.77.0.1
RELAY_PORT=41234            # or leave 0
```

**Same machine → not supported, by design.** The state directory
`/etc/wireguard-relay`, the unit names and the nft table are fixed, so a second,
differently configured installation would silently overwrite the first. The
script detects this and refuses:

```
[✗] a different wg-relay installation already exists on this machine (role home).
    - CLIENT_NET      10.66.0.0/24  vs  10.76.0.0/24
```

Re-running with the *same* values is always fine (that is how upgrades and
re-runs work). To actually replace one: `--uninstall` first, or use another
machine. Run several tunnels on one host by giving each its own instance
directory, interface names and unit names via config — not wired in yet.

---

## 9. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| handshake never completes | `RELAY_PORT` not forwarded, or only UDP/TCP forwarded | forward **both**, check `ss -lunp \| grep udp2raw` on both sides |
| tunnel up, nothing reachable | `HOME_DESTS` wrong, or service on a different host without SNAT | keep `HOME_SNAT=yes`, check `nft list table inet wgrelay` |
| clients can reach the VPS | a `FORWARD ACCEPT` rule of another firewall sitting before ours | `wg-relay-fw` inserts at the top; re-run the script |
| relay drops after minutes | ISP shaping or MTU | lower `RELAY_MTU` / `CLIENT_MTU`, try `--mode udp` |
| `masquerade … Operation not supported` (LXC) | the Proxmox **host** has no `nf_nat`/`nft_chain_nat` loaded | `modprobe nf_nat nft_chain_nat` on the host, persist it in `/etc/modules-load.d/`, re-run; or `--set HOME_SNAT=no` if the service is on the relay host itself |
| `port … is already in use` | another service owns it | the script stops before changing anything — pick another |
| `ping` works, HTTP doesn't | MSS clamping missing | re-run the script (it sets `MSS_CLAMP`), or lower the client MTU |
| dashboard does not start | missing `python3-venv`, no internet for pip | the relay works without it; `journalctl -u wireguard-dashboard -n 50` |

---

## 10. Undo

```bash
sudo ./vps-setup.sh --uninstall          # removes everything this script created
sudo ./vps-setup.sh --uninstall --purge  # also the dashboard and wgvpn0
sudo ./vps-setup.sh --diff               # confirm the machine is back to baseline
```

Every file it overwrote is copied to `/var/backups/wg-relay/<timestamp>/`.
Nothing else on the machine is touched, and the script never touches the
default route, `iptables` policies or `nft flush`.

---

## 11. FAQ

**Does it need a public IP at home?** No — only an outbound connection. The home
side *dials out* through udp2raw; the VPS is the only public entry. A static IP
at home is used only to make the udp2raw endpoint stable.

**Why udp2raw on top of WireGuard?** To make the traffic look like an ordinary
TCP session instead of WireGuard's identifiable UDP pattern. `--mode udp` keeps
the obfuscation and drops the fake-TCP layer if you don't need it.

**Can clients use the VPS as a general internet exit?** Not with this config.
If you want that, it is a different product — here the tunnel *only* carries
`HOME_DESTS`.

**IPv6?** Not covered. The relay is IPv4-only on purpose; add `Table=off`-style
routes yourself if you need v6 destinations.

**License:** AGPL-3.0-or-later — see `LICENSE` and `NOTICE`. If you run a
modified version as a network service, the license asks you to publish the
source.
