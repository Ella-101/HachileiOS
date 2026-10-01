import json
import os
import subprocess
import sys
import tempfile

PY = r'C:\Users\stat3\.workbuddy\binaries\python\versions\3.13.12\python.exe'
ROOT = r'D:\HachileiOS\.workbuddy\notes\minios-skeleton'
SCRIPT = os.path.join(ROOT, 'hwprofil.py')

fails = []


def check(label, ok, detail=''):
    print('  %-8s %s%s' % ('OK' if ok else 'FAIL', label, ('  ' + detail) if detail else ''))
    if not ok:
        fails.append(label)


print('=== 1. syntax ===')
r = subprocess.run([PY, '-m', 'py_compile', SCRIPT], capture_output=True, text=True)
check('py_compile hwprofil.py', r.returncode == 0, r.stderr.strip()[:200])

print()
print('=== 2. json ===')
try:
    schema = json.load(open(os.path.join(ROOT, 'profile.schema.json'), encoding='utf-8'))
    check('profile.schema.json parses', True, '$schema=%s' % schema.get('$schema', '?').rsplit('/', 3)[0])
    check('schema requires the core keys', set(['cpu', 'memory', 'firmware', 'platform', 'disks', 'net', 'pci']).issubset(set(schema['required'])))
except Exception as exc:
    check('profile.schema.json parses', False, repr(exc))

print()
print('=== 3. yaml ===')
try:
    import yaml
    for name in ('providers.yaml', 'egress.yaml'):
        try:
            doc = yaml.safe_load(open(os.path.join(ROOT, name), encoding='utf-8'))
            check('%s parses' % name, isinstance(doc, dict), 'top-level keys: %s' % ', '.join(list(doc)[:5]))
        except Exception as exc:
            check('%s parses' % name, False, repr(exc)[:180])
except ImportError:
    check('PyYAML available', False, 'not installed - yaml files unverified')

print()
print('=== 4. mkosi.conf is INI-shaped ===')
try:
    import configparser
    cp = configparser.ConfigParser()
    cp.read(os.path.join(ROOT, 'mkosi.conf'), encoding='utf-8')
    check('mkosi.conf parses', True, 'sections: %s' % ', '.join(cp.sections()))
except Exception as exc:
    check('mkosi.conf parses', False, repr(exc)[:180])

print()
print('=== 5. hwprofil functional test (no /proc here, so mostly nulls) ===')
tmp = tempfile.mkdtemp(prefix='hwprofil-test-')
env = dict(os.environ, MINIOS_STATE_DIR=tmp, MINIOS_RUN_DIR=os.path.join(tmp, 'run'))
os.makedirs(os.path.join(tmp, 'run'), exist_ok=True)

r = subprocess.run([PY, SCRIPT, '--print'], capture_output=True, text=True, env=env)
check('--print exits 0', r.returncode == 0, (r.stderr or '').strip()[:160])
profile = None
if r.returncode == 0:
    try:
        profile = json.loads(r.stdout)
        check('--print emits json', True, 'keys: %s' % ', '.join(list(profile)[:6]))
        check('degraded reads become null, not errors',
              profile['firmware'].get('kind') in ('bios', 'uefi'),
              'firmware.kind=%r secure_boot=%r' % (profile['firmware'].get('kind'), profile['firmware'].get('secure_boot')))
    except Exception as exc:
        check('--print emits json', False, repr(exc)[:160])

r = subprocess.run([PY, SCRIPT], capture_output=True, text=True, env=env)
check('bare run refuses politely', r.returncode == 2, 'exit=%d' % r.returncode)

# first boot: never seen this machine
r = subprocess.run([PY, SCRIPT, '--boot'], capture_output=True, text=True, env=env)
out1 = (r.stdout or '').strip()
check('boot #1 exits 0', r.returncode == 0, out1.replace(chr(10), ' | ')[:180])
check('boot #1 reports a new machine', 'new machine' in out1)

# second boot: same machine, nothing changed
r = subprocess.run([PY, SCRIPT, '--boot'], capture_output=True, text=True, env=env)
out2 = (r.stdout or '').strip()
check('boot #2 exits 0', r.returncode == 0, out2.replace(chr(10), ' | ')[:180])
check('boot #2 says no change', 'no change' in out2, out2[:160])

state = os.path.join(tmp, 'machines')
hwchange = os.path.join(tmp, 'run', 'hwchange.json')
check('hwchange.json written', os.path.exists(hwchange))
if os.path.exists(hwchange):
    summary = json.load(open(hwchange, encoding='utf-8'))
    check('hwchange says seen_before', summary.get('seen_before') is True)
    check('hwchange names the basis', bool(summary.get('compared_against')), str(summary.get('compared_against'))[:80])

check('per-machine history kept', os.path.exists(os.path.join(state, profile['machine_id'], 'profile.json')))
check('previous profile kept as .prev', os.path.exists(os.path.join(state, profile['machine_id'], 'profile.json.prev')))
last_file = os.path.join(state, 'last')
check('last-machine pointer written', os.path.exists(last_file),
      open(last_file, encoding='utf-8').read().strip() if os.path.exists(last_file) else '')
check('last is a plain file, not a symlink (exFAT has no symlinks)',
      os.path.exists(last_file) and not os.path.islink(last_file))

# cross-machine diff: invent the machine we booted on last time
print()
print('=== 6. cross-machine diff (the interesting case for a portable image) ===')
other = os.path.join(state, 'deadbeefdeadbeefdeadbeefdeadbeef')
os.makedirs(other, exist_ok=True)
fake = json.loads(json.dumps(profile))
fake['machine_id'] = 'deadbeefdeadbeefdeadbeefdeadbeef'
fake['memory']['total_kb'] = (profile['memory']['total_kb'] or 0) // 2 or 1024
fake['cpu']['model'] = 'An Older CPU'
fake['disks'] = [{'name': 'sdz', 'size_bytes': 1, 'removable': False, 'rotational': False, 'model': 'OLD'}]
if profile['net']:
    fake['net'] = []
json.dump(fake, open(os.path.join(other, 'profile.json'), 'w', encoding='utf-8'))
open(last_file, 'w', encoding='utf-8').write('deadbeefdeadbeefdeadbeefdeadbeef\n')
os.remove(os.path.join(state, profile['machine_id'], 'profile.json'))
os.remove(os.path.join(state, profile['machine_id'], 'profile.json.prev'))

r = subprocess.run([PY, SCRIPT, '--boot'], capture_output=True, text=True, env=env)
out3 = (r.stdout or '').strip()
check('boot #3 exits 0', r.returncode == 0, out3.replace(chr(10), ' | ')[:200])
check('boot #3 reports a new machine', 'new machine' in out3)
check('boot #3 diffs against the previous host', 'deadbeef' in out3, out3.replace(chr(10), ' | ')[:200])
for needle, label in (('cpu:', 'cpu change reported'), ('disk removed: sdz', 'disk change reported')):
    check(label, needle in out3)

print()
print('=== 7. systemd unit shape ===')
unit = open(os.path.join(ROOT, 'hwprofild.service'), encoding='utf-8').read()
for key in ('[Unit]', '[Service]', '[Install]', 'Type=oneshot', 'ExecStart=', 'WantedBy=sysctl.target' if False else 'WantedBy=sysinit.target'):
    check('unit has %s' % key, key in unit)

print()
print('RESULT:', 'all checks passed' if not fails else '%d FAILED: %s' % (len(fails), ', '.join(fails)))
sys.exit(1 if fails else 0)
