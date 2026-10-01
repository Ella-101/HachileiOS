"""End-to-end tests for the minios command line.

The interesting tests here are not "does the parser build".  They run commands
against a real provider endpoint, a real file tree and a real policy file, and
check what comes back.  Where this machine cannot answer -- there is no /proc on
a Windows development box -- the point is to check that the tool answers
`unknown` instead of inventing something.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, 'minios.py')

fails = []


def check(label, ok, detail=''):
    print('  %-8s %-56s %s' % ('OK' if ok else 'FAIL', label, str(detail)[:96]))
    if not ok:
        fails.append(label)


# ------------------------------------------------------------- fake provider

class FakeProvider(BaseHTTPRequestHandler):
    """A stand-in for an OpenAI-compatible endpoint, including SSE."""

    seen_auth = None

    def log_message(self, *args):
        pass

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeProvider.seen_auth = self.headers.get('Authorization')
        if self.path.endswith('/v1/models'):
            self._send({'object': 'list', 'data': [{'id': 'fake-large'}, {'id': 'fake-mini'}]})
        else:
            self._send({'error': 'not found'}, 404)

    def do_POST(self):
        FakeProvider.seen_auth = self.headers.get('Authorization')
        length = int(self.headers.get('Content-Length') or 0)
        payload = json.loads(self.rfile.read(length) or b'{}')
        if payload.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for piece in ('Hello', ', ', 'world', '!'):
                event = {'choices': [{'delta': {'content': piece}}]}
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode('utf-8'))
                self.wfile.flush()
            self.wfile.write(('data: ' + json.dumps(
                {'usage': {'prompt_tokens': 7, 'completion_tokens': 4}}) + '\n\n').encode('utf-8'))
            self.wfile.write(b'data: [DONE]\n\n')
            return
        last = (payload.get('messages') or [{}])[-1].get('content', '')
        self._send({'choices': [{'message': {'content': 'echo: ' + last}}],
                    'usage': {'prompt_tokens': 5, 'completion_tokens': 9}})


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


server = ThreadingHTTPServer(('127.0.0.1', free_port()), FakeProvider)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

# ------------------------------------------------------------- environment

BASE = tempfile.mkdtemp(prefix='minios-cli-test-')
CONFIG, STATE, RUN, WORK = (os.path.join(BASE, n) for n in ('etc', 'state', 'run', 'work'))
for path in (CONFIG, STATE, RUN, WORK, os.path.join(WORK, 'docs')):
    os.makedirs(path, exist_ok=True)

open(os.path.join(WORK, 'notes.txt'), 'w', encoding='utf-8').write(
    'a note\ncard 4111 1111 1111 1111 should not leave this machine\n')
open(os.path.join(WORK, 'server.pem'), 'w', encoding='utf-8').write('-----BEGIN KEY-----\n')
open(os.path.join(WORK, 'docs', 'readme.md'), 'w', encoding='utf-8').write('# hi\n')
open(os.path.join(BASE, 'outside.txt'), 'w', encoding='utf-8').write('not yours\n')

open(os.path.join(CONFIG, 'providers.yaml'), 'w', encoding='utf-8').write("""
gateway:
  listen: 127.0.0.1:%(p)d
  default_model: fake-large
  request_timeout_s: 10
  stream_idle_timeout_s: 5
providers:
  - name: primary
    type: openai_compatible
    base_url: http://127.0.0.1:%(p)d/v1
    key_ref: "secret:primary"
    models: [fake-large]
    priority: 10
    cooldown_s: 1
  - name: backup
    type: openai_compatible
    base_url: http://127.0.0.1:%(p)d/v1
    key_ref: "secret:backup"
    models: [fake-mini]
    priority: 20
    cooldown_s: 1
