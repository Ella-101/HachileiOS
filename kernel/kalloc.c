// Physical memory allocator, for user processes,
// kernel stacks, page-table pages,
// and pipe buffers. Allocates whole 4096-byte pages.

#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "riscv.h"
#include "defs.h"

void freerange(void *pa_start, void *pa_end);

extern char end[]; // first address after kernel.
                   // defined by kernel.ld.

// One byte of reference count per physical page, indexed by
// (pa - KERNBASE)/PGSIZE.  128 MiB of RAM is 32768 pages, so the table costs
// 32 KiB -- about 0.025% of the memory it describes.
//
// The byte carries two jobs.  The first is the one the table was added for:
// zero means "on the free list", one or more means "handed out", and a
// kfree() of a page that is already at zero is a double free.  Without that
// check a double free splices the page into the free list twice, and the
// second kalloc() of that page hands the same memory to two different
// callers.
//
// The second job is copy-on-write: fork() no longer copies user pages, it
// maps the parent's pages into the child and counts an extra reference, so a
// count above one means the page is shared and must not be written in place.
// A uchar is plenty: a page can be referenced at most once per slot in the
// process table, and kref() panics rather than wrap.
//
// The table is touched only while kmem.lock is held, so it needs no atomics
// of its own.

#define NPAGE ((PHYSTOP - KERNBASE) / PGSIZE)

static uchar page_ref[NPAGE];

static int
page_index(void *pa)
{
  return (int)(((uint64)pa - KERNBASE) / PGSIZE);
}

struct run {
  struct run *next;
};

struct {
  struct spinlock lock;
  struct run *freelist;
  uint64 nfree; // number of pages currently on the free list

  // Cumulative since boot.  kalloc_calls - pages_released is the number of
  // pages currently handed out: only a kfree() that drops the last reference
  // actually puts a page back on the list.  kinit() zeroes these after the
  // kernel's own initial freerange(), so the difference means "since boot"
  // instead of counting the pages the kernel starts life with.
  uint64 kalloc_calls;
  uint64 kfree_calls;    // kfree() calls, not all of which release a page
  uint64 kref_calls;     // kref() calls, i.e. references added by a cow fork
  uint64 pages_released; // kfree() calls that did put a page back on the list

  // O(1) count of pages with more than one reference.  kshared_walk() is the
  // O(N) reference implementation this is checked against.
  uint64 shared_pages;
} kmem;

void
kinit()
{
  initlock(&kmem.lock, "kmem");
  kmem.nfree = 0;

  // Every page starts out owned by the kernel exactly once: kinit()'s own
  // freerange() is the one legitimate case of freeing a page that was never
  // allocated, and starting from 1 makes it the single sanctioned 1 -> 0
  // transition rather than a special case inside kfree().
  memset(page_ref, 1, sizeof(page_ref));

  freerange(end, (void *)PHYSTOP);

  // Those pages were not "allocations", so start the counters from zero.
  kmem.kalloc_calls = 0;
  kmem.kfree_calls = 0;
  kmem.kref_calls = 0;
  kmem.pages_released = 0;
  kmem.shared_pages = 0;
}

void
freerange(void *pa_start, void *pa_end)
{
  char *p;
  p = (char *)PGROUNDUP((uint64)pa_start);
  for (; p + PGSIZE <= (char *)pa_end; p += PGSIZE)
    kfree(p);
}

// Drop one reference to the page of physical memory pointed at by pa, which
// must have come from kalloc() or from a kref() made on this caller's behalf.
// The page only goes back on the free list when the last reference goes away.
void
kfree(void *pa)
{
  struct run *r;
  int i;

  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("kfree");

  i = page_index(pa);

  acquire(&kmem.lock);
  if (page_ref[i] == 0)
    panic("kfree: page is already free (double free?)");
  page_ref[i]--;
  kmem.kfree_calls++;

  if (page_ref[i] > 0) {
    // Somebody else still maps it -- the ordinary case after a copy-on-write
    // fork, where both processes point at the same page and only this one is
    // letting go.  Nothing to do to the page itself.
    if (page_ref[i] == 1)
      kmem.shared_pages--; // 2 -> 1: nobody else refers to it any more
    release(&kmem.lock);
    return;
  }

  // Last reference.  Fill with junk to catch dangling refs, then link the
  // page.  Both happen with the lock held and before the page becomes
  // visible on the list, so no other hart can pick it up half-cleared.
  kmem.pages_released++;
  memset(pa, 1, PGSIZE);
  r = (struct run *)pa;
  r->next = kmem.freelist;
  kmem.freelist = r;
  kmem.nfree++;
  release(&kmem.lock);
}

