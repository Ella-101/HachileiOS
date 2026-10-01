#!/usr/bin/env python3
"""minios - the command line.

One entry point, two levels (`minios <object> <verb>`).  A portable appliance
should add one name to PATH, not twenty, and one registry is what keeps `help`,
the man page and the written specification from drifting apart: everything
below is rendered from COMMANDS.

This file is deliberately thin.  It owns the command registry, the argument
parser and the dispatch table; the behaviour lives in minioscore, because the
same functions are also called by the self-check and by the tests, and a rule
that exists in two of those three is wrong in the third.

Two rules the whole surface follows.

  * Human output by default, `--json` wherever a command reports something.
    The acceptance harness uses the same code path, so the two cannot drift;
    and there is no second interface to keep in step, because there is no UI
    (decision ADR-5) -- the command line is the product.

  * Exit codes carry meaning: 0 ok, 1 it ran and the answer was bad, 2 usage,
    3 not implemented yet, 4 a precondition is unmet (locked, offline, no
    provider).  A script can tell "we are not ready" from "we are broken".
"""

import argparse
import os
import sys
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import minioscore as core
from minioscore import (EXIT_FAILED, EXIT_OK, EXIT_PRECONDITION, EXIT_UNIMPLEMENTED,
                        EXIT_USAGE, emit, fail)


Command = namedtuple(
    "Command",
    "path summary phase admin network json note",
)

# phase: which build batch introduces it (see the checklist).
# admin: needs root.
# network: does nothing useful without connectivity.
# json:  accepts --json.

COMMANDS = [
    # ---------------------------------------------------------------- status
    Command("help", "show this command index", "P1", False, False, True,
            "`--format markdown` renders the table the specification embeds."),
    Command("version", "print the image version and build id", "P1", False, False, True, ""),
    Command("status", "one screen: machine, network, models, sessions", "P1", False, False, True,
            "Reports `unknown` rather than guessing; it never probes the network itself."),
    Command("doctor", "check the system against the acceptance checklist", "P1", False, False, True,
            "Results are keyed by checklist id (A1, B3, C6...). A check that cannot be evaluated answers `unknown`."),

    # ---------------------------------------------------------- administrator
    Command("admin.passwd", "set the administrator password", "P1", True, False, False,
            "Reads stdin, so it cannot land in shell history; stored as a scrypt hash, never in the clear."),
    Command("admin.status", "is an administrator password set, and with what", "P1", False, False, True, ""),

    # -------------------------------------------------------------- hardware
    Command("hw.show", "describe the hardware we booted on", "P1", False, False, True,
            "Bare `minios hw` runs this.  Reads /proc and /sys directly; no lspci or dmidecode needed."),
    Command("hw.diff", "what changed since the last profile", "P1", False, False, True,
            "Diffs against this machine's own history, or against the machine we booted on last time."),

    # ------------------------------------------------------------------- net
    Command("net.status", "what the network looks like right now", "P2", False, False, True,
            "Reads the kernel's view only; it sends no packets."),
    Command("net.probe", "probe DNS and each provider endpoint", "P2", False, True, True,
            "Separates `there is a network` from `a model is reachable`: two different questions."),

    # -------------------------------------------------------------- providers
    Command("prov.list", "list configured model providers and their health", "P3", False, False, True, ""),
    Command("prov.add", "register a provider", "P3", True, False, False,
            "Writes into providers.d/, so the commented providers.yaml is never rewritten."),
    Command("prov.rm", "remove a provider that prov add created", "P3", True, False, False, ""),
    Command("prov.test", "ask a provider for its model list", "P3", False, True, True,
            "Shows the provider's own error text; the reason matters more than the verdict."),
    Command("prov.use", "make a provider or model the default", "P3", True, False, False, ""),

    # ---------------------------------------------------------------- secrets
    Command("key.set", "store an API key (read from stdin, never from argv)", "P5", True, False, False,
            "Reads stdin so the key cannot land in shell history or in ps."),
    Command("key.list", "list which providers have a key, without showing it", "P5", False, False, True,
            "Prints the last four characters at most."),
    Command("key.rm", "delete a stored key", "P5", True, False, False, ""),

    # ----------------------------------------------------------------- policy
    Command("policy.show", "show the egress rules", "P5", False, False, True, ""),
    Command("policy.check", "ask whether a path would be sent to a provider", "P5", False, False, True,
            "Answers before the fact, so the user can find out without spending a request."),

    # -------------------------------------------------------------- sessions
    Command("chat", "talk to the model, with tools", "P4", False, True, True,
            "Streams.  `/exit` leaves, `/model NAME` switches, `/session` prints the id."),
    Command("ask", "one question, one answer, then exit", "P4", False, True, True,
            "The scripting entry point: pipe a question in, get the answer on stdout."),
    Command("session.list", "list stored sessions", "P5", False, False, True, ""),
    Command("session.show", "print a session's transcript", "P5", False, False, True, ""),
    Command("session.resume", "continue a stored session", "P5", False, True, True, ""),
    Command("session.export", "export a session as text or json", "P5", False, False, True, ""),
    Command("session.rm", "delete a session", "P5", False, False, False, ""),

    # ------------------------------------------------------------------ tools
    Command("tools", "list the tools the agent may call, and their limits", "P4", False, False, True, ""),
    Command("tool.run", "run one tool directly, for testing and for the audit trail", "P4", False, False, True,
            "Same code path the agent uses, so this is a real test of the sandbox."),

    # ----------------------------------------------------------------- audit
    Command("audit.tail", "show the most recent audit records", "P5", False, False, True,
            "Arguments are recorded as a digest; the audit log is not a copy of your files."),
    Command("audit.export", "export audit records for a time range", "P5", False, False, True, ""),
    Command("log.show", "show the system log", "P1", False, False, True,
            "Bare `minios log` runs this."),
    Command("log.follow", "follow the system log", "P1", False, False, True, ""),

    # ------------------------------------------------------------ persistence
    Command("persist.status", "is the encrypted store mounted, and what is in it", "P5", False, False, True, ""),
    Command("persist.unlock", "unlock the encrypted store for this boot", "P5", True, False, False, ""),
    Command("persist.lock", "lock the encrypted store now", "P5", True, False, False, ""),
    Command("incognito", "turn the no-trace mode on, off, or ask", "P5", False, False, True,
            "No-trace means the writable layer is memory, so a reboot leaves nothing behind."),

    # --------------------------------------------------------------- lifecycle
    Command("update.check", "is a newer signed image available", "P6", False, True, True, ""),
    Command("update.apply", "apply it, atomically, with rollback on failure", "P6", True, True, True, ""),
    Command("update.rollback", "go back to the previous image", "P6", True, False, False, ""),
    Command("support-bundle", "collect logs, profile and config, redacted, for a bug report", "P7", False, False, True,
            "Runs the redaction rules from egress.yaml over everything it collects."),
]

