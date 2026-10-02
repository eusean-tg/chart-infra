#!/usr/bin/env python3
"""Run an offline Docker build and retained-storage fixture inside a prepared box."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args[:3])}\n{result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--box", required=True)
    p.add_argument("--busybox", required=True, type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    require(re.fullmatch(r"[a-z][a-z0-9-]{1,40}", args.box), "Invalid box name")
    require(socket.gethostname() == args.box, "Wrong box hostname")
    run("mountpoint", "-q", "/srv/chart/data")
    marker = json.loads(Path("/srv/chart/data/.chart-incus-box.json").read_text())
    require(marker.get("name") == args.box and marker.get("owner") == "chart-incus-v1", "Wrong data marker")
    require(Path("/srv/chart/data/identity/guest-prepared").is_file(), "Guest preparation incomplete")
    require(re.fullmatch(r"[a-f0-9]{64}", args.sha256), "Invalid binary checksum")
    with args.busybox.open("rb") as source:
        require(hashlib.file_digest(source, "sha256").hexdigest() == args.sha256, "BusyBox checksum differs")
    uid_map = Path("/proc/self/uid_map").read_text().split()
    require(len(uid_map) == 3 and uid_map[0] == "0" and int(uid_map[1]) > 0, "Expected isolated unprivileged UID map")
    rootfs = json.loads(run("findmnt", "-J", "-T", "/var/lib/docker", "-o", "FSTYPE"))["filesystems"][0]
    require(rootfs["fstype"] == "btrfs", "Docker data must reside on box SSD rootfs")
    require(not run("docker", "ps", "-q"), "Stop running app/test containers before this fixture")
    if not args.apply:
        print("Plan: offline scratch-image build with RUN, two containers, one retained Docker volume and one retained HDD fixture directory.")
        return

    ident = "runtime-v1-" + uuid.uuid4().hex[:12]
    base = Path("/root/chart-prep") / ident
    base.mkdir(mode=0o700)
    (base / "busybox").write_bytes(args.busybox.read_bytes())
    (base / "busybox").chmod(0o755)
    (base / "Dockerfile").write_text(
        'FROM scratch\nCOPY busybox /busybox\n'
        'RUN ["/busybox", "sh", "-ec", "echo build-v1 > /build-proof"]\n'
        'ENTRYPOINT ["/busybox"]\n')
    image = "chart-infra/runtime-proof:" + ident
    report = {"box": args.box, "fixture": ident, "binary_sha256": args.sha256,
              "image": image, "volume": ident, "containers": [ident + "-writer", ident + "-reader"],
              "context": str(base), "result": "incomplete"}
    report_path = base / "report.json"
    try:
        build = run("docker", "build", "--network=none", "--pull=false", "-t", image, str(base))
        (base / "build.txt").write_text(build + "\n")
        report["image_id"] = run("docker", "image", "inspect", "--format", "{{.Id}}", image)
        run("docker", "volume", "create", "--label", "chart-infra.fixture=runtime-v1", ident)
        hdd = Path("/srv/chart/data/verification")
        require(not hdd.is_symlink(), "Unexpected verification path symlink")
        hdd.mkdir(exist_ok=True, mode=0o700)
        hdd = hdd / ident
        hdd.mkdir(mode=0o700)
        report["hdd_directory"] = str(hdd)
        mounts = ["--mount", f"type=volume,src={ident},dst=/volume",
                  "--mount", f"type=bind,src={hdd},dst=/hdd"]
        writer = 'test ! -e /volume/token; test ! -e /hdd/token; printf "%s" "$1" > /volume/token; printf "%s" "$1" > /hdd/token; /busybox sync'
        reader = 'test "$(/busybox cat /build-proof)" = build-v1; test "$(/busybox cat /volume/token)" = "$1"; test "$(/busybox cat /hdd/token)" = "$1"; echo retained-fixture-ok'
        run("docker", "run", "--name", ident + "-writer", "--network=none", *mounts, image, "sh", "-eu", "-c", writer, "fixture", ident)
        output = run("docker", "run", "--name", ident + "-reader", "--network=none", *mounts, image, "sh", "-eu", "-c", reader, "fixture", ident)
        require(output == "retained-fixture-ok", "Reader output differs")
        require(run("docker", "start", "-a", ident + "-reader") == output, "Restarted reader differs")
        report.update(result="passed", docker=run("docker", "version", "--format", "{{.Server.Version}}"),
                      compose=run("docker", "compose", "version", "--short"))
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        print("Retained report:", report_path)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
