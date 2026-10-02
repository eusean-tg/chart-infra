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
    require(set(c) == set(expected), "Host config fields must match host.example.json")
    require(re.fullmatch(r"[a-f0-9]{32}", c["machine_id"]), "Set the actual host machine_id")
    require(re.fullmatch(r"[a-fA-F0-9-]{36}", c["hdd_uuid"]), "Set the actual HDD UUID")
    for key in ("project", "pool"):
        require(re.fullmatch(r"[a-z][a-z0-9-]{1,30}", c[key]), f"Invalid {key}")
    require(re.fullmatch(r"[a-z][a-z0-9]{1,14}", c["bridge"]), "Invalid bridge name")
    require(c["pool_size"] == "200GiB", "Pool resizing requires a separate reviewed change")
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
    if shutil.which("incus") and os.geteuid() == 0:
        pools = query("/1.0/storage-pools?recursion=1")
        if any(p["name"] == c["pool"] for p in pools):
            space = query(f"/1.0/storage-pools/{c['pool']}/resources")["space"]
            used = space["used"] / space["total"]
            report.update(pool=space, pool_used_fraction=used, pause_heavy_work=used >= .85)
            report["review"] |= used >= .70
    return report


def host_prepare(c, apply):
    check_host(c)
    report = capacity(c)
    print(json.dumps({"capacity": report, "host_packages": {p: PINS["packages"][p] for p in HOST_PACKAGES},
                      "create": {"pool": c["pool"], "size": c["pool_size"], "bridge": c["bridge_address"],
                                 "project": c["project"], "boxes": c["boxes_root"]},
                      "firewall": "bridge-only nft restrictions, scoped iptables acceptance, systemd reconciliation",
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
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "chart-incus-firewall.service"], capture=False)
        run(["systemctl", "reload", "chart-incus-firewall.service"], capture=False)
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
            "devices": {"root": {"type": "disk", "path": "/", "pool": c["pool"]},
                        "eth0": {"type": "nic", "network": c["bridge"], "name": "eth0", "security.port_isolation": "true"},
                        "tun": {"type": "unix-char", "source": "/dev/net/tun", "path": "/dev/net/tun"},
                        "data": {"type": "disk", "source": str(Path(c["boxes_root"]) / name),
                                 "path": "/srv/chart/data", "shift": "true", "required": "true"}}}


def validate_instance(c, name, obj):
    wanted = instance_spec(c, name)
    require(obj["profiles"] == [], "Unexpected inherited profiles")
    require(obj["devices"] == wanted["devices"], "Instance devices differ; do not adopt or reconfigure")
    for k, v in wanted["config"].items():
        require(obj["config"].get(k) == v, "Instance config differs: " + k)
    require(not any(k.startswith(("raw.", "limits.")) for k in obj["config"]), "Unexpected raw options or resource limits")
    require(obj["config"].get("volatile.base_image") == PINS["image"]["fingerprint"], "Wrong Ubuntu image")
    require(all(k in wanted["config"] or k.startswith(("image.", "volatile.")) for k in obj["config"]),
            "Unexpected instance configuration; review before using this instance")


def box_create(c, args):
    check_host(c)
    name = box_name(args.name)
    key = public_key(args.ssh_key)
    print(json.dumps({"mode": "apply" if args.apply else "plan", "instance": instance_spec(c, name),
                      "ssh_key_sha256": hashlib.sha256(key.encode()).hexdigest(), "result": "stopped box"}, indent=2))
    if not args.apply:
        return
    with locked():
        require(HOST_CONFIG.exists() and json.loads(HOST_CONFIG.read_text()) == c, "Run host-prepare first")
        require(not capacity(c)["review"], "Review capacity before creating another box")
        directory = artifacts(args.artifacts)
        boxes = query(f"/1.0/instances?project={c['project']}&recursion=1")
        existing = next((b for b in boxes if b["name"] == name), None)
        private = STATE / "boxes" / name
        target = Path(c["boxes_root"]) / name
        marker = target / ".chart-incus-box.json"
        identity = {"owner": OWNER, "name": name, "machine_id": c["machine_id"], "hdd_uuid": c["hdd_uuid"]}
        no_symlinks(target)
        if existing:
            validate_instance(c, name, existing)
            require(marker.is_file() and json.loads(marker.read_text()) == identity, "Missing or changed HDD box marker")
            require((private / "authorized_key").read_text() == key, "Public key differs; no implicit key rotation")
            print("Existing owned box retained; no start or reconfiguration.")
            return
        # The private intent permits recovery only of a marked, still-empty failed creation.
        if target.exists():
            require((private / "intent.json").is_file() and marker.is_file(), "Refusing an existing unregistered data directory")
            require(json.loads(marker.read_text()) == identity and list(target.iterdir()) == [marker],
                    "Retained box data exists without its instance; explicit recovery is required")
        else:
            require(not private.exists(), "Missing previously registered data directory; refusing replacement")
            target.mkdir(mode=0o700)
        write_owned(private / "intent.json", json.dumps(identity, indent=2) + "\n")
        write_owned(private / "authorized_key", key)
        write_owned(marker, json.dumps(identity, indent=2) + "\n")
        images = query(f"/1.0/images?project={c['project']}&recursion=1")
        if not any(i["fingerprint"] == PINS["image"]["fingerprint"] for i in images):
            run(["incus", "image", "import", directory / "incus.tar.xz", directory / "rootfs.tar.xz",
                 "local:", "--project", c["project"]], capture=False)
        query(f"/1.0/instances?project={c['project']}", instance_spec(c, name), "POST")
        obj = query(f"/1.0/instances/{name}?project={c['project']}")
        validate_instance(c, name, obj)
        write_owned(private / "created.json", json.dumps(obj, indent=2) + "\n")
        print("Stopped box created. Use box-provision for the separate networked package preparation.")


