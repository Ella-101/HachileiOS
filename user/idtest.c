// Identity rules: fork inherits, exec keeps, and only uid 0 may hand out an
// identity.
//
// Everything interesting happens *after* a process gives up its privilege,
// and that change is one-way, so the cases cannot all be probed from one
// process.  This test starts as root, forks, and lets the child demote
// itself; the parent stays root throughout and checks that the child's
// change is invisible to it.

#include "kernel/types.h"
#include "user/user.h"

#define UID_DEMOTED 1001
#define GID_DEMOTED 100
#define UID_OTHER   1002
#define GID_OTHER   101

static void
check(int ok, char *what)
{
  if (!ok) {
    fprintf(2, "idtest: FAIL %s\n", what);
    exit(1);
  }
}

// printf() emits one byte per write(2), so a pipe read may return a single
// byte.  Read until the writer closes instead of trusting one read's size.
static void
readall(int fd, char *buf, int cap)
{
  int n = 0;

  while (n < cap - 1) {
    int r = read(fd, buf + n, 1);
    if (r <= 0)
      break;
    n++;
  }
  buf[n] = 0;
}

// Return the number that follows `key' in a line of text, or -1 if the key
// is absent or is not followed by a digit.
static int
field(char *s, char *key)
{
  int i, v;

  for (; *s; s++) {
    for (i = 0; key[i] && s[i] == key[i]; i++)
      ;
    if (key[i] != 0)
      continue;
    if (s[i] < '0' || s[i] > '9')
      return -1;
    for (v = 0; s[i] >= '0' && s[i] <= '9'; i++)
      v = v * 10 + (s[i] - '0');
    return v;
  }
  return -1;
}

int
main(void)
{
  char buf[64];
  int gid_pipe[2], status, pid, g;

  // The negative cases below need a privileged parent, so a non-root run is
  // itself a failure rather than a skipped test.
  check(getuid() == 0 && getgid() == 0, "must start as root");

  pid = fork();
  check(pid >= 0, "fork");

  if (pid == 0) {
    // ---- child: demote, then probe what a non-root process may do ----
    check(getuid() == 0 && getgid() == 0, "fork inherits identity");

    // Group first.  ksetuid() is one-way, and a non-root process can no
    // longer change its group afterwards.
    check(setgid(GID_DEMOTED) == 0, "setgid while root");
    check(setuid(UID_DEMOTED) == 0, "setuid while root");
    check(getuid() == UID_DEMOTED, "uid after demotion");
    check(getgid() == GID_DEMOTED, "gid after demotion");

    // One way only: there is no saved-uid and no setuid bit to return by.
    check(setuid(0) == -1, "cannot regain root");
    check(setuid(UID_OTHER) == -1, "cannot adopt another uid");
    check(setgid(GID_OTHER) == -1, "cannot adopt another gid");

    // The no-op form stays legal, so a program can call it unconditionally
    // instead of branching on "am I root".
    check(setuid(UID_DEMOTED) == 0, "setuid to own uid");
    check(setgid(GID_DEMOTED) == 0, "setgid to own gid");

    // Bounds are rejected before the privilege rule is consulted.
    check(setuid(70000) == -1, "uid above 65535");
    check(setuid(-1) == -1, "negative uid");

    // A grandchild must see the demoted identity, and must still see it
    // after exec(): kexec() replaces the address space, not struct proc.
    check(pipe(gid_pipe) == 0, "pipe");
    g = fork();
    check(g >= 0, "fork");
    if (g == 0) {
      char *argv[] = {"id", 0};

      close(gid_pipe[0]);
      close(1);
      dup(gid_pipe[1]);
      close(gid_pipe[1]);
      exec("id", argv);
      fprintf(2, "idtest: FAIL exec id\n");
      exit(1);
    }
    close(gid_pipe[1]);
    readall(gid_pipe[0], buf, sizeof(buf));
    close(gid_pipe[0]);
    check(wait(&status) == g && status == 0, "grandchild status");
    check(field(buf, "uid=") == UID_DEMOTED, "exec keeps uid");
    check(field(buf, "gid=") == GID_DEMOTED, "exec keeps gid");
    exit(0);
  }

  // ---- parent: unchanged by anything the child did to itself ----
  check(wait(&status) == pid && status == 0, "child status");
  check(getuid() == 0 && getgid() == 0, "parent still root");

  printf("idtest: OK\n");
  exit(0);
}
