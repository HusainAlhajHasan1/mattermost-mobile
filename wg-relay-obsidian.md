# WireGuard → VPS → udp2raw → home Mattermost

> [!important] What this note covers
> This is a record of the configuration that you reported working, including Mattermost Calls. Commands are labeled by the machine where they run. It is a recovery and reference guide, not a promise that a new installation will be disruption-free. Check existing ports, routes, firewall rules, services, and backups before changing a server.
>
> The Calls firewall guard below is a **separate manual addition**. It is not built into the published `wg-relay-setup.sh` v1.0.5 script. Copying or reinstalling the script alone does not recreate the guard.

## 1. Map of the setup

```text
Phone WireGuard app
  └─ UDP 20000 → VPS 5.75.198.81 (wgvpn0)
      └─ WireGuard wgrelay0 → local udp2raw client
          └─ fake TCP 20000 → home public IP 94.183.52.210
              └─ home relay LXC 192.168.0.49 (wgrelay0)
                  ├─ Caddy HTTPS + AdGuard DNS on 192.168.0.49
                  └─ Mattermost Calls UDP 8443 → 192.168.0.40
```

| Item | Value |
|---|---|
| VPS public IP | `5.75.198.81` |
| Home public IP | `94.183.52.210` |
| Home relay, Caddy and AdGuard LXC | `192.168.0.49` |
| Mattermost LXC | `192.168.0.40` |
| Mattermost domain | `mattermost.m.com` |
| Mattermost Calls ICE host | `192.168.0.40` |
| Mattermost Calls UDP listener | `192.168.0.40:8443` |
| Client VPN network | `10.66.0.0/24` |
| VPS client VPN gateway | `10.66.0.1` |
| Relay addresses | Home `10.67.0.1`, VPS `10.67.0.2` |
| VPS client-facing interface / port | `wgvpn0`, UDP `20000` |
| Relay interface | `wgrelay0` on both machines |
| Home udp2raw port | fake TCP `20000` |
| udp2raw local / home WireGuard port | `20001`, loopback |
| Dashboard | `127.0.0.1:8080` on the VPS |
| Home public WireGuard key | `BY8LiThSro8rm78tk1kd7tm7LVPmQ67d10KDlgAW0Aw=` |
| VPS relay public WireGuard key | `xCq3Ay/ZBbTYI5ZHjVKdf1Bx8GXMgHe9UfCSAogQFSc=` |

These values describe this pair only. For a second independent pair choose and check new IP ranges, ports, interfaces, service names and keys; the scripts are not guaranteed to support multiple instances on the same host.

> [!warning] Security and backups
> Never post private WireGuard keys, preshared keys, `relay.conf`, udp2raw secrets, dashboard passwords, TURN credentials, or full WireGuard client profiles. The dashboard password was exposed in chat: rotate it. Back up `/etc/wireguard-relay`, relevant files under `/etc/wireguard`, the dashboard database, and firewall/service configuration securely before changing anything. `SaveConfig = false`: ensure dashboard-created peers are saved in the persistent configuration, not only in the live interface. Do not publish backups to GitHub.

## 2. Where to run commands

- **Termux:** the phone terminal, normally showing `~ $`.
- **Home relay:** root shell on `192.168.0.49`, usually `root@wireguadAdguardCaddy`.
- **Mattermost LXC:** root shell on `192.168.0.40`, usually `root@mattermost`.
- **VPS:** root shell on `5.75.198.81`, usually `root@HX`.
- **Proxmox host:** the host running the LXCs, not the relay LXC.

Connect from Termux where reachable:

```bash
ssh root@5.75.198.81
# Or, when your existing home network/VPN can reach it:
ssh root@192.168.0.49
```

Run commands directly as root; no `sudo` is required in these root shells. Do not paste Markdown links in place of URLs or type placeholders surrounded by `< >` into the shell.

## 3. Current client configuration

The WireGuard app's peer profile must route these two destinations through the VPS VPN:

```ini
AllowedIPs = 192.168.0.49/32, 192.168.0.40/32
DNS = 192.168.0.49
```

