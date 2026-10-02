#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "riscv.h"
#include "spinlock.h"
#include "proc.h"
#include "psinfo.h"
#include "defs.h"

struct cpu cpus[NCPU];

// Harts that have reached scheduler().  Each hart increments this once, so
// there is a single writer per hart and the value only ever moves forward.
// A relaxed atomic is enough: this is a statistic, not a synchronisation
// device -- contrast the release/acquire pair used for `started` in main.c,
// which really does order memory between harts.
static uint64 ncpu_online_cnt;

void
cpu_online_inc(void)
{
  __atomic_fetch_add(&ncpu_online_cnt, 1, __ATOMIC_RELAXED);
}

// Read by sysinfo().  This counts harts that have entered the scheduler, not
// the number QEMU was configured with (-smp): a hart still initialising is
// not counted yet.  Reporting what is really online is more useful than
// echoing a compile-time constant.
uint64
ncpu_online(void)
{
  return __atomic_load_n(&ncpu_online_cnt, __ATOMIC_RELAXED);
}

// psinfo() fills a single kalloc page with one entry per process slot.
// This fails to compile (negative array size) if that stops being true.
typedef char
  psinfo_fits_one_page[(NPROC * sizeof(struct psinfo) <= PGSIZE) ? 1 : -1];

struct proc proc[NPROC];

struct proc *initproc;

int nextpid = 1;
struct spinlock pid_lock;

extern void forkret(void);
static void freeproc(struct proc *p);

extern char trampoline[]; // trampoline.S

// helps ensure that wakeups of wait()ing
// parents are not lost. helps obey the
// memory model when using p->parent.
// must be acquired before any p->lock.
struct spinlock wait_lock;
static struct spinlock tty_lock;
static int tty_pgid;

// Allocate a page for each process's kernel stack.
// Map it high in memory, followed by an invalid
// guard page.
void
proc_mapstacks(pagetable_t kpgtbl)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    char *pa = kalloc();
    if (pa == 0)
      panic("kalloc");
    uint64 va = KSTACK((int)(p - proc));
    kvmmap(kpgtbl, va, (uint64)pa, PGSIZE, PTE_R | PTE_W);
  }
}

// initialize the proc table.
void
procinit(void)
{
  struct proc *p;

  initlock(&pid_lock, "nextpid");
  initlock(&wait_lock, "wait_lock");
  initlock(&tty_lock, "tty");
  for (p = proc; p < &proc[NPROC]; p++) {
    initlock(&p->lock, "proc");
    p->state = UNUSED;
    p->kstack = KSTACK((int)(p - proc));
  }
}

// Must be called with interrupts disabled,
// to prevent race with process being moved
// to a different CPU.
int
cpuid()
{
  int id = r_tp();
  return id;
}

// Return this CPU's cpu struct.
// Interrupts must be disabled.
struct cpu *
mycpu(void)
{
  int id = cpuid();
  struct cpu *c = &cpus[id];
  return c;
}

// Return the current struct proc *, or zero if none.
struct proc *
myproc(void)
{
  push_off();
  struct cpu *c = mycpu();
  struct proc *p = c->proc;
  pop_off();
  return p;
}

// The identity to hand to the permission checks: "who is
// asking".  Reading it here, at the call site, is what lets
// perm_ok() depend on its arguments and nothing else.
struct cred
mycred(void)
{
  struct proc *p = myproc();

  return (struct cred){p->uid, p->gid};
}

int
allocpid()
{
  int pid;

  acquire(&pid_lock);
  pid = nextpid;
  nextpid = nextpid + 1;
  release(&pid_lock);

  return pid;
}

// Aging floor for cur_prio.  A waiting process gains one notch of urgency
// on every scheduling pass and is put back at its static priority once it
// is chosen, so a PRIO_LOWEST process is picked at least once every
// (PRIO_LOWEST - PRIO_HIGHEST + 1) passes.  The floor stops the counter
// from drifting towards INT_MIN over a long run; it sits below every real
// priority, so it never hides an actual difference.
#define PRIO_AGING_FLOOR (-(PRIO_LOWEST + 1))

// Look in the process table for an UNUSED proc.
// If found, initialize state required to run in the kernel,
// and return with p->lock held.
// If there are no free procs, or a memory allocation fails, return 0.
static struct proc *
allocproc(void)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->state == UNUSED) {
      goto found;
    } else {
      release(&p->lock);
    }
  }
  return 0;

