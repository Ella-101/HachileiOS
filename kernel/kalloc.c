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

// One byte of state per physical page, indexed by (pa - KERNBASE)/PGSIZE.
// 128 MiB of RAM is 32768 pages, so the table costs 32 KiB -- about 0.025% of
// the memory it describes -- in exchange for turning a silent free-list
// corruption into an immediate panic: without it, freeing the same page twice
// splices it into the list twice, and the second kalloc() of that page hands
// the same memory to two different callers.
//
// The table is touched only while kmem.lock is held (kalloc, kfree), so it
// needs no atomics of its own.
#define PAGE_FREE  0 // sits on kmem.freelist
#define PAGE_ALLOC 1 // handed out by kalloc() and not returned yet

#define NPAGE ((PHYSTOP - KERNBASE) / PGSIZE)

static uchar page_state[NPAGE];

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

  // Cumulative since boot; kalloc_calls - kfree_calls is the number of
  // pages currently handed out.  kinit() zeroes both after the kernel's own
  // initial freerange(), so the difference means "since boot" instead of
  // counting the pages the kernel starts life with.
  uint64 kalloc_calls;
  uint64 kfree_calls;
} kmem;

void
kinit()
{
  initlock(&kmem.lock, "kmem");
  kmem.nfree = 0;

  // Every page starts out owned by the kernel: kinit()'s own freerange() is
  // the one legitimate case of freeing a page that was never allocated, and
  // starting from ALLOC makes it the single sanctioned ALLOC->FREE transition
  // rather than a special case inside kfree().
  memset(page_state, PAGE_ALLOC, sizeof(page_state));

  freerange(end, (void *)PHYSTOP);

  // Those pages were not "allocations", so start the counters from zero.
  kmem.kalloc_calls = 0;
  kmem.kfree_calls = 0;
}

void
freerange(void *pa_start, void *pa_end)
{
  char *p;
  p = (char *)PGROUNDUP((uint64)pa_start);
  for (; p + PGSIZE <= (char *)pa_end; p += PGSIZE)
    kfree(p);
}

// Free the page of physical memory pointed at by pa,
// which normally should have been returned by a
// call to kalloc().  (The exception is when
// initializing the allocator; see kinit above.)
void
kfree(void *pa)
{
  struct run *r;
  int i;

  if (((uint64)pa % PGSIZE) != 0 || (char *)pa < end || (uint64)pa >= PHYSTOP)
    panic("kfree");

  // Fill with junk to catch dangling refs.  Copying happens before the page
  // becomes visible on the free list, so no other hart can pick it up
  // half-initialised.
  memset(pa, 1, PGSIZE);

  r = (struct run *)pa;
  i = page_index(pa);

  acquire(&kmem.lock);
  if (page_state[i] != PAGE_ALLOC)
    panic("kfree: page was not allocated (double free?)");
  page_state[i] = PAGE_FREE;
  kmem.kfree_calls++;
  r->next = kmem.freelist;
  kmem.freelist = r;
  kmem.nfree++;
  release(&kmem.lock);
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
    // The mirror of the check in kfree(): a page on the free list must be
    // marked free.  If it is not, either the list or the table is wrong, and
    // both are worth stopping for.
    if (page_state[i] != PAGE_FREE)
      panic("kalloc: page on the free list is not marked free");
    page_state[i] = PAGE_ALLOC;
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

// Report the allocator accounting to sysinfo().  All three values are O(1);
// the O(N) walk in freemem_walk() stays the reference implementation that the
// free-page count can be checked against.
void
kalloc_stats(uint64 *total, uint64 *live, uint64 *calls)
{
  acquire(&kmem.lock);
  *total = NPAGE;
  *live = kmem.kalloc_calls - kmem.kfree_calls;
  *calls = kmem.kalloc_calls;
  release(&kmem.lock);
}