Keep the other existing profile values (private key, address, endpoint, peer key, preshared key, etc.) unchanged. After editing, reconnect the profile. AdGuard must resolve `mattermost.m.com` to `192.168.0.49`; you reported updating that record. Verify the app is actually using AdGuard, particularly if Android Private DNS or another DNS override is enabled.

Caddy uses a **local CA** certificate. Each client needs to trust the correct Caddy local root CA for HTTPS verification; some Android apps do not trust user-installed CAs. Obtain the CA certificate over a trusted channel and verify its fingerprint; do not disable certificate validation as a permanent workaround.

Allowing `.49:443` also allows access to other sites served by that Caddy instance; you explicitly accepted this. AdGuard is also accessible on DNS port 53.

## 4. Successful home-side setup and Calls guard

> [!warning] Already working?
> Do not rerun installation commands just to check status. The setup restarts relay interfaces, can briefly disconnect clients, updates firewall state, and may affect a production system. The guard below protects `.40` from broad forwarding access; install and verify it **before** adding `.40` as an allowed destination. Other firewall tables and host policies still apply.

### 4.1 Download the in-repository version (home relay)

The in-repository generic version is on the Arena branch. This download overwrites matching script files in `/root/wg-relay`; back up local modifications first.

```bash
mkdir -p /root/wg-relay
cd /root/wg-relay
curl -fL https://codeload.github.com/HusainAlhajHasan1/mattermost-mobile/tar.gz/refs/heads/arena/832c15bb-mattermost-mobile -o /tmp/wg-relay-source.tar.gz
tar -xz -f /tmp/wg-relay-source.tar.gz --strip-components=3 --wildcards '*/infra/wg-relay/*'
```

### 4.2 Install the separate Calls guard (home relay)

This matches the guard used for the successful call. It restricts packets entering `wgrelay0` and forwarded to `192.168.0.40` to UDP port 8443. It does not filter other paths or other sites on `.49`. A separate systemd unit and dependency make the guard persistent and require it before the home `wgrelay0` service starts.

```bash
mkdir -p /etc/wireguard-relay
cat >/etc/wireguard-relay/calls-guard.nft <<'EOF'
add table inet wg_calls_guard
delete table inet wg_calls_guard
add table inet wg_calls_guard
add chain inet wg_calls_guard forward_guard { type filter hook forward priority -10; policy accept; }
add rule inet wg_calls_guard forward_guard iifname "wgrelay0" ip daddr 192.168.0.40 meta l4proto != udp drop
add rule inet wg_calls_guard forward_guard iifname "wgrelay0" ip daddr 192.168.0.40 udp dport != 8443 drop
EOF

cat >/etc/systemd/system/wg-relay-calls-guard.service <<'EOF'
[Unit]
Description=Restrict relay access to Mattermost Calls
Before=wg-quick@wgrelay0.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/wireguard-relay/calls-guard.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/systemd/system/wg-quick@wgrelay0.service.d
cat >/etc/systemd/system/wg-quick@wgrelay0.service.d/calls-guard.conf <<'EOF'
[Unit]
Requires=wg-relay-calls-guard.service
After=wg-relay-calls-guard.service
EOF

systemctl daemon-reload
nft -c -f /etc/wireguard-relay/calls-guard.nft && \
  systemctl enable --now wg-relay-calls-guard.service
```

Stop if the last command fails. Check the guard:

```bash
systemctl is-enabled wg-relay-calls-guard
systemctl status wg-relay-calls-guard --no-pager
nft list table inet wg_calls_guard
```

The expected unit state is enabled and active (exited); check the nft rules, not just the unit status. The table uses `wgrelay0` and the exact `.40` address—change it deliberately if your topology changes.

### 4.3 Configure home relay (home relay)

```bash
cd /root/wg-relay
./home-setup.sh \
  --home-public-ip 94.183.52.210 \
  --home-dests "192.168.0.49/32, 192.168.0.40/32" \
  --home-allow-tcp "53 80 443" \
  --home-allow-udp "53" \
  --home-dns 192.168.0.49
```

