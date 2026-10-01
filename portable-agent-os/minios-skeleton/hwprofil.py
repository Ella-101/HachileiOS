#!/usr/bin/env python3
"""hwprofil - describe the machine we just booted on.

Writes profile.json, the one place the rest of the system looks to find out
what hardware it is running on.  Started by hwprofild.service before anything
that touches hardware, and usable on demand.

    hwprofil.py --out /var/lib/minios/machines/$ID/profile.json
    hwprofil.py --print
    hwprofil.py --diff /var/lib/minios/machines/$ID/profile.prev.json

Design notes that matter:

* Standard library only.  The image ships a bare Debian; lspci, dmidecode and
  lsusb are not guaranteed to be installed, and anything we shell out to can
  behave differently on the next machine.  /proc and /sys are always there.
* Every read is optional.  A missing file or an unparsable value becomes null,
  never an exception.  Running on hardware we have never seen is the normal
  case for this image, not an error case, so a partial profile is still a
  useful profile.
* Nothing here needs root except reading the EFI variable, which is also
  optional -- if it is unreadable, secure_boot is reported as null rather than
  guessed, because guessing wrong here changes whether we tell the user their
  machine can boot us.
"""

import argparse
import json
import os
import platform
import re
import sys
import time

SCHEMA = 1

# PCI class codes we care enough about to name.  Anything else keeps its raw
# code so an unknown device is still visible instead of silently bucketed.
PCI_CLASSES = {
    "0x010601": "sata",
    "0x010802": "nvme",
    "0x020000": "ethernet",
    "0x028000": "network-other",
    "0x030000": "display",
    "0x030200": "display-3d",
    "0x040300": "audio",
    "0x060400": "pci-bridge",
    "0x078000": "serial",
    "0x080500": "sd-host",
    "0x0c0010": "firewire",
    "0x0c0320": "usb2",
    "0x0c0330": "usb3",
}

# The secure-boot EFI variable: 4 attribute bytes, then a 1-byte value.
EFI_SECURE_BOOT = (
    "/sys/firmware/efi/efivars/"
    "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
)

X86_FLAGS_OF_INTEREST = ("aes", "avx", "avx2", "avx512f", "bmi2", "sha_ni", "vmx", "svm")
ARM_FLAGS_OF_INTEREST = ("aes", "asimd", "crc32", "sha1", "sha2", "sve", "bf16")

DMI_HINTS = (
    ("kvm", "kvm"),
    ("qemu", "qemu"),
    ("vmware", "vmware"),
    ("microsoft corporation", "hyperv"),
    ("innotek", "vbox"),
    ("oracle corporation", "vbox"),
    ("xen", "xen"),
)

SKIP_BLOCK_PREFIXES = ("loop", "ram", "zram", "dm-", "sr", "fd", "md")


def read_text(path, default=None):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def read_int(path, default=None):
    raw = read_text(path)
    if not raw:
        return default
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return default


def cpuinfo_blocks():
    text = read_text("/proc/cpuinfo", "") or ""
    return [b for b in text.split("\n\n") if b.strip()]


def field_of(blocks, name):
    for block in blocks:
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() == name:
                return value.strip()
    return None


def cpu_model(blocks):
    return (
        field_of(blocks, "model name")
        or field_of(blocks, "Model")
        or read_text("/proc/device-tree/model")
    )


def cpu_counts(blocks):
    logical = 0
    core_ids = set()
    for block in blocks:
        seen = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                seen[key.strip()] = value.strip()
        if "processor" in seen:
            logical += 1
        if "physical id" in seen and "core id" in seen:
            core_ids.add((seen["physical id"], seen["core id"]))
    return (len(core_ids) or None, logical or os.cpu_count() or None)


def cpu_section():
    blocks = cpuinfo_blocks()
    arch = platform.machine()
    raw_flags = (field_of(blocks, "flags") or field_of(blocks, "Features") or "").split()
    interesting = ARM_FLAGS_OF_INTEREST if arch in ("aarch64", "arm64") else X86_FLAGS_OF_INTEREST
    cores, threads = cpu_counts(blocks)
    return {
        "arch": arch or None,
        "model": cpu_model(blocks),
        "vendor": field_of(blocks, "vendor_id") or field_of(blocks, "CPU implementer"),
        "cores": cores,
        "threads": threads,
        "features": sorted(f for f in raw_flags if f in interesting),
    }