found:
  p->pid = allocpid();
  p->state = USED;

  // A recycled slot must not inherit the previous process's CPU time.
  p->u_ticks = 0;
  p->k_ticks = 0;
  // Same reasoning for the exit-time copy: kexit() overwrites them
  // before anyone may read them, but zeroing here means a stale
  // predecessor can never leak through even if that changes.
  p->xutime = 0;
  p->xktime = 0;

  // name is filled in by kexec()/kfork() with safestrcpy(), which
  // writes only up to the terminator and leaves the rest of the
  // field untouched.  Without this, a recycled slot keeps the tail
  // of its predecessor's name -- "initcode" renamed to "init"
  // leaves "ode" in name[4..7].  printf("%s") stops at the NUL so
  // ps looks fine, but psinfo() copies all 16 bytes to user space
  // and would hand those stale kernel bytes to the caller.
  memset(p->name, 0, sizeof(p->name));

  // Same reasoning as the counters above: a recycled slot must not inherit
  // the previous process priority.
  p->prio = PRIO_DEFAULT;
  p->cur_prio = PRIO_DEFAULT;
  p->pgid = p->pid;
  // A fresh process starts privileged: userinit() and forkret() need it
  // to exec("/init").  The only way down is an explicit ksetuid(), and
  // that does not come back up.
  p->uid = 0;
  p->gid = 0;
  p->pending = 0;
  p->sigmask = 0;
  memset(p->sighandlers, 0, sizeof(p->sighandlers));
  p->sigframe_active = 0;

  // Allocate a trapframe page.
  if ((p->trapframe = (struct trapframe *)kalloc()) == 0) {
    freeproc(p);
    release(&p->lock);
    return 0;
  }

  // An empty user page table.
  p->pagetable = proc_pagetable(p);
  if (p->pagetable == 0) {
    freeproc(p);
    release(&p->lock);
    return 0;
  }

  // Set up new context to start executing at forkret,
  // which returns to user space.
  memset(&p->context, 0, sizeof(p->context));
  p->context.ra = (uint64)forkret;
  p->context.sp = p->kstack + PGSIZE;

  return p;
}

// free a proc structure and the data hanging from it,
// including user pages.
// p->lock must be held.
static void
freeproc(struct proc *p)
{
  if (p->trapframe)
    kfree((void *)p->trapframe);
  p->trapframe = 0;
  if (p->pagetable)
    proc_freepagetable(p->pagetable, p->sz);
  p->pagetable = 0;
  p->sz = 0;
  p->pid = 0;
  p->name[0] = 0;
  p->chan = 0;
  p->killed = 0;
  p->xstate = 0;
  p->state = UNUSED;
  p->pending = 0;
  p->sigmask = 0;
  p->pgid = 0;
}

// Create a user page table for a given process, with no user memory,
// but with trampoline and trapframe pages.
pagetable_t
proc_pagetable(struct proc *p)
{
  pagetable_t pagetable;

  // An empty page table.
  pagetable = uvmcreate();
  if (pagetable == 0)
    return 0;

  // map the trampoline code (for system call return)
  // at the highest user virtual address.
  // only the supervisor uses it, on the way
  // to/from user space, so not PTE_U.
  if (mappages(pagetable, TRAMPOLINE, PGSIZE, (uint64)trampoline,
               PTE_R | PTE_X) < 0) {
    uvmfree(pagetable, 0);
    return 0;
  }

  // map the trapframe page just below the trampoline page, for
  // trampoline.S.
  if (mappages(pagetable, TRAPFRAME, PGSIZE, (uint64)(p->trapframe),
               PTE_R | PTE_W) < 0) {
    uvmunmap(pagetable, TRAMPOLINE, 1, 0);
    uvmfree(pagetable, 0);
    return 0;
  }

  return pagetable;
}

// Free a process's page table, and free the
// physical memory it refers to.
void
proc_freepagetable(pagetable_t pagetable, uint64 sz)
{
  uvmunmap(pagetable, TRAMPOLINE, 1, 0);
  uvmunmap(pagetable, TRAPFRAME, 1, 0);
  uvmfree(pagetable, sz);
}