def box_provision(c, args):
    check_host(c)
    name = box_name(args.name)
    print("Provision Ubuntu packages, Docker/Compose, Tailscale (unenrolled), key-only SSH and guest firewall.")
    print("No source fetch, Mutagen session, application, data initialization or tailnet enrollment.")
    if not args.apply:
        return
    with locked():
        require(json.loads(HOST_CONFIG.read_text()) == c, "Host config differs")
        directory = artifacts(args.artifacts)
        obj = query(f"/1.0/instances/{name}?project={c['project']}")
        validate_instance(c, name, obj)
        require(not capacity(c)["review"], "Review capacity before package preparation")
        target = Path(c["boxes_root"]) / name
        no_symlinks(target)
        identity = json.loads((STATE / "boxes" / name / "intent.json").read_text())
        require(json.loads((target / ".chart-incus-box.json").read_text()) == identity, "HDD marker differs")
        require(not (target / "identity/guest-prepared").exists(), "Already provisioned; use status. No implicit package update.")
        run(["systemctl", "reload", "chart-incus-firewall.service"], capture=False)
        if obj["status"] == "Stopped":
            run(["incus", "start", "local:" + name, "--project", c["project"]], capture=False)
        prefix = ["incus", "exec", "local:" + name, "--project", c["project"], "--"]
        run([*prefix, "timeout", "90", "sh", "-c", "until test -r /etc/resolv.conf && ip -4 route | grep -q default; do sleep 1; done"])
        run([*prefix, "mountpoint", "-q", "/srv/chart/data"])
        # This tests idmapped writes only in the new box's private identity directory.
        run([*prefix, "install", "-d", "-m", "700", "/srv/chart/data/identity", "/root/chart-prep"])
        files = {"guest-prepare.sh": HERE / "guest-prepare.sh", "versions.lock.json": HERE / "versions.lock.json",
                 "tailscale.deb": directory / "tailscale.deb", "authorized_key": STATE / "boxes" / name / "authorized_key"}
        for dst, src in files.items():
            run(["incus", "file", "push", src, f"local:{name}/root/chart-prep/{dst}", "--project", c["project"]])
        selected = [p + "=" + PINS["packages"][p] for p in
                    ("docker.io", "docker-compose-v2", "openssh-server", "nftables", "iptables")]
        run([*prefix, "env", "DEBIAN_FRONTEND=noninteractive", "bash", "/root/chart-prep/guest-prepare.sh", *selected], capture=False)
        mapped = query(f"/1.0/instances/{name}?project={c['project']}")
        write_owned(STATE / "boxes" / name / "idmap.json", json.dumps({k: v for k, v in mapped["config"].items()
                    if "idmap" in k}, indent=2) + "\n")
        print("Prepared box is running without applications. Complete runtime/network proof before laptop enrollment.")


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
            print("Stopped: per-box Docker/cache usage unmeasured.")
            continue
        prefix = ["incus", "exec", "local:" + name, "--project", c["project"], "--"]
        for command in (["docker", "system", "df"], ["du", "-sx", "--block-size=1", "/srv/chart/cache/pnpm"]):
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
    for name in ("host-prepare", "box-create", "box-provision", "status"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        if name != "status":
            p.add_argument("--apply", action="store_true", help="Apply; omission prints a plan")
        if name.startswith("box-"):
            p.add_argument("--name", required=True)
            p.add_argument("--artifacts", required=True)
        if name == "box-create":
            p.add_argument("--ssh-key", required=True)
    args = parser.parse_args()
    if args.command == "inspect":
        inspect()
    elif args.command == "fetch":
        print("Verified artifacts:", artifacts(args.artifacts, fetch=True))
    else:
        c = config(args.config)
        if args.command == "host-prepare":
            host_prepare(c, args.apply)
        elif args.command == "box-create":
            box_create(c, args)
        elif args.command == "box-provision":
            box_provision(c, args)
        else:
            status(c)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print("Refused:", error, file=sys.stderr)
        sys.exit(1)