For the original first-time key exchange, after installing the VPS side, the home side was also run with the VPS **relay** public key:

```bash
cd /root/wg-relay
./home-setup.sh \
  --home-public-ip 94.183.52.210 \
  --home-dests "192.168.0.49/32, 192.168.0.40/32" \
  --home-allow-tcp "53 80 443" --home-allow-udp "53" \
  --home-dns 192.168.0.49 \
  --peer-pubkey 'xCq3Ay/ZBbTYI5ZHjVKdf1Bx8GXMgHe9UfCSAogQFSc='
```

On an existing setup this can restart the relay; it is not needed for a routine status check. If an override must be retained on an older script version, pass it explicitly again when rerunning.

The home router was configured to forward **TCP and UDP port 20000** to `192.168.0.49`; preserve the working router configuration. The relay uses udp2raw `faketcp`. Do not change the existing direct home `wg0` service.

## 5. VPS setup

### 5.1 Original config transfer (home relay)

The generated config includes a secret, so transfer it only over SSH and keep its permissions restrictive:

```bash
scp /etc/wireguard-relay/relay.conf root@5.75.198.81:/root/wg-relay/relay.conf
```

**Important:** the in-repo v1.0.5 setup script can prefer the pre-existing `/etc/wireguard-relay/relay.conf` over the copied file even when `--config relay.conf` is passed. This happened during the successful setup. Do not assume copying the file updated the VPS. The explicit CLI overrides below are essential for the current destinations and DNS. The older script also does not persist every DNS/port override in the master config; pass them again on future reruns and verify the output.

### 5.2 Preview without changing the VPS

```bash
cd /root/wg-relay
./vps-setup.sh --check \
  --home-dests "192.168.0.49/32, 192.168.0.40/32" \
  --home-dns 192.168.0.49 \
  --peer-pubkey 'BY8LiThSro8rm78tk1kd7tm7LVPmQ67d10KDlgAW0Aw='
```

Check `reachable` and any warnings. A check is not a guarantee the actual change will succeed.

### 5.3 Apply the current settings (VPS)

```bash
cd /root/wg-relay
./vps-setup.sh \
  --home-dests "192.168.0.49/32, 192.168.0.40/32" \
  --home-dns 192.168.0.49 \
  --peer-pubkey 'BY8LiThSro8rm78tk1kd7tm7LVPmQ67d10KDlgAW0Aw='
```

This can restart `wgrelay0`, `wgvpn0`, udp2raw and the relay firewall; clients may disconnect briefly. Existing unrelated services should not be intentionally restarted, but zero disruption is not guaranteed. Check the final summary and live peer AllowedIPs:

```bash
wg show wgrelay0
wg show wgvpn0
nft list table inet wgrelay
```

Do not share private keys or preshared keys. The VPS relay peer should include `.49/32` and `.40/32` as well as the home relay address. Note that running the VPS setup may also refresh dashboard settings/service.

## 6. Dashboard from Termux

The dashboard listens on the VPS loopback address, not on the public Internet. Keep it that way.

In **Termux**, run this and keep the SSH session open:

```bash
ssh -N -L 8080:127.0.0.1:8080 root@5.75.198.81
```

On the **same phone**, open a browser at:

```text
http://127.0.0.1:8080
```

If port 8080 is already in use on the phone, use `ssh -N -L 18080:127.0.0.1:8080 root@5.75.198.81` and open `http://127.0.0.1:18080`. Closing the SSH session closes the dashboard tunnel.

Username was `admin`. The initial password was exposed; rotate it. On the VPS use the dashboard's supported password-change method, or, if this version of the CLI supports it:

```bash
cd /opt/wireguard-dashboard
./venv/bin/python -m wgdash.cli --config /etc/wireguard-dashboard/config.json passwd --help
```

The CLI supports `passwd -u admin -p PASSWORD` in the version inspected, but putting a password on a command line can expose it to shell history and process listings. Prefer a dashboard UI or interactive password method if available. If using CLI, take suitable precautions and verify the installed CLI's help first. Never copy a password into this note or Git.

