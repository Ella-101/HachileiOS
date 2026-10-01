#!/usr/bin/env python3
"""minioscore - everything minios does that is not the command line.

minios.py owns the command registry and the argument parser; this module owns
the behaviour.  The split is not tidiness for its own sake: the same rule has
to hold in three callers -- the command the user runs, the self-check in
`doctor`, and the tests.  A rule about what may leave this machine that lives
in only two of those three is wrong in the third.

Two principles run through the whole file.

  * Nothing is guessed.  A value we could not read is None, and the caller is
    told so.  The one place this matters most is the self-check: a check that
    cannot be evaluated answers `unknown`, never `pass`.

  * Errors are values.  Tool failures come back as a result the model can read
    (`{"ok": false, "error": {"code": "PATH_OUTSIDE_WORKSPACE", ...}}`), not as
    exceptions.  An exception tells the user something broke; a code tells the
    model how to fix what it asked for.
"""

import base64
import calendar
import fnmatch
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNIMPLEMENTED = 3
EXIT_PRECONDITION = 4

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
SKIP = "skip"

HERE = os.path.dirname(os.path.abspath(__file__))


# ==========================================================================
# where things live
# ==========================================================================

def state_dir():
    return os.environ.get("MINIOS_STATE_DIR", "/var/lib/minios")


def run_dir():
    return os.environ.get("MINIOS_RUN_DIR", "/run/minios")


def config_dir():
    return os.environ.get("MINIOS_CONFIG_DIR", "/etc/minios")


def workspace_root():
    """Where the file tools are allowed to touch.  Not a chroot: a boundary."""
    return os.path.realpath(os.environ.get("MINIOS_WORKSPACE", "/root/work"))


def sessions_dir():
    return os.path.join(state_dir(), "sessions")


def audit_path():
    return os.path.join(state_dir(), "audit", "audit.jsonl")


def usage_path():
    return os.path.join(state_dir(), "usage", "usage.jsonl")


def secrets_dir():
    return os.path.join(state_dir(), "secrets")


def providers_dir():
    return os.path.join(config_dir(), "providers.d")


# ==========================================================================
# io
# ==========================================================================

def read_text(path, default=None):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_text_atomic(path, text, mode=0o644):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_json_atomic(path, payload, mode=0o644):
    write_text_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n", mode)


def append_jsonl(path, record):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ==========================================================================
# output
# ==========================================================================

def use_color():
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def paint(text, kind):
    if not use_color():
        return text
    codes = {PASS: "32", FAIL: "31", UNKNOWN: "33", SKIP: "2"}
    code = codes.get(kind)
    return "\x1b[%sm%s\x1b[0m" % (code, text) if code else text


def emit(payload, as_json, lines):
    """One place decides between the two output modes, so no command forgets."""
    if as_json:
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return EXIT_OK
    for line in lines:
        print(line)
    return EXIT_OK


def fail(message, code=EXIT_FAILED):
    print("minios: %s" % message, file=sys.stderr)
    return code


def human_bytes(value):
    if value is None:
        return "unknown"
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < step or unit == "TiB":
            return "%d B" % value if unit == "B" else "%.1f %s" % (value, unit)
        value /= step


def human_kb(kb):
    return human_bytes(None if kb is None else kb * 1024)


# ==========================================================================
# configuration
# ==========================================================================

def load_yaml(path):
    """Returns (document, problem).  A missing PyYAML is itself a finding."""
    try:
        import yaml
    except ImportError:
        return None, "PyYAML is not installed in this image"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh), None
    except OSError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, "cannot parse %s: %s" % (path, exc)


def dump_yaml(payload):
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def load_providers():
    """Base file, then every overlay in providers.d/ in name order.

    The shipped providers.yaml is heavily commented -- it is documentation as
    much as configuration -- so it is never rewritten.  User changes go into
    providers.d/, which is merged on top.  That keeps `prov add` from quietly
    deleting the explanation of what it just added, which is what a
    round-trip through a YAML writer would do.
    """
    base, problem = load_yaml(os.path.join(config_dir(), "providers.yaml"))
    if base is None and problem:
        base = {}
    merged = dict(base or {})
    providers = list(merged.get("providers") or [])

    overlay_dir = providers_dir()
    origin = {}
    if os.path.isdir(overlay_dir):
        for name in sorted(os.listdir(overlay_dir)):
            if not name.endswith((".yaml", ".yml")):
                continue
            doc, _ = load_yaml(os.path.join(overlay_dir, name))
            if not isinstance(doc, dict):
                continue
            gw = doc.get("gateway")
            if isinstance(gw, dict):
                merged.setdefault("gateway", {})
                merged["gateway"].update(gw)
            routing = doc.get("routing")
            if isinstance(routing, dict):
                merged.setdefault("routing", {})
                merged["routing"].update(routing)
            for entry in doc.get("providers") or []:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                existing = [i for i, p in enumerate(providers) if p.get("name") == entry["name"]]
                if existing:
                    providers[existing[0]] = entry
                else:
                    providers.append(entry)
                origin[entry["name"]] = os.path.join(overlay_dir, name)

    merged["providers"] = providers
    return merged, problem, origin


def load_egress():
    return load_yaml(os.path.join(config_dir(), "egress.yaml"))


# ==========================================================================
# the egress policy: may this path be sent to a provider?
# ==========================================================================

def _glob_to_regex(pattern):
    """`**` crosses directory separators, `*` does not.

    fnmatch gets this wrong in the direction that matters: its `*` happily
    matches `/`, so `**/.ssh/*` would also match `**/.ssh/keys/id_rsa` and a
    pattern meant to cover one level would silently cover all of them.  Here
    `*` stops at a separator, which is what every user of this file expects.
    """
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if pattern[i:i + 1] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def norm(path):
    """One separator, always `/`.

    The rules in egress.yaml are written with `/`, and `os.path.realpath` hands
    back `\\` on Windows.  Comparing the two directly makes every rule miss --
    which is the quiet kind of failure: nothing errors, the deny list just
    stops denying.  Paths are normalised at the point of comparison, not on the
    way to disk.
    """
    return path.replace(os.sep, "/") if os.sep != "/" else path


class Policy:
    """Answers two questions: may this go out, and what must be scrubbed.

    Both are needed before any text leaves the machine, and both are used by
    three callers (`policy check`, the file tools, and the support bundle), so
    they live together here rather than in whichever caller asked first.
    """

    def __init__(self, document, problem=None):
        self.document = document if isinstance(document, dict) else {}
        self.problem = problem
        self.mode = self.document.get("mode") or "denylist"
        self.deny = [self._compile(p) for p in self.document.get("deny_paths") or []]
        self.allow = [self._compile(p) for p in self.document.get("allow_paths") or []]
        self.hosts = [str(h).lower() for h in self.document.get("egress_hosts") or []]
        self.redactions = []
        for rule in self.document.get("redact") or []:
            if not isinstance(rule, dict) or not rule.get("pattern"):
                continue
            try:
                self.redactions.append((rule.get("name") or "rule",
                                        re.compile(rule["pattern"]),
                                        rule.get("replace", "[redacted]")))
            except re.error as exc:
                self.redactions.append((rule.get("name") or "rule", None, str(exc)))

    @staticmethod
    def _compile(pattern):
        text = str(pattern)
        if "/" not in text:
            text = "**/" + text
        return (str(pattern), re.compile("^(?:" + _glob_to_regex(text) + ")$"))

    @classmethod
    def load(cls):
        document, problem = load_egress()
        return cls(document, problem)

    def decide(self, path):
        """Returns a dict: allowed, rule, reason.  Never raises."""
        if self.problem and not self.document:
            return {"path": path, "allowed": False, "resolved": None,
                    "rule": None, "reason": "no egress policy could be read: %s" % self.problem}

        try:
            resolved = os.path.realpath(os.path.expanduser(path))
        except (OSError, ValueError) as exc:
            return {"path": path, "allowed": False, "resolved": None,
                    "rule": None, "reason": "cannot resolve path: %s" % exc}

        if self.mode == "off":
            return {"path": path, "resolved": resolved, "allowed": False, "rule": "mode=off",
                    "reason": "egress is disabled by policy"}

        subject = norm(resolved)
        for raw, rx in self.deny:
            if rx.match(subject):
                return {"path": path, "resolved": resolved, "allowed": False, "rule": raw,
                        "reason": "matches deny rule `%s`" % raw}

        if self.mode == "allowlist":
            for raw, rx in self.allow:
                if rx.match(subject):
                    return {"path": path, "resolved": resolved, "allowed": True, "rule": raw,
                            "reason": "matches allow rule `%s`" % raw}
            return {"path": path, "resolved": resolved, "allowed": False, "rule": "allowlist",
                    "reason": "allowlist mode and no allow rule matches"}

        return {"path": path, "resolved": resolved, "allowed": True, "rule": None,
                "reason": "no deny rule matches"}

    def redact(self, text):
        """Returns (scrubbed text, list of rule names that fired)."""
        fired = []
        for name, rx, replacement in self.redactions:
            if rx is None:
                continue
            text, count = rx.subn(replacement, text)
            if count:
                fired.append("%s x%d" % (name, count))
        return text, fired

    def host_allowed(self, host):
        if not host:
            return False
        host = host.lower()
        for entry in self.hosts:
            if entry == host or entry == "*":
                return True
            if entry.startswith("*.") and host.endswith(entry[1:]):
                return True
        return False


# ==========================================================================
# the sandbox boundary for the file tools
# ==========================================================================

def resolve_in_workspace(path, policy=None):
    """Returns (ok, resolved_or_None, error_code_or_None, detail).

    Order matters.  The workspace check comes first because it is the stronger
    statement: a path outside the workspace is not offered to the policy at
    all, so no configuration mistake can widen the boundary.
    """
    if not path:
        return False, None, "E_BAD_ARGUMENTS", "no path given"
    try:
        resolved = os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError) as exc:
        return False, None, "E_BAD_ARGUMENTS", "cannot resolve path: %s" % exc

    root = workspace_root()
    # Compared with one separator: on a build where os.sep is `\`, comparing
    # `C:\work` against `C:\work/` marks every path as outside the workspace.
    # That fails closed rather than open, but it also makes the tools useless,
    # and it hides the real question the check is meant to answer.
    if norm(resolved) != norm(root) and not norm(resolved).startswith(norm(root).rstrip("/") + "/"):
        return (False, resolved, "E_PATH_OUTSIDE_WORKSPACE",
                "%s is outside the workspace %s" % (resolved, root))

    if policy is not None:
        verdict = policy.decide(resolved)
        if not verdict["allowed"]:
            return False, resolved, "E_PATH_DENIED_BY_POLICY", verdict["reason"]

    return True, resolved, None, None