// Set up first user process.
void
userinit(void)
{
  struct proc *p;

  p = allocproc();
  initproc = p;

  // "/" resolves without walking a single directory, and this is
  // kernel setup rather than a process asking on its own behalf, so
  // this identity is never consulted -- but the parameter is not
  // optional, and a system identity is what belongs here.
  p->cwd = namei("/", (struct cred){0, 0});

  p->state = RUNNABLE;

  release(&p->lock);
}

// Grow or shrink user memory by n bytes.
// Return 0 on success, -1 on failure.
int
growproc(int n)
{
  uint64 sz;
  struct proc *p = myproc();

  sz = p->sz;
  if (n > 0) {
    if (sz + n > TRAPFRAME) {
      return -1;
    }
    if ((sz = uvmalloc(p->pagetable, sz, sz + n, PTE_W)) == 0) {
      return -1;
    }
  } else if (n < 0) {
    sz = uvmdealloc(p->pagetable, sz, sz + n);
  }
  p->sz = sz;
  return 0;
}

// Create a new process, copying the parent.
// Sets up child kernel stack to return as if from fork() system call.
int
kfork(void)
{
  int i, pid;
  struct proc *np;
  struct proc *p = myproc();

  // Allocate process.
  if ((np = allocproc()) == 0) {
    return -1;
  }

  // Copy user memory from parent to child.
  if (uvmcopy(p->pagetable, np->pagetable, p->sz) < 0) {
    freeproc(np);
    release(&np->lock);
    return -1;
  }
  np->sz = p->sz;
  np->pgid = p->pgid;
  // Identity is inherited, not reset.  A child of a process that dropped
  // its privilege must not be born as root, or the drop would last only
  // until the next fork.  kexec() replaces the address space rather than
  // the process, so exec keeps the identity for the same reason.
  np->uid = p->uid;
  np->gid = p->gid;
  np->sigmask = p->sigmask;
  memmove(np->sighandlers, p->sighandlers, sizeof(np->sighandlers));
  np->sigframe = p->sigframe;
  np->sigframe_mask = p->sigframe_mask;
  np->sigframe_active = p->sigframe_active;

  // copy saved user registers.
  *(np->trapframe) = *(p->trapframe);

  // Cause fork to return 0 in the child.
  np->trapframe->a0 = 0;

  // increment reference counts on open file descriptors.
  for (i = 0; i < NOFILE; i++)
    if (p->ofile[i])
      np->ofile[i] = filedup(p->ofile[i]);
  np->cwd = idup(p->cwd);

  safestrcpy(np->name, p->name, sizeof(p->name));

  pid = np->pid;

  release(&np->lock);

  acquire(&wait_lock);
  np->parent = p;
  release(&wait_lock);

  acquire(&np->lock);
  np->state = RUNNABLE;
  release(&np->lock);

  return pid;
}

// Pass p's abandoned children to init.
// Caller must hold wait_lock.
void
reparent(struct proc *p)
{
  struct proc *pp;

  for (pp = proc; pp < &proc[NPROC]; pp++) {
    if (pp->parent == p) {
      pp->parent = initproc;
      wakeup(initproc);
    }
  }
}

// Exit the current process.  Does not return.
// An exited process remains in the zombie state
// until its parent calls wait().
void
kexit(int status)
{
  struct proc *p = myproc();

  if (p == initproc)
    panic("init exiting");

  // Close all open files.
  for (int fd = 0; fd < NOFILE; fd++) {
    if (p->ofile[fd]) {
      struct file *f = p->ofile[fd];
      fileclose(f);
      p->ofile[fd] = 0;
    }
  }

  begin_op();
  iput(p->cwd);
  end_op();
  p->cwd = 0;

  acquire(&wait_lock);

  // Give any children to init.
  reparent(p);

  // Parent might be sleeping in wait().
  wakeup(p->parent);

  acquire(&p->lock);

  p->xstate = status;
  // Freeze lifetime CPU accounting next to the exit status.  The exit
  // path above (closing files, iput) ran in the kernel and so has
  // already been charged to k_ticks, which is exactly what we want the
  // parent to see.  These are read back without atomics by kwait(),
  // which holds p->lock just like we do here.
  //
  // This is still sampling, so it can be up to one tick short: a timer
  // interrupt landing between this load and the sched() below would
  // charge a tick nobody reports. Same granularity as xstate itself.
  p->xutime = __atomic_load_n(&p->u_ticks, __ATOMIC_RELAXED);
  p->xktime = __atomic_load_n(&p->k_ticks, __ATOMIC_RELAXED);
  p->state = ZOMBIE;

  release(&wait_lock);

  // Jump into the scheduler, never to return.
  sched();
  panic("zombie exit");
}

