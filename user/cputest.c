// cputest: cross-check per-process CPU time accounting against the
// global uptime clock.
//
// The kernel charges one timer tick to whichever process it interrupted,
// splitting user from supervisor time by sstatus.SPP.  We verify both
// halves from user space:
//
//   phase 1 (user-heavy): spin in user mode, almost no syscalls
//                         -> user ticks should track elapsed
//   phase 2 (sys-heavy):  hammer the heavy psinfo() syscall
//                         -> sys ticks should dominate elapsed
//
// uptime() counts ticks on hart 0 only, while we may be scheduled on any
// hart, so a little skew is expected -- the point is to print the
// discrepancy, not to hide it.

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
    fprintf(2, "cputest: psinfo failed\n");
    exit(1);
  }
  for (i = 0; i < n; i++) {
    if (procs[i].pid == pid) {
      *u = procs[i].u_ticks;
      *k = procs[i].k_ticks;
      return;
    }
  }
  fprintf(2, "cputest: own pid %d not found\n", pid);
  exit(1);
}

static void
report(char *what, uint elapsed, uint64 du, uint64 dk)
{
  printf("cputest: %s: elapsed %d ticks, user %ld, sys %ld\n", what, elapsed,
         du, dk);
}

int
main(int argc, char *argv[])
{
  uint t0, t1, elapsed;
  uint64 u0, k0, u1, k1, du, dk;
  int need = 20;
  volatile int sink = 0;
  int i, ok = 1;

  if (argc > 1)
    need = atoi(argv[1]);
  if (need < 1)
    need = 1;

  // ---- phase 1: user time --------------------------------------------
  self(&u0, &k0);
  t0 = uptime();

  // Spin in batches so the loop body itself makes no syscalls.  Batching
  // matters: calling uptime() every iteration would charge a large slice
  // of the window to system time and defeat the point.  The batch is
  // large enough that a handful of uptime() calls cost ~nothing.
  do {
    for (i = 0; i < 5000000; i++)
      sink++;
    t1 = uptime();
  } while ((int)(t1 - t0) < need);

  self(&u1, &k1);
  elapsed = t1 - t0;
  du = u1 - u0;
  dk = k1 - k0;
  report("user-heavy", elapsed, du, dk);
  printf("cputest:   user - elapsed = %ld\n", (long)du - (long)elapsed);
  if (du + TOLERANCE < elapsed || du > elapsed + TOLERANCE) {
    printf("cputest:   MISMATCH (expected user ~= elapsed)\n");
    ok = 0;
  }

  // ---- phase 2: system time -----------------------------------------
  self(&u0, &k0);
  t0 = uptime();

  // Every iteration is a syscall, so most of the window is spent
  // executing in the kernel and must be charged as system time.  We use
  // psinfo() rather than uptime() because it does real kernel work
  // (kalloc, walk the proc table, copy a page out); uptime() is so cheap
  // that the user-mode loop overhead around it is a comparable fraction
  // of the window, which shows up as user time.
  //
  // Note a user program can never reach 100% system time: the loop that
  // issues the syscalls is itself user code, so some user ticks always
  // remain.  The honest check is that sys dominates.
  do {
    for (i = 0; i < 500; i++)
      (void)psinfo(procs, NPROC);
    t1 = uptime();
  } while ((int)(t1 - t0) < need);

  self(&u1, &k1);
  elapsed = t1 - t0;
  du = u1 - u0;
  dk = k1 - k0;
  report("sys-heavy ", elapsed, du, dk);
  printf("cputest:   sys = %ld%% of elapsed\n",
         elapsed ? (long)(dk * 100 / elapsed) : 0L);
  if (dk <= du || dk * 2 < elapsed) {
    printf("cputest:   MISMATCH (expected sys to dominate)\n");
    ok = 0;
  }

  printf("cputest: %s\n", ok ? "OK" : "FAILED");

  if (sink == 42)
    printf("cputest: impossible\n");

  exit(ok ? 0 : 1);
}