# ==========================================================================
# tools
# ==========================================================================

FILE_READ_LIMIT = int(os.environ.get("MINIOS_MAX_READ_BYTES", 262144))
FILE_WRITE_LIMIT = int(os.environ.get("MINIOS_MAX_WRITE_BYTES", 1048576))
LIST_LIMIT = int(os.environ.get("MINIOS_MAX_LIST_ENTRIES", 500))

TOOLS = [
    {"name": "fs.list", "side_effect": "read", "requires_confirmation": False,
     "description": "List a directory inside the workspace.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
     "limits": {"max_entries": LIST_LIMIT}},
    {"name": "fs.read", "side_effect": "read", "requires_confirmation": False,
     "description": "Read a file inside the workspace, up to the size limit.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
     "limits": {"max_bytes": FILE_READ_LIMIT}},
    {"name": "fs.write", "side_effect": "write", "requires_confirmation": True,
     "description": "Write a file inside the workspace.  Replaces it if it exists.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
     "limits": {"max_bytes": FILE_WRITE_LIMIT}},
    {"name": "sh.exec", "side_effect": "exec", "requires_confirmation": True,
     "description": "Run a shell command.  Requires the sandbox, which is absent on this build.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                    "required": ["command"]},
     "limits": {"timeout_s": 30}},
    {"name": "http.request", "side_effect": "network", "requires_confirmation": False,
     "description": "Fetch a URL, if the host is on the egress allowlist.",
     "parameters": {"type": "object",
                    "properties": {"url": {"type": "string"}, "method": {"type": "string"}},
                    "required": ["url"]},
     "limits": {"timeout_s": 30, "max_bytes": FILE_READ_LIMIT}},
]


def tool_descriptor(name):
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


def tool_error(code, message, retryable=False, hint=None):
    error = {"code": code, "message": message, "retryable": retryable}
    if hint:
        error["hint"] = hint
    return {"ok": False, "error": error}


def run_tool(name, arguments, confirmed=False, policy=None, session=None):
    """Run one tool.  Returns a result dict; never raises for bad input."""
    tool = tool_descriptor(name)
    if tool is None:
        return tool_error("E_UNKNOWN_TOOL", "no tool named %r" % name, retryable=False,
                          hint="run `minios tools` for the list")

    if not isinstance(arguments, dict):
        return tool_error("E_BAD_ARGUMENTS", "arguments must be an object")

    if tool["requires_confirmation"] and not confirmed:
        return tool_error("E_CONFIRMATION_REQUIRED",
                          "%s changes things outside this conversation" % name,
                          retryable=True,
                          hint="ask the user, then re-run with confirmation")

    if policy is None:
        policy = Policy.load()

    started = time.time()
    if name == "fs.list":
        result = _tool_fs_list(arguments, policy)
    elif name == "fs.read":
        result = _tool_fs_read(arguments, policy)
    elif name == "fs.write":
        result = _tool_fs_write(arguments, policy)
    elif name == "sh.exec":
        result = tool_error("E_SANDBOX_UNAVAILABLE",
                            "this build has no sandbox, so shell execution is refused",
                            retryable=False,
                            hint="bubblewrap and seccomp are not installed in this image")
    elif name == "http.request":
        result = _tool_http(arguments, policy)
    else:
        result = tool_error("E_UNKNOWN_TOOL", "no implementation for %r" % name)

    result["tool"] = name
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    audit("tool", tool=name, ok=result["ok"],
          code=(result.get("error") or {}).get("code"),
          args=arguments, session=session, elapsed_ms=result["elapsed_ms"])
    return result


def _tool_fs_list(arguments, policy):
    ok, resolved, code, detail = resolve_in_workspace(arguments.get("path"), policy)
    if not ok:
        return tool_error(code, detail, retryable=True,
                          hint="paths must stay inside %s" % workspace_root())
    if not os.path.isdir(resolved):
        return tool_error("E_NOT_FOUND", "%s is not a directory" % resolved, retryable=True)
    entries = []
    try:
        for name in sorted(os.listdir(resolved))[:LIST_LIMIT]:
            full = os.path.join(resolved, name)
            entry = {"name": name, "kind": "dir" if os.path.isdir(full) else "file"}
            try:
                entry["size_bytes"] = os.path.getsize(full)
            except OSError:
                entry["size_bytes"] = None
            entries.append(entry)
    except OSError as exc:
        return tool_error("E_IO", "cannot list %s: %s" % (resolved, exc), retryable=True)
    return {"ok": True, "result": {"path": resolved, "entries": entries,
                                   "truncated": len(entries) >= LIST_LIMIT}}


def _tool_fs_read(arguments, policy):
    ok, resolved, code, detail = resolve_in_workspace(arguments.get("path"), policy)
    if not ok:
        return tool_error(code, detail, retryable=True,
                          hint="paths must stay inside %s" % workspace_root())
    if not os.path.isfile(resolved):
        return tool_error("E_NOT_FOUND", "%s is not a file" % resolved, retryable=True)
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        return tool_error("E_IO", str(exc), retryable=True)
    if size > FILE_READ_LIMIT:
        return tool_error("E_TOO_LARGE",
                          "%s is %d bytes, over the %d byte read limit" % (resolved, size, FILE_READ_LIMIT),
                          retryable=False,
                          hint="read part of it, or raise MINIOS_MAX_READ_BYTES deliberately")
    try:
        with open(resolved, "r", errors="replace") as fh:
            content = fh.read()
    except OSError as exc:
        return tool_error("E_IO", str(exc), retryable=True)
    scrubbed, fired = policy.redact(content)
    return {"ok": True, "result": {"path": resolved, "bytes": size, "content": scrubbed,
                                   "redacted": fired}}


def _tool_fs_write(arguments, policy):
    ok, resolved, code, detail = resolve_in_workspace(arguments.get("path"), policy)
    if not ok:
        return tool_error(code, detail, retryable=True,
                          hint="paths must stay inside %s" % workspace_root())
    content = arguments.get("content")
    if not isinstance(content, str):
        return tool_error("E_BAD_ARGUMENTS", "content must be a string")
    if len(content.encode("utf-8")) > FILE_WRITE_LIMIT:
        return tool_error("E_TOO_LARGE", "content exceeds the %d byte write limit" % FILE_WRITE_LIMIT)
    if not os.path.isdir(os.path.dirname(resolved)):
        return tool_error("E_NOT_FOUND", "directory %s does not exist" % os.path.dirname(resolved),
                          retryable=True)
    existed = os.path.exists(resolved)
    try:
        write_text_atomic(resolved, content)
    except OSError as exc:
        return tool_error("E_IO", str(exc), retryable=True)
    return {"ok": True, "result": {"path": resolved, "bytes": len(content.encode("utf-8")),
                                   "replaced": existed}}


def _tool_http(arguments, policy):
    url = arguments.get("url")
    if not url:
        return tool_error("E_BAD_ARGUMENTS", "url is required")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return tool_error("E_BAD_ARGUMENTS", "only http and https are allowed")
    if not policy.host_allowed(parsed.hostname or ""):
        return tool_error("E_HOST_NOT_ALLOWED",
                          "%s is not on the egress allowlist" % (parsed.hostname or "?"),
                          retryable=False,
                          hint="add it to egress_hosts in egress.yaml if it should be reachable")
    try:
        req = urllib.request.Request(url, method=(arguments.get("method") or "GET").upper())
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(FILE_READ_LIMIT + 1)
            truncated = len(body) > FILE_READ_LIMIT
            text = body[:FILE_READ_LIMIT].decode("utf-8", "replace")
            scrubbed, fired = policy.redact(text)
            return {"ok": True, "result": {"status": response.status, "content": scrubbed,
                                           "truncated": truncated, "redacted": fired,
                                           "host": parsed.hostname}}
    except urllib.error.HTTPError as exc:
        return tool_error("E_HTTP_STATUS", "HTTP %s from %s" % (exc.code, parsed.hostname),
                          retryable=exc.code >= 500)
    except Exception as exc:
        return tool_error("E_NETWORK", str(exc), retryable=True)


# ==========================================================================
# audit: what the agent did, without keeping what it read
# ==========================================================================

def audit(kind, **fields):
    """Append one record.  Arguments are hashed, never stored.

    The audit trail has to answer "what did it touch, and was it allowed to"
    without becoming a second copy of the user's files.  So the arguments are
    recorded as a digest plus their size, and any content stays out.
    """
    record = {"t": now_iso(), "kind": kind}
    arguments = fields.pop("args", None)
    if arguments is not None:
        try:
            blob = json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            blob = repr(arguments).encode("utf-8")
        record["args_sha256"] = hashlib.sha256(blob).hexdigest()[:32]
        record["args_bytes"] = len(blob)
        if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
            record["path"] = arguments["path"]
    record.update({k: v for k, v in fields.items() if v is not None})
    try:
        append_jsonl(audit_path(), record)
    except OSError:
        pass
    return record


def audit_records(since=None, until=None):
    out = []
    for record in read_jsonl(audit_path()):
        stamp = record.get("t") or ""
        if since and stamp < since:
            continue
        if until and stamp > until:
            continue
        out.append(record)
    return out


# ==========================================================================
# sessions
# ==========================================================================

def session_id():
    return "%s-%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
                      os.urandom(2).hex())


def session_path(identifier):
    safe = re.sub(r"[^A-Za-z0-9._-]", "", identifier or "")
    return os.path.join(sessions_dir(), safe + ".jsonl")


def session_append(identifier, role, text, **extra):
    record = {"t": now_iso(), "role": role, "text": text}
    record.update({k: v for k, v in extra.items() if v is not None})
    append_jsonl(session_path(identifier), record)
    return record