// Wait for a child process to exit and return its pid.
// Return -1 if this process has no children.
//
// addr receives the exit status (plain wait); uaddr/kaddr receive the
// child's lifetime user/supervisor ticks (waitx).  All three are
// optional and may be 0.  Anything copied out has to be read before
// freeproc() wipes the slot, which is why xstate/xutime/xktime are all
// frozen at exit time rather than read live.
int
kwait(uint64 addr, uint64 uaddr, uint64 kaddr)
{
  struct proc *pp;
  int havekids, pid;
  struct proc *p = myproc();

  acquire(&wait_lock);

  for (;;) {
    // Scan through table looking for exited children.
    havekids = 0;
    for (pp = proc; pp < &proc[NPROC]; pp++) {
      if (pp->parent == p) {
        // make sure the child isn't still in exit() or swtch().
        acquire(&pp->lock);

        havekids = 1;
        if (pp->state == ZOMBIE) {
          // Found one.
          pid = pp->pid;
          // Short-circuiting || stops at the first failure, leaving any
          // later destination untouched, same as plain wait() does.
          if ((addr != 0 &&
               copyout(p->pagetable, p->sz, addr, (char *)&pp->xstate,
                       sizeof(pp->xstate)) < 0) ||
              (uaddr != 0 &&
               copyout(p->pagetable, p->sz, uaddr, (char *)&pp->xutime,
                       sizeof(pp->xutime)) < 0) ||
              (kaddr != 0 &&
               copyout(p->pagetable, p->sz, kaddr, (char *)&pp->xktime,
                       sizeof(pp->xktime)) < 0)) {
            release(&pp->lock);
            release(&wait_lock);
            return -1;
          }
          pp->parent = 0;
          freeproc(pp);
          release(&pp->lock);
          release(&wait_lock);
          return pid;
        }
        release(&pp->lock);
      }
    }

    // No point waiting if we don't have any children.
    if (!havekids || killed(p) || signal_pending(p)) {
      release(&wait_lock);
      return -1;
    }

    // Wait for a child to exit.
    sleep_prepare(p); //DOC: wait-sleep
    release(&wait_lock);
    sleep();
    acquire(&wait_lock);
  }
}

// Per-CPU process scheduler.
// Each CPU calls scheduler() after setting itself up.
// Scheduler never returns.  It loops, doing:
//  - choose a process to run.
//  - swtch to start running that process.
//  - eventually that process transfers control
//    via swtch back to the scheduler.
void
scheduler(void)
{
  struct proc *p;
  struct cpu *c = mycpu();

  c->proc = 0;
  for (;;) {
    // The most recent process to run may have had interrupts
    // turned off; enable them to avoid a deadlock if all
    // processes are waiting. Then turn them back off
    // to avoid a possible race between an interrupt
    // and wfi.
    intr_on();
    intr_off();

    // One pass both chooses and ages.  `best` is the runnable process
    // with the smallest dynamic priority; ties go to the earlier slot, and
    // because every loser gains a notch the tie cannot persist.  The
    // pointer stays valid whatever happens next -- the proc array is
    // static -- but the state must be re-checked under the lock below:
    // the winner may have slept, exited, or had its slot recycled during
    // the pass.
    struct proc *best = 0;
    for (p = proc; p < &proc[NPROC]; p++) {
      acquire(&p->lock);
      if (p->state == RUNNABLE) {
        if (best == 0 || p->cur_prio < best->cur_prio)
          best = p;
        // Aging: a process that keeps losing becomes more urgent.
        if (p->cur_prio > PRIO_AGING_FLOOR)
          p->cur_prio--;
      }
      release(&p->lock);
    }

    int found = 0;
    if (best) {
      acquire(&best->lock);
      if (best->state == RUNNABLE) {
        // It has had its turn, so back to its static priority.
        best->cur_prio = best->prio;
        best->state = RUNNING;
        c->proc = best;
        swtch(&c->context, &best->context);

        // Don't re-enable interrupts on release.
        mycpu()->intena = 0;

        // Process is done running for now.
        // It should have changed its p->state before coming back.
        c->proc = 0;
        found = 1;
      }
      release(&best->lock);
    }

    if (found == 0) {
      // nothing to run; stop running on this core until an interrupt.
      asm volatile("wfi");
    }
  }
}

