#!/usr/bin/env python3
"""Explicit host and Ubuntu box preparation. No application or data teardown."""
import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
PINS = json.loads((HERE / "versions.lock.json").read_text())
OWNER = "chart-incus-v1"
HOST_CONFIG = Path("/etc/chart-incus/host.json")
STATE = Path("/var/lib/chart-incus")
HOST_PACKAGES = ("incus-base", "incus-client", "btrfs-progs", "uidmap", "iptables", "nftables", "dnsmasq-base")
INOTIFY_SETTINGS = "# Chart development file watchers.\nfs.inotify.max_user_instances = 1024\nfs.inotify.max_user_watches = 1048576\n"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args, *, capture=True, input=None):
    result = subprocess.run([str(a) for a in args], text=True, input=input,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(map(str, args[:4]))}\n"
                           + (result.stderr or "See command output."))
    return result.stdout or ""


def query(path, data=None, method="GET"):
    args = ["incus", "query", "local:" + path]
    if data is not None:
        args += ["-X", method, "--wait", "-d", json.dumps(data)]
    output = run(args)
    return json.loads(output) if output.strip() else None


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def no_symlinks(path):
    path = Path(path)
    require(path.is_absolute(), f"Absolute path required: {path}")
    require(".." not in path.parts, f"Parent traversal forbidden: {path}")
    for part in [path, *path.parents]:
        require(not part.is_symlink(), f"Symlink forbidden: {part}")