def session_events(identifier):
    return read_jsonl(session_path(identifier))


def session_summaries():
    out = []
    directory = sessions_dir()
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".jsonl"):
            continue
        events = read_jsonl(os.path.join(directory, name))
        if not events:
            continue
        model = next((e.get("model") for e in events if e.get("model")), None)
        first = next((e.get("text") for e in events if e.get("role") == "user"), "")
        out.append({
            "id": name[:-6],
            "started": events[0].get("t"),
            "updated": events[-1].get("t"),
            "events": len(events),
            "turns": sum(1 for e in events if e.get("role") == "user"),
            "model": model,
            "first_question": (first or "")[:60],
        })
    out.sort(key=lambda s: s.get("updated") or "", reverse=True)
    return out


def session_remove(identifier):
    path = session_path(identifier)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def sessions_message(identifier, limit=20):
    """Turn stored events into the message list the gateway expects."""
    messages = []
    for event in session_events(identifier):
        role = event.get("role")
        if role in ("user", "assistant") and event.get("text"):
            messages.append({"role": role, "content": event["text"]})
    return messages[-limit:]


# ==========================================================================
# the key store
# ==========================================================================

def is_mountpoint(path):
    if not os.path.isdir(path):
        return False
    try:
        parent = os.path.dirname(path.rstrip("/")) or "/"
        return os.stat(path).st_dev != os.stat(parent).st_dev
    except OSError:
        return False


def keystore_backend():
    """Returns (backend, detail).

    There is deliberately no "just put it in a file" backend.  A key store that
    silently falls back to plaintext is worse than one that is missing, because
    the user cannot tell which one they got.  The plaintext backend exists only
    to make the development machine testable, and it must be asked for by name.
    """
    forced = os.environ.get("MINIOS_KEY_BACKEND")
    if forced:
        return forced, "forced by MINIOS_KEY_BACKEND"
    if is_mountpoint(secrets_dir()):
        return "luks", "%s is a mounted encrypted volume" % secrets_dir()
    if os.environ.get("MINIOS_ALLOW_PLAINTEXT_KEYS") == "1":
        return "plaintext-dev", "MINIOS_ALLOW_PLAINTEXT_KEYS=1 -- development only"
    return None, "no encrypted volume is mounted at %s" % secrets_dir()


def keystore_available():
    backend, _ = keystore_backend()
    return backend not in (None, "plaintext-dev")


class KeyStore:
    def __init__(self):
        self.backend, self.detail = keystore_backend()

    @property
    def usable(self):
        return self.backend is not None

    def _path(self, name):
        safe = re.sub(r"[^A-Za-z0-9._-]", "", name or "")
        return os.path.join(secrets_dir(), safe)

    def names(self):
        if not os.path.isdir(secrets_dir()):
            return []
        return sorted(n for n in os.listdir(secrets_dir())
                      if not n.endswith(".tmp") and os.path.isfile(os.path.join(secrets_dir(), n)))

    def get(self, name):
        if not self.usable:
            return None
        return read_text(self._path(name))

    def set(self, name, value):
        if not self.usable:
            return False
        write_text_atomic(self._path(name), value, mode=0o600)
        audit("key.store", key_name=name, backend=self.backend)
        return True

    def remove(self, name):
        path = self._path(name)
        if not os.path.exists(path):
            return False
        os.remove(path)
        audit("key.remove", key_name=name)
        return True

    def hint(self):
        """The last four characters, so the user can tell two keys apart."""
        for name in self.names():
            value = self.get(name) or ""
            return "%s...%s" % (name, value[-4:]) if len(value) > 4 else name
        return None


# ==========================================================================
# the gateway
# ==========================================================================

class GatewayError(Exception):
    def __init__(self, message, attempts=None):
        Exception.__init__(self, message)
        self.attempts = attempts or []


def provider_entries(config):
    entries = [p for p in (config or {}).get("providers") or [] if isinstance(p, dict)]
    entries.sort(key=lambda p: (p.get("priority", 100), p.get("name") or ""))
    return entries


def provider_by_name(config, name):
    for entry in provider_entries(config):
        if entry.get("name") == name:
            return entry
    return None


def cooldown_path():
    return os.path.join(run_dir(), "provider-cooldown.json")


def provider_cooling(name):
    state = read_json(cooldown_path()) or {}
    until = state.get(name)
    return bool(until) and until > time.time()


def mark_provider_failed(name, seconds):
    state = read_json(cooldown_path()) or {}
    state[name] = time.time() + seconds
    try:
        write_json_atomic(cooldown_path(), state)
    except OSError:
        pass