New peer profiles may contain old defaults if settings were not updated: inspect their AllowedIPs and DNS privately before importing. Do not delete working peers merely to edit a phone profile.

## 7. Verification and troubleshooting

### 7.1 Home relay

```bash
wg show wgrelay0
systemctl status udp2raw-relay wg-relay-firewall wg-relay-calls-guard --no-pager
nft list table inet wgrelay
nft list table inet wg_calls_guard
journalctl -u udp2raw-relay -n 30 --no-pager
```

AdGuard DNS and Caddy:

```bash
nslookup mattermost.m.com. 192.168.0.49
curl -m 5 -I http://192.168.0.40:8065/
```

For a **temporary connectivity diagnostic only**, the following skips TLS verification. Do not use `-k` as a permanent client setting:

```bash
curl -k -m 5 -I --resolve mattermost.m.com:443:192.168.0.49 https://mattermost.m.com/
```

The DNS answer should be `192.168.0.49`. The last curl was observed to return HTTP 200 through Caddy. It does not prove certificate trust.

### 7.2 Mattermost LXC

```bash
ss -lunp
```

The working listener was `192.168.0.40:8443` UDP, owned by the Calls plugin. The configured ICE host was `192.168.0.40`.

### 7.3 VPS

```bash
wg show wgrelay0
wg show wgvpn0
systemctl status udp2raw-relay wg-relay-firewall wireguard-dashboard --no-pager
nft list table inet wgrelay
journalctl -u udp2raw-relay -n 30 --no-pager
```

A recent relay handshake verifies the WireGuard link, not the entire application path. On-demand client peers may have an old handshake if the phone is idle.

### 7.4 Phone / Termux

Use the new VPS WireGuard profile. If testing from home Wi-Fi, a local route for `192.168.0.0/24` can conflict with the VPN route; use mobile data or a non-overlapping network for a clear test.

```bash
ping -c 3 192.168.0.49
curl -k -m 5 -I --resolve mattermost.m.com:443:192.168.0.49 https://mattermost.m.com/
```

The curl is a connectivity diagnostic only. Test Mattermost normally with trusted TLS, then start a call. UDP port 8443 must be routed to `.40`; a successful web page does not by itself prove calls will work.

To test VPS isolation, temporarily add `10.66.0.1/32` to the phone profile's AllowedIPs, reconnect, and try `ping -c 3 10.66.0.1`. It should fail. Remove the temporary route afterwards. A timeout alone isn't proof of isolation—also inspect the VPS firewall and test deliberately. Pinging the public VPS IP is **not** a valid split-tunnel isolation test; it normally bypasses the VPN.

## 8. Restart and persistence

Check enabled services **without changing them**:

Home relay:

```bash
systemctl is-enabled wg-quick@wgrelay0 udp2raw-relay wg-relay-firewall wg-relay-calls-guard
```

VPS:

```bash
systemctl is-enabled wg-quick@wgrelay0 wg-quick@wgvpn0 udp2raw-relay wg-relay-firewall wireguard-dashboard
```

Check IPv4 forwarding on both:

```bash
sysctl net.ipv4.ip_forward
```

It must report `1`. The setup may leave persistence dependent on existing settings when forwarding was already enabled; verify it again after reboot. On the Proxmox host, ensure both necessary LXCs have **Start at boot** enabled. Service enablement inside an LXC does not start the LXC itself.

Do a planned reboot test one machine at a time. Check service status, guard table, forwarding, relay handshake, dashboard, Mattermost, and a call. Keep out-of-band access and backups available. Do not assume an untested reboot will always succeed.

## 9. Update the standalone GitHub repository from Termux

The standalone repository is `https://github.com/HusainAlhajHasan1/wg-relay`. The current generic scripts are in the `infra/wg-relay` directory of the `mattermost-mobile` Arena branch. **The standalone repo was not updated by the agent:** earlier write access failed. The steps below let you review and publish using your own authenticated GitHub access.

