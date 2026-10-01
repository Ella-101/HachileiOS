"""Set up a throwaway environment and show what the commands actually print.

This is not a test -- it is the evidence for a human reading the result.  The
environment is the same shape the tests use, minus the fake provider: a config
directory, a state directory, and a workspace with a file that must not leave.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, 'minios.py')

BASE = tempfile.mkdtemp(prefix='minios-demo-')
CONFIG, STATE, RUN, WORK = (os.path.join(BASE, n) for n in ('etc', 'state', 'run', 'work'))
for path in (CONFIG, STATE, RUN, WORK, os.path.join(WORK, 'notes')):
    os.makedirs(path, exist_ok=True)

open(os.path.join(WORK, 'todo.txt'), 'w', encoding='utf-8').write('buy milk\n')
open(os.path.join(WORK, 'id_ed25519'), 'w', encoding='utf-8').write('-----BEGIN OPENSSH PRIVATE KEY-----\n')
open(os.path.join(WORK, 'notes', 'report.md'), 'w', encoding='utf-8').write(
    'quarterly report\ncard on file: 4111 1111 1111 1111\n')

shutil.copy(os.path.join(HERE, 'providers.yaml'), os.path.join(CONFIG, 'providers.yaml'))
shutil.copy(os.path.join(HERE, 'egress.yaml'), os.path.join(CONFIG, 'egress.yaml'))

ENV = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', NO_COLOR='1',
           MINIOS_CONFIG_DIR=CONFIG, MINIOS_STATE_DIR=STATE, MINIOS_RUN_DIR=RUN,
           MINIOS_WORKSPACE=WORK, MINIOS_ALLOW_PLAINTEXT_KEYS='1',
           MINIOS_UNSAFE_ALLOW_ADMIN='1')


def show(*argv):
    print('$ minios ' + ' '.join(str(a).replace(BASE, '~') for a in argv))
    result = subprocess.run([PY, CLI] + [str(a) for a in argv], capture_output=True,
                            text=True, encoding='utf-8', env=ENV)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print('  [stderr] ' + result.stderr.strip().replace(BASE, '~'))
    print('  [exit %d]' % result.returncode)
    print()


print('=' * 78)
show('policy', 'check', os.path.join(WORK, 'id_ed25519'))
print('=' * 78)
show('policy', 'check', os.path.join(WORK, 'notes/report.md'))
print('=' * 78)
show('tool', 'run', 'fs.read', '--args', json.dumps({'path': os.path.join(WORK, 'notes/report.md')}))
print('=' * 78)
show('tool', 'run', 'fs.read', '--args', json.dumps({'path': '/etc/shadow'}))
print('=' * 78)
show('tools')
print('=' * 78)
show('doctor')
print('=' * 78)
show('status')
print('=' * 78)
show('persist', 'status')

shutil.rmtree(BASE, ignore_errors=True)
