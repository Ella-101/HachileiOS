#include "param.h"
#include "types.h"
#include "memlayout.h"
#include "elf.h"
#include "riscv.h"
#include "defs.h"
#include "spinlock.h"
#include "proc.h"
#include "fs.h"

/*
 * the kernel's page table.
 */
pagetable_t kernel_pagetable;

extern char etext[]; // kernel.ld sets this to end of kernel code.

extern char trampoline[]; // trampoline.S

// Make a direct-map page table for the kernel.
pagetable_t
kvmmake(void)
{
  pagetable_t kpgtbl;

  kpgtbl = (pagetable_t)kalloc();
  memset(kpgtbl, 0, PGSIZE);

  // uart registers
  kvmmap(kpgtbl, UART0, UART0, PGSIZE, PTE_R | PTE_W);

  // virtio mmio disk interface
  kvmmap(kpgtbl, VIRTIO0, VIRTIO0, PGSIZE, PTE_R | PTE_W);

  // PLIC
  kvmmap(kpgtbl, PLIC, PLIC, 0x4000000, PTE_R | PTE_W);

  // map kernel text executable and read-only.
  kvmmap(kpgtbl, KERNBASE, KERNBASE, (uint64)etext - KERNBASE, PTE_R | PTE_X);

  // map kernel data and the physical RAM we'll make use of.
  kvmmap(kpgtbl, (uint64)etext, (uint64)etext, PHYSTOP - (uint64)etext,
         PTE_R | PTE_W);

  // map the trampoline for trap entry/exit to
  // the highest virtual address in the kernel.
  kvmmap(kpgtbl, TRAMPOLINE, (uint64)trampoline, PGSIZE, PTE_R | PTE_X);

  // allocate and map a kernel stack for each process.
  proc_mapstacks(kpgtbl);

  return kpgtbl;
}

// add a mapping to the kernel page table.
// only used when booting.
// does not flush TLB or enable paging.
void
kvmmap(pagetable_t kpgtbl, uint64 va, uint64 pa, uint64 sz, int perm)
{
  if (mappages(kpgtbl, va, sz, pa, perm) != 0)
    panic("kvmmap");
}

// Initialize the kernel_pagetable, shared by all CPUs.
void
kvminit(void)
{
  kernel_pagetable = kvmmake();
}

// Switch the current CPU's h/w page table register to
// the kernel's page table, and enable paging.
void
kvminithart()
{
  // wait for any previous writes to the page table memory to finish.
  sfence_vma();

  w_satp(MAKE_SATP(kernel_pagetable));

  // flush stale entries from the TLB.
  sfence_vma();
}

// Return the address of the PTE in page table pagetable
// that corresponds to virtual address va.  If alloc!=0,
// create any required page-table pages.
//
// The risc-v Sv39 scheme has three levels of page-table
// pages. A page-table page contains 512 64-bit PTEs.
// A 64-bit virtual address is split into five fields:
//   39..63 -- must be zero.
//   30..38 -- 9 bits of level-2 index.
//   21..29 -- 9 bits of level-1 index.
//   12..20 -- 9 bits of level-0 index.
//    0..11 -- 12 bits of byte offset within the page.
pte_t *
walk(pagetable_t pagetable, uint64 va, int alloc)
{
  if (va >= MAXVA)
    panic("walk");

  for (int level = 2; level > 0; level--) {
    pte_t *pte = &pagetable[PX(level, va)];
    if (*pte & PTE_V) {
      pagetable = (pagetable_t)PTE2PA(*pte);
    } else {
      if (!alloc || (pagetable = (pde_t *)kalloc()) == 0)
        return 0;
      memset(pagetable, 0, PGSIZE);
      *pte = PA2PTE(pagetable) | PTE_V;
    }
  }
  return &pagetable[PX(0, va)];
}

// Look up a virtual address, return the physical address,
// or 0 if not mapped.
// Can only be used to look up user pages.
uint64
walkaddr(pagetable_t pagetable, uint64 va)
{
  pte_t *pte;
  uint64 pa;

  if (va >= MAXVA)
    return 0;

  pte = walk(pagetable, va, 0);
  if (pte == 0)
    return 0;
  if ((*pte & PTE_V) == 0)
    return 0;
  if ((*pte & PTE_U) == 0)
    return 0;
  pa = PTE2PA(*pte);
  return pa;
}

