#include "types.h"
#include "riscv.h"
#include "defs.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "proc.h"
#include "vm.h"
#include "sysinfo.h"

uint64
sys_exit(void)
{
  int n;
  argint(0, &n);
  kexit(n);
  return 0; // not reached
}

uint64
sys_getpid(void)
{
  return myproc()->pid;
}

uint64
sys_fork(void)
{
  return kfork();
}

uint64
sys_wait(void)
{
  uint64 p;
  argaddr(0, &p);
  return kwait(p, 0, 0);
}

// waitx(status, utime, ktime): wait() plus the child's lifetime CPU
// accounting.  The kernel freezes both counters in kexit(), so this
// reads a value that no longer moves even though the child is gone.
uint64
sys_waitx(void)
{
  uint64 status, utime, ktime;
  argaddr(0, &status);
  argaddr(1, &utime);
  argaddr(2, &ktime);
  return kwait(status, utime, ktime);
}

uint64
sys_sbrk(void)
{
  uint64 addr;
  int t;
  int n;

  argint(0, &n);
  argint(1, &t);
  addr = myproc()->sz;

  if (t == SBRK_EAGER || n < 0) {
    if (growproc(n) < 0) {
      return -1;
    }
  } else {
    // Lazily allocate memory for this process: increase its memory
    // size but don't allocate memory. If the processes uses the
    // memory, vmfault() will allocate it.
    if (addr + n < addr)
      return -1;
    if (addr + n > TRAPFRAME)
      return -1;
    myproc()->sz += n;
  }
  return addr;
}

uint64
sys_pause(void)
{
  int n;
  uint ticks0;

  argint(0, &n);
  if (n < 0)
    n = 0;
  acquire(&tickslock);
  ticks0 = ticks;
  while (ticks - ticks0 < n) {
    if (killed(myproc())) {
      release(&tickslock);
      return -1;
    }
    sleep_prepare(&ticks);
    release(&tickslock);
    sleep();
    acquire(&tickslock);
  }
  release(&tickslock);
  return 0;
}

uint64
sys_kill(void)
{
  int pid;

  argint(0, &pid);
  return kkill(pid);
}

// return how many clock tick interrupts have occurred
// since start.
uint64
sys_uptime(void)
{
  uint xticks;

  acquire(&tickslock);
  xticks = ticks;
  release(&tickslock);
  return xticks;
}

// Snapshot the process table into the user buffer at arg 0, which holds
// room for arg 1 entries.  Returns the number of entries written.
uint64
sys_psinfo(void)
{
  uint64 buf;
  int max;

  argaddr(0, &buf);
  argint(1, &max);
  return psinfo(buf, max);
}

// Return the number of bytes of free physical memory.
uint64
sys_freemem(void)
{
  return freemem();
}

// Copy kernel log text into the user buffer at arg 0 (arg 1 bytes).
// arg 2 is an in/out cursor; arg 3 receives the count of bytes that were
// overwritten before the cursor.  Returns the number of bytes copied.
uint64
sys_klog(void)
{
  struct proc *p = myproc();
  uint64 buf, useq, ulost;
  uint64 seq, lost;
  int max, n;

  argaddr(0, &buf);
  argint(1, &max);
  argaddr(2, &useq);
  argaddr(3, &ulost);
  if (max < 0)
    return -1;

  if (copyin(p->pagetable, p->sz, (char *)&seq, useq, sizeof(seq)) < 0)
    return -1;

  n = klog_read(p->pagetable, p->sz, buf, max, &seq, &lost);
  if (n < 0)
    return -1;

  if (copyout(p->pagetable, p->sz, useq, (char *)&seq, sizeof(seq)) < 0)
    return -1;
  if (copyout(p->pagetable, p->sz, ulost, (char *)&lost, sizeof(lost)) < 0)
    return -1;

  return n;
}

// sysinfo(info): snapshot of the system-wide counters.
//
// The fields are read one at a time rather than under a single lock, so
// this is not an atomic view of the machine: by the time the last counter
// is read the first ones may already be stale.  That is deliberate --
// these are monotonic statistics, and a consistent snapshot would mean
// stopping every hart.
uint64
sys_sysinfo(void)
{
  uint64 addr;
  struct sysinfo info;
  struct proc *p = myproc();

  argaddr(0, &addr);

  info.ncpu_online = ncpu_online();
  info.ncpu_max = NCPU;
  // Same quantity as MINIOS_MEM_TOTAL in minios.h; derived here from
  // memlayout.h so there is only one place to keep in sync.
  info.mem_total = PHYSTOP - KERNBASE;
  info.mem_free = freemem();
  bio_stats(&info.bcache_hits, &info.bcache_misses);
  disk_stats(&info.disk_reads, &info.disk_writes);
  info.vmfaults = vmfaults();

  if (copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info)) < 0)
    return -1;
  return 0;
}
