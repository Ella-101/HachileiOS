// Over-subscribe all online CPUs; compare aggregate CPU time, not wall speed.
#include "kernel/types.h"
#include "kernel/param.h"
#include "user/user.h"

static void
check(int ok, char *what)
{
  if (!ok) {
    fprintf(2, "priotest: FAIL %s\n", what);
    exit(1);
  }
}

int
main(int argc, char **argv)
{
  struct sysinfo si;
  int ready[2], gate[2], pids[2 * NCPU], status;
  uint64 high = 0, low = 0;
  int ticks = argc > 1 ? atoi(argv[1]) : 60;
  if (ticks < 40)
    ticks = 40;
  check(sysinfo(&si) == 0, "sysinfo");
  int count = 2 * si.ncpu_online;
  check(count >= 2 && count <= 2 * NCPU, "cpu count");
  check(pipe(ready) == 0 && pipe(gate) == 0, "pipe");
  for (int i = 0; i < count; i++) {
    pids[i] = fork();
    check(pids[i] >= 0, "fork");
    if (pids[i] == 0) {
      close(ready[0]);
      close(gate[1]);
      check(setprio(getpid(), i % 2 ? PRIO_LOWEST : PRIO_HIGHEST) == 0,
            "setprio");
      check(write(ready[1], "r", 1) == 1, "ready");
      close(ready[1]);
      int end;
      check(read(gate[0], &end, sizeof(end)) == sizeof(end), "gate");
      close(gate[0]);
      volatile int sink = 0;
      while (uptime() < end)
        for (int j = 0; j < 100000; j++)
          sink++;
      // Read the counter before leaving.  A local that is only ever written
      // makes -Wunused-but-set-variable fire, and this build hands -Werror to
      // the compiler, so on some toolchains the child would not build at all.
      // sink advances in steps of 100000, so the branch is never taken; it is
      // the same impossible-value guard cputest.c and waitxtest.c already use.
      if (sink == 42)
        printf("priotest: impossible\n");
      exit(0);
    }
  }
  close(ready[1]);
  close(gate[0]);
  for (int i = 0; i < count; i++) {
    char c;
    check(read(ready[0], &c, 1) == 1, "ready read");
  }
  close(ready[0]);
  int end = uptime() + ticks;
  for (int i = 0; i < count; i++)
    check(write(gate[1], &end, sizeof(end)) == sizeof(end), "release");
  close(gate[1]);
  for (int i = 0; i < count; i++) {
    uint64 u, k;
    int pid = waitx(&status, &u, &k), slot;
    check(pid > 0 && status == 0, "waitx");
    for (slot = 0; slot < count && pids[slot] != pid; slot++)
      ;
    check(slot < count, "unexpected child");
    check(u + k > 0, "starvation");
    if (slot % 2)
      low += u + k;
    else
      high += u + k;
  }
  printf("priotest: %d workers, urgent %ld slack %ld ticks\n", count, high,
         low);
  check(high > low, "urgent group did not get more CPU");
  printf("priotest: OK\n");
  exit(0);
}