// Create PTEs for virtual addresses starting at va that refer to
// physical addresses starting at pa.
// va and size MUST be page-aligned.
// Returns 0 on success, -1 if walk() couldn't
// allocate a needed page-table page.
int
mappages(pagetable_t pagetable, uint64 va, uint64 size, uint64 pa, int perm)
{
  uint64 a, last;
  pte_t *pte;

  if ((va % PGSIZE) != 0)
    panic("mappages: va not aligned");

  if ((size % PGSIZE) != 0)
    panic("mappages: size not aligned");

  if (size == 0)
    panic("mappages: size");

  a = va;
  last = va + size - PGSIZE;
  for (;;) {
    if ((pte = walk(pagetable, a, 1)) == 0)
      return -1;
    if (*pte & PTE_V)
      panic("mappages: remap");
    *pte = PA2PTE(pa) | perm | PTE_V;
    if (a == last)
      break;
    a += PGSIZE;
    pa += PGSIZE;
  }
  return 0;
}

// create an empty user page table.
// returns 0 if out of memory.
pagetable_t
uvmcreate()
{
  pagetable_t pagetable;
  pagetable = (pagetable_t)kalloc();
  if (pagetable == 0)
    return 0;
  memset(pagetable, 0, PGSIZE);
  return pagetable;
}

// Remove npages of mappings starting from va. va must be
// page-aligned. It's OK if the mappings don't exist.
// Optionally free the physical memory.
void
uvmunmap(pagetable_t pagetable, uint64 va, uint64 npages, int do_free)
{
  uint64 a;
  pte_t *pte;

  if ((va % PGSIZE) != 0)
    panic("uvmunmap: not aligned");

  for (a = va; a < va + npages * PGSIZE; a += PGSIZE) {
    if ((pte = walk(pagetable, a, 0)) == 0) // leaf page table entry allocated?
      continue;
    if ((*pte & PTE_V) == 0) // has physical page been allocated?
      continue;
    if (do_free) {
      uint64 pa = PTE2PA(*pte);
      kfree((void *)pa);
    }
    *pte = 0;
  }
}

// Allocate PTEs and physical memory to grow a process from oldsz to
// newsz, which need not be page aligned.  Returns new size or 0 on error.
uint64
uvmalloc(pagetable_t pagetable, uint64 oldsz, uint64 newsz, int xperm)
{
  char *mem;
  uint64 a;

  if (newsz < oldsz)
    return oldsz;

  oldsz = PGROUNDUP(oldsz);
  for (a = oldsz; a < newsz; a += PGSIZE) {
    mem = kalloc();
    if (mem == 0) {
      uvmdealloc(pagetable, a, oldsz);
      return 0;
    }
    memset(mem, 0, PGSIZE);
    if (mappages(pagetable, a, PGSIZE, (uint64)mem, PTE_R | PTE_U | xperm) !=
        0) {
      kfree(mem);
      uvmdealloc(pagetable, a, oldsz);
      return 0;
    }
  }
  return newsz;
}

// Deallocate user pages to bring the process size from oldsz to
// newsz.  oldsz and newsz need not be page-aligned, nor does newsz
// need to be less than oldsz.  oldsz can be larger than the actual
// process size.  Returns the new process size.
uint64
uvmdealloc(pagetable_t pagetable, uint64 oldsz, uint64 newsz)
{
  if (newsz >= oldsz)
    return oldsz;

  if (PGROUNDUP(newsz) < PGROUNDUP(oldsz)) {
    int npages = (PGROUNDUP(oldsz) - PGROUNDUP(newsz)) / PGSIZE;
    uvmunmap(pagetable, PGROUNDUP(newsz), npages, 1);
  }

  return newsz;
}

// Recursively free page-table pages.
// All leaf mappings must already have been removed.
void
freewalk(pagetable_t pagetable)
{
  // there are 2^9 = 512 PTEs in a page table.
  for (int i = 0; i < 512; i++) {
    pte_t pte = pagetable[i];
    if ((pte & PTE_V) && (pte & (PTE_R | PTE_W | PTE_X)) == 0) {
      // this PTE points to a lower-level page table.
      uint64 child = PTE2PA(pte);
      freewalk((pagetable_t)child);
      pagetable[i] = 0;
    } else if (pte & PTE_V) {
      panic("freewalk: leaf");
    }
  }
  kfree((void *)pagetable);
}

// Free user memory pages,
// then free page-table pages.
void
uvmfree(pagetable_t pagetable, uint64 sz)
{
  if (sz > 0)
    uvmunmap(pagetable, 0, PGROUNDUP(sz) / PGSIZE, 1);
  freewalk(pagetable);
}