def config(path):
    c = json.loads(Path(path).read_text())
    expected = json.loads((HERE / "host.example.json").read_text())
    require(set(expected) <= set(c) <= set(expected) | {'instance_pools'},
            "Host config fields must match host.example.json plus optional instance_pools")
    require(re.fullmatch(r"[a-f0-9]{32}", c["machine_id"]), "Set the actual host machine_id")
    require(re.fullmatch(r"[a-fA-F0-9-]{36}", c["hdd_uuid"]), "Set the actual HDD UUID")
    for key in ("project", "pool"):
        require(re.fullmatch(r"[a-z][a-z0-9-]{1,30}", c[key]), f"Invalid {key}")
    require(re.fullmatch(r"[a-z][a-z0-9]{1,14}", c["bridge"]), "Invalid bridge name")
    require(c["pool_size"] == "200GiB", "Pool resizing requires a separate reviewed change")
    overrides = c.get('instance_pools', {})
    require(isinstance(overrides, dict), 'instance_pools must map box names to pools')
    for name, pool in overrides.items():
        box_name(name)
        require(pool == 'hdd' and pool != c['pool'], 'Only explicit HDD migration overrides are supported')
    interface = ipaddress.IPv4Interface(c["bridge_address"])
    require(interface.network.prefixlen == 24 and interface.ip == interface.network.network_address + 1,
            "Use the first address of a private /24 bridge network")
    require(any(interface.network.subnet_of(ipaddress.ip_network(n)) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")), "Bridge must be RFC1918")
    for key in ("hdd_mount", "boxes_root"):
        no_symlinks(c[key])
    require(Path(c["boxes_root"]) == Path(c["hdd_mount"]) / "shared-dev/boxes", "Unexpected boxes root")
    return c


def check_routes(c, routes):
    network = ipaddress.ip_interface(c["bridge_address"]).network
    for row in routes:
        dst = row.get("dst", "default")
        if dst == "default" or row.get("dev") == c["bridge"]:
            continue
        route = ipaddress.ip_network(dst, strict=False)
        require(not network.overlaps(route), f"Bridge conflicts with route {dst} via {row.get('dev')}")


def check_host(c):
    require(Path("/etc/machine-id").read_text().strip() == c["machine_id"], "Wrong host machine-id")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    require(release.get("ID", "").strip('"') == "ubuntu" and
            release.get("VERSION_ID", "").strip('"') == "26.04", "Host must be Ubuntu 26.04")
    require(run(["dpkg", "--print-architecture"]).strip() == "amd64", "Host must be amd64")
    mount = json.loads(run(["findmnt", "-J", "-M", c["hdd_mount"], "-o", "TARGET,UUID,FSTYPE"]))["filesystems"][0]
    require(mount["target"] == c["hdd_mount"] and mount["uuid"] == c["hdd_uuid"] and mount["fstype"] == "ext4",
            "HDD missing, replaced, or not ext4")
    no_symlinks("/var/lib/incus")
    root_device = run(["findmnt", "-n", "-T", "/", "-o", "MAJ:MIN"]).strip()
    incus_parent = Path("/var/lib/incus") if Path("/var/lib/incus").exists() else Path("/var/lib")
    require(run(["findmnt", "-n", "-T", incus_parent, "-o", "MAJ:MIN"]).strip() == root_device,
            "Incus storage is not on the host root SSD")
    require(Path("/dev/net/tun").is_char_device(), "TUN unavailable")
    check_routes(c, json.loads(run(["ip", "-j", "-4", "route", "show", "table", "all"])))
    require("nf_tables" in run(["iptables", "--version"]), "Review non-nft iptables backend before preparation")


def root():
    require(os.geteuid() == 0, "Apply requires local sudo. Plan and inspect do not require sudo.")


@contextlib.contextmanager
def locked():
    root()
    with open("/run/lock/chart-incus-prep.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_owned(path, content, mode=0o600):
    path = Path(path)
    no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        require(path.read_text() == content, f"Existing file differs; review it before changing: {path}")
        return
    with path.open("x") as handle:
        os.chmod(path, mode)
        handle.write(content)


def resource(kind, name, desired, project=None):
    path = f"/1.0/{kind}"
    suffix = f"?project={project}" if project else ""
    rows = query(path + suffix + ("&" if suffix else "?") + "recursion=1")
    existing = next((r for r in rows if r["name"] == name), None)
    if existing is None:
        if kind == "storage-pools":
            # Incus formats its loop file; an orphan backing file is retained data.
            require(not Path(f"/var/lib/incus/disks/{name}.img").exists(),
                    "Unregistered pool backing file exists; refuse to format it")
            require(not Path(f"/var/lib/incus/storage-pools/{name}").exists(),
                    "Unregistered pool mount directory exists; explicit recovery required")
        query(path + suffix, {"name": name, **desired}, "POST")
        return
    require(existing.get("config", {}).get("user.chart-infra") == OWNER, f"Refusing foreign {kind}/{name}")
    for key, value in desired.items():
        if key == "config":
            for ck, cv in value.items():
                require(existing["config"].get(ck) == cv, f"Conflicting {kind}/{name}: {ck}")
        else:
            require(existing.get(key) == value, f"Conflicting {kind}/{name}: {key}")


def capacity(c):
    disk = shutil.disk_usage("/")
    report = {"host_total_bytes": disk.total, "host_free_bytes": disk.free,
              "host_free_fraction": round(disk.free / disk.total, 4), "review": disk.free / disk.total < .25}
    if c.get('instance_pools'):
        hdd = shutil.disk_usage(c['hdd_mount'])
        report['hdd'] = {'total_bytes': hdd.total, 'free_bytes': hdd.free,
                         'free_fraction': round(hdd.free / hdd.total, 4)}
        report['review'] |= hdd.free < max(hdd.total * .30, 10 * 1024**3)
        report['hdd_pause_heavy_work'] = hdd.free < max(hdd.total * .15, 10 * 1024**3)
    if shutil.which("incus") and os.geteuid() == 0:
        pools = query("/1.0/storage-pools?recursion=1")
        if any(p["name"] == c["pool"] for p in pools):
            space = query(f"/1.0/storage-pools/{c['pool']}/resources")["space"]
            used = space["used"] / space["total"]
            report.update(pool=space, pool_used_fraction=used, pause_heavy_work=used >= .85)
            report["review"] |= used >= .70
    return report


def host_prepare(c, apply, lan_cidr=None):
    check_host(c)
    lan_cidr = resolve_lan(c, lan_cidr)
    report = capacity(c)
    print(json.dumps({"capacity": report, "host_packages": {p: PINS["packages"][p] for p in HOST_PACKAGES},
                      "create": {"pool": c["pool"], "size": c["pool_size"], "bridge": c["bridge_address"],
                                 "project": c["project"], "boxes": c["boxes_root"]},
                      "firewall": "bridge restrictions, Tailscale LAN UDP, scoped iptables acceptance, systemd reconciliation",
                      "lan_cidr": lan_cidr, "inotify_instances": 1024, "inotify_watches": 1048576,
                      "mode": "apply" if apply else "plan"}, indent=2))
    if not apply:
        return
    with locked():
        require(not report["review"] and not report.get("pause_heavy_work"), "Review disk capacity before provisioning")
        if not Path(f"/var/lib/incus/disks/{c['pool']}.img").exists():
            disk = shutil.disk_usage("/")
            require(disk.free - 200 * 1024**3 >= disk.total * .25,
                    "A fully allocated 200GiB pool would violate the 25% SSD reserve")
        if HOST_CONFIG.exists():
            require(json.loads(HOST_CONFIG.read_text()) == c, "Registered host config differs")
        else:
            nft_exists = subprocess.run(["nft", "list", "table", "inet", "chart_inc_us"], capture_output=True).returncode == 0
            chain_exists = subprocess.run(["iptables", "-w", "-S", "CHART-INCUS"], capture_output=True).returncode == 0
            require(not nft_exists and not chain_exists, "Unregistered chart firewall objects exist; review ownership")
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Root-only preservation evidence contains firewall/network state, not Secrets.
        for name, command in {
            "iptables.before": ["iptables-save"], "nft.before": ["nft", "list", "ruleset"],
            "routes.before.json": ["ip", "-j", "route", "show", "table", "all"],
            "packages.before.tsv": ["dpkg-query", "-W"],
        }.items():
            if not (STATE / name).exists():
                write_owned(STATE / name, run(command))
        run(["apt-get", "update"], capture=False)
        selected = [p + "=" + PINS["packages"][p] for p in HOST_PACKAGES]
        simulation = run(["apt-get", "-s", "install", "--no-install-recommends", *selected])
        require(not any(line.startswith("Remv ") for line in simulation.splitlines()), "APT proposes removals")
        for line in simulation.splitlines():
            if line.startswith("Inst ") and re.match(r"Inst \S+ \[", line):
                require(line.split()[1].split(":")[0] in HOST_PACKAGES, "APT proposes unrelated installed-package changes: " + line)
        print(simulation)
        run(["apt-get", "install", "--yes", "--no-remove", "--no-install-recommends", *selected], capture=False)
        run(["systemctl", "enable", "--now", "incus.service"], capture=False)
        server = query("/1.0")
        require(not server["config"].get("core.https_address"), "Incus remote API is enabled; review before proceeding")
        for path in ("/etc/subuid", "/etc/subgid"):
            ranges = [line.split(":") for line in Path(path).read_text().splitlines() if line.startswith("root:")]
            require(any(int(r[2]) >= 4 * 65536 for r in ranges), f"Incus needs operator-reviewed root subordinate IDs in {path}")
        check_host(c)
        owned = {"user.chart-infra": OWNER}
        resource("storage-pools", c["pool"], {"driver": "btrfs", "config": {**owned, "size": c["pool_size"]}})
        pool = query(f"/1.0/storage-pools/{c['pool']}")
        require(pool["config"].get("source") == f"/var/lib/incus/disks/{c['pool']}.img", "Unexpected pool backing file")
        no_symlinks(pool["config"]["source"])
        resource("projects", c["project"], {"config": {**owned, "features.images": "true", "features.profiles": "true",
                 "features.storage.volumes": "true", "features.networks": "false"}})
        write_owned(HOST_CONFIG, json.dumps(c, indent=2) + "\n")
        write_owned("/usr/local/lib/chart-incus/firewall.py", (HERE / "firewall.py").read_text(), 0o700)
        write_owned("/etc/systemd/system/chart-incus-firewall.service", (HERE / "chart-incus-firewall.service").read_text(), 0o644)
        write_tuning(lan_cidr)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "chart-incus-firewall.service"], capture=False)
        run(["systemctl", "reload", "chart-incus-firewall.service"], capture=False)
        apply_inotify()
        resource("networks", c["bridge"], {"type": "bridge", "config": {**owned,
            "ipv4.address": c["bridge_address"], "ipv4.nat": "true", "ipv4.firewall": "false",
            "ipv6.address": "none", "ipv6.firewall": "false", "dns.mode": "none"}})
        no_symlinks(c["boxes_root"])
        Path(c["boxes_root"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        require(Path(c["boxes_root"]).stat().st_uid == 0, "Boxes root must belong to host root")
        print("Host foundation prepared; no instance or application has been started.")


def artifacts(directory, fetch=False):
    directory = Path(directory).absolute()
    no_symlinks(directory)
    entries = {**PINS["image"]["files"], "tailscale.deb": PINS["tailscale"]}
    if fetch:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, item in entries.items():
        path = directory / name
        no_symlinks(path)
        if fetch and not path.exists():
            with tempfile.NamedTemporaryFile(dir=directory, delete=False, prefix=name + ".partial-") as target:
                temporary = Path(target.name)
                with urllib.request.urlopen(item["url"], timeout=60) as response:
                    shutil.copyfileobj(response, target)
            require(digest(temporary) == item["sha256"], f"Downloaded checksum mismatch; retained {temporary}")
            os.link(temporary, path)
            temporary.unlink()
        require(path.is_file() and digest(path) == item["sha256"], f"Missing or invalid pinned artifact: {path}")
    combined = hashlib.sha256()
    for name in ("incus.tar.xz", "rootfs.tar.xz"):
        with (directory / name).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                combined.update(block)
    require(combined.hexdigest() == PINS["image"]["fingerprint"], "Combined Incus fingerprint mismatch")
    return directory


def check_lan(c, cidr, routes):
    network = ipaddress.IPv4Network(cidr)
    require(any(network.subnet_of(ipaddress.IPv4Network(n)) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")), "LAN must be RFC1918")
    require(not network.overlaps(ipaddress.IPv4Interface(c["bridge_address"]).network), "LAN overlaps box bridge")
    uplinks = {r.get("dev") for r in routes if r.get("dst") == "default" and r.get("gateway")}
    require(any(r.get("dst") == str(network) and r.get("scope") == "link" and r.get("dev") in uplinks
                and r.get("dev") not in {c["bridge"], "tailscale0", "CloudflareWARP", "docker0", "cni0", "flannel.1"}
                for r in routes), "LAN must match a connected subnet on a main-table default uplink")
    return str(network)


def resolve_lan(c, cidr=None):
    routes = json.loads(run(["ip", "-j", "-4", "route", "show", "table", "main"]))
    if cidr:
        return check_lan(c, cidr, routes)
    candidates = set()
    for row in routes:
        if row.get("scope") != "link":
            continue
        try:
            candidates.add(check_lan(c, row["dst"], routes))
        except (RuntimeError, ValueError):
            pass
    require(len(candidates) == 1, "Cannot select one LAN automatically; supply --lan-cidr for the intended connected LAN")
    return candidates.pop()


def write_tuning(cidr):
    write_owned("/etc/chart-incus/lan.json", json.dumps({"ipv4_cidrs": [cidr]}, indent=2) + "\n")
    write_owned("/etc/sysctl.d/99-chart-incus-inotify.conf", INOTIFY_SETTINGS, 0o644)


def apply_inotify():
    # Apply only the requested keys; leave unrelated host sysctls untouched.
    run(["sysctl", "--load", "/etc/sysctl.d/99-chart-incus-inotify.conf"], capture=False)
    for name, value in (("max_user_instances", "1024"), ("max_user_watches", "1048576")):
        require(run(["sysctl", "-n", "fs.inotify." + name]).strip() == value, "Inotify setting did not apply")


def host_tune(c, cidr, apply):
    from firewall import TABLE, rules
    check_host(c)
    cidr = resolve_lan(c, cidr)
    print(json.dumps({"mode": "apply" if apply else "plan", "lan": cidr,
                      "allow": "established bridge replies; UDP source 41641 to the selected LAN",
                      "inotify_instances": 1024, "inotify_watches": 1048576}, indent=2))
    if not apply:
        return
    with locked():
        require(json.loads(HOST_CONFIG.read_text()) == c, "Registered host config differs")
        installed = Path("/usr/local/lib/chart-incus/firewall.py")
        no_symlinks(installed)
        expected = {"f9a894eb14ad29337990ce2c53b2bbf7d10af4c51d78fb5b8485f1d3cf075fa7", digest(HERE / "firewall.py")}
        require(installed.is_file() and digest(installed) in expected,
                "Installed firewall has unrecognized edits; review before replacing it")
        # Check the complete atomic nft transaction before updating installed files.
        body = f"delete table inet {TABLE}\n" + rules({**c, "lan_ipv4_cidrs": [cidr]})
        run(["nft", "--check", "-f", "-"], input=body)
        lan = Path("/etc/chart-incus/lan.json")
        settings = Path("/etc/sysctl.d/99-chart-incus-inotify.conf")
        for path in (lan, settings):
            no_symlinks(path)
        require(not lan.exists() or json.loads(lan.read_text()) == {"ipv4_cidrs": [cidr]},
                "Existing LAN settings differ; review before changing the exception")
        require(not settings.exists() or settings.read_text() == INOTIFY_SETTINGS, "Existing chart inotify settings differ")
        backup = STATE / "firewall-backups" / (digest(installed) + ".py")
        write_owned(backup, installed.read_text())
        write_tuning(cidr)
        with tempfile.NamedTemporaryFile(mode="w", dir=installed.parent, delete=False) as output:
            output.write((HERE / "firewall.py").read_text())
            os.chmod(output.name, 0o700)
        os.replace(output.name, installed)
        run(["systemctl", "reload", "chart-incus-firewall.service"], capture=False)
        apply_inotify()
        print("Host tuning applied. Verify direct Tailscale paths from the PC and a LAN laptop; DERP fallback remains available.")


def box_name(value):
    require(re.fullmatch(r"[a-z][a-z0-9-]{1,40}", value) and not value.endswith("-"), "Invalid box name")
    return value


def public_key(path):
    text = Path(path).read_text().strip()
    require("\n" not in text and text.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-nistp256 ")),
            "Supply exactly one OpenSSH public key, without authorized_keys options")
    run(["ssh-keygen", "-l", "-f", path])
    return " ".join(text.split()[:2]) + "\n"


def instance_spec(c, name):
    return {"name": name, "type": "container", "architecture": "x86_64", "profiles": [],
            "source": {"type": "image", "fingerprint": PINS["image"]["fingerprint"]},
            "config": {"user.chart-infra": OWNER, "security.privileged": "false", "security.nesting": "true",
                       "security.idmap.isolated": "true", "security.idmap.size": "65536", "boot.autostart": "false"},
            "devices": {"root": {"type": "disk", "path": "/", "pool": c.get('instance_pools', {}).get(name, c["pool"])},
                        "eth0": {"type": "nic", "network": c["bridge"], "name": "eth0", "security.port_isolation": "true"},
                        "tun": {"type": "unix-char", "source": "/dev/net/tun", "path": "/dev/net/tun"},
                        "data": {"type": "disk", "source": str(Path(c["boxes_root"]) / name),
                                 "path": "/srv/chart/data", "shift": "true", "required": "true"}}}


def inspect():
    print(json.dumps({"machine_id": Path("/etc/machine-id").read_text().strip(),
        "hdd": json.loads(run(["findmnt", "-J", "-M", "/mnt/hdd", "-o", "TARGET,UUID,FSTYPE"])),
        "ssd": dict(zip(("total", "used", "free"), shutil.disk_usage("/"))),
        "routes_ipv4": json.loads(run(["ip", "-j", "-4", "route", "show", "table", "all"])),
        "incus_installed": bool(shutil.which("incus")),
        "privileged_firewall_inspection": os.geteuid() == 0}, indent=2))


def status(c):
    check_host(c)
    print(json.dumps(capacity(c), indent=2))
    if os.geteuid() != 0 or not shutil.which("incus"):
        print("Incus/btrfs status requires an installed Incus and local sudo; not verified.")
        return
    print(run(["incus", "list", "local:", "--project", c["project"]]))
    print(run(["incus", "image", "list", "local:", "--project", c["project"]]))
    print(run(["incus", "storage", "info", "local:" + c["pool"]]))
    print(run(["btrfs", "filesystem", "usage", "-b", f"/var/lib/incus/storage-pools/{c['pool']}"]))
    for box in query(f"/1.0/instances?project={c['project']}&recursion=1"):
        name = box["name"]
        snapshots = query(f"/1.0/instances/{name}/snapshots?project={c['project']}&recursion=1")
        print(json.dumps({"box": name, "snapshots": [s["name"] for s in snapshots]}))
        if box["status"] != "Running":
            print("Stopped: per-box Docker/source usage unmeasured.")
            continue
        prefix = ["incus", "exec", "local:" + name, "--project", c["project"], "--"]
        for command in (["docker", "system", "df"], ["du", "-sx", "--block-size=1", "/srv/chart/source"]):
            try:
                print(run([*prefix, *command]))
            except RuntimeError:
                print(f"{name}: {command[0]} measurement unavailable; guest may be unprepared.")
    print("Review btrfs metadata pressure and unexpected growth even below thresholds. No cleanup is performed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    fetch = sub.add_parser("fetch", help="Download and verify pinned public artifacts; does not deploy")
    fetch.add_argument("--artifacts", required=True)
    for name in ("host-prepare", "host-tune", "status"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        if name != "status":
            p.add_argument("--apply", action="store_true", help="Apply; omission prints a plan")
        if name in ("host-tune", "host-prepare"):
            p.add_argument("--lan-cidr", help="Connected RFC1918 LAN; auto-select only when unambiguous")
    args = parser.parse_args()
    if args.command == "inspect":
        inspect()
    elif args.command == "fetch":
        print("Verified artifacts:", artifacts(args.artifacts, fetch=True))
    else:
        c = config(args.config)
        if args.command == "host-prepare":
            host_prepare(c, args.apply, args.lan_cidr)
        elif args.command == "host-tune":
            host_tune(c, args.lan_cidr, args.apply)
        else:
            status(c)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print("Refused:", error, file=sys.stderr)
        sys.exit(1)