// Switch to scheduler.  Must hold only p->lock
// and have changed proc->state. Saves and restores
// intena because intena is a property of this
// kernel thread, not this CPU. It should
// be proc->intena and proc->noff, but that would
// break in the few places where a lock is held but
// there's no process.
void
sched(void)
{
  int intena;
  struct proc *p = myproc();

  if (!holding(&p->lock))
    panic("sched p->lock");
  if (mycpu()->noff != 1)
    panic("sched locks");
  if (p->state == RUNNING)
    panic("sched RUNNING");
  if (intr_get())
    panic("sched interruptible");

  intena = mycpu()->intena;
  swtch(&p->context, &mycpu()->context);
  mycpu()->intena = intena;
}

// Give up the CPU for one scheduling round.
void
yield(void)
{
  struct proc *p = myproc();
  acquire(&p->lock);
  p->state = RUNNABLE;
  sched();
  release(&p->lock);
}

// A fork child's very first scheduling by scheduler()
// will swtch to forkret.
void
forkret(void)
{
  extern char userret[];
  static int first = 1;
  struct proc *p = myproc();

  // Still holding p->lock from scheduler.
  release(&p->lock);

  if (__atomic_load_n(&first, __ATOMIC_ACQUIRE)) {
    // File system initialization must be run in the context of a
    // regular process (e.g., because it calls sleep), and thus cannot
    // be run from main().
    fsinit(ROOTDEV);

    // ensure other cores see first=0.
    __atomic_store_n(&first, 0, __ATOMIC_RELEASE);

    // We can invoke kexec() now that file system is initialized.
    // Put the return value (argc) of kexec into a0.
    p->trapframe->a0 = kexec("/init", (char *[]){"/init", 0});
    if (p->trapframe->a0 == -1) {
      panic("exec");
    }
  }

  // return to user space, mimicing usertrap()'s return.
  prepare_return();
  uint64 satp = MAKE_SATP(p->pagetable);
  uint64 trampoline_userret = TRAMPOLINE + (userret - trampoline);
  ((void (*)(uint64))trampoline_userret)(satp);
}

// Register current process as waiting for wakeups on chan.
void
sleep_prepare(void *chan)
{
  struct proc *p = myproc();

  acquire(&p->lock);
  if (chan == 0)
    panic("sleep_prepare: zero chan");
  p->chan = chan;
  release(&p->lock);
}

// Put the thread to sleep.  Assumes sleep_prepare() was called before.
// If the channel registered by sleep_prepare() has been woken up in
// the meantime, do not go to sleep, and instead return immediately.
void
sleep(void)
{
  struct proc *p = myproc();

  acquire(&p->lock);
  if (p->chan != 0) {
    p->state = SLEEPING;
    sched();
  }
  release(&p->lock);
}

// Wake up all processes sleeping on channel chan.
void
wakeup(void *chan)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->chan == chan) {
      // If the process is waiting for wakeups on this channel,
      // signal that the wakeup happened by clearing p->chan.
      p->chan = 0;

      // If this waiting process has gotten so far as to actually
      // go to sleep, also set it back to RUNNING.
      if (p->state == SLEEPING) {
        p->state = RUNNABLE;
      }
    }
    release(&p->lock);
  }
}

// Kill the process with the given pid.
// The victim won't exit until it tries to return
// to user space (see usertrap() in trap.c).
int
kkill(int pid)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pid == pid) {
      p->killed = 1;
      if (p->state == SLEEPING || p->state == STOPPED) {
        // Wake a sleeping or stopped process.
        p->chan = 0;
        p->state = RUNNABLE;
      }
      release(&p->lock);
      return 0;
    }
    release(&p->lock);
  }
  return -1;
}

int
ksignal(int pid, int sig)
{
  struct proc *p;
  if (sig < 1 || sig > NSIG)
    return -1;
  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pid == pid && p->state != UNUSED && p->state != ZOMBIE) {
      p->pending |= 1U << sig;
      if (p->state == STOPPED && sig != 4)
        p->state = RUNNABLE;
      else if (p->state == SLEEPING) {
        p->chan = 0;
        p->state = RUNNABLE;
      }
      release(&p->lock);
      return 0;
    }
    release(&p->lock);
  }
  return -1;
}