EXTRA_ARGS = {
    "help": [(("command",), {"nargs": "?", "help": "show detail for one command"}),
             (("--format",), {"choices": ("table", "markdown", "json"), "default": "table"})],
    "status": [],
    "doctor": [(("--only",), {"metavar": "IDS", "help": "comma-separated checklist ids, e.g. C1,C2"}),
               (("--check",), {"metavar": "ID"})],
    "admin.passwd": [],
    "admin.status": [],
    "hw.show": [(("--raw",), {"action": "store_true", "help": "print the profile json"}),
                (("--diff",), {"metavar": "PATH", "help": "compare against an earlier profile"})],
    "hw.diff": [(("against",), {"nargs": "?", "help": "path to an earlier profile; default is this machine's own history"})],
    "net.probe": [(("--timeout",), {"type": float, "default": 5.0})],
    "prov.add": [(("name",), {}), (("--base-url",), {}), (("--model",), {"action": "append"})],
    "prov.rm": [(("name",), {})],
    "prov.test": [(("name",), {"nargs": "?"})],
    "prov.use": [(("name",), {"nargs": "?"}), (("--model",), {})],
    "key.set": [(("provider",), {})],
    "key.rm": [(("provider",), {})],
    "policy.check": [(("path",), {})],
    "chat": [(("--model",), {}), (("--session",), {})],
    "ask": [(("question",), {"nargs": "?"}),
            (("--model",), {}), (("--max-tokens",), {"type": int, "default": 1024}),
            (("--session",), {}),
            (("--stream",), {"action": "store_true", "help": "print tokens as they arrive"}),
            (("--stdin",), {"action": "store_true", "help": "read the question from stdin"})],
    "session.show": [(("session",), {"nargs": "?"})],
    "session.resume": [(("session",), {}), (("--model",), {})],
    "session.export": [(("session",), {}), (("--format",), {"choices": ("text", "json"), "default": "text"})],
    "session.rm": [(("session",), {})],
    "tool.run": [(("name",), {}), (("--args",), {"metavar": "JSON", "default": "{}"})],
    "audit.tail": [(("-n",), {"type": int, "default": 20})],
    "audit.export": [(("--since",), {}), (("--until",), {})],
    "log.show": [(("-n",), {"type": int, "default": 40})],
    "log.follow": [],
    "incognito": [(("state",), {"choices": ("on", "off", "status"), "nargs": "?", "default": "status"})],
    "update.apply": [(("--yes",), {"action": "store_true"})],
    "support-bundle": [(("--out",), {"metavar": "PATH", "default": "support-bundle.tar.gz"})],
}

