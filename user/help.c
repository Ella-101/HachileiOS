// help: list the commands available in this miniOS image.
//
// xv6's shell has no built-in commands -- every command is a separate
// program sitting in the root directory -- so making them discoverable
// means adding one more program.  `ls' already lists the names, but says
// nothing about what they do or which ones this project added.
//
// Layout deliberately avoids printf width specifiers (xv6's printf does
// not implement them), so nothing here depends on column alignment.

#include "kernel/types.h"
#include "user/user.h"

struct entry {
  char *group; // printed once, when it changes
  char *name;
  char *args; // "" if it takes none
  char *desc;
  char *extra; // optional detail shown by `help <name>'
  int minios;  // 1 = added by this project, 0 = stock xv6
};

static struct entry cmds[] = {
  // clang-format off
  {"system", "init", "",
   "first user process; started by the kernel, then runs sh",
   "Not meant to be run by hand.", 0},
  {"system", "sh", "",
   "the shell itself; supports ; | & < > >> ( )",
   "Background: `cmd &'.  Redirect: `cmd > f', `cmd >> f', `cmd < f'.",
   0},

  {"files & text", "cat", "[file ...]",
   "concatenate files and print; reads stdin if none", 0, 0},
  {"files & text", "ls", "[path ...]",
   "list directory contents", 0, 0},
  {"files & text", "mkdir", "dir ...",
   "create directories", 0, 0},
  {"files & text", "rm", "path ...",
   "remove files and directories", 0, 0},
  {"files & text", "ln", "target linkname",
   "create a hard link", 0, 0},
  {"files & text", "echo", "[text ...]",
   "print its arguments to stdout", 0, 0},
  {"files & text", "grep", "pattern [file ...]",
   "print matching lines; supports ^ . * $ only",
   "Reads stdin when no file is given.", 0},
  {"files & text", "wc", "[file]",
   "count lines, words and bytes; reads stdin if none", 0, 0},

  {"processes", "ps", "",
   "snapshot the process table",
   "Columns: pid ppid state vsz rss usr sys prio name; vsz/rss in KB.", 1},
  {"processes", "top", "[refreshes]",
   "refresh the process table periodically",
   // Adjacent string literals concatenate, so this is still one
   // field even though it is written on two lines.
   "Also shows dcpu/busy% since the last refresh; defaults to 10 "
   "refreshes and exits, because xv6 has no signals so it cannot "
   "run forever.", 1},
  {"processes", "kill", "pid",
   "ask a process to exit",
   "Only sets a flag that the target checks at its next syscall.", 0},

  {"system info", "free", "",
   "physical memory: total, used and free",
   "Printed in bytes, KB and pages.", 1},
  {"system info", "df", "",
   "file system block and inode usage",
   "Prints a whole-image and a data-block view; both share one free count.",
   1},
  {"system info", "neofetch", "",
   "one-shot system overview",
   "Reports the online hart count, disk I/O and buffer-cache hits.", 1},
  {"system info", "dmesg", "",
   "replay the kernel log ring buffer",
   "Only what printk() emitted; user output is not captured.", 1},

  {"self-checks", "cputest", "[ticks]",
   "cross-check CPU accounting against uptime()",
   "Defaults to 20 ticks.  Passes when user + sys == elapsed.", 1},
  {"self-checks", "waitxtest", "[ticks]",
   "cross-check waitx() against the child's own psinfo()",
   "Defaults to 5 ticks.  The parent may see more, never less.", 1},
  {"self-checks", "priotest", "[ticks]",
   "check that the scheduler honours priorities",
   "Defaults to 20 ticks.  Also checks that aging prevents starvation.", 1},

  {"tests", "usertests", "[-q|-c|-C|testname]",
   "the full regression suite",
   "-q runs the quick set; a bare name runs just that test.", 0},
  {"tests", "forktest", "",
   "check that fork fails gracefully when the table is full", 0, 0},
  {"tests", "zombie", "",
   "create a zombie that must be reparented at exit", 0, 0},
  {"tests", "grind", "",
   "run random system calls in parallel, forever",
   "No arguments and no exit; kill it or reboot.", 0},
  {"tests", "stressfs", "",
   "hammer the log with concurrent writes to one directory", 0, 0},
  {"tests", "logstress", "file ...",
   "each of several processes writes its own file",
   "e.g. logstress f0 f1 f2 f3", 0},
  {"tests", "forphan", "",
   "create an orphaned file for test-xv6.py to recover", 0, 0},
  {"tests", "dorphan", "",
   "create an orphaned directory for test-xv6.py to recover", 0, 0},
  {"tests", "sync", "",
   "flush the log to disk", 0, 0},
  // clang-format on
};

int
main(int argc, char *argv[])
{
  int i, n = sizeof(cmds) / sizeof(cmds[0]);
  int nminios = 0;
  char *group = 0;

  for (i = 0; i < n; i++)
    if (cmds[i].minios)
      nminios++;

  // help <command>: a single entry, with the detail lines.
  if (argc > 1) {
    for (i = 0; i < n; i++) {
      if (strcmp(cmds[i].name, argv[1]) == 0) {
        printf("%s %s\n", cmds[i].name, cmds[i].args);
        printf("  %s\n", cmds[i].desc);
        printf("  %s\n", cmds[i].minios ? "added by miniOS" : "stock xv6");
        if (cmds[i].extra)
          printf("  %s\n", cmds[i].extra);
        exit(0);
      }
    }
    fprintf(2, "help: no such command: %s\n", argv[1]);
    exit(1);
  }

  printf("miniOS help: %d commands (%d stock xv6, %d added by miniOS)\n", n,
         n - nminios, nminios);
  printf("entries marked * are added by this project\n");
  printf("run 'help <command>' for a single entry\n\n");

  for (i = 0; i < n; i++) {
    if (group == 0 || strcmp(group, cmds[i].group) != 0) {
      group = cmds[i].group;
      printf("%s\n", group);
    }
    printf("  %s%s%s%s - %s\n", cmds[i].minios ? "* " : "  ", cmds[i].name,
           cmds[i].args[0] ? " " : "", cmds[i].args, cmds[i].desc);
  }

  exit(0);
}
