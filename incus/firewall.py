#!/usr/bin/env python3
"""Host bridge firewall; installed as an operator-owned systemd service."""
import fcntl
import ipaddress
import json
import subprocess
from pathlib import Path

TABLE = "chart_inc_us"
CHAIN = "CHART-INCUS"


def rules(config):
    bridge = config["bridge"]
    # Identifiers originate in the reviewed host config, never in guest input.
    if not bridge.isalnum() or len(bridge) > 15:
        raise ValueError("Invalid bridge name")
    address = str(ipaddress.IPv4Interface(config["bridge_address"]).ip)
    return f'''table inet {TABLE} {{
  chain input {{
    type filter hook input priority -10; policy accept;
    iifname "{bridge}" meta nfproto ipv6 drop
    iifname "{bridge}" udp sport 68 udp dport 67 accept
    iifname "{bridge}" ip daddr {address} udp dport 53 accept
    iifname "{bridge}" ip daddr {address} tcp dport 53 accept
    iifname "{bridge}" reject with icmpx type admin-prohibited
  }}
  chain forward {{
    type filter hook forward priority -10; policy accept;
    iifname "{bridge}" meta nfproto ipv6 drop
    oifname "{bridge}" meta nfproto ipv6 drop
    iifname "{bridge}" oifname "{bridge}" reject with icmpx type admin-prohibited
    iifname "{bridge}" oifname {{ "tailscale0", "CloudflareWARP" }} reject with icmpx type admin-prohibited
    iifname "{bridge}" ip daddr {{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 }} reject with icmpx type admin-prohibited
    oifname "{bridge}" ct state established,related accept
    oifname "{bridge}" udp dport 41641 accept
    oifname "{bridge}" reject with icmpx type admin-prohibited
  }}
  chain output {{
    type filter hook output priority -10; policy accept;
    oifname "{bridge}" meta nfproto ipv6 drop
    oifname "{bridge}" udp sport 67 udp dport 68 accept
    oifname "{bridge}" ct state established,related accept
    oifname "{bridge}" udp dport 41641 accept
    oifname "{bridge}" reject with icmpx type admin-prohibited
  }}
}}
'''


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def apply(config):
    bridge = config["bridge"]
    body = rules(config)
    exists = subprocess.run(["nft", "list", "table", "inet", TABLE],
                            capture_output=True).returncode == 0
    if exists:
        body = f"delete table inet {TABLE}\n" + body
    # One atomic nft transaction installs all restrictions before FORWARD accepts.
    run("nft", "--check", "-f", "-", input=body)
    run("nft", "-f", "-", input=body)
    if subprocess.run(["iptables", "-w", "-S", CHAIN], capture_output=True).returncode:
        run("iptables", "-w", "-N", CHAIN)
    desired = [
        ["-i", bridge, "-j", "ACCEPT"],
        ["-o", bridge, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-o", bridge, "-p", "udp", "--dport", "41641", "-j", "ACCEPT"],
    ]
    # Only this dedicated chain is flushed; shared Docker/k3s chains are retained.
    run("iptables", "-w", "-F", CHAIN)
    for rule in desired:
        run("iptables", "-w", "-A", CHAIN, *rule)
    # Relocate only our jump if another daemon inserted rules ahead of it.
    while subprocess.run(["iptables", "-w", "-C", "FORWARD", "-j", CHAIN],
                         capture_output=True).returncode == 0:
        run("iptables", "-w", "-D", "FORWARD", "-j", CHAIN)
    run("iptables", "-w", "-I", "FORWARD", "1", "-j", CHAIN)
    for proto, port in [("udp", "67"), ("udp", "53"), ("tcp", "53")]:
        rule = ["-i", bridge, "-p", proto, "--dport", port, "-m", "comment",
                "--comment", "chart-incus-dhcp-dns", "-j", "ACCEPT"]
        if subprocess.run(["iptables", "-w", "-C", "INPUT", *rule], capture_output=True).returncode:
            run("iptables", "-w", "-I", "INPUT", "1", *rule)
    run("nft", "list", "table", "inet", TABLE)
    run("iptables", "-w", "-S", CHAIN)


if __name__ == "__main__":
    with open("/run/lock/chart-incus-firewall.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        apply(json.loads(Path("/etc/chart-incus/host.json").read_text()))