def memory_section():
    text = read_text("/proc/meminfo", "") or ""
    out = {}
    for key, name in (
        ("MemTotal", "total_kb"),
        ("MemAvailable", "available_kb"),
        ("SwapTotal", "swap_kb"),
    ):
        match = re.search(r"^%s:\s+(\d+)\s+kB" % key, text, re.MULTILINE)
        out[name] = int(match.group(1)) if match else None
    return out


def firmware_section():
    uefi = os.path.isdir("/sys/firmware/efi")
    secure = None
    if uefi:
        blob = read_bytes(EFI_SECURE_BOOT)
        if blob is not None and len(blob) >= 5:
            secure = blob[4] == 1
    return {"kind": "uefi" if uefi else "bios", "secure_boot": secure}


def platform_section():
    vendor = read_text("/sys/class/dmi/id/sys_vendor") or ""
    product = read_text("/sys/class/dmi/id/product_name") or ""
    haystack = (vendor + " " + product).lower()
    kind = None
    for needle, name in DMI_HINTS:
        if needle in haystack:
            kind = name
            break
    cpuinfo = read_text("/proc/cpuinfo", "") or ""
    virt_flag = re.search(r"^flags\s*:.*\bhypervisor\b", cpuinfo, re.MULTILINE) is not None
    model = (vendor + " " + product).strip()
    return {
        "model": model or None,
        "virtualized": kind is not None or virt_flag,
        "hypervisor": kind,
    }


def disk_section():
    base = "/sys/block"
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        if name.startswith(SKIP_BLOCK_PREFIXES):
            continue
        path = os.path.join(base, name)
        sectors = read_int(os.path.join(path, "size"))
        out.append(
            {
                "name": name,
                "size_bytes": sectors * 512 if sectors is not None else None,
                "removable": read_int(os.path.join(path, "removable")) == 1,
                "rotational": read_int(os.path.join(path, "queue", "rotational")) == 1,
                "model": read_text(os.path.join(path, "device", "model")),
            }
        )
    return out


def net_section():
    base = "/sys/class/net"
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        if name == "lo":
            continue
        path = os.path.join(base, name)
        wireless = os.path.isdir(os.path.join(path, "wireless")) or os.path.isdir(
            os.path.join(path, "phy80211")
        )
        driver = None
        driver_link = os.path.join(path, "device", "driver")
        if os.path.islink(driver_link):
            driver = os.path.basename(os.path.realpath(driver_link))
        kind = "wifi" if wireless else ("ethernet" if read_int(os.path.join(path, "type")) == 1 else "other")
        if driver and driver.startswith("virtio"):
            kind = "virtio"
        out.append(
            {
                "name": name,
                "kind": kind,
                "mac": read_text(os.path.join(path, "address")),
                "driver": driver,
                "operstate": read_text(os.path.join(path, "operstate")),
                "speed_mbps": read_int(os.path.join(path, "speed")),
            }
        )
    return out


def pci_class_name(raw):
    if not raw:
        return None
    return PCI_CLASSES.get(raw, raw)


def pci_section():
    base = "/sys/bus/pci/devices"
    result = {"devices_total": 0, "undriven": [], "gpus": []}
    if not os.path.isdir(base):
        return result
    for slot in sorted(os.listdir(base)):
        path = os.path.join(base, slot)
        if not os.path.isdir(path):
            continue
        result["devices_total"] += 1
        raw_class = read_text(os.path.join(path, "class")) or ""
        driver = None
        driver_link = os.path.join(path, "driver")
        if os.path.islink(driver_link):
            driver = os.path.basename(os.path.realpath(driver_link))
        if raw_class.startswith("0x03"):
            result["gpus"].append(
                {
                    "slot": slot,
                    "vendor_id": read_text(os.path.join(path, "vendor")),
                    "device_id": read_text(os.path.join(path, "device")),
                    "driver": driver,
                }
            )
            continue
        if driver is None:
            result["undriven"].append(
                {
                    "slot": slot,
                    "vendor_id": read_text(os.path.join(path, "vendor")),
                    "device_id": read_text(os.path.join(path, "device")),
                    "class": pci_class_name(raw_class),
                }
            )
    return result


