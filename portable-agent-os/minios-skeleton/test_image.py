"""Tests for the image layer: firstboot and the staging manifest.

Three things are worth checking here, and they are all failures that a boot
would otherwise discover for you:

  * firstboot is idempotent, and never overwrites a file the user has edited;
  * the defaults it seeds are loadable by the real loaders, not just by a YAML
    parser -- a config that parses but that load_providers() cannot use is a
    machine that boots and then cannot talk to anything;
  * the staged image tree still matches the manifest, so nothing is shipped at
    a path the code does not look at.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
FIRSTBOOT = os.path.join(HERE, 'firstboot.py')
IMAGE = os.path.join(HERE, 'image.py')

fails = []


def check(label, ok, detail=''):
    print('  %-8s %-58s %s' % ('OK' if ok else 'FAIL', label, str(detail)[:92]))
    if not ok:
        fails.append(label)


def run(script, *argv, env=None, stdin=None):
    return subprocess.run([PY, script] + [str(a) for a in argv], capture_output=True,
                          text=True, encoding='utf-8', env=env, input=stdin)


def jrun(script, *argv, env=None, stdin=None):
    result = run(script, *(list(argv) + ['--json']), env=env, stdin=stdin)
    try:
        return json.loads(result.stdout), result
    except ValueError:
        return None, result


print('=== syntax ===')
for name in ('firstboot.py', 'image.py'):
    r = subprocess.run([PY, '-m', 'py_compile', os.path.join(HERE, name)],
                       capture_output=True, text=True)
    check('py_compile %s' % name, r.returncode == 0, r.stderr.strip()[:140])
if fails:
    sys.exit(1)

print()
print('=== firstboot: the first run ===')
ROOT = tempfile.mkdtemp(prefix='minios-firstboot-')
open(os.path.join(ROOT, 'README'), 'w').write('staging root\n')  # root must exist first

# The image would ship these; here we lay them out the same way.
os.makedirs(os.path.join(ROOT, 'usr/lib/minios/defaults'), exist_ok=True)
for name in ('providers.yaml', 'egress.yaml'):
    shutil.copy(os.path.join(HERE, name),
                os.path.join(ROOT, 'usr/lib/minios/defaults', name))

payload, result = jrun(FIRSTBOOT, '--root', ROOT)
check('firstboot exits 0 on a fresh root', result.returncode == 0, result.stderr.strip()[:70])
check('it reports what it created', payload['counts']['created'] > 5,
      payload['counts'])

state = os.path.join(ROOT, 'var/lib/minios')
for name in ('machines', 'sessions', 'audit', 'usage', 'secrets'):
    check('state dir %s exists' % name, os.path.isdir(os.path.join(state, name)))
check('the run directory exists', os.path.isdir(os.path.join(ROOT, 'run/minios')))
check('the workspace exists', os.path.isdir(os.path.join(ROOT, 'root/work')))

machine_id = open(os.path.join(ROOT, 'etc/machine-id'), encoding='utf-8').read().strip()
check('a machine id was generated', len(machine_id) == 32, machine_id)
check('it is a hex string', all(c in '0123456789abcdef' for c in machine_id))
check('the machine id is read-only', os.stat(os.path.join(ROOT, 'etc/machine-id')).st_mode & 0o200 == 0
      if os.name != 'nt' else True)

for name in ('providers.yaml', 'egress.yaml'):
    check('%s was seeded into /etc/minios' % name,
          os.path.exists(os.path.join(ROOT, 'etc/minios', name)))
check('the admin marker says unset',
      open(os.path.join(ROOT, 'etc/minios/admin'), encoding='utf-8').read().strip() == 'unset')
check('the workspace explains itself',
      'sandbox boundary' in open(os.path.join(ROOT, 'root/work/README'), encoding='utf-8').read())
check('a report was written for later inspection',
      os.path.exists(os.path.join(ROOT, 'run/minios/firstboot.json')))

print()
print('=== firstboot: the second run changes nothing ===')
payload2, result2 = jrun(FIRSTBOOT, '--root', ROOT)
check('second run exits 0', result2.returncode == 0)
check('second run creates nothing', payload2['counts']['created'] == 0, payload2['counts'])
check('the machine id is unchanged',
      open(os.path.join(ROOT, 'etc/machine-id'), encoding='utf-8').read().strip() == machine_id)
check('every step reports an existing state',
      all(s['status'] in ('existing', 'kept') for s in payload2['steps']),
      sorted({s['status'] for s in payload2['steps']}))

print()
print('=== firstboot: a file the user edited is never overwritten ===')
custom = '# my own provider list\nproviders:\n  - name: mine\n    base_url: https://x/v1\n'
open(os.path.join(ROOT, 'etc/minios/providers.yaml'), 'w', encoding='utf-8').write(custom)
jrun(FIRSTBOOT, '--root', ROOT)
check('the edited config survived',
      open(os.path.join(ROOT, 'etc/minios/providers.yaml'), encoding='utf-8').read() == custom)
payload3, _ = jrun(FIRSTBOOT, '--root', ROOT)
check('and it is reported as kept, not created',
      any(s['step'] == 'config providers.yaml' and s['status'] == 'kept' for s in payload3['steps']))

print()
print('=== firstboot: dry run writes nothing ===')
DRY = tempfile.mkdtemp(prefix='minios-firstboot-dry-')
os.makedirs(os.path.join(DRY, 'usr/lib/minios/defaults'), exist_ok=True)
for name in ('providers.yaml', 'egress.yaml'):
    shutil.copy(os.path.join(HERE, name), os.path.join(DRY, 'usr/lib/minios/defaults', name))
payload4, result4 = jrun(FIRSTBOOT, '--root', DRY, '--dry-run')
check('dry run exits 0', result4.returncode == 0)
check('dry run claims it would create things', payload4['counts']['created'] > 5)
check('but nothing was written', not os.path.isdir(os.path.join(DRY, 'var')))
check('and no machine id appeared', not os.path.exists(os.path.join(DRY, 'etc/machine-id')))

check('a missing root is refused', run(FIRSTBOOT, '--root', os.path.join(ROOT, 'nope')).returncode == 4)

print()
print('=== the seeded defaults are loadable by the real loaders ===')
# A config that parses but that the code cannot use is a machine that boots and
# then cannot talk to anything, so this goes through minioscore, not PyYAML.
# It gets its own root: the one above deliberately had its providers.yaml
# replaced by a user edit, which is exactly what must not be tested here.
ROOT2 = tempfile.mkdtemp(prefix='minios-firstboot-seeded-')
os.makedirs(os.path.join(ROOT2, 'usr/lib/minios/defaults'), exist_ok=True)
for name in ('providers.yaml', 'egress.yaml'):
    shutil.copy(os.path.join(HERE, name),
                os.path.join(ROOT2, 'usr/lib/minios/defaults', name))
run(FIRSTBOOT, '--root', ROOT2)
check('the seeded root has its own machine id',
      os.path.exists(os.path.join(ROOT2, 'etc/machine-id')))

probe = (
    'import json, sys; sys.path.insert(0, %r); import minioscore as core;'
    'config, problem, origin = core.load_providers();'
    'policy = core.Policy.load();'
    'print(json.dumps({"providers": len(config.get("providers") or []),'
    ' "problem": problem, "mode": policy.mode,'
    ' "deny": len(policy.deny), "redactions": len(policy.redactions)}))'
) % HERE
state2 = os.path.join(ROOT2, 'var/lib/minios')
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
           MINIOS_UNSAFE_ALLOW_ADMIN='1',
           MINIOS_CONFIG_DIR=os.path.join(ROOT2, 'etc/minios'),
           MINIOS_STATE_DIR=state2, MINIOS_RUN_DIR=os.path.join(ROOT2, 'run/minios'))
r = subprocess.run([PY, '-c', probe], capture_output=True, text=True, encoding='utf-8', env=env)
loaded = json.loads(r.stdout) if r.returncode == 0 else None
check('the shipped providers.yaml loads', loaded and loaded['providers'] >= 2,
      loaded and loaded['providers'])
check('the shipped egress.yaml loads', loaded and loaded['mode'] == 'denylist'
      and loaded['deny'] > 0 and loaded['redactions'] > 0, loaded)

print()
print('=== doctor: the layout checks ===')
result = run(os.path.join(HERE, 'minios.py'), 'doctor', '--only', 'F1b,E1b', '--json', env=env)
payload5 = json.loads(result.stdout)
by_id = {row['id']: row for row in payload5['results']}
check('F1b passes on a seeded root', by_id['F1b']['status'] == 'pass', by_id['F1b']['detail'][:54])
check('E1b fails while the password is unset', by_id['E1b']['status'] == 'fail',
      by_id['E1b']['detail'][:60])

print()
print('=== admin passwd: the action E1b was asking for ===')
# A check with no way to satisfy it is a defect in the check.  This drives the
# real command rather than hand-writing the marker.
CLI = os.path.join(HERE, 'minios.py')
check('admin status reports unset', 'unset' in run(CLI, 'admin', 'status', env=env).stdout)
result = run(CLI, 'admin', 'passwd', env=env, stdin='correct horse battery\ncorrect horse battery\n')
bad = run(CLI, 'admin', 'passwd', env=env, stdin='one\ntwo\n')
short = run(CLI, 'admin', 'passwd', env=env, stdin='short\nshort\n')
check('admin passwd accepts a good password', result.returncode == 0, result.stdout.strip()[:60])
check('it refuses a mismatched confirmation', bad.returncode == 1, bad.stderr.strip()[:60])
check('it refuses a short password', short.returncode == 1, short.stderr.strip()[:60])

marker = open(os.path.join(ROOT2, 'etc/minios/admin'), encoding='utf-8').read()
check('the password is stored as a scrypt hash', marker.startswith('scrypt$'),
      marker.split('$')[1] if marker.count('$') == 3 else marker[:40])
check('the password itself is not stored anywhere',
      'correct horse battery' not in marker)
payload6 = json.loads(run(CLI, 'doctor', '--only', 'E1b', '--json', env=env).stdout)
check('E1b now passes', payload6['results'][0]['status'] == 'pass',
      payload6['results'][0]['detail'][:60])
check('admin status reports the kdf and the time',
      'scrypt' in run(CLI, 'admin', 'status', env=env).stdout)
check('admin passwd needs root too',
      run(CLI, 'admin', 'passwd', env=dict(env, MINIOS_UNSAFE_ALLOW_ADMIN='0'),
          stdin='x\ny\n').returncode == 4)
payload7 = json.loads(run(os.path.join(HERE, 'minios.py'), 'doctor', '--only', 'F1b', '--json',
                          env=dict(env, MINIOS_STATE_DIR=os.path.join(ROOT2, 'empty-state'),
                                   MINIOS_RUN_DIR=os.path.join(ROOT2, 'empty-run'))).stdout)
check('F1b answers unknown when nothing has ever run',
      payload7['results'][0]['status'] == 'unknown', payload7['results'][0]['detail'][:60])
# A report saying firstboot ran, with the directories it creates missing, is a
# different answer: it ran and the result is gone, which is a failure and not a
# gap in our knowledge.
payload8 = json.loads(run(os.path.join(HERE, 'minios.py'), 'doctor', '--only', 'F1b', '--json',
                          env=dict(env, MINIOS_STATE_DIR=os.path.join(ROOT2, 'empty-state'))).stdout)
check('F1b fails when it ran but the layout is gone',
      payload8['results'][0]['status'] == 'fail', payload8['results'][0]['detail'][:60])

print()
print('=== image: the staging manifest ===')
entries, _ = jrun(IMAGE, 'list')
check('the manifest renders as json', isinstance(entries, list) and len(entries) >= 11,
      '%d entries' % (len(entries) if entries else 0))
check('every destination is absolute',
      all(e['dest'].startswith('/') for e in entries))
check('the image ships no /etc/minios',
      not any(e['dest'].startswith('/etc/') for e in entries),
      [e['dest'] for e in entries if e['dest'].startswith('/etc/')])
check('defaults live under /usr/lib/minios/defaults',
      sum(1 for e in entries if e['dest'].startswith('/usr/lib/minios/defaults/')) == 2)
check('the launcher is the only thing in /usr/bin',
      [e['dest'] for e in entries if e['dest'].startswith('/usr/bin/')] == ['/usr/bin/minios'])

SKEL = os.path.join(tempfile.mkdtemp(prefix='minios-image-'), 'mkosi.skeleton')
result = run(IMAGE, 'stage', '--out', SKEL)
check('stage exits 0', result.returncode == 0, result.stderr.strip()[:80])
check('stage reports what it placed', 'staged %d file' % len(entries) in result.stdout,
      result.stdout.strip()[:60])
check('every entry is on disk',
      all(os.path.exists(os.path.join(SKEL, e['dest'].lstrip('/'))) for e in entries))
check('check passes on a fresh stage', run(IMAGE, 'check', '--out', SKEL).returncode == 0,
      run(IMAGE, 'check', '--out', SKEL).stderr.strip()[:60])

launcher = open(os.path.join(SKEL, 'usr/bin/minios'), encoding='utf-8').read()
check('the launcher execs the real module',
      'exec python3 /usr/lib/minios/minios.py "$@"' in launcher)
check('the staged minios.py is byte-identical to the source',
      open(os.path.join(SKEL, 'usr/lib/minios/minios.py'), 'rb').read()
      == open(os.path.join(HERE, 'minios.py'), 'rb').read())

print()
print('=== image: check notices when the tree falls behind ===')
victim = os.path.join(SKEL, 'usr/lib/minios/minioscore.py')
open(victim, 'a', encoding='utf-8').write('\n# an edit somebody made by hand\n')
result = run(IMAGE, 'check', '--out', SKEL)
check('check fails on an edited file', result.returncode == 1)
check('and names it', 'out of date: /usr/lib/minios/minioscore.py' in result.stderr,
      result.stderr.strip()[:80])

marker = os.path.join(SKEL, '.staged.json')
data = json.load(open(marker, encoding='utf-8'))
data['placed'].append('/usr/lib/minios/gone.py')
json.dump(data, open(marker, 'w', encoding='utf-8'))
open(os.path.join(SKEL, 'usr/lib/minios/gone.py'), 'w').write('# left over\n')
result = run(IMAGE, 'check', '--out', SKEL)
check('check reports a file that left the manifest',
      'no longer in the manifest' in result.stderr, result.stderr.strip()[:80])
result = run(IMAGE, 'stage', '--out', SKEL)
check('stage removes it', 'removed /usr/lib/minios/gone.py' in result.stdout,
      result.stdout.strip().replace('\n', ' | ')[:80])
check('and the tree is clean again', run(IMAGE, 'check', '--out', SKEL).returncode == 0,
      run(IMAGE, 'check', '--out', SKEL).stderr.strip()[:70])

print()
print('=== the manifest and the code agree on where things are ===')
# minioscore reads /usr/lib/minios/version and expects its modules side by side;
# the launcher hard-codes the same directory. If the manifest moved them without
# the code following, this is where it shows up.
mkosi = open(os.path.join(HERE, 'mkosi.conf'), encoding='utf-8').read()
check('mkosi.conf points at the tree this program builds',
      'SkeletonTrees=mkosi.skeleton' in mkosi, 'SkeletonTrees line')
check('the version file the code reads is in the manifest',
      any(e['dest'] == '/usr/lib/minios/version' for e in entries))
check('both units are in the manifest',
      sum(1 for e in entries if e['dest'].startswith('/usr/lib/systemd/system/')) == 2)
firstboot_unit = open(os.path.join(HERE, 'minios-firstboot.service'), encoding='utf-8').read()
check('the firstboot unit runs the staged script',
      'ExecStart=/usr/lib/minios/firstboot.py' in firstboot_unit)
check('firstboot runs before hwprofild',
      'Before=hwprofild.service' in firstboot_unit)

shutil.rmtree(ROOT, ignore_errors=True)
shutil.rmtree(ROOT2, ignore_errors=True)
shutil.rmtree(DRY, ignore_errors=True)
shutil.rmtree(os.path.dirname(SKEL), ignore_errors=True)

print()
print('RESULT:', 'all checks passed' if not fails else '%d FAILED: %s' % (len(fails), ', '.join(fails)))
sys.exit(1 if fails else 0)