# Everything except the update machinery is implemented.  Those three need a
# signed second image and a bootloader that can switch to it, which is P6 work;
# a `update check` that cannot verify a signature would be worse than an
# honest "not yet".
HANDLERS = {
    "version": core.cmd_version,
    "status": core.cmd_status,
    "doctor": core.cmd_doctor,
    "admin.passwd": core.cmd_admin_passwd,
    "admin.status": core.cmd_admin_status,
    "hw.show": core.cmd_hw,
    "hw.diff": core.cmd_hw,
    "net.status": core.cmd_net_status,
    "net.probe": core.cmd_net_probe,
    "prov.list": core.cmd_prov_list,
    "prov.add": core.cmd_prov_add,
    "prov.rm": core.cmd_prov_rm,
    "prov.test": core.cmd_prov_test,
    "prov.use": core.cmd_prov_use,
    "key.set": core.cmd_key_set,
    "key.list": core.cmd_key_list,
    "key.rm": core.cmd_key_rm,
    "policy.show": core.cmd_policy_show,
    "policy.check": core.cmd_policy_check,
    "chat": core.cmd_chat,
    "ask": core.cmd_ask,
    "session.list": core.cmd_session_list,
    "session.show": core.cmd_session_show,
    "session.resume": core.cmd_chat,
    "session.export": core.cmd_session_export,
    "session.rm": core.cmd_session_rm,
    "tools": core.cmd_tools,
    "tool.run": core.cmd_tool_run,
    "audit.tail": core.cmd_audit_tail,
    "audit.export": core.cmd_audit_export,
    "log.show": core.cmd_log_show,
    "log.follow": core.cmd_log_follow,
    "persist.status": core.cmd_persist_status,
    "persist.unlock": core.cmd_persist_unlock,
    "persist.lock": core.cmd_persist_lock,
    "incognito": core.cmd_incognito,
    "support-bundle": core.cmd_support_bundle,
}


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------

def cmd_help(args):
    wanted = getattr(args, "command", None)
    fmt = getattr(args, "format", "table")
    rows = [c for c in COMMANDS if wanted is None
            or c.path == wanted or c.path.startswith(wanted.replace(" ", ".") + ".")]
    if wanted and not rows:
        return fail("no such command: %s" % wanted, EXIT_USAGE)

    if fmt == "json":
        json_ready = [c._asdict() for c in rows]
        return emit(json_ready, True, [])

    if fmt == "markdown":
        print("| command | what it does | phase | admin | needs network |")
        print("| --- | --- | --- | --- | --- |")
        for c in rows:
            print("| `minios %s` | %s | %s | %s | %s |"
                  % (c.path.replace(".", " "), c.summary, c.phase,
                     "yes" if c.admin else "", "yes" if c.network else ""))
        return EXIT_OK

    width = max(len(c.path.replace(".", " ")) for c in rows)
    print("minios <command> [options]   (image %s)" % core.image_version())
    print()
    print("  %-*s  %-6s  %s" % (width, "command", "phase", "what it does"))
    for c in rows:
        tags = [t for t in ("admin" if c.admin else None,
                            "network" if c.network else None) if t]
        print("  %-*s  %-6s  %s%s" % (width, c.path.replace(".", " "), c.phase, c.summary,
                                      "  [" + ", ".join(tags) + "]" if tags else ""))
    print()
    print("Every command takes --json; --no-color disables colour.  Exit codes:"
          " 0 ok, 1 failed, 2 usage, 3 not implemented, 4 precondition unmet.")
    return EXIT_OK


