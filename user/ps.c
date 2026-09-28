// ps: list the live processes, using the psinfo() system call.
//
// A snapshot of the whole process table is fetched into a static buffer
// (not the stack, which is only one page) and then printed.

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/psinfo.h"
#include "user/user.h"

static struct psinfo psinfos[NPROC];

static char *states[] = {
  // clang-format off
    [PSTATE_UNUSED]   = "unused",
    [PSTATE_USED]     = "used",
    [PSTATE_SLEEPING] = "sleep",
    [PSTATE_RUNNABLE] = "runble",
    [PSTATE_RUNNING]  = "run",
    [PSTATE_ZOMBIE]   = "zombie",
  // clang-format on
};

int
main(int argc, char *argv[])
{
  int n, i;
  char *state;

  n = psinfo(psinfos, NPROC);
  if (n < 0) {
    fprintf(2, "ps: psinfo failed\n");
    exit(1);
  }

  printf("pid  ppid state  vsz  rss  usr sys name\n");
  for (i = 0; i < n; i++) {
    struct psinfo *pi = &psinfos[i];
    if (pi->state >= 0 && pi->state < sizeof(states) / sizeof(states[0]) &&
        states[pi->state])
      state = states[pi->state];
    else
      state = "???";
    // vsz is what sbrk handed out, rss is what actually got mapped; with
    // lazy allocation the two drift apart until the pages are touched.
    printf("%d  %d  %s  %d  %ld  %d  %d  %s\n", pi->pid, pi->ppid, state,
           (int)(pi->sz / 1024), pi->rss / 1024, (int)pi->u_ticks,
           (int)pi->k_ticks, pi->name);
  }
  printf("(vsz/rss are KB, usr/sys are timer ticks, 1 tick = 100 ms)\n");

  exit(0);
}
