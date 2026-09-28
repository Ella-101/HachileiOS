// top: periodically print the process table plus a memory/uptime
// summary and per-process CPU time.
//
// xv6 has no signals, so a user program cannot be interrupted from the
// shell; instead of looping forever, top refreshes a bounded number of
// times (default 10, `top [n]` to change) and exits.
//
// CPU time is in timer ticks (10 Hz, so 1 tick = 100 ms).  Cumulative
// usr/sys are not very interesting on their own, so we also keep the
// previous snapshot and show the delta since the last refresh, which is
// what actually tells you who is consuming the CPU right now.
//
// Note this is sampling: the kernel charges a tick to whoever it
// interrupted, so a process that burns less than one tick (100 ms) at a
// stretch may show up as 0.

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/psinfo.h"
#include "user/user.h"

static struct psinfo procs[NPROC];
static struct psinfo prev[NPROC];
static int prev_n;
static uint prev_up;

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

static char *
statename(int s)
{
  if (s >= 0 && s < sizeof(states) / sizeof(states[0]) && states[s])
    return states[s];
  return "???";
}

// Find p's previous row by pid.  Pids can appear and disappear between
// refreshes, and a pid can even be recycled onto a new process (which
// restarts the counters at 0), so a missing match -- or counters that
// went backwards -- means "no usable baseline".
static int
findprev(int pid, uint64 u, uint64 k, struct psinfo *old)
{
  int i;

  for (i = 0; i < prev_n; i++) {
    if (prev[i].pid == pid) {
      if (u < prev[i].u_ticks || k < prev[i].k_ticks)
        return -1; // pid was reused by a new process
      *old = prev[i];
      return 0;
    }
  }
  return -1;
}

int
main(int argc, char *argv[])
{
  int iters = 10, it, i, n;
  uint up, elapsed;

  if (argc > 1)
    iters = atoi(argv[1]);
  if (iters < 1)
    iters = 1;

  prev_n = 0;
  prev_up = 0;

  for (it = 0; it < iters; it++) {
    n = psinfo(procs, NPROC);
    if (n < 0) {
      fprintf(2, "top: psinfo failed\n");
      exit(1);
    }
    up = uptime();
    elapsed = (prev_up == 0) ? 0 : up - prev_up;

    // ANSI clear screen + cursor home, so each refresh overwrites the
    // previous one.
    printf("\033[2J\033[H");
    printf("miniOS top   refresh %d/%d   uptime %d ticks   free %ld bytes\n",
           it + 1, iters, up, freemem());
    printf("pid  ppid state  vsz  rss  usr sys  dcpu  busy%%  name\n");
    for (i = 0; i < n; i++) {
      struct psinfo old;
      uint64 du, dk;
      int have;

      have = (elapsed != 0) && (findprev(procs[i].pid, procs[i].u_ticks,
                                         procs[i].k_ticks, &old) == 0);
      du = have ? procs[i].u_ticks - old.u_ticks : 0;
      dk = have ? procs[i].k_ticks - old.k_ticks : 0;

      printf("%d  %d  %s  %d  %ld  %d  %d  ", procs[i].pid, procs[i].ppid,
             statename(procs[i].state), (int)(procs[i].sz / 1024),
             procs[i].rss / 1024, (int)procs[i].u_ticks,
             (int)procs[i].k_ticks);
      if (have)
        printf("%d  %d  %s\n", (int)(du + dk), (int)((du + dk) * 100 / elapsed),
               procs[i].name);
      else
        printf("-  -  %s\n", procs[i].name);
    }
    printf("(vsz/rss are KB, usr/sys/dcpu are timer ticks, 1 tick = 100 ms)\n");

    // Current snapshot becomes the baseline for the next refresh.
    for (i = 0; i < n; i++)
      prev[i] = procs[i];
    prev_n = n;
    prev_up = up;

    pause(10); // about one second
  }

  printf("\033[2J\033[H");
  exit(0);
}
