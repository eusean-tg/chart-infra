#!/usr/bin/env python3
"""Offline safety checks; no Incus, firewall or cluster mutations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "incus"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "incus" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = load("prep")
firewall = load("firewall")


class Guards(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((ROOT / "incus/host.example.json").read_text())
        self.c.update(machine_id="a" * 32, hdd_uuid="14ef1cfa-28ee-4986-8989-2e248896bf07")

    def test_route_conflicts_in_any_table(self):
        for route in ["10.200.0.0/16", "10.200.0.41/32", "10.0.0.0/8"]:
            with self.subTest(route=route), self.assertRaises(RuntimeError):
                prep.check_routes(self.c, [{"dst": route, "dev": "CloudflareWARP", "table": 65743}])

    def test_routes_allow_default_and_owned_bridge(self):
        prep.check_routes(self.c, [{"dst": "default"}, {"dst": "10.200.0.0/24", "dev": "chartbr0"},
                                  {"dst": "10.42.0.0/16", "dev": "flannel.1"}])

    def test_symlink_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "link").symlink_to(p, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                prep.no_symlinks(p / "link/new-data")

    def test_parent_traversal_rejected(self):
        with self.assertRaises(RuntimeError):
            prep.no_symlinks("/mnt/hdd/shared-dev/boxes/../profiles")

    def test_invalid_box_names(self):
        for name in ["../sean", "sean/../../", "sean;id", "--help", "sean-", "a b"]:
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                prep.box_name(name)

    def test_owned_files_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "identity"
            prep.write_owned(p, "original")
            prep.write_owned(p, "original")
            with self.assertRaises(RuntimeError):
                prep.write_owned(p, "replacement")
            self.assertEqual(p.read_text(), "original")

    def test_foreign_incus_resource_rejected(self):
        with patch.object(prep, "query", return_value=[{"name": "ssd", "config": {}}]) as query:
            with self.assertRaises(RuntimeError):
                prep.resource("storage-pools", "ssd", {"config": {"user.chart-infra": prep.OWNER}})
            self.assertEqual(query.call_count, 1)

    def test_conflicting_owned_resource_rejected(self):
        value = {"name": "ssd", "driver": "dir", "config": {"user.chart-infra": prep.OWNER}}
        with patch.object(prep, "query", return_value=[value]) as query:
            with self.assertRaises(RuntimeError):
                prep.resource("storage-pools", "ssd", {"driver": "btrfs", "config": value["config"]})
            self.assertEqual(query.call_count, 1)

    def test_owned_resource_rerun_is_read_only(self):
        value = {"name": "ssd", "driver": "btrfs", "config": {"user.chart-infra": prep.OWNER}}
        with patch.object(prep, "query", return_value=[value]) as query:
            prep.resource("storage-pools", "ssd", {"driver": "btrfs", "config": value["config"]})
            self.assertEqual(query.call_count, 1)

    def test_new_resource_created_with_explicit_owner(self):
        with patch.object(prep, "query", side_effect=[[], None]) as query:
            prep.resource("projects", "chart-dev", {"config": {"user.chart-infra": prep.OWNER}})
            self.assertEqual(query.call_args.args[2], "POST")
            self.assertEqual(query.call_args.args[1]["config"]["user.chart-infra"], prep.OWNER)

    def test_orphan_pool_file_cannot_be_formatted(self):
        with patch.object(prep, "query", return_value=[]) as query, \
             patch.object(prep.Path, "exists", return_value=True):
            with self.assertRaises(RuntimeError):
                prep.resource("storage-pools", "ssd", {"driver": "btrfs", "config": {"user.chart-infra": prep.OWNER}})
            self.assertEqual(query.call_count, 1)

    def test_box_has_no_inherited_access_or_autostart_or_limits(self):
        spec = prep.instance_spec(self.c, "developer-box")
        self.assertEqual(spec["profiles"], [])
        self.assertEqual(spec["config"]["security.privileged"], "false")
        self.assertEqual(spec["config"]["boot.autostart"], "false")
        self.assertEqual(spec["config"]["security.idmap.isolated"], "true")
        self.assertTrue(spec["devices"]["data"]["source"].endswith("/boxes/developer-box"))
        self.assertNotIn("limits.memory", spec["config"])
        self.assertEqual(set(spec["devices"]), {"root", "eth0", "tun", "data"})

    def test_plan_does_not_apply(self):
        with patch.object(prep, "check_host"), patch.object(prep, "capacity", return_value={}), \
             patch.object(prep, "resolve_lan", return_value="192.168.100.0/24"), \
             patch.object(prep, "run") as run, patch.object(prep, "query") as query, \
             patch("builtins.print"):
            prep.host_prepare(self.c, False)
            run.assert_not_called()
            query.assert_not_called()

    def test_host_tune_plan_does_not_apply(self):
        with patch.object(prep, "check_host"), patch.object(prep, "resolve_lan", return_value="192.168.100.0/24"), \
             patch.object(prep, "run") as run, patch.object(prep, "write_tuning") as write, patch("builtins.print"):
            prep.host_tune(self.c, "192.168.100.0/24", False)
            run.assert_not_called()
            write.assert_not_called()

    def test_lan_is_connected_uplink_only(self):
        routes = [{"dst": "default", "gateway": "192.168.100.1", "dev": "eno1"},
                  {"dst": "192.168.100.0/24", "dev": "eno1", "scope": "link"},
                  {"dst": "10.42.0.0/24", "dev": "cni0", "scope": "link"}]
        self.assertEqual(prep.check_lan(self.c, "192.168.100.0/24", routes), "192.168.100.0/24")
        for cidr in ("10.42.0.0/24", "10.200.0.0/24", "0.0.0.0/0", "192.168.0.0/16"):
            with self.subTest(cidr=cidr), self.assertRaises(RuntimeError):
                prep.check_lan(self.c, cidr, routes)
        with patch.object(prep, "run", return_value=json.dumps(routes)):
            self.assertEqual(prep.resolve_lan(self.c), "192.168.100.0/24")

    def test_lan_ambiguity_fails(self):
        routes = [{"dst": "default", "gateway": "192.168.100.1", "dev": "eno1"},
                  {"dst": "192.168.100.0/24", "dev": "eno1", "scope": "link"},
                  {"dst": "10.1.0.0/24", "dev": "eno1", "scope": "link"}]
        with patch.object(prep, "run", return_value=json.dumps(routes)), self.assertRaises(RuntimeError):
            prep.resolve_lan(self.c)

    def test_inotify_applies_only_dedicated_file(self):
        with patch.object(prep, "run", side_effect=["", "1024\n", "1048576\n"]) as run:
            prep.apply_inotify()
            self.assertEqual(run.call_args_list[0].args[0],
                             ["sysctl", "--load", "/etc/sysctl.d/99-chart-incus-inotify.conf"])

    def test_artifact_corruption_fails_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "incus.tar.xz").write_text("corrupt")
            with patch("urllib.request.urlopen") as network, self.assertRaises(RuntimeError):
                prep.artifacts(p)
            network.assert_not_called()

    def test_firewall_guards_before_allowances(self):
        body = firewall.rules(self.c)
        forward = body.split("chain forward", 1)[1].split("chain output", 1)[0]
        self.assertLess(forward.index("10.0.0.0/8"), forward.index("ct state established"))
        self.assertIn("CloudflareWARP", forward)
        self.assertIn("meta nfproto ipv6 drop", forward)
        self.assertIn("iifname \"chartbr0\" reject", body.split("chain forward")[0])
        self.assertNotIn("flush ruleset", body)

    def test_firewall_rejects_identifier_injection(self):
        with self.assertRaises(ValueError):
            firewall.rules({**self.c, "bridge": 'x"; flush ruleset;'})

    def test_lan_exception_is_udp_from_fixed_box_port(self):
        body = firewall.rules({**self.c, "lan_ipv4_cidrs": ["192.168.100.0/24"]})
        input_chain = body.split("chain forward")[0]
        self.assertLess(input_chain.index("ct state established,related accept"), input_chain.index('iifname "chartbr0" reject'))
        forward = body.split("chain forward", 1)[1].split("chain output", 1)[0]
        line = next(l for l in forward.splitlines() if "192.168.100.0/24" in l)
        self.assertIn("udp sport 41641 accept", line)
        self.assertNotIn("tcp", line)
        self.assertLess(forward.index("CloudflareWARP"), forward.index(line))
        self.assertLess(forward.index(line), forward.index("192.168.0.0/16"))
        for cidr in ("0.0.0.0/0", "10.200.0.0/24", "192.168.100.0/24; flush ruleset"):
            with self.subTest(cidr=cidr), self.assertRaises(ValueError):
                firewall.rules({**self.c, "lan_ipv4_cidrs": [cidr]})

    def test_firewall_install_orders_nft_before_forward_accept(self):
        calls = []
        class Result:
            returncode = 1
        with patch.object(firewall.subprocess, "run", return_value=Result()), \
             patch.object(firewall, "run", side_effect=lambda *a, **k: calls.append(a)):
            firewall.apply(self.c)
        nft_apply = calls.index(("nft", "-f", "-"))
        first_allow = next(i for i, a in enumerate(calls) if "ACCEPT" in a)
        self.assertLess(nft_apply, first_allow)
        self.assertFalse(any(a[:4] == ("iptables", "-w", "-F", "FORWARD") for a in calls))


if __name__ == "__main__":
    unittest.main()