// Take one more reference to a page that has already been handed out.  Used
// by uvmcopy() to share a page with a forked child instead of copying it.
void
kref(void *pa)
{
  int i;

  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("kref");

  i = page_index(pa);

  acquire(&kmem.lock);
  if (page_ref[i] == 0)
    panic("kref: page is on the free list");
  if (page_ref[i] == 255)
    panic("kref: reference count would overflow");
  if (page_ref[i] == 1)
    kmem.shared_pages++; // 1 -> 2: the page counts as shared from here on
  page_ref[i]++;
  kmem.kref_calls++;
  release(&kmem.lock);
}

// How many references a page currently has.  Only the page tables and the
// fork error path need the exact number; everything else wants kshared().
int
krefcnt(void *pa)
{
  int i, n;

  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("krefcnt");

  i = page_index(pa);

  acquire(&kmem.lock);
  n = page_ref[i];
  release(&kmem.lock);

  return n;
}

// Allocate one 4096-byte page of physical memory.
// Returns a pointer that the kernel can use.
// Returns 0 if the memory cannot be allocated.
void *
kalloc(void)
{
  struct run *r;
  int i;

  acquire(&kmem.lock);
  r = kmem.freelist;
  if (r) {
    i = page_index(r);
    // The mirror of the check in kfree(): a page on the free list must have a
    // reference count of zero.  If it does not, either the list or the table
    // is wrong, and both are worth stopping for.
    if (page_ref[i] != 0)
      panic("kalloc: page on the free list is still referenced");
    page_ref[i] = 1;
    kmem.freelist = r->next;
    kmem.nfree--;
    kmem.kalloc_calls++;
  }
  release(&kmem.lock);

  if (r)
    memset((char *)r, 5, PGSIZE); // fill with junk
  return (void *)r;
}

// Count the free physical memory, in bytes, by walking the free list.
//
// O(N) in the number of free pages, but obviously correct: it is the
// reference against which the O(1) counter (see freemem) is checked.
uint64
freemem_walk(void)
{
  struct run *r;
  uint64 n = 0;

  acquire(&kmem.lock);
  for (r = kmem.freelist; r; r = r->next)
    n++;
  release(&kmem.lock);

  return n * PGSIZE;
}

// Return the free physical memory, in bytes, in O(1) time by reading
// the counter maintained by kalloc/kfree.  freemem_walk() is the
// O(N) reference used to validate this counter.
uint64
freemem(void)
{
  uint64 n;

  acquire(&kmem.lock);
  n = kmem.nfree;
  release(&kmem.lock);

  return n * PGSIZE;
}

// How many pages currently have more than one reference, in O(1) time.
uint64
kshared(void)
{
  uint64 n;

  acquire(&kmem.lock);
  n = kmem.shared_pages;
  release(&kmem.lock);

  return n;
}

// The O(N) reference implementation for kshared(): read the table itself.
// O(NPAGE) in the pages of RAM rather than in the pages actually shared, so
// it is far more expensive than the counter -- which is exactly why the
// counter is the one the kernel uses and this is only ever compared to it.
uint64
kshared_walk(void)
{
  uint64 n = 0;

  acquire(&kmem.lock);
  for (int i = 0; i < NPAGE; i++)
    if (page_ref[i] >= 2)
      n++;
  release(&kmem.lock);

  return n;
}

// Cumulative kref() calls: references added by copy-on-write forks.
uint64
krefs(void)
{
  uint64 n;

  acquire(&kmem.lock);
  n = kmem.kref_calls;
  release(&kmem.lock);

  return n;
}

// Report the allocator accounting to sysinfo().  All three values are O(1);
// the O(N) walk in freemem_walk() stays the reference implementation that the
// free-page count can be checked against.
void
kalloc_stats(uint64 *total, uint64 *live, uint64 *calls)
{
  acquire(&kmem.lock);
  *total = NPAGE;
  // pages_released rather than kfree_calls: a kfree() of a page that is still
  // shared drops a reference without returning the page, so counting those
  // would make live creep downwards.  live must stay "pages the allocator
  // owns", which is what makes mem_free + live a constant.
  *live = kmem.kalloc_calls - kmem.pages_released;
  *calls = kmem.kalloc_calls;
  release(&kmem.lock);
}
