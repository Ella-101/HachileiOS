// neofetch: a one-shot summary of the running system.

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/psinfo.h"
#include "kernel/sysinfo.h"
#include "user/user.h"

static struct psinfo procs[NPROC];

int
main(int argc, char *argv[])
{
  int n, i, nproc = 0;
  struct sysinfo si;

  n = psinfo(procs, NPROC);
  if (n < 0) {
    fprintf(2, "neofetch: psinfo failed\n");
    exit(1);
  }
  for (i = 0; i < n; i++)
    if (procs[i].state != PSTATE_UNUSED)
      nproc++;

  if (sysinfo(&si) < 0) {
    fprintf(2, "neofetch: sysinfo failed\n");
    exit(1);
  }

  printf("     +------------------------------+\n");
  printf("     |    m i n i O S   x v 6       |\n");
  printf("     |    riscv64  .  rv64gc        |\n");
  printf("     +------------------------------+\n");
  printf("\n");
  printf("   user      : user\n");
  printf("   os        : miniOS on xv6-riscv\n");
  printf("   arch      : riscv64 (rv64gc)\n");
  printf("   cpus      : %ld online of %ld max\n", si.ncpu_online, si.ncpu_max);
  printf("   memory    : %ld MB total, %ld KB free\n",
         si.mem_total / (1024 * 1024), si.mem_free / 1024);
  printf("   processes : %d\n", nproc);
  printf("   disk      : %ld block reads, %ld writes\n", si.disk_reads,
         si.disk_writes);
  printf("   bcache    : %ld hits, %ld misses\n", si.bcache_hits,
         si.bcache_misses);
  printf("   vmfaults  : %ld\n", si.vmfaults);
  printf("   uptime    : %d ticks\n", uptime());
  printf("\n");

  exit(0);
}