// Given a parent process's page table, spread its memory into a child's
// page table by sharing the physical pages instead of duplicating them.
//
// A page the parent can write is mapped read-only and marked PTE_COW in
// both tables, and its reference count is raised; the first store by either
// process then takes a private copy (see cowfault).  A page the parent
// cannot write -- the text segment of an exec'd program -- is shared
// read-only as it stands and deliberately NOT marked PTE_COW, so that a
// store to it stays the error it always was instead of quietly earning a
// writable copy.
//
// Only the parent's own hart runs here (an xv6 process has a single thread
// of control), so rewriting the parent's PTEs needs no lock, and the child
// is not runnable yet so nobody can see its table either.  The other
// references to a page we share belong to other processes, and dropping a
// reference can never free a page the table still credits to us.
//
// returns 0 on success, -1 on failure.
// frees any allocated pages on failure.
int
uvmcopy(pagetable_t old, pagetable_t new, uint64 sz)
{
  pte_t *pte;
  uint64 pa, i, j;
  uint flags;

  for (i = 0; i < sz; i += PGSIZE) {
    if ((pte = walk(old, i, 0)) == 0)
      continue; // page table entry hasn't been allocated
    if ((*pte & PTE_V) == 0)
      continue; // physical page hasn't been allocated
    pa = PTE2PA(*pte);
    flags = PTE_FLAGS(*pte);

    if ((*pte & PTE_W) == 0) {
      // Read-only: the child can simply share it, and flags already carries the
      // reason it is read-only.  A text page has neither PTE_W nor PTE_COW, so it
      // is shared as it stands and a store to it still kills the process.  A page
      // that an earlier fork made shared is read-only *because* of PTE_COW, and
      // flags carries that bit through, so a store still takes a copy.  The two
      // cases differ in why the write bit is clear, not in what a write does.
      kref((void *)pa);
      if (mappages(new, i, PGSIZE, pa, flags) != 0) {
        kfree((void *)pa);
        goto err;
      }
      continue;
    }

    // Writable: share it, but clear W on both sides so the first store by
    // either process traps, and record why it trapped.
    kref((void *)pa);
    if (mappages(new, i, PGSIZE, pa, (flags & ~PTE_W) | PTE_COW) != 0) {
      kfree((void *)pa);
      goto err;
    }
    *pte = PA2PTE(pa) | ((flags & ~PTE_W) | PTE_COW);
  }
  return 0;

err:
  // Unmapping the child drops the references this fork just added...
  uvmunmap(new, 0, i / PGSIZE, 1);
  // ...and that is what makes the parent's pages safe to look at again: a
  // page belongs to the parent alone only once its count is back down to
  // one, so a page an earlier fork still shares has to stay PTE_COW.
  for (j = 0; j < i; j += PGSIZE) {
    pte_t *ppte = walk(old, j, 0);
    if (ppte != 0 && (*ppte & PTE_COW) != 0 &&
        krefcnt((void *)PTE2PA(*ppte)) == 1)
      *ppte = (*ppte & ~PTE_COW) | PTE_W;
  }
  return -1;
}

// mark a PTE invalid for user access.
// used by exec for the user stack guard page.
void
uvmclear(pagetable_t pagetable, uint64 va)
{
  pte_t *pte;

  pte = walk(pagetable, va, 0);
  if (pte == 0)
    panic("uvmclear");
  *pte &= ~PTE_U;
}

// Copy from kernel to user.
// Copy len bytes from src to virtual address dstva in a given page table.
// Return 0 on success, -1 on error.
int
copyout(pagetable_t pagetable, uint64 psz, uint64 dstva, char *src, uint64 len)
{
  uint64 n, va0, pa0;
  pte_t *pte;

  while (len > 0) {
    va0 = PGROUNDDOWN(dstva);
    if (va0 >= MAXVA)
      return -1;

    pa0 = walkaddr(pagetable, va0);
    if (pa0 == 0) {
      if ((pa0 = vmfault(pagetable, psz, va0, 0)) == 0) {
        return -1;
      }
    }

    pte = walk(pagetable, va0, 0);
    if ((*pte & PTE_W) == 0) {
      // A page shared by a copy-on-write fork is read-only on purpose.  The
      // kernel is as much a writer as the user is, so it has to take the
      // private copy too: writing straight through would put the bytes into
      // the other process's page as well and undo the isolation the fork
      // promised.
      if ((*pte & PTE_COW) == 0)
        return -1; // read-only user text, exactly as before
      if ((pa0 = cowfault(pagetable, psz, va0)) == 0)
        return -1;
    }

    n = PGSIZE - (dstva - va0);
    if (n > len)
      n = len;
    memmove((void *)(pa0 + (dstva - va0)), src, n);

    len -= n;
    src += n;
    dstva = va0 + PGSIZE;
  }
  return 0;
}