# Registered here rather than in the dict above, because `help` renders from
# COMMANDS and would be a name error if it were referenced before it is defined.
HANDLERS["help"] = cmd_help


def unimplemented(path, phase, args):
    print("minios: `%s` is not implemented yet (planned for %s)"
          % (path.replace(".", " "), phase), file=sys.stderr)
    return EXIT_UNIMPLEMENTED


# --------------------------------------------------------------------------
# the command tree, built from the registry
# --------------------------------------------------------------------------

def add_common(parser, suppress=False):
    """--json and --no-color belong at every level.

    On subparsers they must not carry a default: a `store_true` default would
    overwrite the value the root parser already parsed, so `minios --json
    status` would silently print human output.  SUPPRESS leaves the attribute
    alone unless the flag really appears here.
    """
    default = argparse.SUPPRESS if suppress else False
    parser.add_argument("--json", action="store_true", default=default,
                        help="machine-readable output")
    parser.add_argument("--no-color", action="store_true", default=default,
                        help="never colourise output")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="minios",
        description="Command line for the portable agent OS.",
        epilog="Run `minios help` for the command index, `minios doctor` for the self-check.",
    )
    add_common(parser)
    groups = {}
    roots = parser.add_subparsers(dest="root", metavar="<command>")

    def leaf(container, name, command):
        sub = container.add_parser(name, help=command.summary, description=command.summary)
        add_common(sub, suppress=True)
        for flags, kwargs in EXTRA_ARGS.get(command.path, []):
            sub.add_argument(*flags, **kwargs)
        return sub

    # Registry order, not sorted: the list is grouped by purpose, and within a
    # group the first entry declared is what a bare `minios <object>` runs.
    #
    # The path of the command being run is deliberately NOT carried in the
    # namespace via set_defaults: a default set on the group parser would
    # already be present in the namespace by the time the verb's parser runs,
    # so the verb's own default would be silently ignored and every subcommand
    # would resolve to the group default.  main() works the path out from the
    # two dest values instead.
    for command in COMMANDS:
        parts = command.path.split(".")
        if len(parts) == 1:
            leaf(roots, parts[0], command)
            continue
        group_name, verb = parts[0], parts[1]
        if group_name not in groups:
            container = roots.add_parser(group_name, help="%s commands" % group_name)
            groups[group_name] = container.add_subparsers(dest="verb", metavar="<verb>")
        leaf(groups[group_name], verb, command)
    return parser


def bare_group_default(root):
    """What `minios hw` means: the first verb the registry lists for that group.

    The bare form is a convenience and takes no options; anything with a flag
    needs the verb spelled out, which is also what makes the help readable.
    """
    for command in COMMANDS:
        if command.path.startswith(root + "."):
            return command.path
    return None


def resolve_path(args):
    root = getattr(args, "root", None)
    if not root:
        return None
    verb = getattr(args, "verb", None)
    if verb:
        return "%s.%s" % (root, verb)
    return bare_group_default(root) or root


def require_admin(path):
    """Root, unless this is a development machine that has said so out loud."""
    if os.environ.get("MINIOS_UNSAFE_ALLOW_ADMIN") == "1":
        return None
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return None
    return fail("`%s` needs root (or MINIOS_UNSAFE_ALLOW_ADMIN=1 on a machine you are "
                "developing on)" % path.replace(".", " "), EXIT_PRECONDITION)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "no_color", False):
        os.environ["NO_COLOR"] = "1"

    path = resolve_path(args)
    if not path:
        return cmd_help(argparse.Namespace(command=None, format="table"))

    # Reserved name, deliberately not `path`: `minios policy check <path>` has a
    # positional called `path`, and storing the command path under the same
    # attribute silently replaces the user's argument with the command's own
    # name.  Nothing errors; the command just answers about a different file.
    args.cmd_path = path
    command = next((c for c in COMMANDS if c.path == path), None)
    if command is None:
        return fail("unknown command: %s" % path.replace(".", " "), EXIT_USAGE)

    if command.admin:
        gate = require_admin(path)
        if gate is not None:
            return gate

    handler = HANDLERS.get(path)
    if handler is None:
        return unimplemented(path, command.phase, args)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