int
ksignalpg(int pgid, int sig)
{
  int found = 0;
  struct proc *p;
  if (pgid <= 0 || sig < 1 || sig > NSIG)
    return -1;
  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pgid == pgid && p->state != UNUSED && p->state != ZOMBIE) {
      p->pending |= 1U << sig;
      if (p->state == STOPPED && sig != 4)
        p->state = RUNNABLE;
      else if (p->state == SLEEPING) {
        p->chan = 0;
        p->state = RUNNABLE;
      }
      found = 1;
    }
    release(&p->lock);
  }
  return found ? 0 : -1;
}

int
ksetpgid(int pid, int pgid)
{
  struct proc *p;
  if (pgid <= 0)
    return -1;
  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pid == pid && p->state != UNUSED && p->state != ZOMBIE) {
      p->pgid = pgid;
      release(&p->lock);
      return 0;
    }
    release(&p->lock);
  }
  return -1;
}

int
kgetpgid(void)
{
  return myproc()->pgid;
}

int
kjobstate(int pgid)
{
  int exists = 0, running = 0;
  struct proc *p;
  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pgid == pgid && p->state != UNUSED && p->state != ZOMBIE) {
      exists = 1;
      if (p->state != STOPPED)
        running = 1;
    }
    release(&p->lock);
  }
  return !exists ? 0 : running ? 1 : 2;
}

int
kwaitpg(int pgid, uint64 addr)
{
  struct proc *pp;
  struct proc *p = myproc();
  int havekids, pid, status;
  acquire(&wait_lock);
  for (;;) {
    havekids = 0;
    for (pp = proc; pp < &proc[NPROC]; pp++) {
      if (pp->parent != p)
        continue;
      acquire(&pp->lock);
      if (pp->pgid == pgid) {
        havekids = 1;
        if (pp->state == STOPPED) {
          pid = pp->pid;
          status = -2;
          if (addr && copyout(p->pagetable, p->sz, addr, (char *)&status,
                              sizeof(status)) < 0) {
            release(&pp->lock);
            release(&wait_lock);
            return -1;
          }
          release(&pp->lock);
          release(&wait_lock);
          return pid;
        }
        if (pp->state == ZOMBIE) {
          pid = pp->pid;
          if (addr && copyout(p->pagetable, p->sz, addr, (char *)&pp->xstate,
                              sizeof(pp->xstate)) < 0) {
            release(&pp->lock);
            release(&wait_lock);
            return -1;
          }
          pp->parent = 0;
          freeproc(pp);
          release(&pp->lock);
          release(&wait_lock);
          return pid;
        }
      }
      release(&pp->lock);
    }
    if (!havekids || killed(p) || signal_pending(p)) {
      release(&wait_lock);
      return -1;
    }
    sleep_prepare(p);
    release(&wait_lock);
    sleep();
    acquire(&wait_lock);
  }
}

void
tty_setpgid(int pgid)
{
  acquire(&tty_lock);
  tty_pgid = pgid;
  release(&tty_lock);
}

void
tty_interrupt(void)
{
  int pgid;
  acquire(&tty_lock);
  pgid = tty_pgid;
  release(&tty_lock);
  if (pgid > 0)
    ksignalpg(pgid, 2);
}

int
tty_signal(int sig)
{
  int pgid;
  acquire(&tty_lock);
  pgid = tty_pgid;
  release(&tty_lock);
  return pgid > 0 ? ksignalpg(pgid, sig) : -1;
}

int
signal_pending(struct proc *p)
{
  int yes;
  acquire(&p->lock);
  yes = (p->pending & ~p->sigmask) != 0;
  release(&p->lock);
  return yes;
}

void
signal_deliver(struct proc *p)
{
  int sig;
  uint64 handler;
  acquire(&p->lock);
  for (sig = 1; sig <= NSIG; sig++)
    if ((p->pending & (1U << sig)) && (sig == 3 || !(p->sigmask & (1U << sig))))
      break;
  if (sig > NSIG) {
    release(&p->lock);
    return;
  }
  p->pending &= ~(1U << sig);
  handler = p->sighandlers[sig];
  if (sig == 3 || sig == 4 || handler == 0) {
    if (sig == 6) {
      release(&p->lock);
      return;
    }
    if (sig == 4 || sig == 5) {
      p->state = STOPPED;
      sched();
      release(&p->lock);
      signal_deliver(p);
      return;
    }
    release(&p->lock);
    kexit(128 + sig);
  }
  if (handler == (uint64)-1) {
    release(&p->lock);
    return;
  }
  handler--;
  p->sigframe = *p->trapframe;
  p->sigframe_mask = p->sigmask;
  p->sigmask = 0xffffffffU;
  p->sigmask &= ~((1U << 3) | (1U << 4));
  p->sigframe_active = 1;
  p->trapframe->epc = handler;
  p->trapframe->a0 = sig;
  release(&p->lock);
}