// Copy from user to kernel.
// Copy len bytes to dst from virtual address srcva in a given page table.
// Return 0 on success, -1 on error.
int
copyin(pagetable_t pagetable, uint64 psz, char *dst, uint64 srcva, uint64 len)
{
  uint64 n, va0, pa0;

  while (len > 0) {
    va0 = PGROUNDDOWN(srcva);
    pa0 = walkaddr(pagetable, va0);
    if (pa0 == 0) {
      if ((pa0 = vmfault(pagetable, psz, va0, 1)) == 0) {
        return -1;
      }
    }
    n = PGSIZE - (srcva - va0);
    if (n > len)
      n = len;
    memmove(dst, (void *)(pa0 + (srcva - va0)), n);

    len -= n;
    dst += n;
    srcva = va0 + PGSIZE;
  }
  return 0;
}

// Copy a null-terminated string from user to kernel.
// Copy bytes to dst from virtual address srcva in a given page table,
// until a '\0', or max.
// Return 0 on success, -1 on error.
int
copyinstr(pagetable_t pagetable, uint64 psz, char *dst, uint64 srcva,
          uint64 max)
{
  uint64 n, va0, pa0;
  int got_null = 0;

  while (got_null == 0 && max > 0) {
    va0 = PGROUNDDOWN(srcva);
    pa0 = walkaddr(pagetable, va0);
    if (pa0 == 0) {
      if ((pa0 = vmfault(pagetable, psz, va0, 1)) == 0) {
        return -1;
      }
    }
    n = PGSIZE - (srcva - va0);
    if (n > max)
      n = max;

    char *p = (char *)(pa0 + (srcva - va0));
    while (n > 0) {
      if (*p == '\0') {
        *dst = '\0';
        got_null = 1;
        break;
      } else {
        *dst = *p;
      }
      --n;
      --max;
      p++;
      dst++;
    }

    srcva = va0 + PGSIZE;
  }
  if (got_null) {
    return 0;
  } else {
    return -1;
  }
}

// allocate and map user memory if process is referencing a page
// that was lazily allocated in sys_sbrk().
// returns 0 if va is invalid or already mapped, or if
// out of physical memory, and physical address if successful.
// Pages mapped on demand, counted only once the mapping actually
// succeeded, so the figure can be compared against the growth of rss.
// Relaxed atomic: several harts fault at once and the value is only read
// for reporting.  Note that copyin()/copyout() fault user buffers through
// this same path, so it counts demand-paged allocations rather than
// user-mode page faults.
static uint64 vmfault_cnt;

uint64
vmfaults(void)
{
  return __atomic_load_n(&vmfault_cnt, __ATOMIC_RELAXED);
}

uint64
vmfault(pagetable_t pagetable, uint64 psz, uint64 va, int read)
{
  uint64 mem;

  if (va >= psz)
    return 0;
  va = PGROUNDDOWN(va);
  if (ismapped(pagetable, va)) {
    return 0;
  }
  mem = (uint64)kalloc();
  if (mem == 0)
    return 0;
  memset((void *)mem, 0, PGSIZE);
  if (mappages(pagetable, va, PGSIZE, mem, PTE_W | PTE_U | PTE_R) != 0) {
    kfree((void *)mem);
    return 0;
  }
  __atomic_fetch_add(&vmfault_cnt, 1, __ATOMIC_RELAXED);
  return mem;
}

// Store faults that cowfault() resolved, for sysinfo().  Relaxed atomics, for
// the same reason as vmfault_cnt: several harts fault at once and the values
// are only read for reporting.
static uint64 cow_fault_cnt;
static uint64 cow_copy_cnt; // the subset of those that had to copy a page

uint64
cowfaults(void)
{
  return __atomic_load_n(&cow_fault_cnt, __ATOMIC_RELAXED);
}

uint64
cowcopies(void)
{
  return __atomic_load_n(&cow_copy_cnt, __ATOMIC_RELAXED);
}

