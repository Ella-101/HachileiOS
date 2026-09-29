// priotest: check that the scheduler honours priorities, and that the aging
// pass keeps a low-priority process from starving.
//
// Two children spin in user space for the same number of timer ticks.  The
// parent gives one the most urgent priority and the other the least urgent,
// drops itself to the slack end so it does not compete, and then compares the
// two children's CPU time with waitx().  The urgent child must come out
// clearly ahead; the slack child must still get CPU, or aging is broken.
//
// Note that kfork() copies fields individually rather than the whole proc
// struct, so a new process starts at PRIO_DEFAULT instead of inheriting its
// parent's priority -- which is why each child sets its own here.

#include "kernel/types.h"
#include "kernel/psinfo.h"
#include "user/user.h"

#define SETTLE 3 // ticks both children wait so they start together

static void
spin(int ticks)
{
  int start = uptime();
  while (uptime() - start < ticks)
    ;
}

int
main(int argc, char *argv[])
{
  int ticks = 20;
  int i, pid, status, high, low;
  uint64 uh = 0, ul = 0, kh = 0, kl = 0;

  if (argc > 1)
    ticks = atoi(argv[1]);
  if (ticks < 5)
    ticks = 5;

  high = fork();
  if (high == 0) {
    setprio(getpid(), PRIO_HIGHEST);
    pause(SETTLE);
    spin(ticks);
    exit(0);
  }

  low = fork();
  if (low == 0) {
    setprio(getpid(), PRIO_LOWEST);
    pause(SETTLE);
    spin(ticks);
    exit(0);
  }

  if (high < 0 || low < 0) {
    fprintf(2, "priotest: fork failed\n");
    exit(1);
  }

  // Get out of the way: the parent must not compete for the CPU it is
  // trying to measure.
  setprio(getpid(), PRIO_LOWEST);
  pause(SETTLE + ticks + 5);

  for (i = 0; i < 2; i++) {
    uint64 u = 0, k = 0;
    pid = waitx(&status, &u, &k);
    if (pid == high) {
      uh = u;
      kh = k;
    } else if (pid == low) {
      ul = u;
      kl = k;
    }
  }

  printf("priotest: urgent (prio %d): user %ld sys %ld\n", PRIO_HIGHEST, uh,
         kh);
  printf("priotest: slack  (prio %d): user %ld sys %ld\n", PRIO_LOWEST, ul, kl);

  if (uh <= ul) {
    printf("priotest: FAIL urgent process did not get more CPU\n");
    exit(1);
  }
  if (ul == 0) {
    printf("priotest: FAIL slack process starved; aging is not working\n");
    exit(1);
  }

  printf("priotest: OK (urgent %ld vs slack %ld ticks)\n", uh, ul);
  exit(0);
}
