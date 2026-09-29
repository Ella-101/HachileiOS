// cowtest: check that fork() shares pages until one side writes to them.
//
// Copy-on-write fork makes two promises that can both be checked from user
// space, using instrumentation this project already had: the allocator's
// free-page count and the page reference counts, both exported by sysinfo().
//
//   1. A fork must cost page tables and a kernel stack, not a copy of the
//      parent's memory.  mem_free is measured while the child is provably
//      alive, so a fork that still copied would show up immediately.
//   2. The sharing must be invisible.  Once either side writes, neither may
//      see the other's bytes -- including when the writer is the kernel,
//      because copyout() writes into user memory too.
//
// The reference count is checked against its own reference implementation at
// every step: pages_shared is the O(1) counter, pages_shared_ref counts the
// same pages by walking the table, and sysinfo() produces both from one
// instant so that comparing them means something.
//
// cow_faults counts the store faults that were resolved, and cow_copies the
// subset that actually had to copy a page.  The two diverge whenever the
// other side of a fork has already exited: a page with a single reference
// left needs no copy, and one of the checks below is that this really does
// save the copy.

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/sysinfo.h"
#include "user/user.h"

#define PGSIZE 4096 // not exported to user space; see memlayout.h in the kernel

// Resident pages the parent brings in, so a fork has something to share.
#define NPAGES 32
// What a bare fork() may cost: a top-level table, an L0 table, a kernel stack
// and a trapframe.  Anything near NPAGES would mean the pages were copied.
#define FORK_MAX_PAGES 16
#define TOLERANCE      8 // pages; the shell and the console may allocate a little

static struct sysinfo si;
static char *area;   // NPAGES pages, touched so that they are resident
static char *shared; // one page of its own; see copyoutcow()

static void
fail(char *what)
{
  printf("cowtest: FAIL %s\n", what);
  exit(1);
}

// Snapshot, and check the O(1) counter against the O(N) walk.
static void
snap(char *what)
{
  if (sysinfo(&si) < 0)
    fail("sysinfo failed");
  if (si.pages_shared != si.pages_shared_ref) {
    printf("cowtest: FAIL shared pages: counter %ld, table walk %ld (%s)\n",
           si.pages_shared, si.pages_shared_ref, what);
    exit(1);
  }
}

static void
fill(char *p, int n, int c)
{
  for (int i = 0; i < n; i++)
    p[i] = c;
}

static void
check(char *p, int n, int c, char *what)
{
  for (int i = 0; i < n; i++)
    if (p[i] != c) {
      printf("cowtest: FAIL byte %d of %d is %d, wanted %d (%s)\n", i, n, p[i],
             c, what);
      exit(1);
    }
}

// 1. What a fork costs, and that it really shares.
//
// The child reports that it is running and then blocks on a second pipe until
// we answer, so the measurement below is taken while its mappings certainly
// exist.  It also calls sysinfo() itself, which writes into its own copy of
// `si` -- a page the fork shared -- so the kernel has to take a private copy.
static void
forkcost(void)
{
  int pid, status, up[2], down[2];
  char c;
  uint64 before, after, shared_before, shared_peak;

  snap("baseline");
  shared_before = si.pages_shared;
  before = si.mem_free;
  printf("cowtest: %d resident pages, %ld KB free before the fork\n", NPAGES,
         before / 1024);

  if (pipe(up) < 0 || pipe(down) < 0)
    fail("pipe failed");

  pid = fork();
  if (pid < 0)
    fail("fork failed");

  if (pid == 0) {
    close(up[0]);
    close(down[1]);
    if (sysinfo(&si) < 0)
      fail("sysinfo in child");
    if (si.mem_free == 0)
      fail("child saw no memory");
    if (write(up[1], "x", 1) != 1)
      fail("child could not report in");
    // Block until the parent has finished measuring, then die.
    if (read(down[0], &c, 1) != 1)
      fail("child could not be released");
    exit(0);
  }

  close(up[1]);
  close(down[0]);
  if (read(up[0], &c, 1) != 1)
    fail("parent did not hear from the child");

  snap("after fork");
  after = si.mem_free;
  shared_peak = si.pages_shared;

  if (before < after)
    fail("free memory grew across a fork");
  if ((before - after) / PGSIZE > FORK_MAX_PAGES) {
    printf("cowtest: FAIL fork cost %ld pages, more than %d -- pages copied?\n",
           (before - after) / PGSIZE, FORK_MAX_PAGES);
    exit(1);
  }
  if (shared_peak < shared_before + NPAGES) {
    printf("cowtest: FAIL only %ld pages shared, wanted at least %d more\n",
           shared_peak - shared_before, NPAGES);
    exit(1);
  }
  printf("cowtest: fork cost %ld pages for %d resident pages (%ld shared)\n",
         (before - after) / PGSIZE, NPAGES, shared_peak - shared_before);

  if (write(down[1], "x", 1) != 1)
    fail("could not release the child");
  wait(&status);
  if (status != 0)
    fail("child failed");

  snap("after the child exited");
  if (si.pages_shared != shared_before) {
    printf("cowtest: FAIL %ld pages still shared after the child exited\n",
           si.pages_shared - shared_before);
    exit(1);
  }
  printf("cowtest: child exited, shared count back to %ld\n", shared_before);
}

