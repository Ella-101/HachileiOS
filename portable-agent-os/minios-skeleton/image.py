#!/usr/bin/env python3
"""image - where every file goes inside the image, in one place.

The build needs a tree to hand to mkosi, and the tree needs to agree with what
the code expects to find at runtime.  Keeping that mapping in a program rather
than in a directory of copied files means there is exactly one place to look
when `/usr/lib/minios/minios.py` is not where something thought it was.

    python image.py list            # the manifest, with the reason for each entry
    python image.py stage           # build mkosi.skeleton/ from it
    python image.py check           # is the staged tree still what the manifest says

Two rules this file is built on:

  * Nothing is copied by hand.  `stage` is safe to re-run, and `check` fails if
    the staged tree has fallen behind the sources -- which is the failure mode
    that would otherwise be found by booting an image and watching it break.

  * Defaults live in /usr/lib/minios/defaults/ and are *seeded* into /etc by
    firstboot.  The image itself ships no /etc/minios: on a read-only root
    nothing can write there at build time and have it mean anything, and a file
    the user is expected to edit does not belong on a read-only layer.
"""

import argparse
import json
import os
import shutil
import sys
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, 'mkosi.skeleton')
STAGED_MARKER = '.staged.json'

LAUNCHER = '''#!/bin/sh
# Nothing but a shim: the real program is a module with imports beside it, and
# putting it in /usr/bin would split it from minioscore.py.
exec python3 /usr/lib/minios/minios.py "$@"
'''

Entry = namedtuple('Entry', 'dest mode source text why')


def image_version():
    try:
        with open(os.path.join(HERE, 'build.json'), encoding='utf-8') as fh:
            build = json.load(fh)
        return '%s+%s' % (build.get('version', 'dev'), build.get('build_id', 'unbuilt'))
    except (OSError, ValueError):
        return 'dev'


def manifest():
    return [
        Entry('/usr/bin/minios', 0o755, None, LAUNCHER,
              'the one name added to PATH'),
        Entry('/usr/lib/minios/minios.py', 0o644, 'minios.py', None,
              'command registry, parser and dispatch'),
        Entry('/usr/lib/minios/minioscore.py', 0o644, 'minioscore.py', None,
              'behaviour: policy, sandbox, audit, gateway'),
        Entry('/usr/lib/minios/hwprofil.py', 0o644, 'hwprofil.py', None,
              'hardware profile, shared with the boot unit'),
        Entry('/usr/lib/minios/firstboot.py', 0o644, 'firstboot.py', None,
              'seeds the writable layer'),
        Entry('/usr/lib/minios/profile.schema.json', 0o644, 'profile.schema.json', None,
              'the contract the profile is checked against'),
        Entry('/usr/lib/minios/version', 0o644, None, image_version() + '\n',
              'what `minios version` reports'),
        Entry('/usr/lib/minios/defaults/providers.yaml', 0o644, 'providers.yaml', None,
              'seeded into /etc/minios by firstboot'),
        Entry('/usr/lib/minios/defaults/egress.yaml', 0o644, 'egress.yaml', None,
              'seeded into /etc/minios by firstboot'),
        Entry('/usr/lib/systemd/system/hwprofild.service', 0o644, 'hwprofild.service', None,
              'writes the profile before anything needs it'),
        Entry('/usr/lib/systemd/system/minios-firstboot.service', 0o644,
              'minios-firstboot.service', None,
              'runs firstboot before hwprofild'),
    ]


def target_path(skeleton, dest):
    return os.path.join(skeleton, dest.lstrip('/'))


def missing_sources(entries):
    return [e.source for e in entries
            if e.source and not os.path.exists(os.path.join(HERE, e.source))]