class Gateway:
    """One OpenAI-compatible endpoint over several providers.

    The failover here is not a nicety.  This image has no local model, so when
    every provider is down the agent is down, and the user is owed a sentence
    that says which one failed and why rather than a spinner.
    """

    def __init__(self, config, keystore=None):
        self.config = config or {}
        self.settings = self.config.get("gateway") or {}
        self.keystore = keystore or KeyStore()

    def credentials(self, provider):
        ref = str(provider.get("key_ref") or "")
        if not ref.startswith("secret:"):
            return None
        return self.keystore.get(ref.split(":", 1)[1].strip())

    def base(self, provider):
        return str(provider.get("base_url") or "").rstrip("/")

    def _request(self, provider, path, payload=None, timeout=None, stream=False):
        url = self.base(provider) + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data,
                                         method="POST" if data is not None else "GET")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "text/event-stream" if stream else "application/json")
        key = self.credentials(provider)
        if key:
            request.add_header("Authorization", "Bearer " + key)
        timeout = timeout or float(self.settings.get("request_timeout_s", 60))
        return urllib.request.urlopen(request, timeout=timeout)

    def models(self, provider, timeout=None):
        with self._request(provider, "/v1/models", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def chat(self, messages, model=None, max_tokens=None, provider_name=None,
             stream=False, timeout=None):
        """Returns (text, meta).  Raises GatewayError with every attempt listed."""
        entries = provider_entries(self.config)
        if provider_name:
            chosen = provider_by_name(self.config, provider_name)
            if chosen is None:
                raise GatewayError("no provider named %r" % provider_name)
            entries = [chosen]
        if not entries:
            raise GatewayError("no providers are configured")

        attempts = []
        for provider in entries:
            name = provider.get("name") or "?"
            if provider_cooling(name) and not provider_name:
                attempts.append({"provider": name, "skipped": "cooling down"})
                continue
            target = model or provider.get("default_model") or (provider.get("models") or [None])[0]
            if not target:
                attempts.append({"provider": name, "error": "no model named for this provider"})
                continue
            payload = {"model": target, "messages": messages,
                       "max_tokens": max_tokens or provider.get("max_output_tokens") or 1024}
            if stream:
                payload["stream"] = True
            try:
                started = time.time()
                if stream:
                    text, usage = self._stream(provider, payload, timeout)
                else:
                    text, usage = self._once(provider, payload, timeout)
                elapsed = int((time.time() - started) * 1000)
                meta = {"provider": name, "model": target, "elapsed_ms": elapsed,
                        "usage": usage, "attempts": attempts}
                self._record_usage(meta)
                audit("model.call", provider=name, model=target, ok=True, elapsed_ms=elapsed,
                      prompt_tokens=(usage or {}).get("prompt_tokens"),
                      completion_tokens=(usage or {}).get("completion_tokens"))
                return text, meta
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                attempts.append({"provider": name, "status": exc.code, "error": body})
                if exc.code in (429, 500, 502, 503, 504):
                    mark_provider_failed(name, float(provider.get("cooldown_s", 60)))
                continue
            except Exception as exc:
                attempts.append({"provider": name, "error": str(exc)})
                mark_provider_failed(name, float(provider.get("cooldown_s", 60)))
                continue

        raise GatewayError("no provider could answer", attempts)

    def _once(self, provider, payload, timeout):
        with self._request(provider, "/v1/chat/completions", payload, timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        choices = body.get("choices") or [{}]
        message = choices[0].get("message") or {}
        return message.get("content") or "", body.get("usage")

    def _stream(self, provider, payload, timeout):
        """Reads the event stream, and can be told to give up.

        A stream that stalls has to end: without an idle timeout the UI shows a
        cursor forever, which is the one outcome the user cannot act on.
        """
        idle = float(self.settings.get("stream_idle_timeout_s", 30))
        chunks = []
        usage = None
        with self._request(provider, "/v1/chat/completions", payload, timeout, stream=True) as response:
            deadline = time.time() + idle
            for raw in response:
                if time.time() > deadline:
                    raise GatewayError("the stream went quiet for %ss" % idle)
                deadline = time.time() + idle
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("usage"):
                    usage = event["usage"]
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    chunks.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()
        return "".join(chunks), usage

    def _record_usage(self, meta):
        usage = meta.get("usage") or {}
        if not usage:
            return
        prices = read_json(os.path.join(config_dir(), "prices.json")) or {}
        price = prices.get(meta["model"]) or {}
        cost = None
        if price:
            cost = (usage.get("prompt_tokens", 0) * price.get("in", 0)
                    + usage.get("completion_tokens", 0) * price.get("out", 0)) / 1_000_000.0
        try:
            append_jsonl(usage_path(), {"t": now_iso(), "provider": meta["provider"],
                                        "model": meta["model"], "usage": usage,
                                        "cost_usd": cost})
        except OSError:
            pass


def estimate_cost(model, usage):
    prices = read_json(os.path.join(config_dir(), "prices.json")) or {}
    price = prices.get(model) or {}
    if not price or not usage:
        return None
    return (usage.get("prompt_tokens", 0) * price.get("in", 0)
            + usage.get("completion_tokens", 0) * price.get("out", 0)) / 1_000_000.0


# ==========================================================================
# network state
# ==========================================================================

def net_state_path():
    return os.path.join(run_dir(), "net.json")


def probe_ttl_seconds():
    return int(os.environ.get("MINIOS_PROBE_TTL_S", "300"))


def probe_age():
    """Seconds since the last probe, or None if there has never been one.

    A stale reachability result is worse than no result: "reachable" from forty
    minutes ago reads exactly like "reachable" from a moment ago, and the user
    acts on it.  So the age is returned rather than a boolean, and the caller is
    expected to say so out loud.
    """
    net = read_json(net_state_path())
    stamp = (net or {}).get("probed_at")
    if not stamp:
        return None
    try:
        parsed = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return max(0, int(time.time() - calendar.timegm(parsed)))


def human_age(seconds):
    if seconds is None:
        return "never"
    if seconds < 60:
        return "%ds ago" % seconds
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    if seconds < 86400:
        return "%dh ago" % (seconds // 3600)
    return "%dd ago" % (seconds // 86400)


def firstboot_report():
    return read_json(os.path.join(run_dir(), "firstboot.json"))


def admin_password_state():
    """`unset`, `set`, or None when there is no marker at all."""
    value = read_text(os.path.join(config_dir(), "admin"))
    if value is None:
        return None
    return "unset" if value.strip() == "unset" else "set"


SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1


def hash_password(password, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    """A scrypt hash, salt and parameters included in the string.

    scrypt comes from hashlib, so this is a standard construction rather than
    something invented here -- which is the only acceptable kind of password
    storage.  The parameters are stored with the hash so that raising them later
    does not invalidate existing passwords.
    """
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt$n=%d,r=%d,p=%d$%s$%s" % (
        n, r, p,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"))


def verify_password(password, encoded):
    if not encoded or not encoded.startswith("scrypt$"):
        return False
    try:
        _, params, salt_b64, digest_b64 = encoded.split("$")
        settings = dict(part.split("=") for part in params.split(","))
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                n=int(settings["n"]), r=int(settings["r"]),
                                p=int(settings["p"]), dklen=len(expected))
    except (ValueError, KeyError, TypeError):
        return False
    # hmac.compare_digest, not ==: comparing digests in variable time leaks how
    # much of a guess was right, which is exactly the information an attacker
    # wants to have.
    return hmac.compare_digest(actual, expected)


def network_facts():
    """What the kernel can tell us without sending a packet."""
    facts = {"default_route": None, "interfaces": [], "nameservers": [], "source": "kernel"}
    routes = read_text("/proc/net/route")
    if routes is None:
        facts["source"] = "unavailable"
    else:
        for line in routes.splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == "00000000":
                facts["default_route"] = fields[0]
                break
    for line in (read_text("/etc/resolv.conf") or "").splitlines():
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) > 1:
                facts["nameservers"].append(parts[1])
    base = "/sys/class/net"
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name == "lo":
                continue
            facts["interfaces"].append({
                "name": name,
                "operstate": read_text(os.path.join(base, name, "operstate")),
                "mac": read_text(os.path.join(base, name, "address")),
            })
    return facts


def probe_dns(timeout=5.0):
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo("example.com", 443)
        return True, None
    except OSError as exc:
        return False, str(exc)
    finally:
        socket.setdefaulttimeout(None)


def probe_host(host, port=443, timeout=5.0):
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, int((time.time() - started) * 1000), None
    except OSError as exc:
        return False, int((time.time() - started) * 1000), str(exc)


# ==========================================================================
# persistence and no-trace mode
# ==========================================================================

def incognito_path():
    return os.path.join(state_dir(), "incognito")


def incognito_on():
    return os.path.exists(incognito_path())


def persist_state_path():
    return os.path.join(run_dir(), "persist.json")


def read_profile():
    """The profile for this boot, or the newest one we can find.  Never raises."""
    mine = os.path.join(state_dir(), "machines", current_machine(), "profile.json")
    found = read_json(mine)
    if found is not None:
        return found, mine
    machines = os.path.join(state_dir(), "machines")
    newest, newest_time = None, -1.0
    if os.path.isdir(machines):
        for name in os.listdir(machines):
            candidate = os.path.join(machines, name, "profile.json")
            try:
                mtime = os.path.getmtime(candidate)
            except OSError:
                continue
            if mtime > newest_time:
                newest, newest_time = candidate, mtime
    if newest:
        return read_json(newest), newest
    return None, mine


def current_machine():
    return read_text("/etc/machine-id") or read_text("/var/lib/dbus/machine-id") or "unknown"


def hwchange():
    return read_json(os.path.join(run_dir(), "hwchange.json"))


def image_version():
    text = read_text("/usr/lib/minios/version")
    if text:
        return text
    buildinfo = read_json(os.path.join(HERE, "build.json"))
    if buildinfo:
        return "%s+%s" % (buildinfo.get("version", "?"), buildinfo.get("build_id", "?"))
    return "dev"


# ==========================================================================
# commands
# ==========================================================================

def cmd_version(args):
    payload = {"version": image_version(),
               "kernel": read_text("/proc/sys/kernel/osrelease"),
               "built": (read_json(os.path.join(HERE, "build.json")) or {}).get("built")}
    return emit(payload, args.json,
                ["minios %s" % payload["version"],
                 "kernel %s" % (payload["kernel"] or "unknown"),
                 "built  %s" % (payload["built"] or "unknown")])


def cmd_status(args):
    prof, prof_path = read_profile()
    change = hwchange()
    providers, problem, _ = load_providers()
    policy = Policy.load()
    net = read_json(net_state_path())
    facts = network_facts()
    entries = provider_entries(providers)

    sessions = session_summaries()
    age = probe_age()
    ttl = probe_ttl_seconds()
    stale = age is not None and age > ttl
    boot = firstboot_report()
    payload = {
        "version": image_version(),
        "machine": {"id": current_machine(), "profile": prof_path if prof else None,
                    "seen_before": (change or {}).get("seen_before")},
        "changes_since_last_boot": (change or {}).get("changes") or [],
        "incognito": incognito_on(),
        "hardware": None if prof is None else {
            "cpu": (prof.get("cpu") or {}).get("model"),
            "cores": (prof.get("cpu") or {}).get("cores"),
            "memory_kb": (prof.get("memory") or {}).get("total_kb"),
            "firmware": (prof.get("firmware") or {}).get("kind"),
        },
        "firstboot": None if boot is None else {
            "at": boot.get("at"), "counts": boot.get("counts"),
        },
        "admin_password": admin_password_state(),
        "network": None if net is None else {
            "online": net.get("online"), "dns": net.get("dns"),
            "probed_at": net.get("probed_at"),
            "age_seconds": age, "stale": stale, "ttl_seconds": ttl,
            "reachable": [p["name"] for p in net.get("providers") or [] if p.get("reachable")],
            "unreachable": [p["name"] for p in net.get("providers") or [] if not p.get("reachable")],
        },
        "kernel_network": facts,
        "providers": [{"name": p.get("name"), "priority": p.get("priority"),
                       "cooldown": provider_cooling(p.get("name") or "")}
                      for p in entries],
        "egress_policy": policy.mode if policy.document else None,
        "keystore": keystore_backend()[0],
        "sessions": len(sessions),
        "problems": [p for p in (problem, policy.problem) if p],
    }

    hw = payload["hardware"]
    lines = [
        "minios   : %s on %s%s" % (payload["version"], payload["machine"]["id"],
                                   "  [no-trace]" if payload["incognito"] else ""),
        "hardware : %s" % ("no profile yet" if hw is None else
                           "%s, %s cores, %s" % (hw["cpu"] or "unknown cpu", hw["cores"] or "?",
                                                 human_kb(hw["memory_kb"]))),
    ]

    # Two independent axes.  "There is a network" and "a model is reachable"
    # are different questions, and reporting one as the other is how a user
    # ends up debugging their router when their API key expired.
    if payload["network"] is None:
        lines.append("network  : not probed            (run `minios net probe`)")
        lines.append("models   : %d provider(s) configured, reachability not probed" % len(entries))
    else:
        when = human_age(age) + (", STALE" if stale else "")
        lines.append("network  : %s, dns %s        (probed %s)"
                     % ("online" if payload["network"]["online"] else "offline",
                        "ok" if payload["network"]["dns"] else "failing", when))
        reachable = payload["network"]["reachable"]
        # A stale result is labelled rather than dropped: it is still the best
        # information available, it just must not be mistaken for a current one.
        suffix = " (from a stale probe)" if stale else ""
        if reachable:
            lines.append("models   : reachable: %s%s" % (", ".join(reachable), suffix))
        elif entries:
            lines.append("models   : none of the %d provider(s) answered%s" % (len(entries), suffix))
        else:
            lines.append("models   : no provider is configured")
        if stale:
            lines.append("           run `minios net probe` to refresh")

    lines.append("sessions : %d stored" % payload["sessions"])
    if payload["admin_password"] == "unset":
        lines.append("first run: no administrator password has been set yet")
    if boot is None and payload["admin_password"] is None:
        lines.append("first run: the writable layer has not been seeded "
                     "(firstboot has not run)")
    if payload["changes_since_last_boot"]:
        lines.append("since last: %d change(s)" % len(payload["changes_since_last_boot"]))
        for item in payload["changes_since_last_boot"][:5]:
            lines.append("            " + item)
    if payload["problems"]:
        lines.append("problems : " + "; ".join(payload["problems"]))
    return emit(payload, args.json, lines)


def cmd_hw(args):
    sys.path.insert(0, HERE)
    try:
        import hwprofil
    except ImportError as exc:
        return fail("cannot load hwprofil.py placed next to this command: %s" % exc)

    if args.cmd_path == "hw.diff":
        against = getattr(args, "against", None) or default_comparison()
        if not against:
            return fail("nothing to compare against yet: no stored profile", EXIT_PRECONDITION)
        if not os.path.exists(against):
            return fail("no such profile: %s" % against, EXIT_PRECONDITION)
        changes = hwprofil.load_and_compare(hwprofil.build(), against)
        return emit({"compared_against": against, "changes": changes}, args.json,
                    changes or ["no change since %s" % against])

    prof, path = read_profile()
    if prof is None:
        prof = hwprofil.build()
        path = "(generated now, not stored)"

    # getattr, not args.raw: `minios hw` reaches this handler through the
    # group's default verb, so the verb's own options were never declared.
    if getattr(args, "raw", False):
        if args.json:
            json.dump(prof, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print(json.dumps(prof, indent=2, ensure_ascii=False))
        return EXIT_OK

    cpu = prof.get("cpu") or {}
    mem = prof.get("memory") or {}
    fw = prof.get("firmware") or {}
    plat = prof.get("platform") or {}
    pci = prof.get("pci") or {}
    lines = [
        "source    : %s" % path,
        "generated : %s" % prof.get("generated_at", "unknown"),
        "machine   : %s%s" % (prof.get("machine_id", "?"),
                              "" if plat.get("virtualized") else "  (bare metal)"),
        "cpu       : %s, %s cores / %s threads%s"
        % (cpu.get("model") or "unknown", cpu.get("cores") or "?", cpu.get("threads") or "?",
           (", " + " ".join(cpu.get("features") or [])) if cpu.get("features") else ""),
        "memory    : %s total, %s available" % (human_kb(mem.get("total_kb")),
                                                human_kb(mem.get("available_kb"))),
        "firmware  : %s, secure boot %s"
        % (fw.get("kind", "?"),
           "on" if fw.get("secure_boot") is True else "off" if fw.get("secure_boot") is False else "unknown"),
        "platform  : %s%s" % (plat.get("model") or "unknown",
                              (" / " + plat["hypervisor"]) if plat.get("hypervisor") else ""),
        "disks     : %s" % (", ".join("%s %s%s" % (d.get("name"), human_bytes(d.get("size_bytes")),
                                                   " [removable]" if d.get("removable") else "")
                                      for d in prof.get("disks") or []) or "none found"),
        "network   : %s" % (", ".join("%s (%s%s)" % (n.get("name"), n.get("kind"),
                                                     "" if n.get("driver") else ", NO DRIVER")
                                      for n in prof.get("net") or []) or "none found"),
        "pci       : %s devices, %s without a driver"
        % (pci.get("devices_total", 0), len(pci.get("undriven") or [])),
    ]
    for dev in pci.get("undriven") or []:
        lines.append("            %s %s %s -- no driver" % (dev.get("slot"), dev.get("vendor_id"),
                                                            dev.get("class")))
    return emit(prof, args.json, lines)


def default_comparison():
    machines = os.path.join(state_dir(), "machines")
    previously = os.path.join(machines, current_machine(), "profile.json.prev")
    if os.path.exists(previously):
        return previously
    last = read_text(os.path.join(machines, "last"))
    if last:
        candidate = os.path.join(machines, last, "profile.json")
        if os.path.exists(candidate):
            return candidate
    return None


def cmd_net_status(args):
    facts = network_facts()
    net = read_json(net_state_path())
    payload = {"kernel": facts, "last_probe": net}
    lines = [
        "default route : %s" % (facts["default_route"] or "none"),
        "interfaces    : %s" % (", ".join("%s (%s)" % (i["name"], i.get("operstate") or "?")
                                          for i in facts["interfaces"]) or "none"),
        "nameservers   : %s" % (", ".join(facts["nameservers"]) or "none"),
        "source        : %s" % facts["source"],
    ]
    if net is None:
        lines.append("last probe    : never run            (run `minios net probe`)")
    else:
        lines.append("last probe    : %s, dns %s" % (net.get("probed_at"),
                                                     "ok" if net.get("dns") else "failing"))
        for entry in net.get("providers") or []:
            lines.append("                %-10s %s" % (entry["name"],
                                                       "reachable" if entry.get("reachable") else
                                                       "unreachable: %s" % (entry.get("error") or "?")))
    return emit(payload, args.json, lines)


def cmd_net_probe(args):
    timeout = getattr(args, "timeout", 5.0)
    providers, _, _ = load_providers()
    dns_ok, dns_error = probe_dns(timeout)

    results = []
    for entry in provider_entries(providers):
        parsed = urllib.parse.urlparse(str(entry.get("base_url") or ""))
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            results.append({"name": entry.get("name"), "host": None, "reachable": False,
                            "error": "no base_url", "ms": None})
            continue
        if not dns_ok and not host.replace(".", "").isdigit():
            results.append({"name": entry.get("name"), "host": host, "reachable": False,
                            "error": "dns is failing", "ms": None})
            continue
        ok, ms, error = probe_host(host, port, timeout)
        results.append({"name": entry.get("name"), "host": host, "port": port, "reachable": ok,
                        "error": error, "ms": ms})

    reachable = [r for r in results if r["reachable"]]
    payload = {"probed_at": now_iso(), "online": dns_ok and bool(reachable),
               "dns": dns_ok, "dns_error": dns_error, "providers": results,
               "note": ("`online` means the network works AND at least one provider answered. "
                        "They are reported separately because they fail for different reasons.")}
    write_json_atomic(net_state_path(), payload)

    lines = ["dns        : %s" % ("ok" if dns_ok else "failed: %s" % dns_error)]
    for entry in results:
        lines.append("%-10s : %s" % (entry["name"],
                                     "reachable in %sms" % entry["ms"] if entry["reachable"]
                                     else "unreachable -- %s" % (entry.get("error") or "?")))
    lines.append("")
    lines.append(payload["note"])
    return emit(payload, args.json, lines)


def cmd_prov_list(args):
    providers, problem, origin = load_providers()
    store = KeyStore()
    entries = provider_entries(providers)
    rows = []
    for entry in entries:
        name = entry.get("name")
        ref = str(entry.get("key_ref") or "")
        key_name = ref.split(":", 1)[1].strip() if ref.startswith("secret:") else None
        rows.append({
            "name": name,
            "base_url": entry.get("base_url"),
            "models": entry.get("models") or [],
            "priority": entry.get("priority"),
            "key_name": key_name,
            "has_key": bool(key_name and store.get(key_name)),
            "cooling": provider_cooling(name or ""),
            "defined_in": origin.get(name, os.path.join(config_dir(), "providers.yaml")),
        })
    default_model = (providers.get("gateway") or {}).get("default_model")
    payload = {"providers": rows, "default_model": default_model, "keystore": store.backend,
               "problem": problem}
    lines = ["%-12s %-8s %-9s %-7s %s" % ("name", "priority", "key", "cooling", "base_url")]
    for row in rows:
        lines.append("%-12s %-8s %-9s %-7s %s"
                     % (row["name"], row["priority"], "yes" if row["has_key"] else "no",
                        "yes" if row["cooling"] else "", row["base_url"]))
    if not rows:
        lines.append("(no providers configured)")
    lines.append("")
    lines.append("default model : %s" % (default_model or "not set"))
    lines.append("key store     : %s" % (store.backend or "unavailable -- %s" % store.detail))
    if problem:
        lines.append("problem       : %s" % problem)
    return emit(payload, args.json, lines)


def cmd_prov_add(args):
    name = args.name
    if not re.match(r"^[A-Za-z0-9._-]+$", name or ""):
        return fail("provider names use letters, digits, dot, dash and underscore", EXIT_USAGE)
    if not args.base_url:
        return fail("--base-url is required", EXIT_USAGE)
    providers, _, origin = load_providers()
    if name in origin:
        return fail("%s is already defined in %s" % (name, origin[name]), EXIT_FAILED)

    entry = {"name": name, "type": "openai_compatible", "base_url": args.base_url,
             "key_ref": "secret:%s" % name,
             "models": list(getattr(args, "model", None) or []),
             "priority": 50, "cooldown_s": 60}
    document = {"providers": [entry]}
    text = dump_yaml(document)
    if text is None:
        return fail("PyYAML is not installed, so the overlay cannot be written", EXIT_PRECONDITION)
    path = os.path.join(providers_dir(), "10-%s.yaml" % name)
    write_text_atomic(path, "# Added by `minios prov add`.\n"
                            "# The shipped providers.yaml keeps its comments; changes live here.\n"
                            "# Review `key_ref` and `priority` before relying on this.\n" + text)
    audit("prov.add", provider=name, base_url=args.base_url)
    return emit({"path": path, "provider": entry}, args.json,
                ["wrote %s" % path,
                 "next: `minios key set %s` then `minios prov test %s`" % (name, name)])


def cmd_prov_rm(args):
    providers, _, origin = load_providers()
    name = args.name
    if name not in origin:
        if provider_by_name(providers, name):
            return fail("%s is defined in providers.yaml; edit that file instead" % name, EXIT_FAILED)
        return fail("no provider named %s" % name, EXIT_FAILED)
    os.remove(origin[name])
    audit("prov.remove", provider=name)
    return emit({"removed": name, "path": origin[name]}, args.json,
                ["removed %s (deleted %s)" % (name, origin[name])])


def cmd_prov_use(args):
    name = getattr(args, "name", None)
    model = getattr(args, "model", None)
    if not name and not model:
        return fail("give a provider name, or --model, or both", EXIT_USAGE)
    providers, _, _ = load_providers()
    if name and provider_by_name(providers, name) is None:
        return fail("no provider named %s" % name, EXIT_FAILED)

    document = {}
    if model:
        document["gateway"] = {"default_model": model}
    if name:
        document["routing"] = {"preferred": name}
    text = dump_yaml(document)
    if text is None:
        return fail("PyYAML is not installed, so the overlay cannot be written", EXIT_PRECONDITION)
    path = os.path.join(providers_dir(), "00-defaults.yaml")
    write_text_atomic(path, "# Written by `minios prov use`.\n" + text)
    return emit({"path": path, "provider": name, "model": model}, args.json,
                ["wrote %s" % path] + ([ "default model is now %s" % model] if model else []))


def cmd_prov_test(args):
    config, problem, _ = load_providers()
    if problem and not config.get("providers"):
        return fail("cannot read the provider configuration: %s" % problem, EXIT_PRECONDITION)
    name = getattr(args, "name", None)
    gateway = Gateway(config)
    entry = provider_by_name(config, name) if name else (
        provider_entries(config)[0] if provider_entries(config) else None)
    if entry is None:
        return fail("no provider to test", EXIT_PRECONDITION)
    started = time.time()
    try:
        listing = gateway.models(entry, timeout=10)
        elapsed = int((time.time() - started) * 1000)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return fail("%s answered HTTP %s: %s" % (entry.get("name"), exc.code, detail), EXIT_FAILED)
    except Exception as exc:
        return fail("%s could not be reached: %s" % (entry.get("name"), exc), EXIT_FAILED)

    models = [m.get("id") for m in (listing.get("data") or []) if isinstance(m, dict)]
    audit("prov.test", provider=entry.get("name"), ok=True, elapsed_ms=elapsed)
    return emit({"provider": entry.get("name"), "elapsed_ms": elapsed, "models": models,
                 "configured_models": entry.get("models") or []}, args.json,
                ["%s answered in %dms, %d model(s) offered" % (entry.get("name"), elapsed, len(models)),
                 "configured: %s" % (", ".join(entry.get("models") or []) or "none listed")])


def cmd_key_set(args):
    store = KeyStore()
    if not store.usable:
        return fail("no encrypted store is available: %s" % store.detail, EXIT_PRECONDITION)
    provider = args.provider
    if store.backend == "plaintext-dev":
        print("minios: WARNING: the key will be stored in plaintext (development backend)",
              file=sys.stderr)
    try:
        value = sys.stdin.readline().strip()
    except KeyboardInterrupt:
        return fail("cancelled", EXIT_FAILED)
    if not value:
        return fail("no key was read on stdin", EXIT_USAGE)
    store.set(provider, value)
    return emit({"provider": provider, "backend": store.backend, "stored": True}, args.json,
                ["stored a key for %s (%d characters, backend %s)"
                 % (provider, len(value), store.backend)])


def cmd_key_list(args):
    store = KeyStore()
    providers, _, _ = load_providers()
    rows = []
    for entry in provider_entries(providers):
        ref = str(entry.get("key_ref") or "")
        key_name = ref.split(":", 1)[1].strip() if ref.startswith("secret:") else None
        value = store.get(key_name) if key_name else None
        rows.append({"provider": entry.get("name"), "key_name": key_name,
                     "present": bool(value),
                     "hint": ("…" + value[-4:]) if value and len(value) > 4 else None})
    payload = {"keystore": store.backend, "detail": store.detail, "keys": rows,
               "all_stored": store.names() if store.usable else []}
    lines = ["%-12s %-16s %-8s %s" % ("provider", "key name", "present", "hint")]
    for row in rows:
        lines.append("%-12s %-16s %-8s %s" % (row["provider"], row["key_name"] or "-",
                                              "yes" if row["present"] else "no", row["hint"] or ""))
    lines.append("")
    lines.append("key store : %s" % (store.backend or "unavailable -- %s" % store.detail))
    return emit(payload, args.json, lines)


def cmd_key_rm(args):
    store = KeyStore()
    if not store.usable:
        return fail("no encrypted store is available: %s" % store.detail, EXIT_PRECONDITION)
    if not store.remove(args.provider):
        return fail("no key stored for %s" % args.provider, EXIT_FAILED)
    return emit({"provider": args.provider, "removed": True}, args.json,
                ["removed the key for %s" % args.provider])


def cmd_policy_show(args):
    policy = Policy.load()
    payload = {"mode": policy.mode, "deny_paths": policy.document.get("deny_paths") or [],
               "allow_paths": policy.document.get("allow_paths") or [],
               "redact": [r[0] for r in policy.redactions],
               "egress_hosts": policy.hosts, "problem": policy.problem,
               "workspace": workspace_root()}
    lines = ["mode          : %s" % policy.mode,
             "workspace     : %s" % workspace_root(),
             "deny paths    :"]
    for pattern in payload["deny_paths"]:
        lines.append("                %s" % pattern)
    if not payload["deny_paths"]:
        lines.append("                (none -- nothing is denied)")
    lines.append("allow paths   : %s" % (", ".join(payload["allow_paths"]) or "(none listed)"))
    lines.append("redaction     : %s" % (", ".join(payload["redact"]) or "(none)"))
    lines.append("egress hosts  : %s" % (", ".join(policy.hosts) or "(none -- http.request always refuses)"))
    if policy.problem:
        lines.append("problem       : %s" % policy.problem)
    return emit(payload, args.json, lines)


def cmd_policy_check(args):
    policy = Policy.load()
    verdict = policy.decide(args.path)
    in_workspace = None
    if verdict.get("resolved"):
        root = norm(workspace_root())
        subject = norm(verdict["resolved"])
        in_workspace = subject == root or subject.startswith(root.rstrip("/") + "/")
    payload = dict(verdict)
    payload["in_workspace"] = in_workspace
    payload["mode"] = policy.mode
    lines = [
        "path      : %s" % verdict["path"],
        "resolved  : %s" % (verdict.get("resolved") or "?"),
        "verdict   : %s" % ("would be sent" if verdict["allowed"] else "would NOT be sent"),
        "because   : %s" % verdict["reason"],
        "workspace : %s" % ("inside" if in_workspace else
                            "outside %s, so the file tools refuse it regardless"
                            % workspace_root() if in_workspace is False else "unknown"),
    ]
    if verdict["allowed"]:
        lines.append("note      : allowed by the deny rules; redaction rules still apply to the text")
    return emit(payload, args.json, lines)


def cmd_tools(args):
    payload = {"workspace": workspace_root(), "tools": TOOLS}
    lines = ["%-14s %-10s %-13s %s" % ("tool", "effect", "confirm", "limits")]
    for tool in TOOLS:
        lines.append("%-14s %-10s %-13s %s"
                     % (tool["name"], tool["side_effect"],
                        "yes" if tool["requires_confirmation"] else "",
                        ", ".join("%s=%s" % kv for kv in (tool["limits"] or {}).items())))
    lines.append("")
    lines.append("workspace  : %s  (file tools cannot leave it)" % workspace_root())
    lines.append("confirmation: a tool marked `confirm` returns E_CONFIRMATION_REQUIRED "
                 "until the user agrees")
    return emit(payload, args.json, lines)


def cmd_tool_run(args):
    try:
        arguments = json.loads(args.args or "{}")
    except ValueError as exc:
        return fail("--args is not valid json: %s" % exc, EXIT_USAGE)
    result = run_tool(args.name, arguments, confirmed=True)
    code = EXIT_OK if result["ok"] else EXIT_FAILED
    if args.json:
        return emit(result, True, [])
    if result["ok"]:
        body = result["result"]
        if "content" in body:
            print(body["content"], end="" if body["content"].endswith("\n") else "\n")
        else:
            print(json.dumps(body, indent=2, ensure_ascii=False))
        return code
    print("minios: %s: %s" % (result["error"]["code"], result["error"]["message"]), file=sys.stderr)
    if result["error"].get("hint"):
        print("minios: hint: %s" % result["error"]["hint"], file=sys.stderr)
    return code


def cmd_audit_tail(args):
    records = audit_records()
    recent = records[-int(getattr(args, "n", 20)):]
    payload = {"total": len(records), "shown": len(recent), "records": recent}
    lines = ["%-20s %-12s %-14s %s" % ("when", "kind", "tool", "detail")]
    for record in recent:
        detail = record.get("code") or record.get("model") or record.get("provider") or ""
        if record.get("path"):
            detail = "%s %s" % (record["path"], detail)
        lines.append("%-20s %-12s %-14s %s"
                     % (record.get("t", ""), record.get("kind", ""), record.get("tool", ""), detail))
    if not recent:
        lines.append("(no audit records yet)")
    return emit(payload, args.json, lines)


def cmd_audit_export(args):
    records = audit_records(getattr(args, "since", None), getattr(args, "until", None))
    payload = {"count": len(records), "records": records}
    if args.json:
        return emit(payload, True, [])
    for record in records:
        print(json.dumps(record, ensure_ascii=False))
    return EXIT_OK


def cmd_session_list(args):
    summaries = session_summaries()
    payload = {"sessions": summaries}
    lines = ["%-22s %-20s %-6s %-6s %s" % ("id", "updated", "events", "turns", "first question")]
    for summary in summaries:
        lines.append("%-22s %-20s %-6d %-6d %s"
                     % (summary["id"], summary.get("updated") or "", summary["events"],
                        summary["turns"], summary["first_question"]))
    if not summaries:
        lines.append("(no sessions yet)")
    return emit(payload, args.json, lines)


def cmd_session_show(args):
    identifier = getattr(args, "session", None)
    if not identifier:
        summaries = session_summaries()
        if not summaries:
            return fail("no sessions yet", EXIT_PRECONDITION)
        identifier = summaries[0]["id"]
    events = session_events(identifier)
    if not events:
        return fail("no such session: %s" % identifier, EXIT_FAILED)
    payload = {"id": identifier, "events": events}
    lines = []
    for event in events:
        if event.get("role") == "tool":
            lines.append("[tool] %s -> %s" % (event.get("tool"), "ok" if event.get("ok") else "failed"))
        else:
            lines.append("[%s] %s" % (event.get("role"), event.get("text", "")))
    return emit(payload, args.json, lines)


def cmd_session_export(args):
    identifier = args.session
    events = session_events(identifier)
    if not events:
        return fail("no such session: %s" % identifier, EXIT_FAILED)
    if getattr(args, "format", "text") == "json":
        payload = {"id": identifier, "events": events}
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return EXIT_OK
    for event in events:
        print("[%s] %s" % (event.get("role"), event.get("text", "")))
    return EXIT_OK


def cmd_session_rm(args):
    if not session_remove(args.session):
        return fail("no such session: %s" % args.session, EXIT_FAILED)
    return emit({"removed": args.session}, args.json, ["removed %s" % args.session])


def _gateway_or_fail():
    config, problem, _ = load_providers()
    if not provider_entries(config):
        return None, fail("no providers are configured; run `minios prov add` first",
                          EXIT_PRECONDITION)
    return Gateway(config), None


def _ask_once(gateway, question, model, max_tokens, stream, session, quiet=False):
    messages = [{"role": "user", "content": question}]
    if session:
        messages = sessions_message(session) + messages
    if session:
        session_append(session, "user", question)
    try:
        text, meta = gateway.chat(messages, model=model, max_tokens=max_tokens, stream=stream)
    except GatewayError as exc:
        if session:
            session_append(session, "system", "no answer: %s" % exc)
        detail = "; ".join(json.dumps(a, ensure_ascii=False) for a in (exc.attempts or [])[:3])
        return fail("%s%s" % (exc, (" -- " + detail) if detail else ""), EXIT_FAILED)
    if not stream and not quiet:
        print(text)
    if session:
        session_append(session, "assistant", text, model=meta["model"])
    return EXIT_OK, text, meta


def cmd_ask(args):
    gateway, problem = _gateway_or_fail()
    if gateway is None:
        return problem
    question = args.question
    if getattr(args, "stdin", False) or not question:
        question = sys.stdin.read().strip()
    if not question:
        return fail("no question given (pass one, use --stdin, or pipe it in)", EXIT_USAGE)

    session = None
    if getattr(args, "session", None):
        session = args.session
    elif os.environ.get("MINIOS_ASK_SESSION"):
        session = os.environ["MINIOS_ASK_SESSION"]

    # --json means one document on stdout, so the answer goes inside it rather
    # than in front of it; and a stream would interleave tokens with the json.
    as_json = bool(getattr(args, "json", False))
    stream = bool(getattr(args, "stream", False)) and not as_json
    outcome = _ask_once(gateway, question, getattr(args, "model", None),
                        getattr(args, "max_tokens", 1024), stream, session, quiet=as_json)
    if isinstance(outcome, tuple):
        code, text, meta = outcome
        if as_json:
            json.dump({"answer": text, "model": meta["model"], "provider": meta["provider"],
                       "usage": meta.get("usage"),
                       "cost_usd": estimate_cost(meta["model"], meta.get("usage"))},
                      sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        return code
    return outcome


def cmd_chat(args):
    gateway, problem = _gateway_or_fail()
    if gateway is None:
        return problem
    session = getattr(args, "session", None) or session_id()
    model = getattr(args, "model", None)
    if not sys.stdin.isatty():
        print("minios: chat on session %s (stdin is a pipe; /exit to finish)" % session,
              file=sys.stderr)
    for line in sys.stdin:
        question = line.strip()
        if not question:
            continue
        if question in ("/exit", "/quit"):
            break
        if question == "/session":
            print(session)
            continue
        if question.startswith("/model "):
            model = question.split(None, 1)[1].strip()
            print("model is now %s" % model)
            continue
        outcome = _ask_once(gateway, question, model, 1024, stream=True, session=session)
        if isinstance(outcome, tuple):
            print()
        else:
            return outcome
    print("session %s" % session)
    return EXIT_OK


def cmd_admin_status(args):
    path = os.path.join(config_dir(), "admin")
    state = admin_password_state()
    payload = {"path": path, "state": state}
    if state == "set":
        encoded = read_text(path) or ""
        # scrypt$params$salt$digest -- four parts, and getting that wrong shows
        # up as "?" rather than as an error, so the count is checked here.
        parts = encoded.split("$")
        algo = parts[0] if len(parts) == 4 else "?"
        settings = parts[1] if len(parts) == 4 else "?"
        payload["kdf"] = "%s (%s)" % (algo, settings)
        try:
            payload["set_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime(os.path.getmtime(path)))
        except OSError:
            payload["set_at"] = None
    lines = ["password  : %s" % (state or "no marker (firstboot has not run)"),
             "path      : %s" % path]
    if state == "set":
        lines.append("kdf       : %s" % payload.get("kdf"))
        lines.append("set at    : %s" % payload.get("set_at"))
    elif state == "unset":
        lines.append("next      : `minios admin passwd`")
    return emit(payload, args.json, lines)


MIN_PASSWORD_LENGTH = 8


def cmd_admin_passwd(args):
    """Set the administrator password.

    Read from stdin, never from argv: `minios admin passwd hunter2` would put
    the password in the shell history, in `ps` for anyone on the machine, and
    in whatever logs the shell keeps.
    """
    if admin_password_state() is None:
        return fail("%s does not exist: the writable layer has not been seeded "
                    "(run firstboot)" % os.path.join(config_dir(), "admin"),
                    EXIT_PRECONDITION)
    try:
        first = sys.stdin.readline().rstrip("\n")
        second = sys.stdin.readline().rstrip("\n")
    except KeyboardInterrupt:
        return fail("cancelled", EXIT_FAILED)
    if not first:
        return fail("no password was read on stdin", EXIT_USAGE)
    if first != second:
        return fail("the two entries did not match", EXIT_FAILED)
    if len(first) < MIN_PASSWORD_LENGTH:
        return fail("the password must be at least %d characters" % MIN_PASSWORD_LENGTH,
                    EXIT_FAILED)

    encoded = hash_password(first)
    path = os.path.join(config_dir(), "admin")
    write_text_atomic(path, encoded + "\n", mode=0o600)
    audit("admin.passwd", path=path)
    return emit({"path": path, "set": True, "kdf": encoded.split("$")[1]}, args.json,
                ["administrator password set",
                 "stored as a scrypt hash in %s -- the password itself is not kept" % path])


def cmd_log_show(args):
    code, lines = _read_log(int(getattr(args, "n", 40)))
    return emit({"lines": lines, "source": _log_source()}, args.json, lines) if code == EXIT_OK \
        else code


def _log_source():
    if shutil.which("journalctl"):
        return "journalctl"
    path = os.environ.get("MINIOS_LOG_FILE", "/var/log/minios.log")
    if os.path.exists(path):
        return path
    return None


def _read_log(count):
    source = _log_source()
    if source is None:
        return fail("no log source: journalctl is absent and no MINIOS_LOG_FILE exists",
                    EXIT_PRECONDITION), []
    if source == "journalctl":
        try:
            out = subprocess.run(["journalctl", "-t", "minios", "-n", str(count), "--no-pager"],
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return fail("journalctl failed: %s" % exc, EXIT_FAILED), []
        return EXIT_OK, (out.stdout or "").rstrip("\n").splitlines()
    text = read_text(source, "")
    return EXIT_OK, (text or "").splitlines()[-count:]


def cmd_log_follow(args):
    source = _log_source()
    if source is None:
        return fail("no log source: journalctl is absent and no MINIOS_LOG_FILE exists",
                    EXIT_PRECONDITION)
    if source == "journalctl":
        try:
            subprocess.run(["journalctl", "-t", "minios", "-f", "--no-pager"])
        except KeyboardInterrupt:
            pass
        except OSError as exc:
            return fail("journalctl failed: %s" % exc, EXIT_FAILED)
        return EXIT_OK
    print("minios: following %s (Ctrl-C to stop)" % source, file=sys.stderr)
    try:
        with open(source, "r", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                line = fh.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def cmd_persist_status(args):
    backend, detail = keystore_backend()
    store = KeyStore()
    payload = {"backend": backend, "detail": detail,
               "mounted": is_mountpoint(secrets_dir()),
               "secrets_dir": secrets_dir(),
               "keys": store.names() if store.usable else [],
               "incognito": incognito_on()}
    lines = [
        "secrets   : %s" % secrets_dir(),
        "mounted   : %s" % ("yes" if payload["mounted"] else "no"),
        "backend   : %s" % (backend or "unavailable"),
        "detail    : %s" % detail,
        "keys      : %s" % (", ".join(payload["keys"]) or "none"),
        "no-trace  : %s" % ("on" if payload["incognito"] else "off"),
    ]
    return emit(payload, args.json, lines)


def cmd_persist_unlock(args):
    if is_mountpoint(secrets_dir()):
        return emit({"unlocked": False, "already": True}, args.json,
                    ["%s is already mounted" % secrets_dir()])
    device = os.environ.get("MINIOS_PERSIST_DEVICE")
    if not device:
        return fail("no persistent device is configured (MINIOS_PERSIST_DEVICE)", EXIT_PRECONDITION)
    if not shutil.which("cryptsetup"):
        return fail("cryptsetup is not installed in this image", EXIT_PRECONDITION)
    return fail("unlocking requires the passphrase on a terminal; this build has no "
                "console helper yet", EXIT_UNIMPLEMENTED)


def cmd_persist_lock(args):
    if not is_mountpoint(secrets_dir()):
        return fail("%s is not mounted" % secrets_dir(), EXIT_PRECONDITION)
    if not shutil.which("umount"):
        return fail("umount is not available", EXIT_PRECONDITION)
    try:
        subprocess.run(["umount", secrets_dir()], check=True)
    except subprocess.SubprocessError as exc:
        return fail("could not unmount: %s" % exc, EXIT_FAILED)
    return emit({"locked": True}, args.json, ["locked %s" % secrets_dir()])


def cmd_incognito(args):
    state = getattr(args, "state", "status")
    if state == "status":
        on = incognito_on()
        return emit({"incognito": on, "path": incognito_path()}, args.json,
                    ["no-trace mode is %s" % ("on" if on else "off"),
                     "the writable layer becomes memory at the next boot, so nothing survives a reboot"])
    if state == "on":
        write_text_atomic(incognito_path(), "1\n")
        audit("incognito", enabled=True)
        return emit({"incognito": True, "effective": "next boot"}, args.json,
                    ["no-trace mode recorded: it takes effect at the next boot",
                     "sessions and audit records written before then are still on disk"])
    if os.path.exists(incognito_path()):
        os.remove(incognito_path())
    audit("incognito", enabled=False)
    return emit({"incognito": False, "effective": "next boot"}, args.json,
                ["no-trace mode will be off at the next boot"])


def cmd_support_bundle(args):
    policy = Policy.load()
    out_path = args.out
    staged = []

    def stage(arcname, text):
        scrubbed, fired = policy.redact(text)
        staged.append((arcname, scrubbed, fired))

    sys.path.insert(0, HERE)
    try:
        import hwprofil
        stage("hardware/profile.json", json.dumps(hwprofil.build(), indent=2, ensure_ascii=False))
    except ImportError:
        pass
    stage("hardware/hwchange.json", json.dumps(hwchange() or {}, indent=2))
    for name in ("providers.yaml", "egress.yaml"):
        text = read_text(os.path.join(config_dir(), name))
        if text:
            stage("config/%s" % name, text)
    overlay = providers_dir()
    if os.path.isdir(overlay):
        for name in sorted(os.listdir(overlay)):
            text = read_text(os.path.join(overlay, name))
            if text:
                stage("config/providers.d/%s" % name, text)
    for name in ("net.json", "hwchange.json", "persist.json"):
        text = read_text(os.path.join(run_dir(), name))
        if text:
            stage("run/%s" % name, text)
    for summary in session_summaries()[:20]:
        events = session_events(summary["id"])
        stage("sessions/%s.jsonl" % summary["id"],
              "\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    audit_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in audit_records()[-500:])
    if audit_text:
        stage("audit/audit.jsonl", audit_text)
    lines, _ = _read_log(200)
    if isinstance(lines, list) and lines:
        stage("log/minios.log", "\n".join(lines))

    manifest = {
        "generated_at": now_iso(),
        "machine_id": current_machine(),
        "image": image_version(),
        "egress_mode": policy.mode,
        "files": [{"path": p, "redactions": r} for p, _, r in staged],
        "redactions": sum(len(r) for _, _, r in staged),
        "note": ("Every text file here has passed the egress redaction rules before being "
                 "written.  Nothing outside config/, run/, sessions/, audit/ and log/ is "
                 "collected, and no key material is included."),
    }
    try:
        with tarfile.open(out_path, "w:gz") as archive:
            for name, text, _ in staged:
                blob = text.encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                info.mtime = int(time.time())
                archive.addfile(info, io.BytesIO(blob))
            blob = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(blob)
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(blob))
    except OSError as exc:
        return fail("cannot write %s: %s" % (out_path, exc), EXIT_FAILED)

    redacted_total = sum(len(r) for _, _, r in staged)
    payload = {"out": out_path, "files": len(staged), "redactions": redacted_total,
               "manifest": manifest}
    return emit(payload, args.json,
                ["wrote %s" % out_path,
                 "%d files, %d redaction rule(s) fired" % (len(staged), redacted_total),
                 "review it before sending: it contains session transcripts"])


def cmd_doctor(args):
    wanted = None
    if getattr(args, "only", None):
        wanted = {i.strip() for i in args.only.split(",") if i.strip()}
    elif getattr(args, "check", None):
        wanted = {args.check}

    results = []
    for item_id, title, fn in CHECKS:
        if wanted and item_id not in wanted:
            continue
        try:
            status, detail = fn()
        except Exception as exc:                      # a broken check is a finding
            status, detail = FAIL, "the check itself raised: %r" % exc
        results.append({"id": item_id, "check": title, "status": status, "detail": detail})

    counts = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    payload = {
        "generated_at": now_iso(),
        "counts": counts,
        "results": results,
        "note": ("`unknown` means the check could not be evaluated in this environment. "
                 "It is not a pass: the acceptance run happens on the target image, and a "
                 "self-check that guesses is worse than none."),
    }
    lines = ["%-5s %-8s %-48s %s" % ("id", "status", "check", "detail")]
    for row in results:
        lines.append("%-5s %-8s %-48s %s"
                     % (row["id"], paint(row["status"].ljust(8), row["status"]),
                        row["check"], row["detail"]))
    lines.append("")
    lines.append(", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    lines.append(payload["note"])
    emit(payload, args.json, lines)
    return EXIT_FAILED if counts.get(FAIL) else EXIT_OK


# ==========================================================================
# the checks behind `doctor`
# ==========================================================================

def check_profile():
    prof, path = read_profile()
    if prof is None:
        return UNKNOWN, "no profile found (looked for %s)" % path
    if prof.get("schema") != 1:
        return FAIL, "profile schema is %r, this build understands 1" % prof.get("schema")
    return PASS, path


def check_memory_budget():
    prof, _ = read_profile()
    if prof is None:
        return UNKNOWN, "no profile, cannot size the budget"
    total = (prof.get("memory") or {}).get("total_kb")
    if not total:
        return UNKNOWN, "profile does not state total memory"
    limit_mb = int(os.environ.get("MINIOS_MEM_LIMIT_MB", "6144"))
    used_mb = total / 1024.0
    if used_mb > limit_mb:
        return FAIL, "host memory %.0f MB exceeds the %d MB budget" % (used_mb, limit_mb)
    return PASS, "host memory %.0f MB fits the %d MB budget" % (used_mb, limit_mb)


def check_secure_boot():
    if not os.path.isdir("/sys/firmware/efi"):
        return SKIP, "not booted through EFI, so secure boot does not apply"
    prof, _ = read_profile()
    value = ((prof or {}).get("firmware") or {}).get("secure_boot")
    if value is None:
        return UNKNOWN, "could not read the SecureBoot variable"
    return (PASS if value else FAIL), "secure boot is %s" % ("on" if value else "off")


def check_default_route():
    facts = network_facts()
    if facts["source"] == "unavailable":
        return UNKNOWN, "no /proc/net/route on this machine"
    if facts["default_route"]:
        return PASS, "default route via %s" % facts["default_route"]
    return FAIL, "no default route"


def check_gateway_listening():
    settings = (load_providers()[0].get("gateway") or {})
    listen = str(settings.get("listen") or "127.0.0.1:4000")
    host, _, port = listen.rpartition(":")
    sock = socket.socket()
    sock.settimeout(0.5)
    try:
        sock.connect((host or "127.0.0.1", int(port)))
        return PASS, "%s accepts connections" % listen
    except (OSError, ValueError) as exc:
        return FAIL, "%s did not answer: %s" % (listen, exc)
    finally:
        sock.close()


def check_providers_configured():
    config, problem, _ = load_providers()
    entries = provider_entries(config)
    if not entries:
        return FAIL, "no providers configured%s" % (" (%s)" % problem if problem else "")
    if len(entries) < 2:
        return FAIL, "%d provider: with no local model, one provider is a single point" % len(entries)
    return PASS, "%d providers configured" % len(entries)


def check_keys_out_of_environment():
    suspects = []
    for name, value in os.environ.items():
        if not value or len(value) < 12:
            continue
        upper = name.upper()
        if "KEY" in upper or "TOKEN" in upper or "SECRET" in upper:
            suspects.append(name)
        elif value.startswith(("sk-", "sk_", "gsk_", "xai-")):
            suspects.append(name)
    if suspects:
        return FAIL, "credential-shaped variables in the environment: %s" % ", ".join(sorted(suspects))
    return PASS, "no credential-shaped variables in the environment"


def check_keystore_backend():
    backend, detail = keystore_backend()
    if backend is None:
        return UNKNOWN, detail
    if backend == "plaintext-dev":
        return FAIL, "keys would be stored in plaintext (%s)" % detail
    return PASS, "%s (%s)" % (backend, detail)


def check_egress_policy():
    policy = Policy.load()
    if policy.problem and not policy.document:
        return UNKNOWN, policy.problem
    if not policy.document:
        return FAIL, "egress.yaml is missing or not a mapping"
    if policy.mode not in ("denylist", "allowlist", "off"):
        return FAIL, "mode is %r" % policy.mode
    if policy.mode == "denylist" and not policy.deny:
        return FAIL, "denylist mode with an empty deny list"
    return PASS, "mode %s, %d deny rule(s), %d redaction rule(s)" % (
        policy.mode, len(policy.deny), len(policy.redactions))


def check_readonly_root():
    text = read_text("/proc/mounts")
    if text is None:
        return UNKNOWN, "no /proc/mounts on this machine"
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[1] == "/":
            fstype, opts = fields[2], fields[3]
            if "ro" in opts.split(",") or fstype in ("overlay", "erofs", "squashfs"):
                return PASS, "/ is %s (%s)" % (fstype, opts)
            return FAIL, "/ is %s mounted read-write (%s)" % (fstype, opts)
    return UNKNOWN, "no / entry in /proc/mounts"


def check_writable_layer_seeded():
    """The layout everything else assumes has to be established by something.

    On a read-only image nothing else can: the parts that must change live in
    the writable layer, and only firstboot writes them.
    """
    report = firstboot_report()
    state = state_dir()
    missing = [name for name in ("machines", "sessions", "audit", "usage", "secrets")
               if not os.path.isdir(os.path.join(state, name))]
    if report is None and missing:
        return UNKNOWN, ("firstboot has not run and %s does not have %s"
                         % (state, ", ".join(missing)))
    if missing:
        return FAIL, "firstboot ran but %s is missing %s" % (state, ", ".join(missing))
    if not os.path.exists(os.path.join(config_dir(), "providers.yaml")):
        return FAIL, "no providers.yaml in %s; defaults were never seeded" % config_dir()
    return PASS, "%s is initialised (%s)" % (state, "firstboot ran" if report else "report absent")


def check_admin_password():
    state = admin_password_state()
    if state is None:
        return UNKNOWN, "no %s marker; firstboot has not run" % os.path.join(config_dir(), "admin")
    if state == "unset":
        return FAIL, "no administrator password has been chosen yet"
    return PASS, "an administrator password is set"


def check_sandbox_refuses_escape():
    """Ask the file sandbox to open something outside the workspace and check
    that it says no.  A sandbox that has never been tested is a sandbox that
    has never been shown to work."""
    ok, _, code, _ = resolve_in_workspace("/etc/shadow")
    if ok:
        return FAIL, "the sandbox allowed /etc/shadow"
    if code != "E_PATH_OUTSIDE_WORKSPACE":
        return FAIL, "expected E_PATH_OUTSIDE_WORKSPACE, got %s" % code
    return PASS, "a path outside %s is refused with %s" % (workspace_root(), code)


CHECKS = [
    ("A3", "hardware profile is written and readable", check_profile),
    ("A5", "host memory fits the budget", check_memory_budget),
    ("A2", "secure boot state is known", check_secure_boot),
    ("B1", "there is a default route", check_default_route),
    ("C1", "the model gateway is listening", check_gateway_listening),
    ("C2", "at least two providers are configured", check_providers_configured),
    ("D4", "the file sandbox refuses to leave the workspace", check_sandbox_refuses_escape),
    ("E2", "no credentials in the environment", check_keys_out_of_environment),
    ("E2b", "the key store is not plaintext", check_keystore_backend),
    ("E1b", "an administrator password has been chosen", check_admin_password),
    ("E8", "the egress policy exists and says something", check_egress_policy),
    ("F1", "the system partition is read-only", check_readonly_root),
    ("F1b", "the writable layer has been seeded", check_writable_layer_seeded),
]