int
ksigaction(int sig, uint64 handler)
{
  struct proc *p = myproc();
  pte_t *pte;
  if (sig < 1 || sig > NSIG || sig == 3 || sig == 4)
    return -1;
  if (handler != (uint64)-1 && handler != (uint64)-2) {
    pte = walk(p->pagetable, handler, 0);
    if (handler >= p->sz || pte == 0 || !(*pte & PTE_V) || !(*pte & PTE_U) ||
        !(*pte & PTE_X))
      return -1;
  }
  acquire(&p->lock);
  if (handler == (uint64)-1)
    p->sighandlers[sig] = 0;
  else if (handler == (uint64)-2)
    p->sighandlers[sig] = (uint64)-1;
  else
    p->sighandlers[sig] = handler + 1;
  release(&p->lock);
  return 0;
}

int
ksigmask(uint mask)
{
  struct proc *p = myproc();
  acquire(&p->lock);
  p->sigmask = mask & ~((1U << 3) | (1U << 4) | (1U << 6));
  release(&p->lock);
  return 0;
}

int
ksigreturn(void)
{
  struct proc *p = myproc();
  acquire(&p->lock);
  if (!p->sigframe_active) {
    release(&p->lock);
    return -1;
  }
  *p->trapframe = p->sigframe;
  p->sigmask = p->sigframe_mask;
  p->sigframe_active = 0;
  release(&p->lock);
  return p->sigframe.a0;
}

// Set the scheduling priority of process pid.  The caller has already
// clamped prio to PRIO_HIGHEST..PRIO_LOWEST; see sys_setprio().
//
// xv6 has no uid/gid, so there is no permission check: any process may
// change any other process priority.  That is a teaching
// simplification, stated here on purpose rather than left implicit.
int
ksetprio(int pid, int prio)
{
  struct proc *p;

  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->pid == pid && p->state != UNUSED) {
      p->prio = prio;
      // Take effect at the next scheduling decision rather than waiting
      // for the aging pass to walk the number down.
      p->cur_prio = prio;
      release(&p->lock);
      return 0;
    }
    release(&p->lock);
  }
  return -1;
}

// Set the calling process's user identity.
//
// The entire privilege model is this one rule: uid 0 may set any uid, and
// anyone else may only "set" the uid it already has.  The no-op form is not
// a courtesy -- it lets a program that lowers its own privilege call this
// unconditionally, with no branch on "am I root".
//
// There is no saved-uid and no setuid bit, so the change is one-way: once a
// process is no longer root it can never become root again.  That is
// deliberate.  Remembering the previous uid so it can be restored is exactly
// the mechanism that turns a bug in a privileged program into an escalation.
int
ksetuid(int uid)
{
  struct proc *p = myproc();

  if (uid < 0 || uid > 65535)
    return -1;

  acquire(&p->lock);
  if (p->uid != 0 && uid != p->uid) {
    release(&p->lock);
    return -1;
  }
  p->uid = uid;
  release(&p->lock);
  return 0;
}

// Set the calling process's primary group.  Same rule as ksetuid().
//
// A separate call rather than "the group follows the user", because a group
// is only useful if two accounts can share one.  With gid == uid the group
// permission bits would be a copy of the owner bits and could never grant
// anything the owner class had not already granted.
//
// Ordering matters for a caller that changes both: set the group FIRST,
// while still privileged.  After ksetuid() a non-zero process can no longer
// change its group.
int
ksetgid(int gid)
{
  struct proc *p = myproc();

  if (gid < 0 || gid > 65535)
    return -1;

  acquire(&p->lock);
  if (p->uid != 0 && gid != p->gid) {
    release(&p->lock);
    return -1;
  }
  p->gid = gid;
  release(&p->lock);
  return 0;
}

void
setkilled(struct proc *p)
{
  acquire(&p->lock);
  p->killed = 1;
  release(&p->lock);
}

int
killed(struct proc *p)
{
  int k;

  acquire(&p->lock);
  k = p->killed;
  release(&p->lock);
  return k;
}