def build():
    pci = pci_section()
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine_id": read_text("/etc/machine-id") or read_text("/var/lib/dbus/machine-id") or "unknown",
        "boot_id": read_text("/proc/sys/kernel/random/boot_id", "unknown"),
        "hostname": platform.node() or None,
        "kernel": platform.release() or None,
        "cpu": cpu_section(),
        "memory": memory_section(),
        "firmware": firmware_section(),
        "platform": platform_section(),
        "disks": disk_section(),
        "net": net_section(),
        "pci": pci,
    }


def write_atomic(path, payload):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def disk_names(profile):
    return sorted(d["name"] for d in profile.get("disks", []) if d.get("name"))


def net_keys(profile):
    out = {}
    for nic in profile.get("net", []):
        out[nic.get("mac") or nic.get("name") or "?"] = nic
    return out


def gpu_keys(profile):
    return {g.get("slot", "?"): g for g in profile.get("pci", {}).get("gpus", [])}


def compare(new, old):
    """Return human-readable lines describing what changed between two profiles.

    This is what turns the profile from a log into a message: on a machine we
    have seen before, only the differences are worth telling the user about.
    """
    lines = []

    def note(fmt, *args):
        lines.append(fmt % args)

    if new.get("machine_id") != old.get("machine_id"):
        note("machine changed: %s -> %s", old.get("machine_id"), new.get("machine_id"))

    new_mem = (new.get("memory") or {}).get("total_kb")
    old_mem = (old.get("memory") or {}).get("total_kb")
    if new_mem != old_mem and new_mem and old_mem:
        note("memory: %d MB -> %d MB", old_mem // 1024, new_mem // 1024)

    new_cpu = (new.get("cpu") or {}).get("model")
    old_cpu = (old.get("cpu") or {}).get("model")
    if new_cpu != old_cpu:
        note("cpu: %s -> %s", old_cpu or "unknown", new_cpu or "unknown")

    new_kind = (new.get("firmware") or {}).get("kind")
    old_kind = (old.get("firmware") or {}).get("kind")
    if new_kind != old_kind:
        note("firmware: %s -> %s", old_kind, new_kind)

    new_gw = (new.get("platform") or {}).get("hypervisor")
    old_gw = (old.get("platform") or {}).get("hypervisor")
    if new_gw != old_gw:
        note("hypervisor: %s -> %s", old_gw or "none", new_gw or "none")

    old_disks, new_disks = disk_names(old), disk_names(new)
    for name in sorted(set(new_disks) - set(old_disks)):
        note("disk added: %s", name)
    for name in sorted(set(old_disks) - set(new_disks)):
        note("disk removed: %s", name)

    old_net, new_net = net_keys(old), net_keys(new)
    for key in sorted(set(new_net) - set(old_net)):
        nic = new_net[key]
        note("network interface added: %s (%s, driver %s)", nic.get("name"), nic.get("kind"), nic.get("driver") or "none")
    for key in sorted(set(old_net) - set(new_net)):
        note("network interface removed: %s", old_net[key].get("name"))

    old_gpu, new_gpu = gpu_keys(old), gpu_keys(new)
    for slot in sorted(set(new_gpu) - set(old_gpu)):
        gpu = new_gpu[slot]
        note("gpu added: %s %s (driver %s)", gpu.get("vendor_id"), gpu.get("device_id"), gpu.get("driver") or "none")
    for slot in sorted(set(old_gpu) - set(new_gpu)):
        note("gpu removed: %s", slot)

    old_un = {(d.get("slot"), d.get("device_id")) for d in old.get("pci", {}).get("undriven", [])}
    new_un = {(d.get("slot"), d.get("device_id")) for d in new.get("pci", {}).get("undriven", [])}
    for slot, dev in sorted(new_un - old_un):
        note("device now has no driver: %s %s", slot, dev)
    for slot, dev in sorted(old_un - new_un):
        note("device is driven now: %s %s", slot, dev)

    return lines


def load_and_compare(profile, path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return compare(profile, json.load(fh))
    except (OSError, ValueError):
        return []


def write_text_atomic(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def last_machine(machines_dir):
    """The machine we booted on last time.

    A one-line file rather than a symlink, on purpose: this image is meant to
    live on removable media, and exFAT/FAT32 -- what most USB sticks are
    formatted as -- has no symlinks at all.  A symlink would work here and fail
    on exactly the medium this system is for.
    """
    name = read_text(os.path.join(machines_dir, "last"))
    return name if name and "/" not in name else None


def boot(quiet=False):
    """The entry point hwprofild.service calls once per boot.

    Two comparisons matter here and they answer different questions:

      * against this machine's own previous profile -- did this machine's
        hardware change since we last ran on it (a new NIC, more memory)?
      * against the machine we booted on last time -- is this a machine we have
        never seen, and how does it differ from the one before it?

    The second is the interesting one for a portable image: the common case is
    that we are on a machine we have never met, and what the user needs to hear
    is "this is new hardware, here is how it differs from what you had".  That
    is why `last` is remembered separately from the per-machine history.
    """
    state_dir = os.environ.get("MINIOS_STATE_DIR", "/var/lib/minios")
    run_dir = os.environ.get("MINIOS_RUN_DIR", "/run/minios")

    profile = build()
    machine = profile["machine_id"]
    machines_dir = os.path.join(state_dir, "machines")
    mine = os.path.join(machines_dir, machine, "profile.json")

    seen_before = os.path.exists(mine)
    previous_machine = last_machine(machines_dir)

    changes = []
    basis = None
    if seen_before:
        changes = load_and_compare(profile, mine)
        basis = "this machine, when we last ran here"
    elif previous_machine and previous_machine != machine:
        other = os.path.join(machines_dir, previous_machine, "profile.json")
        if os.path.exists(other):
            changes = load_and_compare(profile, other)
            basis = "machine %s, where we booted last time" % previous_machine

    if seen_before:
        try:
            os.replace(mine, mine + ".prev")
        except OSError as exc:
            if not quiet:
                print("hwprofil: could not keep the previous profile: %s" % exc, file=sys.stderr)
    write_atomic(mine, profile)
    write_text_atomic(os.path.join(machines_dir, "last"), machine + "\n")

    summary = {
        "generated_at": profile["generated_at"],
        "machine_id": machine,
        "seen_before": seen_before,
        "previous_machine": previous_machine,
        "compared_against": basis,
        "changes": changes,
    }
    write_atomic(os.path.join(run_dir, "hwchange.json"), summary)

    if not quiet:
        if not seen_before:
            print("hwprofil: new machine %s" % machine)
        if basis and changes:
            print("hwprofil: %d change(s) since %s" % (len(changes), basis))
            for line in changes:
                print("  " + line)
        elif basis:
            print("hwprofil: no change since %s" % basis)

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hwprofil",
        description="Build or compare this machine's hardware profile.",
    )
    parser.add_argument("--boot", action="store_true", help="per-boot mode: write the profile, report changes, remember this machine")
    parser.add_argument("--out", metavar="PATH", help="write the profile here (atomic, keeps .prev)")
    parser.add_argument("--print", dest="print_only", action="store_true", help="write the profile to stdout")
    parser.add_argument("--diff", metavar="PREV", help="compare against an earlier profile")
    parser.add_argument("--keep-prev", action="store_true", default=True, help="keep the previous profile as .prev (default)")
    parser.add_argument("--no-prev", dest="keep_prev", action="store_false", help="overwrite without keeping a copy")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    args = parser.parse_args(argv)

    if args.boot:
        return boot(quiet=args.quiet)

    if not (args.out or args.print_only or args.diff):
        parser.error("nothing to do: pass --boot, --out, --print or --diff")
        return 2

    profile = build()

    if args.print_only or (args.diff and not args.out):
        print(json.dumps(profile, indent=2, ensure_ascii=False))

    if args.out:
        if args.keep_prev and os.path.exists(args.out):
            try:
                os.replace(args.out, args.out + ".prev")
            except OSError as exc:
                if not args.quiet:
                    print("hwprofil: could not keep the previous profile: %s" % exc, file=sys.stderr)
        write_atomic(args.out, profile)
        if not args.quiet:
            print("hwprofil: wrote %s" % args.out)

    if args.diff:
        previous = None
        try:
            with open(args.diff, "r", encoding="utf-8") as fh:
                previous = json.load(fh)
        except (OSError, ValueError) as exc:
            print("hwprofil: cannot read %s: %s" % (args.diff, exc), file=sys.stderr)
            return 1
        changes = compare(profile, previous)
        if changes:
            for line in changes:
                print(line)
        elif not args.quiet:
            print("hwprofil: no changes since %s" % args.diff)

    return 0


if __name__ == "__main__":
    sys.exit(main())