// 2. Writes must diverge, and each side must pay exactly one copy per page.
static void
writediverges(void)
{
  int pid, status;

  fill(area, NPAGES * PGSIZE, 'A');

  pid = fork();
  if (pid < 0)
    fail("fork failed");
  if (pid == 0) {
    uint64 f0, f1, c0, c1;

    if (sysinfo(&si) < 0)
      fail("sysinfo in writer");
    f0 = si.cow_faults;
    c0 = si.cow_copies;
    fill(area, NPAGES * PGSIZE, 'B');
    if (sysinfo(&si) < 0)
      fail("sysinfo in writer");
    f1 = si.cow_faults;
    c1 = si.cow_copies;

    check(area, NPAGES * PGSIZE, 'B', "child does not see its own writes");
    // At least one fault and one copy per shared page: fewer would mean a
    // store reached the parent's page without being copied first.  Here the
    // parent is still alive and still maps every one of them, so no page can
    // take the single-reference shortcut.
    if (f1 - f0 < NPAGES || c1 - c0 < NPAGES) {
      printf("cowtest: FAIL %ld faults, %ld copies, wanted %d of each\n",
             f1 - f0, c1 - c0, NPAGES);
      exit(1);
    }
    // The count can exceed NPAGES by one or two: the child's own stack and
    // data pages are shared too, and the first call after the fork touches
    // them, so a page of stack gets copied alongside the test area.
    printf("cowtest: child wrote %d pages using %ld copies\n", NPAGES, c1 - c0);
    exit(0);
  }
  wait(&status);
  if (status != 0)
    fail("writer child failed");

  check(area, NPAGES * PGSIZE, 'A', "parent saw the child's writes");
  printf("cowtest: parent's %d pages unchanged after the child wrote them\n",
         NPAGES);
}

// 3. The kernel is a writer too.  read() copies from the pipe into user
//    memory, so if copyout() ignored the shared page the bytes would land in
//    the parent's copy as well.  This child does nothing before reading, so
//    the page really is still shared when the copy happens.
//
//    The page is written by nobody before the fork, which is why the check
//    below is worth making: if it no longer holds 'P', something already
//    reached a page it did not own.
static void
copyoutcow(void)
{
  int pid, status, p[2];
  char *payload;

  check(shared, PGSIZE, 'P', "something wrote the shared page too early");

  if (pipe(p) < 0)
    fail("pipe failed");

  payload = sbrk(PGSIZE);
  if (payload == (char *)-1)
    fail("sbrk for the payload failed");
  fill(payload, PGSIZE, 'C');

  pid = fork();
  if (pid < 0)
    fail("fork failed");
  if (pid == 0) {
    int got = 0, n;
    uint64 f0, f1;

    close(p[1]);
    if (sysinfo(&si) < 0)
      fail("sysinfo in reader");
    f0 = si.cow_faults;
    // A pipe hands back only what its buffer holds -- 512 bytes here -- so
    // read in a loop rather than expecting one call to fill the page.  What
    // matters is that the kernel writes a whole page into a page this child
    // still shares, and the first byte of it is what forces the copy.
    while (got < PGSIZE) {
      if ((n = read(p[0], shared + got, PGSIZE - got)) <= 0)
        break;
      got += n;
    }
    if (got != PGSIZE)
      fail("read into a shared page failed");
    if (sysinfo(&si) < 0)
      fail("sysinfo in reader");
    f1 = si.cow_faults;

    check(shared, PGSIZE, 'C', "the child did not get the copied bytes");
    if (f1 == f0)
      fail("copyout wrote through without taking a copy");
    exit(0);
  }

  close(p[0]);
  if (write(p[1], payload, PGSIZE) != PGSIZE)
    fail("parent write to the pipe failed");

  wait(&status);
  if (status != 0)
    fail("reader child failed");

  check(shared, PGSIZE, 'P', "copyout wrote into the parent's page");
  printf("cowtest: copyout kept the parent's page private\n");
}

