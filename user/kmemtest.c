// kmemtest: check the physical allocator's bookkeeping, and look for leaks.
//
// The per-page state table added in this batch turns a double free into a
// panic, which cannot be exercised from user space (it would take the whole
// system down with it).  What can be checked from here are the two accounting
// claims that surround the table:
//
//   1. Conservation across two independent paths.  mem_free comes from the
//      O(1) nfree counter, while pages_live comes from alloc_calls minus
//      free_calls.  Their sum, in bytes, is a constant -- nfree + live is
//      always the number of pages past the end of the kernel image -- so it
//      must be unchanged before and after allocating.
//   2. No leak.  A child that allocates and then exits must hand every page
//      back, so pages_live returns to its baseline instead of creeping up.
//
// Note that pages_total is every page in RAM, not every page the allocator
// manages: the pages holding the kernel image are never on the free list.

#include "kernel/types.h"
#include "kernel/sysinfo.h"
#include "user/user.h"

#define PGSIZE 4096 // not exported to user space; see memlayout.h in the kernel
#define ROUNDS 3
#define CHILDREN  5
#define MEMPAGES  64
#define TOLERANCE 4 // pages; other processes allocate a little while we look

static struct sysinfo si;

static void
snap(char *what)
{
  if (sysinfo(&si) < 0) {
    fprintf(2, "kmemtest: sysinfo failed (%s)\n", what);
    exit(1);
  }
}

// nfree-as-bytes plus live-as-bytes: constant if the two paths agree.
static uint64
conserved(void)
{
  return si.mem_free + si.pages_live * PGSIZE;
}

int
main(int argc, char *argv[])
{
  int i, j, pid, status;
  uint64 base, begin, now;

  snap("start");
  printf("kmemtest: pages total %ld, live %ld, alloc calls %ld\n",
         si.pages_total, si.pages_live, si.alloc_calls);
  printf("kmemtest: mem total %ld KB, free %ld KB\n", si.mem_total / 1024,
         si.mem_free / 1024);

  if (si.pages_live > si.pages_total) {
    printf("kmemtest: FAIL live pages (%ld) exceed total (%ld)\n",
           si.pages_live, si.pages_total);
    exit(1);
  }

  begin = conserved();
  printf("kmemtest: conserved quantity is %ld bytes (free %ld pages + live)\n",
         begin, si.mem_free / PGSIZE);

  // 1. Allocation must not change the conserved quantity.
  pid = fork();
  if (pid == 0) {
    if (sbrk(MEMPAGES * PGSIZE) == (char *)-1) {
      fprintf(2, "kmemtest: child sbrk failed\n");
      exit(1);
    }
    snap("child");
    if (conserved() != begin) {
      printf("kmemtest: FAIL conserved quantity moved while allocating\n");
      exit(1);
    }
    exit(0);
  }
  if (pid < 0) {
    fprintf(2, "kmemtest: fork failed\n");
    exit(1);
  }
  wait(&status);
  if (status != 0) {
    printf("kmemtest: FAIL allocation check failed in child\n");
    exit(1);
  }

  snap("after allocation check");
  now = conserved();
  if (now != begin) {
    printf("kmemtest: FAIL conserved quantity moved by %ld bytes\n",
           (long)(now - begin));
    exit(1);
  }
  printf("kmemtest: counters agree, %d pages allocated and returned\n",
         MEMPAGES);

  // 2. Leak check: pages must come back when a child exits.
  for (i = 0; i < ROUNDS; i++) {
    snap("round start");
    base = si.pages_live;
    for (j = 0; j < CHILDREN; j++) {
      pid = fork();
      if (pid == 0) {
        if (sbrk(MEMPAGES * PGSIZE) == (char *)-1) {
          fprintf(2, "kmemtest: child sbrk failed\n");
          exit(1);
        }
        exit(0);
      }
      if (pid < 0) {
        fprintf(2, "kmemtest: fork failed\n");
        exit(1);
      }
      wait(&status);
    }
    snap("round end");
    printf("kmemtest: round %d: live %ld -> %ld after %d children\n", i + 1,
           base, si.pages_live, CHILDREN);
    if (si.pages_live > base + TOLERANCE) {
      printf("kmemtest: FAIL %ld pages leaked in round %d\n",
             si.pages_live - base, i + 1);
      exit(1);
    }
  }

  printf("kmemtest: OK (conserved, no leak over %d rounds)\n", ROUNDS);
  exit(0);
}
