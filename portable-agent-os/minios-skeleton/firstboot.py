#!/usr/bin/env python3
"""firstboot - make the layout the rest of the system assumes actually true.

Everything else here assumes /var/lib/minios/{machines,sessions,audit,usage,
secrets} exists and that /etc/minios has a provider list and an egress policy
in it.  On a read-only image nothing creates those, because the image cannot:
the parts that must change have to live in the writable layer.  This is what
seeds them, once, at the first boot.

The layout rule it implements:

    /usr/lib/minios/defaults/   the immutable defaults, on the read-only image
    /etc/minios/                the working copy, on the writable layer

A default is copied across only when the destination is missing.  An existing
file is never touched, so re-running this after the user has edited their
provider list is safe -- and it has to be safe, because "run at every boot"
is the only version of this that cannot be skipped by accident.

Every step reports what it did, including "kept", so `minios doctor` and a
human can both see whether a boot actually changed anything.
"""

import argparse
import json
import os
import shutil
import sys
import time
import uuid

CREATED = "created"
KEPT = "kept"
EXISTS = "existing"

STATE_SUBDIRS = ("machines", "sessions", "audit", "usage", "secrets")
DEFAULT_FILES = ("providers.yaml", "egress.yaml")


def read_text(path, default=None):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def write_text(path, text, mode=0o644):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def path_under(root, absolute):
    """Join an absolute system path under a staging prefix.

    This is what makes the script testable: on the real system the prefix is
    `/` and nothing changes, and in a test it is a temporary directory and the
    whole run -- including the machine-id and the config seeding -- can be
    observed without touching the machine it runs on.
    """
    return os.path.join(root.rstrip("/"), absolute.lstrip("/"))


class Report:
    def __init__(self):
        self.steps = []

    def add(self, step, status, detail):
        self.steps.append({"step": step, "status": status, "detail": detail})

    def did(self, status):
        return [s for s in self.steps if s["status"] == status]

    def as_dict(self, root, dry_run):
        return {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "root": root,
            "dry_run": dry_run,
            "steps": self.steps,
            "counts": {s: len(self.did(s)) for s in (CREATED, KEPT, EXISTS)},
        }


def ensure_machine_id(root, report, dry_run):
    """A per-host identity, not one baked into the image.

    systemd generates this itself when /etc/machine-id is empty, and the image
    ships it empty on purpose.  We only act when that has not happened, because
    a shared machine id would make every host look like the same machine -- and
    the whole hardware profile is filed under it.
    """
    target = path_under(root, "/etc/machine-id")
    current = read_text(target)
    if current:
        report.add("machine-id", EXISTS, current)
        return current
    value = uuid.uuid4().hex
    if not dry_run:
        write_text(target, value + "\n", mode=0o444)
    report.add("machine-id", CREATED, value)
    return value


def ensure_dirs(root, report, dry_run):
    wanted = [
        ("/var/lib/minios", 0o755),
        ("/run/minios", 0o755),
        ("/root/work", 0o700),
        ("/etc/minios", 0o755),
    ]
    for name in STATE_SUBDIRS:
        # secrets is the mount point of the encrypted volume; 0700 because
        # anything in it is key material.
        wanted.append(("/var/lib/minios/" + name, 0o700 if name == "secrets" else 0o755))
    for absolute, mode in wanted:
        target = path_under(root, absolute)
        if os.path.isdir(target):
            report.add("dir " + absolute, EXISTS, "")
            continue
        if not dry_run:
            os.makedirs(target, exist_ok=True)
            os.chmod(target, mode)
        report.add("dir " + absolute, CREATED, oct(mode))


def seed_defaults(root, report, dry_run):
    source_dir = path_under(root, "/usr/lib/minios/defaults")
    target_dir = path_under(root, "/etc/minios")
    for name in DEFAULT_FILES:
        source = os.path.join(source_dir, name)
        target = os.path.join(target_dir, name)
        if os.path.exists(target):
            report.add("config " + name, KEPT, "already present, left alone")
            continue
        if not os.path.exists(source):
            report.add("config " + name, "missing",
                       "no default at %s; the image is incomplete" % source)
            continue
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            shutil.copyfile(source, target)
            os.chmod(target, 0o644)
        report.add("config " + name, CREATED, "copied from %s" % source)


def ensure_admin_marker(root, report, dry_run):
    """Record that no administrator password has been chosen yet.

    Not a default password: a marker saying there is none.  A shipped default
    would be a back door, and leaving the file absent would make "has the user
    set one" unanswerable.
    """
    target = path_under(root, "/etc/minios/admin")
    if os.path.exists(target):
        report.add("admin password", EXISTS, read_text(target) or "set")
        return
    if not dry_run:
        write_text(target, "unset\n", mode=0o600)
    report.add("admin password", CREATED, "unset -- `minios admin passwd` will replace this")


def ensure_workspace_readme(root, report, dry_run):
    target = path_under(root, "/root/work/README")
    if os.path.exists(target):
        report.add("workspace note", EXISTS, "")
        return
    if not dry_run:
        write_text(target,
                   "This directory is the sandbox boundary.\n"
                   "\n"
                   "The file tools the agent may call cannot read or write outside it, and "
                   "the egress policy is applied on top of that. Put the files you want it "
                   "to work on here; anything else is out of reach by construction rather "
                   "than by configuration.\n")
    report.add("workspace note", CREATED, target)


def run(root, dry_run=False):
    report = Report()
    machine = ensure_machine_id(root, report, dry_run)
    ensure_dirs(root, report, dry_run)
    seed_defaults(root, report, dry_run)
    ensure_admin_marker(root, report, dry_run)
    ensure_workspace_readme(root, report, dry_run)

    payload = report.as_dict(root, dry_run)
    payload["machine_id"] = machine
    if not dry_run:
        write_text(path_under(root, "/run/minios/firstboot.json"),
                   json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="firstboot",
        description="Seed the writable layer with the layout the rest of miniOS assumes.")
    parser.add_argument("--root", default="/", metavar="PREFIX",
                        help="stage under this prefix instead of / (for tests and packaging)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="say nothing on success")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("firstboot: %s is not a directory" % root, file=sys.stderr)
        return 4
    if not os.access(root, os.W_OK):
        print("firstboot: %s is not writable" % root, file=sys.stderr)
        return 4

    payload = run(root, args.dry_run)

    if args.json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.quiet:
        return 0

    print("firstboot: root %s%s" % (root, "  (dry run)" if args.dry_run else ""))
    width = max(len(step["step"]) for step in payload["steps"])
    for step in payload["steps"]:
        print("  %-8s %-*s %s" % (step["status"], width, step["step"], step["detail"]))
    counts = payload["counts"]
    print()
    print("%d created, %d kept, %d already there"
          % (counts.get(CREATED, 0), counts.get(KEPT, 0), counts.get(EXISTS, 0)))
    print("running this again changes nothing: existing files are never overwritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