def stage(skeleton):
    entries = manifest()
    missing = missing_sources(entries)
    if missing:
        return None, ['missing source file(s): %s' % ', '.join(sorted(set(missing)))]

    problems = []
    previous = set()
    marker = os.path.join(skeleton, STAGED_MARKER)
    if os.path.exists(marker):
        try:
            with open(marker, encoding='utf-8') as fh:
                previous = set(json.load(fh).get('placed', []))
        except (OSError, ValueError):
            problems.append('the previous staging marker was unreadable; stale files '
                            'may remain')

    placed = []
    for entry in entries:
        destination = target_path(skeleton, entry.dest)
        directory = os.path.dirname(destination)
        os.makedirs(directory, exist_ok=True)
        if entry.text is not None:
            data = entry.text.encode('utf-8')
        else:
            with open(os.path.join(HERE, entry.source), 'rb') as fh:
                data = fh.read()
        tmp = destination + '.tmp'
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.chmod(tmp, entry.mode)
        os.replace(tmp, destination)
        placed.append(entry.dest)

    removed = []
    for stale in sorted(previous - set(placed)):
        victim = target_path(skeleton, stale)
        if os.path.isfile(victim):
            os.remove(victim)
            removed.append(stale)

    with open(marker, 'w', encoding='utf-8') as fh:
        json.dump({'placed': sorted(placed), 'version': image_version()}, fh, indent=2)
        fh.write('\n')

    return {'placed': placed, 'removed': removed, 'skeleton': skeleton}, problems


def check(skeleton):
    """Every entry present, byte-identical, and nothing left over from before."""
    entries = manifest()
    problems = list('missing source file: %s' % s for s in missing_sources(entries))
    for entry in entries:
        destination = target_path(skeleton, entry.dest)
        if not os.path.exists(destination):
            problems.append('not staged: %s' % entry.dest)
            continue
        with open(destination, 'rb') as fh:
            staged = fh.read()
        expected = (entry.text.encode('utf-8') if entry.text is not None
                    else open(os.path.join(HERE, entry.source), 'rb').read())
        if staged != expected:
            problems.append('out of date: %s' % entry.dest)
        if os.name != 'nt':
            mode = os.stat(destination).st_mode & 0o777
            if mode != entry.mode:
                problems.append('wrong mode on %s: %o, expected %o'
                                % (entry.dest, mode, entry.mode))

    marker = os.path.join(skeleton, STAGED_MARKER)
    if os.path.exists(marker):
        try:
            with open(marker, encoding='utf-8') as fh:
                placed = set(json.load(fh).get('placed', []))
            for stale in sorted(placed - set(e.dest for e in entries)):
                if os.path.isfile(target_path(skeleton, stale)):
                    problems.append('no longer in the manifest but still staged: %s' % stale)
        except (OSError, ValueError):
            problems.append('the staging marker is unreadable')
    elif any(os.path.exists(target_path(skeleton, e.dest)) for e in entries):
        problems.append('files are present but there is no staging marker: '
                        '%s was not built by this program' % skeleton)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='image',
        description='Build and check the image file tree.')
    parser.add_argument('action', choices=('list', 'stage', 'check'))
    parser.add_argument('--out', default=DEFAULT_OUT, metavar='DIR',
                        help='the skeleton directory (default: %s)' % DEFAULT_OUT)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    if args.action == 'list':
        entries = manifest()
        if args.json:
            json.dump([e._asdict() for e in entries], sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write('\n')
            return 0
        width = max(len(e.dest) for e in entries)
        for entry in entries:
            source = entry.source or '(generated)'
            print('%-*s  %-4s  %-22s  %s'
                  % (width, entry.dest, oct(entry.mode)[2:], source, entry.why))
        print()
        print('%d files; stage with `python image.py stage`' % len(entries))
        return 0

    if args.action == 'check':
        problems = check(args.out)
        if args.json:
            json.dump({'skeleton': args.out, 'problems': problems}, sys.stdout, indent=2)
            sys.stdout.write('\n')
            return 1 if problems else 0
        if problems:
            for problem in problems:
                print('image: %s' % problem, file=sys.stderr)
            return 1
        print('image: %s matches the manifest' % args.out)
        return 0

    result, problems = stage(args.out)
    for problem in problems:
        print('image: %s' % problem, file=sys.stderr)
    if result is None:
        return 1
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write('\n')
        return 0
    print('staged %d file(s) into %s' % (len(result['placed']), result['skeleton']))
    for stale in result['removed']:
        print('removed %s (no longer in the manifest)' % stale)
    return 0


if __name__ == '__main__':
    sys.exit(main())
