// waitxtest: verify that a parent can reclaim a child's CPU time.
//
// Two independent paths read the same two counters for the same
// process: the child measures itself with psinfo() and reports back
// over a pipe, and the parent collects that process's accounting with
// waitx().  They must agree to within a tick or two -- and only in one
// direction, because kexit() freezes the counters slightly after the
// child read them, so the parent may see a little more, never less.

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/psinfo.h"
#include "user/user.h"

#define TOLERANCE 4

static struct psinfo procs[NPROC];

// Fetch our own row out of a fresh psinfo snapshot.
static void
self(uint64 *u, uint64 *k)
{
  int n, i, pid = getpid();

  n = psinfo(procs, NPROC);
  if (n < 0) {
    fprintf(2, "waitxtest: psinfo failed\n");
    exit(1);
  }
  for (i = 0; i < n; i++) {
    if (procs[i].pid == pid) {
      *u = procs[i].u_ticks;
      *k = procs[i].k_ticks;
      return;
    }
  }
  fprintf(2, "waitxtest: own pid %d not found\n", pid);
  exit(1);
}

// pipe() transfers are not guaranteed to be whole, so loop.
static int
readall(int fd, char *buf, int n)
{
  int got = 0, r;

  while (got < n) {
    r = read(fd, buf + got, n - got);
    if (r <= 0)
      return -1;
    got += r;
  }
  return 0;
}

int
main(int argc, char *argv[])
{
  int fds[2], pid, status = 0;
  uint64 su = 0, sk = 0; // what the child saw for itself
  uint64 pu = 0, pk = 0; // what waitx handed the parent
  uint t0;
  int want = 5, i, ok = 1;
  volatile int sink = 0;

  if (argc > 1)
    want = atoi(argv[1]);
  if (want < 1)
    want = 1;

  if (pipe(fds) < 0) {
    fprintf(2, "waitxtest: pipe failed\n");
    exit(1);
  }

  if ((pid = fork()) < 0) {
    fprintf(2, "waitxtest: fork failed\n");
    exit(1);
  }

  if (pid == 0) {
    close(fds[0]);

    // Burn user time in batches so the loop body itself makes no
    // syscalls; otherwise the child's own polling shows up as system
    // time and there is nothing left to compare against.
    t0 = uptime();
    while ((int)(uptime() - t0) < want)
      for (i = 0; i < 5000000; i++)
        sink++;

    self(&su, &sk);
    write(fds[1], (char *)&su, sizeof(su));
    write(fds[1], (char *)&sk, sizeof(sk));
    close(fds[1]);
    exit(sink == 42 ? 1 : 0);
  }

  close(fds[1]);
  if (readall(fds[0], (char *)&su, sizeof(su)) < 0 ||
      readall(fds[0], (char *)&sk, sizeof(sk)) < 0) {
    fprintf(2, "waitxtest: read from child failed\n");
    exit(1);
  }
  close(fds[0]);

  if ((pid = waitx(&status, &pu, &pk)) < 0) {
    fprintf(2, "waitxtest: waitx failed\n");
    exit(1);
  }

  printf("waitxtest: reaped child pid %d, status %d\n", pid, status);
  printf("waitxtest: child  saw user %ld sys %ld\n", su, sk);
  printf("waitxtest: parent got user %ld sys %ld\n", pu, pk);
  printf("waitxtest: delta  user %ld sys %ld\n", pu - su, pk - sk);

  // Only the parent's view may run ahead, for the reason above.
  if (pu < su || pk < sk || pu - su > TOLERANCE || pk - sk > TOLERANCE) {
    printf("waitxtest:   MISMATCH (waitx must match the child's view)\n");
    ok = 0;
  }
  if (status != 0 || pu + TOLERANCE < (uint64)want) {
    printf("waitxtest:   MISMATCH (expected approximately %d user ticks)\n",
           want);
    ok = 0;
  }

  printf("waitxtest: %s\n", ok ? "OK" : "FAILED");
  exit(ok ? 0 : 1);
}