> [!warning] Scope and secrets
> The v1.0.5 scripts do **not** include the manual Calls guard or automatically solve all of the limitations documented here. Publishing the scripts and this note does not change the live servers. Do not copy `relay.conf`, keys, database, passwords, or client profiles into the repository. The generic version uses AGPL-3.0-or-later notices; review the license and `NOTICE` before publishing.

In **Termux**, install the tools if necessary:

```bash
pkg update
pkg install git curl tar gh
```

Authenticate using the prompts, without posting credentials:

```bash
gh auth login
gh auth status
```

Clone the standalone repository into a **new** directory. If it already exists locally, use a different directory or inspect your existing checkout instead of overwriting it:

```bash
cd ~
git clone https://github.com/HusainAlhajHasan1/wg-relay.git wg-relay-review
cd wg-relay-review
git status
git branch --show-current
```

Fetch the generic scripts into a separate temporary directory, then copy only the seven named project files. This overwrites those files in the clone, not on your servers; it leaves other tracked files intact.

```bash
mkdir -p ~/wg-relay-source
curl -fL https://codeload.github.com/HusainAlhajHasan1/mattermost-mobile/tar.gz/refs/heads/arena/832c15bb-mattermost-mobile -o ~/wg-relay-source.tar.gz
tar -xz -f ~/wg-relay-source.tar.gz -C ~/wg-relay-source --strip-components=3 --wildcards '*/infra/wg-relay/*'

cp ~/wg-relay-source/LICENSE ~/wg-relay-source/NOTICE ~/wg-relay-source/README.md ~/wg-relay-source/relay.conf.example ~/wg-relay-source/home-setup.sh ~/wg-relay-source/vps-setup.sh ~/wg-relay-source/wg-relay-setup.sh .

for f in home-setup.sh vps-setup.sh wg-relay-setup.sh; do bash -n "$f" || break; done
git status --short
git diff
git diff --stat
```

`git diff` doesn't show contents of newly untracked files; inspect those separately. Review the differences and ensure they don't remove changes you want to keep. Optionally copy this Obsidian note into the clone **only after reviewing its IP addresses and domain**, because a public repository makes them public. In Termux, use your actual file location; e.g. after `termux-setup-storage`, a note saved in Downloads might be:

```bash
cp ~/storage/downloads/wg-relay-obsidian.md ./OBSIDIAN-SETUP.md
```

Only run that copy if the file exists there and you want to publish it. If you add it, include `OBSIDIAN-SETUP.md` in the `git add` command below. Otherwise omit it.

After reviewing:

```bash
git add LICENSE NOTICE README.md relay.conf.example home-setup.sh vps-setup.sh wg-relay-setup.sh
git diff --cached
git commit -m "Update generic wg-relay setup"
```

If you also want the note, run `git add OBSIDIAN-SETUP.md` **before** the commit. If Git asks for an author identity, configure your preferred GitHub name and email locally; never add credentials to files or chat.

Check which branch you are on and whether the remote has moved:

```bash
git branch --show-current
git fetch origin
git status -sb
```

Push your current branch explicitly, replacing `BRANCH_NAME` with the branch name shown above:

```bash
git push origin BRANCH_NAME
```

If Git rejects the push because the remote moved, **do not force-push**. Fetch and review the divergence, then integrate changes safely or ask for help. If the branch is protected, push to a branch permitted by the repository and open a pull request with `gh pr create` according to that repository's policy. Publishing requires GitHub permission for this standalone repository.

## 10. Known limitations of the published scripts

- The manual Calls guard in section 4.2 is not part of v1.0.5. The base home forwarding rule permits all ports to destinations in `HOME_DESTS`; without the guard, `.40` would have broader exposure.
- Existing VPS master configuration can override a newly copied `relay.conf`. Use explicit CLI overrides and verify live settings.
- The current script may not persist DNS and home port overrides in the generated config. Pass explicit values on relevant reruns.
- Warnings about destinations not being on a local subnet are only hints: routed destinations can be valid. The earlier claim that the old `10.10.0.5` address was proven nonexistent was incorrect.
- No script can guarantee zero breakage or complete isolation across all existing firewall, routing, Android and network configurations. Verify on the actual hosts.