// 4. A page can be shared by more than two processes.  Here the grandchild
//    writes every page; both of its ancestors must still see their own bytes.
static void
nested(void)
{
  int pid, status, gpid, gstatus;

  fill(area, NPAGES * PGSIZE, 'A');

  pid = fork();
  if (pid < 0)
    fail("fork failed");
  if (pid == 0) {
    gpid = fork();
    if (gpid < 0)
      fail("fork failed in the child");
    if (gpid == 0) {
      fill(area, NPAGES * PGSIZE, 'D');
      check(area, NPAGES * PGSIZE, 'D', "grandchild does not see its writes");
      exit(0);
    }
    wait(&gstatus);
    if (gstatus != 0)
      fail("grandchild failed");
    check(area, NPAGES * PGSIZE, 'A', "child saw the grandchild's writes");
    exit(0);
  }
  wait(&status);
  if (status != 0)
    fail("child failed");

  check(area, NPAGES * PGSIZE, 'A', "parent saw the grandchild's writes");
  printf("cowtest: three generations stayed isolated\n");
}

int
main(int argc, char *argv[])
{
  uint64 base_free, base_shared, base_refs, base_faults, base_copies;

  area = sbrk(NPAGES * PGSIZE);
  if (area == (char *)-1)
    fail("sbrk for the test area failed");
  shared = sbrk(PGSIZE);
  if (shared == (char *)-1)
    fail("sbrk for the shared page failed");
  fill(area, NPAGES * PGSIZE, 'A');
  fill(shared, PGSIZE, 'P');

  snap("start");
  base_free = si.mem_free;
  base_shared = si.pages_shared;
  base_refs = si.kref_calls;
  base_faults = si.cow_faults;
  base_copies = si.cow_copies;
  printf("cowtest: pages total %ld, live %ld, shared %ld, refs %ld\n",
         si.pages_total, si.pages_live, si.pages_shared, si.kref_calls);

  forkcost();
  writediverges();

  // Neither pages nor references may be left behind.
  snap("after the divergence tests");
  if (si.pages_shared != base_shared) {
    printf("cowtest: FAIL %ld pages still shared\n",
           si.pages_shared - base_shared);
    exit(1);
  }
  if (base_free > si.mem_free + TOLERANCE * PGSIZE) {
    printf("cowtest: FAIL %ld pages lost\n",
           (base_free - si.mem_free) / PGSIZE);
    exit(1);
  }
  copyoutcow();
  nested();

  snap("end");
  if (si.pages_shared != base_shared) {
    printf("cowtest: FAIL %ld pages still shared at the end\n",
           si.pages_shared - base_shared);
    exit(1);
  }
  // Reported as deltas: the counters behind sysinfo() are cumulative since
  // boot, so the raw values say nothing about what this run did.
  printf("cowtest: %ld references added by forks, all dropped again\n",
         si.kref_calls - base_refs);
  printf("cowtest: %ld store faults, %ld of them needed a copy\n",
         si.cow_faults - base_faults, si.cow_copies - base_copies);
  printf("cowtest: OK (shared pages agreed with the table walk throughout)\n");
  exit(0);
}