""" % {'p': PORT})

open(os.path.join(CONFIG, 'egress.yaml'), 'w', encoding='utf-8').write(
    'mode: denylist\n'
    'deny_paths:\n'
    '  - "**/*.pem"\n'
    '  - "**/.ssh/**"\n'
    'allow_paths: []\n'
    'redact:\n'
    '  - name: payment-card\n'
    "    pattern: '\\b(?:\\d[ -]?){13,19}\\b'\n"
    '    replace: "[redacted:card]"\n'
    'egress_hosts: ["127.0.0.1"]\n')

open(os.path.join(CONFIG, 'prices.json'), 'w', encoding='utf-8').write(
    json.dumps({'fake-large': {'in': 1.0, 'out': 2.0}}))

ENV = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', NO_COLOR='1',
           MINIOS_CONFIG_DIR=CONFIG, MINIOS_STATE_DIR=STATE, MINIOS_RUN_DIR=RUN,
           MINIOS_WORKSPACE=WORK, MINIOS_ALLOW_PLAINTEXT_KEYS='1',
           MINIOS_UNSAFE_ALLOW_ADMIN='1')


def run(*argv, env=None, stdin=None):
    return subprocess.run([PY, CLI] + [str(a) for a in argv], capture_output=True,
                          text=True, encoding='utf-8', env=env or ENV, input=stdin)


def jrun(*argv, env=None, stdin=None):
    """Same, but ask for json.  Every reporting command has to support it."""
    result = run(*(list(argv) + ['--json']), env=env, stdin=stdin)
    try:
        return json.loads(result.stdout), result
    except ValueError:
        return None, result


print('=== syntax ===')
for name in ('minios.py', 'minioscore.py'):
    r = subprocess.run([PY, '-m', 'py_compile', os.path.join(HERE, name)],
                       capture_output=True, text=True)
    check('py_compile %s' % name, r.returncode == 0, r.stderr.strip()[:140])
if fails:
    sys.exit(1)

print()
print('=== the command tree ===')
commands, _ = jrun('help', '--format', 'json')
check('registry renders as json', isinstance(commands, list) and len(commands) > 30,
      '%d commands' % (len(commands) if commands else 0))
bad = []
for c in commands or []:
    argv = c['path'].replace('.', ' ').split() + ['--help']
    if run(*argv).returncode != 0:
        bad.append(' '.join(argv))
check('all %d commands build a parser' % len(commands or []), not bad, ', '.join(bad[:3]))
check('help --format markdown emits a table',
      run('help', '--format', 'markdown').stdout.startswith('| command |'))
check('help <command> narrows to one row',
      len([l for l in run('help', 'doctor').stdout.splitlines() if l.strip().startswith('doctor')]) == 1)

print()
print('=== exit codes ===')
check('unimplemented update returns 3', run('update', 'check').returncode == 3)
check('and says which phase', 'planned for P6' in run('update', 'apply').stderr)
check('bad flag returns 2', run('net', 'probe', '--bogus').returncode == 2)
check('unknown command returns 2', run('nonsense').returncode == 2)
check('admin gate refuses without root',
      run('prov', 'add', 'x', '--base-url', 'http://h/v1',
          env=dict(ENV, MINIOS_UNSAFE_ALLOW_ADMIN='0')).returncode == 4)

print()
print('=== global flag survives the subparser ===')
a, b = run('--json', 'status'), run('status', '--json')
check('--json at the root equals --json at the leaf',
      a.stdout == b.stdout and a.stdout.lstrip().startswith('{'))
check('human output is not json', not run('status').stdout.lstrip().startswith('{'))

print()
print('=== policy: would this be sent? ===')
payload, _ = jrun('policy', 'check', os.path.join(WORK, 'notes.txt'))
check('an ordinary file is allowed', payload and payload['allowed'])
payload, _ = jrun('policy', 'check', os.path.join(WORK, 'server.pem'))
check('a .pem is denied', payload and not payload['allowed'], payload and payload['rule'])
check('and the rule that denied it is named',
      payload and payload['rule'] == '**/*.pem', payload and payload['reason'])
payload, _ = jrun('policy', 'check', '/etc/shadow')
check('a path outside the workspace is reported as outside',
      payload and payload['in_workspace'] is False, payload and payload['in_workspace'])
check('policy check says the file tools refuse it regardless',
      'regardless' in run('policy', 'check', '/etc/shadow').stdout)
check('policy show lists the deny rules', '**/*.pem' in run('policy', 'show').stdout)

print()
print('=== the matcher is not fnmatch ===')
# fnmatch lets `*` cross "/", so `**/.ssh/*` would silently also cover deeper
# levels.  The difference between covering one level and covering all of them
# is exactly the kind of thing that should not depend on which library ran.
sys.path.insert(0, HERE)
import minioscore as core
check('**/.ssh/* still matches one level',
      not core.Policy({'mode': 'denylist', 'deny_paths': ['**/.ssh/*']})
      .decide('/home/u/.ssh/id_rsa')['allowed'])
check('a bare pattern is anchored at any depth',
      not core.Policy({'mode': 'denylist', 'deny_paths': ['*.pem']})
      .decide('/deep/down/a.pem')['allowed'])
allow = core.Policy({'mode': 'allowlist', 'allow_paths': [core.norm(WORK) + '/**']})
check('allowlist denies everything else', not allow.decide(os.path.join(BASE, 'outside.txt'))['allowed'])
check('allowlist allows what is listed',
      allow.decide(os.path.join(WORK, 'notes.txt'))['allowed'],
      core.norm(WORK) + '/**')
text, fired = core.Policy({'redact': [{'name': 'card', 'pattern': r'\b\d{16}\b',
                                       'replace': '[x]'}]}).redact('n 4111111111111111 end')
check('redaction fires and reports itself', text == 'n [x] end' and fired == ['card x1'])

print()
print('=== tools: the sandbox is a boundary, not a convention ===')
payload, _ = jrun('tools')
check('tools lists the workspace', payload and payload['workspace'] == os.path.realpath(WORK))
check('fs.write is marked as needing confirmation',
      any(t['name'] == 'fs.write' and t['requires_confirmation'] for t in payload['tools']))

payload, _ = jrun('tool', 'run', 'fs.list', '--args', json.dumps({'path': WORK}))
check('fs.list works', payload and payload['ok'],
      payload and [e['name'] for e in payload['result']['entries']])

payload, _ = jrun('tool', 'run', 'fs.read',
                  '--args', json.dumps({'path': os.path.join(WORK, 'docs/readme.md')}))
check('fs.read works', payload and payload['ok'], payload and repr(payload['result']['content'])[:40])

payload, _ = jrun('tool', 'run', 'fs.read',
                  '--args', json.dumps({'path': os.path.join(BASE, 'outside.txt')}))
check('fs.read refuses to leave the workspace',
      payload and not payload['ok'] and payload['error']['code'] == 'E_PATH_OUTSIDE_WORKSPACE',
      payload and payload['error']['code'])

payload, _ = jrun('tool', 'run', 'fs.read',
                  '--args', json.dumps({'path': os.path.join(WORK, 'docs/../server.pem')}))
check('a traversal path is resolved and then denied',
      payload and not payload['ok'] and payload['error']['code'] == 'E_PATH_DENIED_BY_POLICY',
      payload and payload['error']['code'])

payload, _ = jrun('tool', 'run', 'fs.read', '--args', json.dumps({'path': os.path.join(WORK, 'notes.txt')}))
check('fs.read scrubs before returning',
      payload and payload['ok'] and '[redacted:card]' in payload['result']['content']
      and payload['result']['redacted'] == ['payment-card x1'],
      payload and payload['result'].get('redacted'))

payload, _ = jrun('tool', 'run', 'sh.exec', '--args', json.dumps({'command': 'id'}))
check('sh.exec is refused, not quietly allowed',
      payload and not payload['ok'] and payload['error']['code'] == 'E_SANDBOX_UNAVAILABLE',
      payload and payload['error']['code'])

payload, _ = jrun('tool', 'run', 'http.request', '--args', json.dumps({'url': 'http://example.com/'}))
check('http.request refuses a host that is not allowlisted',
      payload and not payload['ok'] and payload['error']['code'] == 'E_HOST_NOT_ALLOWED',
      payload and payload['error']['code'])

payload, _ = jrun('tool', 'run', 'http.request',
                  '--args', json.dumps({'url': 'http://127.0.0.1:%d/v1/models' % PORT}))
check('http.request works for an allowlisted host',
      payload and payload['ok'] and payload['result']['status'] == 200,
      payload and (payload.get('result') or {}).get('status'))

payload, _ = jrun('tool', 'run', 'fs.write',
                  '--args', json.dumps({'path': os.path.join(WORK, 'written.txt'), 'content': 'hello\n'}))
check('fs.write works', payload and payload['ok']
      and os.path.exists(os.path.join(WORK, 'written.txt')))
payload, _ = jrun('tool', 'run', 'nosuch', '--args', '{}')
check('an unknown tool is a structured error',
      payload and payload['error']['code'] == 'E_UNKNOWN_TOOL')

print()
print('=== providers ===')
payload, _ = jrun('prov', 'list')
check('prov list shows both providers', payload and len(payload['providers']) == 2,
      payload and [p['name'] for p in payload['providers']])
base_text_before = open(os.path.join(CONFIG, 'providers.yaml'), encoding='utf-8').read()
result = run('prov', 'add', 'third', '--base-url', 'http://127.0.0.1:%d/v1' % PORT,
             '--model', 'fake-mini')
check('prov add writes an overlay', result.returncode == 0 and 'providers.d' in result.stdout,
      result.stdout.strip()[:70])
check('the shipped providers.yaml is untouched',
      open(os.path.join(CONFIG, 'providers.yaml'), encoding='utf-8').read() == base_text_before)
payload, _ = jrun('prov', 'list')
check('prov add took effect', payload and len(payload['providers']) == 3)
check('prov add refuses a duplicate name',
      run('prov', 'add', 'third', '--base-url', 'http://h/v1').returncode == 1)
check('prov rm removes an overlay provider', run('prov', 'rm', 'third').returncode == 0)
payload, _ = jrun('prov', 'list')
check('and it is gone', payload and len(payload['providers']) == 2)
check('prov rm refuses a provider from the base file', run('prov', 'rm', 'primary').returncode == 1)
check('prov use writes defaults', run('prov', 'use', '--model', 'fake-mini').returncode == 0)
payload, _ = jrun('prov', 'list')
check('prov use changed the default model', payload and payload['default_model'] == 'fake-mini')
run('prov', 'use', '--model', 'fake-large')

print()
print('=== the key store ===')
payload, _ = jrun('key', 'list')
check('key list works with no keys', payload['keys'][0]['present'] is False)
result = run('key', 'set', 'primary', stdin='test-key-1234\n')
check('key set reads stdin', result.returncode == 0 and 'stored a key' in result.stdout,
      result.stdout.strip()[:60])
check('key set warns about the plaintext backend', 'WARNING' in result.stderr,
      result.stderr.strip()[:60])
payload, _ = jrun('key', 'list')
check('key list reports presence and a four-character hint',
      payload['keys'][0]['present'] and payload['keys'][0]['hint'] == '\u20261234',
      payload['keys'][0]['hint'])
check('the key really was stored',
      open(os.path.join(STATE, 'secrets', 'primary'), encoding='utf-8').read().strip() == 'test-key-1234')

print()
print('=== the gateway: a real request against a real endpoint ===')
result = run('prov', 'test', 'primary')
check('prov test reaches the endpoint', result.returncode == 0 and 'answered in' in result.stdout,
      result.stdout.strip().replace('\n', ' | ')[:80])
check('the key went out as a bearer token', FakeProvider.seen_auth == 'Bearer test-key-1234',
      FakeProvider.seen_auth)
check('prov test names the models it found', 'fake-large' in result.stdout)

result = run('ask', 'what is 2+2')
check('ask returns the answer', result.returncode == 0 and 'echo: what is 2+2' in result.stdout,
      result.stdout.strip()[:60])
payload, _ = jrun('ask', 'hello')
check('ask --json reports usage and cost',
      payload and payload['usage']['prompt_tokens'] == 5 and payload['cost_usd'] is not None,
      payload and payload['cost_usd'])
result = run('ask', 'stream this', '--stream')
check('ask --stream prints the pieces as they arrive',
      result.returncode == 0 and 'Hello, world!' in result.stdout, result.stdout.strip()[:50])
result = run('chat', stdin='first question\n/session\n/exit\n')
check('chat streams and reports its session',
      result.returncode == 0 and 'Hello, world!' in result.stdout and 'session ' in result.stdout,
      result.stdout.strip().replace('\n', ' | ')[:80])

print()
print('=== sessions ===')
payload, _ = jrun('session', 'list')
check('the chat session was stored', payload and len(payload['sessions']) >= 1,
      payload and [s['id'] for s in payload['sessions']])
identifier = payload['sessions'][0]['id']
payload, _ = jrun('session', 'show', identifier)
check('session show returns the transcript',
      payload and any(e.get('role') == 'assistant' for e in payload['events']))
check('session export as json is parseable',
      json.loads(run('session', 'export', identifier, '--format', 'json').stdout)['id'] == identifier)
check('session show of a missing id fails', run('session', 'show', 'nope').returncode == 1)
check('session rm works', run('session', 'rm', identifier).returncode == 0)
check('and a second rm fails', run('session', 'rm', identifier).returncode == 1)

print()
print('=== audit: kept, but not a copy of the files ===')
check('audit tail runs', run('audit', 'tail', '-n', '50').returncode == 0)
records = [json.loads(l) for l in run('audit', 'export').stdout.splitlines() if l.strip()]
check('tool calls were recorded', any(r.get('tool') == 'fs.read' for r in records),
      '%d records' % len(records))
check('a denied call was recorded with its code',
      any(r.get('code') == 'E_PATH_OUTSIDE_WORKSPACE' for r in records))
check('model calls were recorded', any(r.get('kind') == 'model.call' for r in records))
check('arguments are hashed, never stored', all('content' not in json.dumps(r) for r in records))
check('a path is kept, so the trail is readable', any(r.get('path') for r in records))
check('audit export honours --since',
      not [l for l in run('audit', 'export', '--since', '2099-01-01T00:00:00Z').stdout.splitlines()
           if l.strip()])

print()
print('=== network: two independent axes ===')
payload, _ = jrun('net', 'status')
check('net status reads the kernel view', payload and 'kernel' in payload)
payload, result = jrun('net', 'probe')
check('net probe runs', result.returncode == 0 and payload is not None)
check('net probe reached the fake provider',
      payload and any(p['reachable'] for p in payload['providers']),
      payload and [(p['name'], p['reachable']) for p in payload['providers']])
check('net probe keeps dns and reachability apart',
      payload and 'dns' in payload and 'online' in payload)
check('status now reports the probe instead of "not probed"',
      'not probed' not in run('status').stdout and jrun('status')[0]['network'] is not None)

print()
print('=== doctor: the executable checklist ===')
payload, _ = jrun('doctor')
ids = [row['id'] for row in payload['results']]
check('doctor now runs 13 checks', len(ids) == 13, ' '.join(ids))
check('doctor reports by checklist id', 'D4' in ids and 'E2b' in ids and 'F1b' in ids)
check('D4 passes: the sandbox really refuses an escape',
      next(r['status'] for r in payload['results'] if r['id'] == 'D4') == 'pass')
check('C1 passes when something listens at the configured address',
      next(r['status'] for r in payload['results'] if r['id'] == 'C1') == 'pass')
check('E2b fails on the plaintext development backend',
      next(r['status'] for r in payload['results'] if r['id'] == 'E2b') == 'fail')
check('a fail makes doctor exit 1', run('doctor').returncode == 1)
check('--only narrows the run',
      [r['id'] for r in jrun('doctor', '--only', 'C2')[0]['results']] == ['C2'])
payload, _ = jrun('doctor', '--only', 'E2', env=dict(ENV, SK_TEST_PROVIDER_KEY='sk-abcdefghijklmnop'))
check('E2 catches a key in the environment', payload['results'][0]['status'] == 'fail',
      payload['results'][0]['detail'][:56])
empty_state = dict(ENV, MINIOS_STATE_DIR=os.path.join(BASE, 'empty-state'))
os.makedirs(empty_state['MINIOS_STATE_DIR'], exist_ok=True)
payload, _ = jrun('doctor', '--only', 'A3', env=empty_state)
check('an undecidable check answers unknown, not pass',
      payload['results'][0]['status'] == 'unknown', payload['results'][0]['detail'][:56])

print()
print('=== incognito and persistence ===')
check('incognito starts off', 'off' in run('incognito').stdout)
check('incognito on is recorded', 'on' in run('incognito', 'on').stdout)
check('incognito status reports on', jrun('incognito', 'status')[0]['incognito'] is True)
check('status shows the no-trace flag', '[no-trace]' in run('status').stdout)
check('incognito off works', 'off' in run('incognito', 'off').stdout)
payload, _ = jrun('persist', 'status')
check('persist status names the keystore backend', payload and payload['backend'] == 'plaintext-dev')
check('persist lock refuses when nothing is mounted', run('persist', 'lock').returncode == 4)

print()
print('=== support-bundle: redacted before it is written ===')
# A session has to exist for this: the section above deliberately removed the
# one it had.
run('chat', stdin='a question kept for the bundle\n/exit\n')
out = os.path.join(BASE, 'bundle.tar.gz')
result = run('support-bundle', '--out', out)
check('support-bundle writes an archive', result.returncode == 0 and os.path.exists(out),
      result.stdout.strip().replace('\n', ' | ')[:70])
with tarfile.open(out) as archive:
    names = archive.getnames()
    manifest = json.loads(archive.extractfile('manifest.json').read().decode('utf-8'))
    providers_copy = archive.extractfile('config/providers.yaml').read().decode('utf-8')
    session_names = [n for n in names if n.startswith('sessions/')]
    session_text = ''.join(archive.extractfile(n).read().decode('utf-8', 'replace')
                           for n in session_names)
    text_files = [n for n in names if n.endswith(('.json', '.jsonl', '.yaml', '.log', '.md'))]
    all_text = ''.join(archive.extractfile(n).read().decode('utf-8', 'replace') for n in text_files)
check('the archive has a manifest', 'generated_at' in manifest)
check('it carries the configuration', 'providers:' in providers_copy)
check('it lists every file it collected', len(manifest['files']) > 3, manifest['files'][:2])
check('it counts its own redactions', 'redactions' in manifest, manifest['redactions'])
check('it says what it did not collect', 'Nothing outside' in manifest['note'])
check('no key material is in the bundle', 'test-key-1234' not in all_text)
check('session transcripts are included', bool(session_names) and 'a question kept for the bundle' in session_text,
      '%d session file(s)' % len(session_names))

server.shutdown()
shutil.rmtree(BASE, ignore_errors=True)

print()
print('RESULT:', 'all checks passed' if not fails else '%d FAILED: %s' % (len(fails), ', '.join(fails)))
sys.exit(1 if fails else 0)
