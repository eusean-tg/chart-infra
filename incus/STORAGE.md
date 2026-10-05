# Storage placement and backup coverage

Personal boxes use their registered Incus root pool. The required `data` disk
attaches `<boxes_root>/<box>` at `/srv/chart/data` independently of rootfs.
Backups stay on the host SSD/NVMe. See [the architecture](ARCHITECTURE.md) for
the storage diagram and [image and lifecycle commands](IMAGES.md) for creation,
recreation and backup procedures.

## Pool registration

Host configuration selects a default `pool`. Optional `instance_pools` entries
select `hdd` for individual names; unlisted names use the default. For example:

```json
{
  "pool": "ssd",
  "instance_pools": {"alex-dev": "hdd"}
}
```

This is a fragment of the host configuration, not a complete configuration file.
The preparation template creates a 200 GiB SSD btrfs pool. Per-box HDD placement
requires an existing owned `dir` pool named `hdd`, with source
`<hdd_mount>/shared-dev/incus` on the registered HDD. Host checks validate its
ownership marker, path and filesystem. Creating a pool does not change placement.

Both operator and installed host configurations, and the installed backup-tool
snapshot, must recognize the selected placement. The mapping controls creation
and ownership checks; editing it does not move an existing instance. Coordinate
placement changes with the operator and exclude concurrent lifecycle/backup work.
Do not relocate mounted pool directories with filesystem commands.

## Files and coverage

| Storage | Contents | Nightly backup |
| --- | --- | --- |
| Registered root pool | Guest OS, synced source, dependencies, package caches, Docker images/volumes and files outside the data attachment | Excluded |
| Required HDD data attachment | Developer databases, private configuration and retained SSH/Tailscale identity under `/srv/chart/data` | Included |
| Host SSD/NVMe backup directory | Data archives, explicit rootfs exports and scratch restore copies | Destination; independent of HDD |

HDD-backed rootfs does not extend nightly coverage. Developers keep durable
application data in the attachment and restore source/dependencies from their
laptop checkouts after recreation. Rootfs preservation requires an explicit
export. Manual exports and incomplete backup generations are retained; routine
nightly pruning does not remove them.

## Capacity and lifecycle

`prep.py status` reports HDD free space when per-box HDD placement is registered.
Review placement and retention at 70% HDD use; pause heavy work at 85%, keeping
at least 10 GiB free. The backup tool reserves 25% of the SSD filesystem plus
1 GiB. Monitor source, dependency and Docker growth as boxes are added; no
background cleanup deletes developer files.

The HDD directory pool shares its filesystem with attached data; copies and
snapshots need additional capacity. SSD btrfs pool allocation is separate from
host filesystem free space, so deleting an instance does not establish how many
host SSD blocks were reclaimed.

Use `boxes.py` for supported box creation/recreation and `backup.py` for retained
backups and restore checks. Before recreation, coordinate paused laptop sessions,
verify independent data and rootfs recovery copies, retain identity, and leave
application restoration to the developer. Published images, stopped builders,
instances, external HDD directories and backup archives are separate resources;
each deletion requires its own explicit scope.