// Copy to either a user address, or kernel address,
// depending on usr_dst.
// Returns 0 on success, -1 on error.
int
either_copyout(int user_dst, uint64 dst, void *src, uint64 len)
{
  struct proc *p = myproc();
  if (user_dst) {
    return copyout(p->pagetable, p->sz, dst, src, len);
  } else {
    memmove((char *)dst, src, len);
    return 0;
  }
}

// Copy from either a user address, or kernel address,
// depending on usr_src.
// Returns 0 on success, -1 on error.
int
either_copyin(void *dst, int user_src, uint64 src, uint64 len)
{
  struct proc *p = myproc();
  if (user_src) {
    return copyin(p->pagetable, p->sz, dst, src, len);
  } else {
    memmove(dst, (char *)src, len);
    return 0;
  }
}

// Snapshot the live process table into the calling process's user buffer
// at uaddr, which must have room for at least max struct psinfo's.
// Returns the number of entries written (<= max), or -1 on error.
//
// Locking: wait_lock must be held to read p->parent, and it must be
// acquired before any p->lock.  p->lock is taken one entry at a time to
// read the fields it protects (pid/state) and to get a consistent view of
// the process-private fields (name/sz).  copyout is NOT done while
// holding a lock: it can fault, which leads to kalloc() and thus to
// kmem.lock, so instead we fill a kernel scratch page and copy it out
// after dropping every lock.
int
psinfo(uint64 uaddr, int max)
{
  struct proc *p, *cur = myproc();
  struct psinfo *kbuf, *e;
  int n = 0, copy_n, r;

  if (max <= 0)
    return 0;
  if (max > NPROC)
    max = NPROC;

  // Allocate scratch before taking any lock: kalloc() itself takes
  // kmem.lock, and one page holds NPROC entries.
  if ((kbuf = (struct psinfo *)kalloc()) == 0)
    return -1;

  acquire(&wait_lock);
  e = kbuf;
  for (p = proc; p < &proc[NPROC]; p++) {
    acquire(&p->lock);
    if (p->state != UNUSED) {
      if (n < max) {
        e->pid = p->pid;
        e->ppid = p->parent ? p->parent->pid : 0;
        e->state = p->state;
        e->prio = p->prio;
        e->sz = p->sz;
        // Walk the page table while still holding p->lock, so the table
        // cannot be torn down underneath us.  This is a tree walk, not
        // one walk() per virtual page, because sz is handed out lazily
        // and may describe far more address space than is mapped.
        e->rss = vm_rss(p->pagetable, p->sz);
        // Updated lock-free by whichever hart is running p, so read
        // them with relaxed atomics for a consistent snapshot.
        e->u_ticks = __atomic_load_n(&p->u_ticks, __ATOMIC_RELAXED);
        e->k_ticks = __atomic_load_n(&p->k_ticks, __ATOMIC_RELAXED);
        // name is a fixed 16 bytes and may have no NUL if exactly full,
        // so copy the whole field rather than using strlen.
        memmove(e->name, p->name, sizeof(p->name));
        e++;
      }
      n++;
    }
    release(&p->lock);
  }
  release(&wait_lock);

  copy_n = (n < max) ? n : max;
  r = (copy_n == 0) ? 0
                    : copyout(cur->pagetable, cur->sz, uaddr, (char *)kbuf,
                              (uint64)copy_n * sizeof(struct psinfo));
  kfree(kbuf);
  if (r < 0)
    return -1;
  return copy_n;
}

// Print a process listing to console.  For debugging.
// Runs when user types ^P on console.
// No lock to avoid wedging a stuck machine further.
void
procdump(void)
{
  static char *states[] = {
    // clang-format off
    [UNUSED]    = "unused",
    [USED]      = "used",
    [SLEEPING]  = "sleep ",
    [RUNNABLE]  = "runble",
    [RUNNING]   = "run   ",
    [ZOMBIE]    = "zombie"
    // clang-format on
  };
  struct proc *p;
  char *state;

  printk("\n");
  for (p = proc; p < &proc[NPROC]; p++) {
    if (p->state == UNUSED)
      continue;
    if (p->state >= 0 && p->state < NELEM(states) && states[p->state])
      state = states[p->state];
    else
      state = "???";
    printk("%d %s %s", p->pid, state, p->name);
    printk("\n");
  }
}