// Handle a store fault on a page that a copy-on-write fork shared: give the
// faulting process a private copy and return its physical address.  Returns
// 0 if va is not a copy-on-write page -- the fault is then the bad access it
// always was and the caller kills the process -- or if memory ran out.
//
// Only stores reach here.  PTE_R stays set on a shared page, so reads do not
// fault, and a store to a page that was never writable carries no PTE_COW
// and is rejected.
uint64
cowfault(pagetable_t pagetable, uint64 psz, uint64 va)
{
  pte_t *pte;
  uint64 pa, mem;
  uint flags;

  if (va >= psz)
    return 0;
  va = PGROUNDDOWN(va);

  pte = walk(pagetable, va, 0);
  if (pte == 0 || (*pte & PTE_V) == 0 || (*pte & PTE_COW) == 0)
    return 0;

  pa = PTE2PA(*pte);
  flags = PTE_FLAGS(*pte);

  if (krefcnt((void *)pa) == 1) {
    // Nobody else maps this page any more: the process that shared it with
    // us has exited.  There is nothing worth copying, so a store is simply
    // a store again -- and copying here would be waste of the kind the shell
    // would pay on the first write to every one of its pages after every
    // command it runs.  Reading the count and then rewriting the PTE is not
    // atomic and does not need to be: a page can only gain a reference when
    // this very process is forked, which cannot happen while we are here,
    // because an xv6 process has a single thread of control.  A concurrent
    // exit can only lower the count, and it has to pass through zero for the
    // page to be freed, which it cannot while we still hold this mapping.
    *pte = PA2PTE(pa) | ((flags & ~PTE_COW) | PTE_W);
    __atomic_fetch_add(&cow_fault_cnt, 1, __ATOMIC_RELAXED);
    return pa;
  }

  if ((mem = (uint64)kalloc()) == 0)
    return 0;
  memmove((char *)mem, (char *)pa, PGSIZE);

  // The copy has to be in place before the old reference is dropped: the page
  // must not go back to the free list while we are still reading it.  Only
  // this hart writes this PTE, so no other hart can observe a half-updated
  // mapping.  kfree() then gives up the reference this process held, which is
  // what makes the private copy genuinely private.
  *pte = PA2PTE(mem) | ((flags & ~PTE_COW) | PTE_W);
  kfree((void *)pa);

  __atomic_fetch_add(&cow_fault_cnt, 1, __ATOMIC_RELAXED);
  __atomic_fetch_add(&cow_copy_cnt, 1, __ATOMIC_RELAXED);
  return mem;
}

int
ismapped(pagetable_t pagetable, uint64 va)
{
  pte_t *pte = walk(pagetable, va, 0);
  if (pte == 0) {
    return 0;
  }
  if (*pte & PTE_V) {
    return 1;
  }
  return 0;
}

// Recursively add up the resident pages below sz.  level is 2 for a
// top-level (L2) table down to 0 for a leaf table, and base is the
// virtual address of slot 0 of this table.
static void
rsswalk(pagetable_t pagetable, int level, uint64 base, uint64 sz, uint64 *acc)
{
  uint64 stride = PGSIZE;
  for (int l = 0; l < level; l++)
    stride *= 512;

  for (int i = 0; i < 512; i++) {
    pte_t pte = pagetable[i];
    uint64 va;

    if ((pte & PTE_V) == 0)
      continue;

    va = base + (uint64)i * stride;
    // Slots ascend with i, so once we pass sz every later slot is past
    // it too and can be skipped.
    if (va >= sz)
      break;

    if (level > 0 && (pte & (PTE_R | PTE_W | PTE_X)) == 0) {
      // A valid PTE with none of R/W/X points to a lower-level
      // table; this is freewalk()'s test, and it rests on the same
      // invariant: no leaf is mapped without at least one of them.
      // freewalk() panics if it meets such a leaf; here it would
      // just be treated as a table pointer, so the assumption is
      // stated rather than guarded.
      rsswalk((pagetable_t)PTE2PA(pte), level - 1, va, sz, acc);
    } else {
      *acc += PGSIZE;
    }
  }
}

// Resident set size: how many bytes of the process's low sz bytes of
// address space are backed by real pages.  This is what ps/top report
// next to the virtual size, because with lazy sbrk growproc() hands out
// address space that is not mapped and costs nothing until touched.
//
// We descend the page-table tree like freewalk() does instead of calling
// walk() once per virtual page: with lazy allocation sz can be enormous
// while almost none of it is mapped, and the tree walk costs time
// proportional to what is actually mapped plus the table pages needed to
// hold it.
//
// Only pages below sz count, which is why the trampoline and trapframe
// pages at the top of the address space are excluded.  The user stack
// lives below sz and so is legitimately part of the RSS; a stack guard
// page is simply not mapped and so does not count.
//
// The table is only read here.  The caller must hold p->lock, exactly as
// psinfo() does, so the walk cannot be racing against fork/exec/exit.
uint64
vm_rss(pagetable_t pagetable, uint64 sz)
{
  uint64 acc = 0;

  if (pagetable == 0 || sz == 0)
    return 0;

  rsswalk(pagetable, 2, 0, PGROUNDUP(sz), &acc);
  return acc;
}
