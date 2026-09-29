# HachileiOS

> 一个在 xv6-riscv 之上逐步演进的 miniOS —— 以学习操作系统原理为目标。
>
> A miniOS evolving on top of xv6-riscv, built for learning OS principles.

---

## 项目定位 / About

**中文：** 本项目以 MIT 6.1810 教学操作系统 **xv6-riscv**（`riscv` 分支，基线 HEAD `9e3161a`）为起点，逐步演进为一个 "miniOS"。目标不是做一个"看起来像操作系统"的演示，而是以**可快速追加、可独立验证**的小步前进方式，补全操作系统概念（进程、内存、锁、日志、CPU 统计），并在每一步都保留真实的工程权衡。

**English:** This project starts from MIT 6.1810's teaching operating system **xv6-riscv** (`riscv` branch, baseline HEAD `9e3161a`) and evolves it into a "miniOS". The goal is not a demo that merely *looks* like an OS, but a sequence of **small, quickly appended, independently verifiable** steps that fill in operating-system concepts (processes, memory, locking, logging, CPU accounting) while preserving the real engineering trade-offs at each step.

### 设计原则 / Design principles

| 中文 | English |
| --- | --- |
| 每次只加一个能独立验证的能力 | Add one independently verifiable capability at a time |
| 优先复用已有内核设施，避免大规模重写 | Reuse existing kernel machinery; avoid large rewrites |
| 并发/一致性上的权衡必须显式写进注释 | Make concurrency and consistency trade-offs explicit in comments |
| 每批改动后跑完整 `usertests` 回归 | Run the full `usertests` regression after every batch |

---

## 快速开始 / Quick Start

**依赖 / Requirements**

- RISC-V 工具链：`riscv64-unknown-elf-` 或 `riscv64-linux-gnu-`
- `qemu-system-riscv64`

**构建与运行 / Build and run**

```sh
make qemu          # 构建内核 + fs.img 并启动 qemu
make clean         # 清理构建产物
```

默认启动参数由 `Makefile` 给出：`-m 128M -smp 3`（128 MiB 内存，3 个 hart）。

The default QEMU invocation (from the `Makefile`) is `-m 128M -smp 3`.

**新增的用户程序 / New user programs**

在 `$` 提示符下可直接运行：

| 命令 / Command | 作用 / Purpose |
| --- | --- |
| `ps` | 列出进程，含用户态/内核态 CPU 时间 |
| `free` | 物理内存总量 / 已用 / 空闲 |
| `dmesg` | 回放内核日志环形缓冲区 |
| `top [n]` | 周期性刷新进程表 + CPU 增量（默认 10 次） |
| `neofetch` | 一次性系统概览（含在线 hart 数、磁盘 I/O） |
| `cputest [ticks]` | CPU 时间统计的自检程序（默认累计 20 tick） |
| `df` | 文件系统用量（块 / inode） |
| `waitxtest [ticks]` | 子进程 CPU 时间回收的自检程序（默认 5 tick） |
| `help [command]` | 命令索引；列出可用命令并标注是 xv6 原版还是 miniOS 新增 |
| `priotest [ticks]` | 调度优先级与老化的自检程序（默认各跑 20 tick） |
| `kmemtest` | 分配器会计与泄漏的自检程序 |
| `cowtest` | 写时复制 fork 的自检程序（共享代价、写隔离、copyout 与三代共享）|

镜像里共有 **32** 个用户程序（即 `UPROGS` 的 32 项）。不带参数运行 `help` 会按类别列出其中 **31 条**——除 `help` 自身以外的全部命令——并把 miniOS 新增的 11 条用 `*` 标出；`help ps` 则只显示该命令的用法与补充细节。`help` 本身是 miniOS 的第 12 个新增程序，没有把自己列进索引（一个刻意的取舍），但它与其他命令一样只是根目录里的普通程序，`ls` 能看到它。xv6 的 shell 没有内建命令，因此命令索引本身也只能是一个普通程序。

The image holds **32** user programs (the 32 `UPROGS` entries). Run with no arguments, `help` lists **31** of them grouped by category — every command except `help` itself — marking the 11 added by this project with `*`; `help ps` shows just that entry. `help` is itself the 11th program this project added; it deliberately does not index itself, but it is otherwise an ordinary program in the root directory, visible to `ls`. xv6's shell has no built-ins, so the index has to be an ordinary program too.

```
$ ps
pid  ppid state  vsz  rss  usr sys prio name
1  0  sleep  16  16  0  0  5  init
2  1  sleep  20  20  0  0  5  sh
3  2  run  20  20  0  0  5  ps
(vsz/rss are KB, usr/sys are timer ticks, 1 tick = 100 ms)
```

默认构建里 `exec()` 与库函数 `sbrk()` 都立即分配内存，所以 `rss` 恰好等于 `vsz`；只有程序显式使用惰性 `sbrk`（`sbrklazy()`，目前仅 `usertests` 的惰性测试用到）时，`vsz` 才会远大于 `rss`。

In the default build both `exec()` and the `sbrk()` library call allocate eagerly, so `rss` equals `vsz`; the two diverge only when a program explicitly opts into lazy `sbrk` (`sbrklazy()`, used only by `usertests`' lazy tests today).

---

## 已实现特性 / Implemented Features

### 总览 / Overview

| 批次 / Batch | 特性 / Feature | 系统调用或程序 / Syscall or program | 状态 |
| --- | --- | --- | --- |
| 1 | 进程表快照 / Process table snapshot | `psinfo()` (23) → `ps` | ✅ 已验证 |
| 1 | 空闲物理内存 / Free physical memory | `freemem()` (24) → `free` | ✅ 已验证 |
| 2 | 内核日志环形缓冲区 / Kernel log ring buffer | `klog()` (25) → `dmesg` | ✅ 已验证 |
| 2 | 周期刷新视图 / Periodic process view | `top` | ✅ 已验证 |
| 2 | 系统概览 / System summary | `neofetch` | ✅ 已验证 |
| 3 | 每进程 CPU 时间 / Per-process CPU time | `psinfo` 扩展字段 → `ps` / `top` / `cputest` | ✅ 已验证 |
| 4 | 驻留内存大小 / Resident set size | `psinfo` 扩展字段 → `ps` / `top` | ✅ 已验证 |
| 4 | 子进程 CPU 时间回收 / Child CPU accounting | `waitx()` (26) → `waitxtest` | ✅ 已验证 |
| 4 | 文件系统用量 / File system usage | `fsinfo()` (27) → `df` | ✅ 已验证 |
| 4 | 命令索引 / Command index | `help` | ✅ 已验证 |
| 5 | 数据块口径 / Data-block view | `fsinfo` 扩展字段 → `df` | ✅ 已验证 |
| 5 | 运行时在线 hart 数 / Online hart count | `sysinfo()` (28) → `neofetch` | ✅ 已验证 |
| 5 | 磁盘 I/O 与缓存命中 / Disk I/O & cache hits | `sysinfo()` (28) → `neofetch` | ✅ 已验证 |
| 5 | 按需分页计数 / Demand paging count | `sysinfo()` (28) → `neofetch` | ✅ 已验证 |
| 6 | 调度器优先级 / Scheduler priorities | `setprio()` (29) → `ps` / `priotest` | ✅ 已验证 |
| 7 | 页状态表与分配器自检 / Per-page state table | `kalloc_stats()` → `sysinfo` (28) → `kmemtest` | ✅ 已验证 |
| 8 | 写时复制 fork / Copy-on-write fork | `kref()`/`kfree()` 引用计数 → `cowfault()` → `cowtest` | ✅ 已验证 |

---

### 批次 1 — 进程与内存可观测性 / Batch 1 — Process & memory observability

**`psinfo()` 系统调用（`SYS_psinfo = 23`）**

- 原型 / Prototype: `int psinfo(struct psinfo *buf, int max);`
- 在内核中**一次性快照整个进程表**到一个 `kalloc` 页，然后一次 `copyout` 给用户，返回写入的条目数。
  One kernel-allocated page holds a snapshot of the whole process table; it is copied out in a single `copyout`. Returns the number of entries written.
- 布局定义在 `kernel/psinfo.h`，被内核和用户态**共同包含**，因此两边的结构体布局必须完全一致。
  The layout lives in `kernel/psinfo.h` and is included by **both** kernel and user code.

| 字段 / Field | 含义 / Meaning |
| --- | --- |
| `pid` / `ppid` | 进程号 / 父进程号 |
| `state` | `PSTATE_UNUSED` … `PSTATE_ZOMBIE`（数值与 `enum procstate` 对齐） |
| `sz` | 虚拟内存大小（**不是**常驻内存 RSS） |
| `rss` | 常驻内存大小：`va < sz` 的已映射页数 × `PGSIZE`（批次 4 加入） |
| `u_ticks` / `k_ticks` | 用户态 / 内核态累计 tick（批次 3 加入） |
| `name[16]` | 进程名，保证 NUL 结尾 |

批次 1 引入时 `sizeof(struct psinfo) == 56`；批次 3 加入 CPU 计数、批次 4 加入 `rss` 后，它变成**正好 64 字节**。内核用编译期断言强制「整张进程表的快照能放进一个 `kalloc()` 页」：

Introduced at 56 bytes in batch 1, it grew to **exactly 64** once batches 3 and 4 added the CPU counters and `rss`. The kernel enforces "the whole process-table snapshot fits in one `kalloc()` page" at compile time:

```c
typedef char psinfo_fits_one_page
    [(NPROC * sizeof(struct psinfo) <= PGSIZE) ? 1 : -1];
```

`64 × NPROC(64) = 4096 = PGSIZE`：断言仍然成立，但余量已经为零（见批次 4 的警示框）。

`64 * NPROC(64) = 4096 = PGSIZE`: the assertion still holds, but with zero headroom (see the batch 4 warning).

**`freemem()` 系统调用（`SYS_freemem = 24`）**

- 原型 / Prototype: `uint64 freemem(void);` —— 返回空闲物理内存字节数。
- 实现为 O(1)：`kernel/kalloc.c` 中的 `kmem.nfree` 计数器在 `kalloc`/`kfree` 时增减。
  This is O(1): the `kmem.nfree` counter is maintained by `kalloc`/`kfree`.
- **参考实现** `freemem_walk()` 以 O(N) 遍历空闲链表，用于校验 O(1) 计数器（`free` 不直接使用它，它是对照组）。
  `freemem_walk()` is the O(N) reference used to validate the counter.

**`ps` / `free` 程序**

- `ps` 把状态码翻译成可读字符串（`sleep` / `run` / `zombie` …）。
- `free` 用 `kernel/minios.h` 的 `MINIOS_MEM_TOTAL` 计算"已用"，以 字节 / KB / 页 三种单位输出。
  `free` derives "used" from `MINIOS_MEM_TOTAL` and prints bytes / KB / pages.

---

### 批次 2 — 内核日志与系统概览 / Batch 2 — Kernel log & system overview

**无锁日志环形缓冲区 / Lock-free log ring buffer**（`kernel/printk.c`）

- 16 KiB 环形缓冲区，`printk` 的**每一个字符**都被复制一份进去。tee 点位于 `printk.c` 内部的 `kputc()`，**不在** `consputc()` —— 因此控制台的**输入回显不会被记入日志**。
  A 16 KiB ring captures every character `printk` emits. The tee lives in `kputc()` inside `printk.c`, **not** in `consputc()`, so console *input echo* is not captured.
- 刻意做成**无锁**：`panic()` 必须在不能获取任何锁的情况下也能记录日志。
  Deliberately lock-free: `panic()` must be able to log without acquiring any lock.
- 写者先写字节、再用 **release** 存储发布写索引；读者用 **acquire** 加载索引，保证读到的字节已写完。
  The writer stores the byte, then publishes the index with a **release** store; a reader that **acquire**-loads the index is guaranteed to see a fully written byte.

```c
static void
klog_putc(int c)
{
  uint64 w = __atomic_load_n(&klog_w, __ATOMIC_RELAXED);
  klogbuf[w % KLOGSIZE] = c;
  __atomic_store_n(&klog_w, w + 1, __ATOMIC_RELEASE);
}
```

**`klog()` 系统调用（`SYS_klog = 25`）**

- 原型 / Prototype: `int klog(char *buf, int max, uint64 *seq, uint64 *lost);`
- `*seq` 是**输入/输出游标**：绝对单调递增的字节索引，由用户态保存，因此**内核不需要为每个进程维护任何读取状态**。
  `*seq` is an in/out cursor — an absolute monotonic byte index kept by user space, so the kernel stores **no per-process reader state**.
- 游标被环形缓冲区覆盖时，通过 `*lost` 报告"永远看不到的字节数"。
  If the cursor has been overwritten, `*lost` reports how many bytes the caller will never see.
- 读取时**不持锁**跨 `copyout`（`copyout` 可能触发缺页 → `kalloc` → `kmem.lock`）。
  No lock is held across `copyout` (which can fault → `kalloc` → `kmem.lock`).

**程序 / Programs**

- `dmesg` —— 用 in/out 游标分块排空环形缓冲区；有界轮数（`MAXROUNDS`）保证在内核持续打印时也能终止。
  `dmesg` drains the ring in chunks using the cursor; a bounded round count guarantees termination even while the kernel keeps logging.
- `top [n]` —— 每轮 ANSI 清屏刷新，显示进程表 + 空闲内存 + uptime，默认刷新 10 次后退出。
  Refreshes an ANSI-cleared screen with the process table, free memory and uptime; exits after a bounded number of refreshes.
- `neofetch` —— 一次性打印架构、CPU 上限、内存、进程数、uptime。
- `kernel/minios.h` —— 内核与用户态共享的系统常量（`MINIOS_MEM_TOTAL`）。

---

### 批次 3 — CPU 时间统计 / Batch 3 — CPU time accounting

**内核侧 / Kernel side**（`kernel/trap.c` 的 `clockintr()`）

定时器中断是**每个 hart 各自触发**的，而全局 `ticks` 只在 hart 0 上递增，因此不能用它来归属 CPU 时间。我们在每次时钟中断时，把这一格记到**当前 hart 上正在运行的进程**头上：

Timer interrupts fire **per-hart**, while the global `ticks` counter only advances on hart 0 — so it cannot be used to attribute CPU time. Each tick is charged to the process running on the current hart:

```c
struct proc *p = myproc();
if (p) {
  if (r_sstatus() & SSTATUS_SPP)
    __atomic_fetch_add(&p->k_ticks, 1, __ATOMIC_RELAXED);
  else
    __atomic_fetch_add(&p->u_ticks, 1, __ATOMIC_RELAXED);
}
```

- **用户态 / 内核态拆分**：用 `sstatus.SPP` 判断陷入来源（0 = 用户态，1 = 监督态）。
  The user/supervisor split comes from `sstatus.SPP`.
- **不需要 `p->lock`**：计数器只有一个写者（当前运行 `p` 的那个 hart），读者只需要一个单调快照，因此用 **relaxed atomic** 即可；在定时器中断里加自旋锁纯属浪费。
  No `p->lock` is needed: there is exactly one writer (the hart running `p`), and a reader only wants a monotonic snapshot, so a **relaxed atomic** suffices. Taking a spinlock in the timer ISR would be pure overhead.
- **槽位复用要清零**：`allocproc()` 在 `found:` 处把两个计数器清零，否则回收的进程槽会继承上一个进程的时间。`kfork()` 不复制这两个字段，子进程从 0 开始。
  `allocproc()` zeroes both counters so a recycled slot does not inherit the previous process's time; children start at 0.

**用户侧 / User side**

- `ps` 增加 `usr` / `sys` 两列（累计 tick）。
- `top` 额外维护上一轮快照，按 pid 匹配后输出增量 `dcpu` 与 `busy% = (du+dk)×100/elapsed`。pid 被复用时计数器会倒退，此时丢弃基线并显示 `-`。
  `top` keeps the previous snapshot and, after matching by pid, prints the delta `dcpu` and `busy%`. If a pid was recycled the counters go backwards, so the baseline is discarded and `-` is shown.
- `cputest` —— 两阶段自检程序，把进程自身的时间统计与全局 `uptime()` 交叉验证：
  `cputest` cross-checks per-process accounting against the global `uptime()`:
  - **阶段 1（用户态密集）**：在用户态批量自旋，断言 `du ≈ elapsed`。
    Phase 1 (user-heavy): spin in user mode; assert `du ≈ elapsed`.
  - **阶段 2（内核态密集）**：批量调用较重的 `psinfo()`，断言 `sys` 占主导（用户程序永远无法达到 100% 系统时间，因为发起系统调用的循环本身就是用户代码）。
    Phase 2 (sys-heavy): hammer the relatively heavy `psinfo()`; assert `sys` dominates — a user program can never reach 100% system time, because the loop issuing the syscalls is itself user code.

---

### 批次 4 — 补齐三个观测盲点 / Batch 4 — Closing three observability gaps

批次 4 不引入任何新机制，只是把批次 1–3 已经铺好的骨架填完整，所以每一项都能独立验证。

Batch 4 introduces no new machinery; it fills in three gaps in the skeleton batches 1-3 already built, so each item is independently verifiable.

**RSS：驻留内存大小 / Resident set size**

`ps` / `top` 原先只能报 `sz`，即 `growproc()` 交出去的**虚拟**地址空间；在惰性分配下它会远大于实际占用。`struct psinfo` 新增 `rss` 字段，由 `kernel/vm.c` 的 `vm_rss()` 计算。

English recap: `sz` is virtual size granted by `growproc()`; under lazy `sbrk` it can far exceed real usage. `rss`, computed by `vm_rss()` in `kernel/vm.c`, reports what is actually mapped.

```c
uint64
vm_rss(pagetable_t pagetable, uint64 sz)
{
  uint64 acc = 0;
  if (pagetable == 0 || sz == 0)
    return 0;
  rsswalk(pagetable, 2, 0, PGROUNDUP(sz), &acc);
  return acc;
}
```

三个决定都能追溯到成本与正确性：

Three decisions worth spelling out:

- **遍历页表树，而不是逐页调用 `walk()`。** 惰性 sbrk 下 `sz` 可以很大而几乎没有映射，按虚拟页循环的成本正比于 `sz / PGSIZE`；像 `freewalk()` 那样下降三级页表，成本只正比于真正映射的页数加上承载它们的页表页。
  Walking the tree rather than looping over virtual pages: with lazy allocation the tree walk costs time proportional to what is actually mapped, not to `sz`.
- **只统计 `va < sz` 的叶子。** 因此位于地址空间顶端的 trampoline 与 trapframe 页被自然排除；用户栈在 `sz` 之下，属于 RSS；栈保护页未被映射，自然不计入。
  Only leaves below `sz` count, so trampoline/trapframe are excluded while the user stack is included.
- **调用者持有 `p->lock`**（`psinfo()` 正是如此），因此遍历期间页表不会被拆掉；本函数只读。
  The caller holds `p->lock`, so the walk cannot race against teardown, and nothing is written.

> ⚠️ **`struct psinfo` 已无任何余量。** 加入 `rss` 后 sizeof 从 56 变到**正好 64 字节**，`64 × NPROC(64) = 4096 = PGSIZE`，编译期断言仍能通过但已经踩满。**再增加任何字段都会编译失败**，届时快照必须跨两页。这是有意的护栏，不是需要绕过的 bug。
> With `rss`, `sizeof(struct psinfo)` is now exactly 64, so `64 * NPROC = PGSIZE`. The assertion still passes but there is zero headroom left: any further field would require the snapshot to span two pages.

**`waitx()` 系统调用（`SYS_waitx = 26`）**

- 原型 / Prototype: `int waitx(int *status, uint64 *utime, uint64 *ktime);`
- 语义等同 `wait()`，额外回填子进程整个生命周期的用户态 / 内核态 tick。三个指针都可以传 0。
  Same semantics as `wait()`, plus the child's lifetime user/supervisor ticks. All three pointers may be 0.
- 子进程账目在 `kexit()` 中**冻结**到 `xutime` / `xktime`（位置紧邻既有的 `xstate`），由 `kwait()` 在仍持 `p->lock` 时读出。之所以必须冻结：`freeproc()` 会在父进程收割的瞬间把整个槽位清零，之后就无值可读。
  Accounting is frozen in `kexit()` into `xutime`/`xktime` and read by `kwait()` under `p->lock`, because `freeproc()` wipes the slot the moment the parent reaps.
- 冻结点刻意放在退出路径**全部清理之后**：`fileclose()` 与 `iput()` 都在内核态执行并已计入 `k_ticks`，父进程理应看到这部分开销。
  The freeze happens after all cleanup, so kernel work done during exit is included.
- `allocproc()` 同时清零这两个字段，与 `u_ticks` / `k_ticks` 采用同一条理由：回收的槽位不得继承上个进程的账目。
  `allocproc()` zeroes them too, for the same reason it zeroes `u_ticks`/`k_ticks`.

**`fsinfo()` 系统调用（`SYS_fsinfo = 27`）**

- 原型 / Prototype: `int fsinfo(struct fsstat *st);` —— 布局定义在共享头 `kernel/fsstat.h`。
- 块用量是 **O(1)**：`balloc()` / `bfree()` 处增减 `fs_nused_blocks`，`ialloc()` / `ifree()` 处增减 `fs_nused_inodes`。这与 `kmem.nfree` 是同一套思路。
  Block usage is O(1), maintained at the four allocator sites, the same idea as `kmem.nfree`.
- **启动时用 `fscount_scan()` 校准一次**，把读数直接从磁盘扫出来的真值灌进计数器。计数器只维护增量，标定不可省；事务回滚可以让它偏高一格，重启即可清除。
  A one-time `fscount_scan()` at boot seeds the counters from ground truth on disk; the counters track deltas only, so seeding cannot be skipped.
- 同理保留 **O(N) 参考实现 `fscount_walk()`**，用于在之后校准/对照——与 `freemem_walk()` 的角色一致（它被导出但不被任何生产路径调用）。
  The O(N) `fscount_walk()` is kept as a reference, mirroring `freemem_walk()`: exported, unused in production paths.
- 用 relaxed atomic 而非自旋锁：分配点本身已在各自的锁（inode 锁、日志锁）之内，加锁反而有锁序反转风险。
  Relaxed atomics, not a spinlock: the allocator sites already sit inside their own locks, so locking here risks lock-order inversion for no accuracy gain.
- `total` 用 **`sb.size`（整盘块数）而非 `sb.nblocks`（数据块数）**：mkfs 把 boot / super / log / inode / 位图块也在同一张位图里标记为已用，用 `nblocks` 会重复扣减元数据并少报可用空间。
  `total` is `sb.size`, not `sb.nblocks`: mkfs marks metadata blocks as allocated in the same bitmap, so using `nblocks` would double-count them.

---

### 批次 5 — 系统信息与运行时计数器 / Batch 5 — System info & runtime counters

批次 5 先补齐批次 4 遗留的两个口径问题（`df` 的元数据口径、编译期 hart 数），再把新增的**事件计数**统一到一个系统调用下。

Batch 5 closed the two gaps batch 4 left open (the metadata figure in `df`, the compile-time hart count), then funnelled the new *event* counters through a single call.

**`sysinfo()` 系统调用（`SYS_sysinfo = 28`）**

- 原型 / Prototype: `int sysinfo(struct sysinfo *info);` —— 布局在共享头 `kernel/sysinfo.h`。
- 与 `struct psinfo` 的关键差别：它是**单个结构体而非进程表快照**，因此**没有页大小约束**，可以随时追加字段。
  Unlike `struct psinfo` this is a single struct, not a process-table snapshot, so it carries **no page-size constraint** and fields can be appended freely.

| 字段 / Field | 来源 / Source |
| --- | --- |
| `ncpu_online` | 每个 hart 进入 `scheduler()` 前自增的计数器（`kernel/proc.c`） |
| `ncpu_max` | 编译期上限 `NCPU` |
| `mem_total` / `mem_free` | `PHYSTOP - KERNBASE` / `freemem()` |
| `disk_reads` / `disk_writes` | `virtio_disk_rw()` 入口，按读写分流（`kernel/virtio_disk.c`） |
| `bcache_hits` / `bcache_misses` | `bget()` 命中分支 / 需要取空槽的分支（`kernel/bio.c`） |
| `vmfaults` | `vmfault()` 中 `mappages()` 成功之后（`kernel/vm.c`） |

- **所有计数器都是 relaxed atomic，理由相同**：计数点已经在各自的临界区内（`bget()` 在 `bcache.lock` 内），而读者只要一个单调快照，在此加锁只会引入锁序反转风险。
  Every counter is a relaxed atomic for the same reason: the increment sites already sit inside their own critical section, and a reader only wants a monotonic snapshot.
- **`vmfaults` 计在成功映射之后**，因此它等于"新映射的页数"，可以直接与 `rss` 的增长对照；计在函数入口则会把 `va >= psz` 与 `ismapped()` 这两种无效调用也算进去。
  Counting after `mappages()` succeeds makes the figure mean "pages newly mapped", comparable against the growth of `rss`.
- **`ncpu_online` 报的是"已进入调度器"的 hart 数**，不是 `-smp` 配置值：hart 0 因先完成设备初始化而最后到达。观测真实在线比回显一个编译期常量更有价值。
  `ncpu_online` counts harts that have *reached the scheduler*, not the `-smp` setting; hart 0 arrives last because it performs all device setup first.

**`nmeta`：`df` 的数据块口径 / A data-block view for `df`**

`struct fsstat` 增加 `nmeta`，由 `fsinfo()` 用 `sb.size - sb.nblocks` 得出 —— 这正是 mkfs 自己的算法（`nblocks = FSSIZE - nmeta`），复用它比在核心里重算 inode 块与位图块数更不容易漂移。`df` 现在同时打印"整盘"与"数据块"两组读数。

  `struct fsstat` gained `nmeta`, derived as `sb.size - sb.nblocks` — exactly how mkfs splits the image — and `df` prints both the whole-image and data-block views.

> 两种口径的 `free` 值必然相同：元数据块在同一张位图里被标记为已用，空闲块不可能落在元数据区。这不是打印错误。
> The two views must agree on `free`: metadata blocks are marked allocated in the same bitmap, so no free block can lie there.

**验证上的一个新情况 / A new verification situation**

`disk_reads` / `bcache_hits` 这类**事件计数没有可扫描的 O(N) 真值**，因此无法沿用 `freemem_walk()` / `fscount_walk()` 那套对照法，只能靠同层恒真不变式（`hits + misses == bget()` 调用次数）与行为对照。这是本项目首个无法用参考实现校验的能力。

  These event counters have no scanable O(N) ground truth, so the `freemem_walk()` / `fscount_walk()` cross-check does not apply. Same-layer invariants and behavioural comparison are all there is.

---


### 批次 6 — 调度器优先级 / Batch 6 — Scheduler priorities

批次 5 为止，本项目新增的每一个系统调用都是**只读**的。批次 6 引入第一个**写型**系统调用，并第一次改动调度语义 —— 这使它不同于前面所有批次：观测能力只读内核状态，随时可以整体回滚；而调度策略会改变每一个进程的运行机会。

Until batch 6 every syscall this project added was read-only. Batch 6 adds the first one that writes kernel state, and the first change to scheduling semantics.

**`setprio()` 系统调用（`SYS_setprio = 29`）**

- 原型 / Prototype: `int setprio(int pid, int prio);` —— **数值越小越紧急**，范围 `PRIO_HIGHEST`(0)..`PRIO_LOWEST`(9)，常量定义在共享头 `kernel/psinfo.h`。
- **无权限检查**：xv6 没有 uid/gid，任何进程都可以修改任何进程的优先级。这是显式记录的教学简化。
  There is no permission check — xv6 has no uid/gid, so any process may change any other process's priority. A stated teaching simplification.
- **fork 不继承优先级**：`kfork()` 是逐字段复制而非 `*np = *p`，所以新进程从 `PRIO_DEFAULT`(5) 开始。这让"每个进程起点平等"成为可预测的语义，也让优先级只能通过显式 `setprio` 改变。

**调度器：单遍择优 + 老化 / One-pass choose-and-age**

`struct proc` 增加两个字段：`prio`（静态，由用户设置）与 `cur_prio`（动态，调度器实际比较的值）。`scheduler()` 从"取第一个 `RUNNABLE`"改为"取 `cur_prio` 最小者"，并在**同一次扫描**中给每个仍可运行却未被选中的进程把 `cur_prio` 减一 —— 这就是老化。

被选中的进程在切换前把 `cur_prio` 重置回 `prio`：稳态下高优先级进程每轮都赢，而低优先级进程的 `cur_prio` 持续下降，因此每 `PRIO_LOWEST - PRIO_HIGHEST + 1 = 10` 轮必然被选中一次 —— **这是不饥饿的保证，也是"优先级"与"饿死"之间的分界线**。`PRIO_AGING_FLOOR` 防止计数器在长时间运行后向 `INT_MIN` 漂移。

三个值得写下来的工程决定 / Three engineering points:

1. **为什么老化不是可选项**：`kernel/trap.c` 每个 tick 都调用 `yield()`，因此调度器每 tick 重新择优。纯静态优先级意味着低优先级进程只能在高优先级进程全部阻塞时才运行 —— 在 `usertests` 那种并发子进程密集的负载下，这会直接表现为间歇性失败。
2. **择优与老化合并成一遍**：唯一后果是"被选中者自己也被减了一"，而它在切换前会被重置，所以结果与两遍扫描等价，却省掉一次 O(NPROC) 遍历。
3. **`best` 指针必须重新校验**：`proc` 数组是静态的，指针始终有效；但选中后必须**重新持锁确认 `state == RUNNABLE`** —— 该进程可能在这一遍扫描期间睡眠、退出，甚至其槽位已被回收复用。这是 `scheduler()` 里唯一容易写错的地方。

**自检 / Self-check**

`priotest` 让两个子进程各在用户态自旋相同的 tick 数，父进程把其中一个设为最紧急、另一个设为最松弛，再用 `waitx()` 比较两者的 CPU 时间。它同时断言两件事：紧急进程必须明显领先（优先级生效），且松弛进程必须拿到 CPU（老化生效）。**只看前者无法区分"调度正确"与"低优先级被饿死"。**

---


### 批次 7 — 页状态表与分配器自检 / Batch 7 — Per-page state table & allocator self-check

`kalloc()` 原有的防护只看**参数**：指针是否页对齐、是否落在 `[end, PHYSTOP)` 之内。它看不见**所有权**错误 —— 同一页被 `kfree()` 两次时，该页会被同一张空闲链表串两次，之后 `kalloc()` 会把同一块内存交给两个不同的调用者，而现场毫无提示。批次 7 用一张每页 1 字节的状态表，把这种静默损坏变成即时 `panic`。

The existing checks in `kalloc()` only validate the *argument*: page alignment and range. They cannot see an *ownership* error -- freeing the same page twice splices it into the free list twice, so a later `kalloc()` hands the same memory to two different callers, silently.

**状态表 / The state table**（`kernel/kalloc.c`）

- `page_state[NPAGE]`，其中 `NPAGE = (PHYSTOP - KERNBASE) / PGSIZE` = 32768，即 **32 KiB**。
- 只有两个取值：`PAGE_FREE`（在空闲链表上）与 `PAGE_ALLOC`（已交出、未归还）。
- 两侧互为镜像：`kfree()` 要求当前是 `PAGE_ALLOC`，`kalloc()` 要求取出的页是 `PAGE_FREE`；任一不符即 `panic`。
- **成本与取舍**：32 KiB 是被描述内存的 0.025%。这是**调试设施**，生产内核不需要它 —— 保留它是本项目的刻意选择，换取"双重释放导致链表损坏"从静默故障变成立即停机。
- **不需要原子操作**：`page_state` 只在持有 `kmem.lock` 时被读写（`kalloc()`/`kfree()` 本就在锁内操作链表），因此它不引入新的同步开销。
- `kinit()` 先把整张表置为 `PAGE_ALLOC` 再调用 `freerange()`：于是"释放一个从未分配过的页"就只剩 `kinit` 自己这一次**合法**的 `ALLOC→FREE` 迁移，不必在 `kfree()` 里开特例。

**计数器与自检 / Counters and self-check**

`kmem` 增加 `kalloc_calls` / `kfree_calls` 两个累计计数，`kinit()` 在 `freerange()` 之后清零，所以差值表示"自启动以来交出的页数"。`kalloc_stats()` 经 `sysinfo()` 导出三个新字段：

| 字段 / Field | 含义 / Meaning |
| --- | --- |
| `pages_total` | 全部物理页 = 32768，**含**从不归 `kalloc` 管的内核映像页 |
| `pages_live` | 已交出未归还的页数 = `kalloc_calls - kfree_calls` |
| `alloc_calls` | 累计 `kalloc()` 成功次数 |

`kmemtest` 交叉检查**两条互相独立**的会计路径：

1. **守恒律**：`mem_free`（来自 O(1) 的 `nfree` 计数）与 `pages_live`（来自调用计数）之和是一个常量 —— `nfree + live` 恒等于内核映像之后的页数 —— 所以分配前后必须完全不变。
2. **泄漏检测**：子进程 `sbrk` 64 页后退出，`pages_live` 必须回落到基线（容差 4 页）。

> ⚠️ 守恒律只能用**增量**判断，不能写成 `pages_live == pages_total - pages_free`：`pages_total` 含内核映像页而 `nfree` 不含，两者起点不同。这是本批次最容易写错的一处，`kmemtest.c` 的注释里也写明了。
> The invariant only holds for *deltas*: `pages_total` includes the kernel image pages while `nfree` does not, so an absolute equality would be wrong.

**无法从用户态测试的部分 / What cannot be tested from user space**：双重释放检测会 `panic`，触发即整机停机，所以它只能靠代码审查与"临时改坏一处再跑"来验证。

---


## 新增系统调用 / New system calls

| 编号 | 名称 | 用户态原型 | 返回 |
| --- | --- | --- | --- |
| 23 | `SYS_psinfo` | `int psinfo(struct psinfo *buf, int max)` | 写入条目数，或 `-1` |
| 24 | `SYS_freemem` | `uint64 freemem(void)` | 空闲字节数 |
| 25 | `SYS_klog` | `int klog(char *buf, int max, uint64 *seq, uint64 *lost)` | 复制字节数，或 `-1` |
| 26 | `SYS_waitx` | `int waitx(int *status, uint64 *utime, uint64 *ktime)` | 子进程 pid，或 `-1` |
| 27 | `SYS_fsinfo` | `int fsinfo(struct fsstat *st)` | `0`，或 `-1` |
| 28 | `SYS_sysinfo` | `int sysinfo(struct sysinfo *info)` | `0`，或 `-1` |
| 29 | `SYS_setprio` | `int setprio(int pid, int prio)` | `0`，或 `-1` |

编号定义在 `kernel/syscall.h`，分发表在 `kernel/syscall.c`，实现在 `kernel/sysproc.c`，用户桩由 `user/usys.pl` 生成。

Numbers live in `kernel/syscall.h`, the dispatch table in `kernel/syscall.c`, the handlers in `kernel/sysproc.c`, and the user stubs are generated from `user/usys.pl`.

---

## 文件清单 / File inventory

### 新增 / New

| 文件 | 说明 |
| --- | --- |
| `kernel/psinfo.h` | `struct psinfo` 布局 + `PSTATE_*` / `PRIO_*` 常量，内核/用户共享（`_pad` 在批次 6 改为 `prio`，`sizeof` 不变） |
| `kernel/minios.h` | `MINIOS_MEM_TOTAL` 等共享常量 |
| `kernel/fsstat.h` | `struct fsstat` 布局（含 `nmeta` 元数据块数），内核/用户共享 |
| `kernel/sysinfo.h` | `struct sysinfo` 布局，内核/用户共享 |
| `user/ps.c` | 进程列表 |
| `user/free.c` | 内存用量 |
| `user/dmesg.c` | 内核日志回放 |
| `user/top.c` | 周期刷新视图 + CPU 增量 |
| `user/neofetch.c` | 系统概览 |
| `user/cputest.c` | CPU 时间统计自检 |
| `user/df.c` | 文件系统用量 |
| `user/waitxtest.c` | 子进程 CPU 时间回收自检 |
| `user/help.c` | 命令索引，标注每条命令的来源 |
| `user/priotest.c` | 调度优先级与老化的自检程序 |
| `user/kmemtest.c` | 分配器会计（守恒律）与泄漏的自检程序 |
| `user/cowtest.c` | 写时复制 fork 的自检程序：fork 代价、写隔离、copyout、三代共享 |

### 修改 / Modified

| 文件 | 改动 |
| --- | --- |
| `kernel/kalloc.c` | `kmem.nfree` O(1) 计数器；`freemem()`、`freemem_walk()`；批次 7 的每页状态表，批次 8 改为**引用计数**并新增 `kref()`/`krefcnt()`/`kshared()`/`kshared_walk()`/`krefs()`；`kalloc_stats()` |
| `kernel/vm.c` | `vm_rss()` 驻留页统计（递归遍历三级页表）；`vmfaults()` 按需分页计数；批次 8 的写时复制 `uvmcopy()`、`cowfault()` 与 `copyout()` 的 COW 分支 |
| `kernel/fs.c` | O(1) 块/inode 计数器；`fscount_scan()`、`fscount_walk()`、`fsinfo()`（含 `nmeta` 推导） |
| `kernel/printk.c` | 无锁日志环形缓冲区；`kputc()` tee；`klog_read()` |
| `kernel/proc.h` | `struct proc` 增加 `u_ticks` / `k_ticks` / `xutime` / `xktime`；批次 6 增加 `prio` / `cur_prio`（受 `p->lock` 保护） |
| `kernel/proc.c` | `allocproc()` 清零计数；`psinfo()` 快照 + 编译期页大小断言；`cpu_online_inc()`/`ncpu_online()`；批次 6 的 `scheduler()` 单遍择优 + 老化、`ksetprio()` |
| `kernel/trap.c` | `clockintr()` 按 hart 计费并拆分为用户/内核态；批次 8 在 `usertrap()` 里接上写时复制缺页 |
| `kernel/riscv.h` | `PTE_COW`：Sv39 留给软件的 PTE 位 8 |
| `kernel/sysinfo.h` | `struct sysinfo`；批次 8 追加 5 个共享 / COW 字段 |
| `kernel/sysproc.c` | `sys_psinfo` / `sys_freemem` / `sys_klog` / `sys_sysinfo` / `sys_setprio` |
| `kernel/main.c` | 每个 hart 进入 `scheduler()` 前调用 `cpu_online_inc()` |
| `kernel/bio.c` | `bcache_hits` / `bcache_misses` 计数器与 `bio_stats()` |
| `kernel/virtio_disk.c` | `disk_reads_cnt` / `disk_writes_cnt` 计数器与 `disk_stats()` |
| `kernel/syscall.h` / `.c` | 新系统调用编号与分发表 |
| `kernel/defs.h` | 新函数原型 |
| `user/user.h` / `usys.pl` | 新系统调用声明与桩 |
| `Makefile` | `UPROGS` 增加 12 个用户程序（`ps` / `free` / `dmesg` / `top` / `neofetch` / `cputest` / `df` / `waitxtest` / `help` / `priotest` / `kmemtest` / `cowtest`） |

---

## 设计权衡 / Design trade-offs

| 决策 / Decision | 理由 / Rationale |
| --- | --- |
| 日志环形缓冲区无锁 | `panic()` 必须能在不获取任何锁的情况下输出；字节先写、索引后发（release/acquire）保证读者看到完整字节 |
| 定时器中断不取 `p->lock` | 计数器单写者；ISR 中加锁是纯开销。读者用 relaxed atomic 取单调快照 |
| 空闲内存用 O(1) 计数器而非 O(N) 遍历 | 高频调用（`top` 每轮一次）下 O(N) 不可接受；保留 `freemem_walk()` 作为可对照的参考实现 |
| `copyout` 前先填内核暂存页并释放所有锁 | `copyout` 可能缺页 → `vmfault` → `kalloc` → `kmem.lock`，在持锁期间调用有死锁风险 |
| `klog` 用用户态持有的 in/out 游标 | 内核无需为每个进程保存读取位置；`lost` 明确报告被覆盖的字节数 |
| `top` 刷新次数有界 | xv6 没有信号，用户程序无法被 shell 中断，因此不能无限循环 |
| RSS 走页表树而非逐页 `walk` | 惰性 `sbrk` 下 `sz` 可远大于实际映射量；树遍历成本只正比于真正在的东西 |
| `waitx` 在 `kexit()` 冻结账目 | `freeproc()` 会清零整个槽位，退出后无处可读；冻结同时把退出路径自身的内核开销包含进去 |
| 块/inode 用量用 O(1) 计数器 | 与 `kmem.nfree` 同一思路；`df` 可能被频繁调用。启动时由 `fscount_scan()` 从磁盘真值标定一次 |
| 计数器用 relaxed atomic 而非自旋锁 | 分配点已在各自的 inode/日志锁之内，此处加锁有锁序反转风险且得不到额外精度 |
| 保留 `fscount_walk()` 参考实现 | 与 `freemem_walk()` 同角色：导出、不被生产路径调用，用于对照校验 O(1) 计数器 |

---

## 验证 / Verification

**回归测试 / Regression**

```sh
make clean && make && make fs.img
```

内核在 `-Wall -Werror` 下零诊断，随后在 qemu 中运行完整 `usertests`：

```sh
$ usertests
...
ALL TESTS PASSED
```

**CPU 统计自检 / CPU accounting self-check**

```sh
$ cputest
cputest: user-heavy: elapsed 20 ticks, user 20, sys 0
cputest:   user - elapsed = 0
cputest: sys-heavy : elapsed 20 ticks, user 1, sys 19
cputest:   sys = 95% of elapsed
cputest: OK
```

每一格 tick 恰好被记一次（`user + sys == elapsed`）。

Every tick is charged exactly once, so `user + sys == elapsed`.

**文件系统用量 / File system usage**

```sh
$ df
              total  used  free
image  blocks  2000  1328  672
       bytes   2048000  1359872  688128
data   blocks  1953  1281  672
       bytes   1999872  1311744  688128
66% of the image is in use; 47 blocks are metadata
inodes  200 total, 32 used, 168 free
```

这是刚 `make fs.img` 出来的镜像首次启动后的读数：1328 个已用块里有 47 块是 mkfs 标出的元数据（boot / super / log / inode / 位图），其余 1281 块存放 `README` 与 29 个用户程序。

These are the numbers for a freshly built image after its first boot: of the 1328 used blocks, 47 are metadata marked by mkfs (boot/super/log/inode/bitmap) and the other 1281 hold `README` and the 29 user programs.

`nmeta` comes straight from the superblock: mkfs sets `nblocks = size - nmeta`, so the kernel derives it as `sb.size - sb.nblocks` instead of re-deriving the inode and bitmap block counts, which would drift if either constant changed.

两种口径共享同一个 free 值：元数据块全部标记为已用，空闲块不可能落在元数据区，所以“整盘”与“数据块”两种视角下的 free 必定相同——这不是打印错误。

O(1) 计数器的正确性靠对照 O(N) 参考实现来校验：在内核里临时把 `fsinfo()` 改为调用 `fscount_walk()`，在同一次启动后两者必须完全一致（实测两侧都是 `1328` 块 / `32` inode）。这与批次 1 用 `freemem_walk()` 校验 `freemem()` 的做法相同。

Validate the O(1) counters the same way batch 1 validated `freemem()`: temporarily make `fsinfo()` call `fscount_walk()` instead; on the same boot the two must agree exactly (measured: both report 1328 blocks / 32 inodes).

**系统信息 / System summary**

```sh
$ neofetch
   cpus      : 3 online of 8 max
   memory    : 128 MB total, 118000 KB free
   disk      : 412 block reads, 8 writes
   bcache    : 61 hits, 57 misses
   vmfaults  : 0
```

（示意输出，数值随运行状态变化）
`ncpu_online` 由每个 hart 在进入 `scheduler()` 前自增一次，因此它报的是**实际在调度**的 hart 数，而不是编译期上限 `NCPU`；用 `make qemu CPUS=1` 与 `CPUS=4` 各跑一次即可验证读数随之变化。

`disk_stats()` 计的是 `virtio_disk_rw()` 的真实传输次数，`bio_stats()` 计的是 `bget()` 的逻辑访问是否命中，两者的差额正是缓冲缓存省下的 I/O。**这一项没有 O(N) 参考实现可对照**（事件计数不存在可扫描的真值），只能靠同层恒真不变式（`hits + misses == bget()` 调用次数）与行为对照来验证。

Validate the cache counters through their same-layer invariants instead of an O(N) walk: `bcache_hits + bcache_misses` must equal the number of `bget()` calls, and `disk_reads + disk_writes` must equal the number of `virtio_disk_rw()` calls. Run `ls` twice and the second run should add cache hits with almost no disk reads.


**子进程 CPU 时间回收 / Child CPU reaping**

```sh
$ waitxtest
waitxtest: reaped child pid 9, status 0
waitxtest: child  saw user 5 sys 0
waitxtest: parent got user 5 sys 0
waitxtest: delta  user 0 sys 0
waitxtest: OK
```

`waitxtest` 让两条**互相独立**的路径读同一进程的两个计数器：子进程用 `psinfo()` 自测并通过管道回报，父进程用 `waitx()` 回收同一进程。两者必须在 `TOLERANCE = 4` tick 内吻合，且**只能单向偏** —— `kexit()` 的冻结晚于子进程的自读时刻，所以父进程可以多一点，绝不可能少。

`waitxtest` reads the same two counters through two independent paths: the child measures itself with `psinfo()` and reports over a pipe, while the parent reaps it with `waitx()`. They must agree within `TOLERANCE = 4` ticks, and only in one direction: the freeze happens after the child read itself, so the parent may see more, never less.

**调度优先级 / Scheduler priorities**

```sh
$ priotest
priotest: urgent (prio 0): user 18 sys 2
priotest: slack  (prio 9): user 2 sys 0
priotest: OK (urgent 18 vs slack 2 ticks)
```

（示意输出，具体数值随运行状态变化）紧急进程获得约 9 倍于松弛进程的 CPU，而松弛进程仍被老化保证运行 —— 两个断言缺一不可。

`prio` 列可直接在 `ps` 里观察：新进程都是 `PRIO_DEFAULT`(5)，`setprio` 之后立即改变。若把 `PRIO_LOWEST` 调到很大的值（例如把范围改成 0..99），可以观察到松弛进程的等待轮数随之线性增长 —— 老化比例由范围宽度决定。

**分配器自检 / Allocator self-check**

```sh
$ kmemtest
kmemtest: pages total 32768, live 118, alloc calls 4210
kmemtest: mem total 131072 KB, free 132763 KB
kmemtest: conserved quantity is 133152768 bytes (free 32512 pages + live)
kmemtest: counters agree, 64 pages allocated and returned
kmemtest: round 1: live 118 -> 118 after 5 children
kmemtest: round 2: live 119 -> 119 after 5 children
kmemtest: round 3: live 119 -> 119 after 5 children
kmemtest: OK (conserved, no leak over 3 rounds)
```

（示意输出，具体数值随运行状态变化）第 3 行是守恒量：`mem_free + pages_live × 4096`，它来自两条互不相干的会计路径，因此它保持不变才算两条路径一致。

**双重释放检测的手工验证 / Exercising the double-free check**：把 `kfree()` 的调用临时改成对同一页释放两次（例如在 `proc_freepagetable()` 之后再加一次 `kfree`），启动后应立刻看到
`panic: kfree: page was not allocated (double free?)`。这一步**不能**放进 `kmemtest`，因为它会停机。

**手动检查 / Manual checks**

- `dmesg` 可回放内核启动日志；执行 `echo hello` 后再 `dmesg`，**不会**包含 `hello` —— 证明 tee 边界正确（用户态 `printf` 不属于内核日志）。实际上 `init: starting sh` 也不出现，因为那是用户程序输出。
  `dmesg` replays the boot log; after `echo hello` a subsequent `dmesg` does **not** contain `hello`, confirming the tee boundary. Notably `init: starting sh` is absent too, because it is user output.
- 环形缓冲区溢出路径：临时把 `KLOGSIZE` 改成 16，`dmesg` 会打印 `[dmesg: N bytes of older log were overwritten]`，数值与 `总字节数 − 保留字节数` 精确吻合。
  Overrun path: with `KLOGSIZE` temporarily set to 16, `dmesg` reports exactly `total log bytes − retained bytes` as overwritten.
- 后台跑 `cputest 200 &` 时 `top` 显示其 `dcpu` 接近满格、`busy% 100`，而 `init` / `sh` 为 0。
- `cowtest` 期望末行 `OK`。三处值得看：`fork cost` 必须远小于 32 页（复制式 fork 会是 32 页以上）；`shared` 在 fork 期间上升、子进程退出后回到 0；`store faults` 与 `needed a copy` 的差值就是"对方已退出、省掉一次拷贝"的次数。
- 批次 8 验证时的实测读数：`fork cost 10 pages for 32 resident pages (36 shared)`；`141 store faults, 72 of them needed a copy`，差值 69 即被"独占页免拷贝"快捷路径省下的拷贝次数；`neofetch` 稳态下 `sharing` 为 0（fork+exec 的必然结果）而 `refs` 持续增长。
  Recorded when batch 8 was verified: `fork cost 10 pages for 32 resident pages (36 shared)`, and `141 store faults of which only 72 needed a copy` -- the other 69 were spared by the single-reference fast path.
  `cowtest` should end with `OK`. Three things to look at: `fork cost` must be far below 32 pages; `shared` rises during a fork and returns to 0 once the child exits; and the gap between `store faults` and `needed a copy` is the number of stores spared a copy because the other side had exited.
- 手动确认 `copyout` 那条路径：临时把 `copyout()` 里的 `PTE_COW` 分支改成 `return -1`，`cowtest` 的第 3 项必须失败（子进程 `read` 报错），因为内核拒绝写共享页而不是取一份私有副本。
  To confirm the `copyout` path by hand, make that branch return -1: `cowtest` must fail at its third check, since the kernel would then refuse to write a shared page instead of taking a private copy.
- 双重释放仍然只能靠审查验证：现在要在有子进程共享页的情况下手动 `kfree()` 同一页，会得到 `panic: kfree: page is already free (double free?)`。
  Double free is still review-only: freeing a page twice -- now including a page a child shares -- panics with `kfree: page is already free (double free?)`.
---

### 批次 8 — 写时复制 fork / Batch 8 — Copy-on-write fork

原先的 `uvmcopy()` 在 fork 时逐页 `kalloc` + `memmove`：父进程有多少页，fork 就复制多少页。批次 8 让 fork 改为**共享父进程的物理页并把它们标记为只读**，第一次写才复制。批次 7 的那张每页状态表在这里从"两态标志"升级为**引用计数** —— 正是它当初被留下的理由。

The old `uvmcopy()` allocated and copied every page the parent had. Batch 8 makes fork share the parent's physical pages and mark them read-only, copying only on the first store. Batch 7's per-page table becomes a reference count here, which is exactly what it was left in place for.

**引用计数 / The reference count**（`kernel/kalloc.c`）

| 值 / Value | 含义 / Meaning |
| --- | --- |
| `0` | 在空闲链表上 / on `kmem.freelist` |
| `1` | 已交出，独占 / handed out to one owner |
| `n > 1` | 被 n 个页表映射 / mapped by n page tables |

- `kalloc()` 要求取出的页计数为 `0` 并置为 `1`；`kfree()` 要求计数非 `0`，减一后**只有归零才真正回链表**。同一页被释放两次因此仍是即时 `panic` —— 批次 7 的检测原封不动。
- `kref()` 只在 `uvmcopy()` 里被调用：把引用计数加一。它 `panic` 而不让 `uchar` 回绕；`NPROC = 64` 保证一页最多被 64 个进程共享，所以 1 字节够用，不必用 `int` 让每页多占 3 字节。
- 计数器语义随之修正：`pages_live` 现在是 `kalloc_calls − pages_released`，**不是** `− kfree_calls`。共享页的 `kfree()` 只减引用、不回链表；若仍按 `kfree_calls` 计，`live` 会一路向下漂移，`mem_free + live` 这条守恒律就不再成立（`kmemtest` 正盯着它）。

**写时复制 / The copy**（`kernel/vm.c`、`kernel/trap.c`）

- `uvmcopy()` 对**可写**页：先 `kref()`，再在子进程页表里以 `PTE_COW` 且清 `PTE_W` 映射，最后清掉父进程页表里的 `PTE_W`。对**只读**页（exec 载入的正文段）：只 `kref()`，不标 `PTE_COW` —— 写它仍然是错误，不会悄悄换来一份可写副本。
- 两侧都清掉 `PTE_W` 是这套方案的核心不变式：**只要引用计数 ≥ 2，就没有任何人能就地写这一页**。写触发 `scause = 15`，`cowfault()` 分配一份私有副本、改写 PTE，再 `kfree()` 掉本进程持有的那个引用。
- **内核也是写者**：`copyout()` 写入用户内存，因此必须走同一条路。若只处理用户态缺页，`read()`、`sysinfo()` 这类系统调用会把字节直接写进父进程的页里，fork 承诺的隔离就白给了 —— 这是最容易被漏掉的一处。
- `PTE_COW` 取 `1L << 8`：Sv39 把 PTE 的第 8、9 位留给软件。它在 `PTE_FLAGS`（`0x3FF`）之内，因此 `uvmcopy()` 复制 flags 时会顺带把它带过去 —— 这正是"一个已被共享的页再被 fork 时仍能继续 COW"的原因。

**独占页不拷贝 / A page with one reference needs no copy**

`cowfault()` 里有一条快捷路径：若引用计数已是 `1`，说明共享它的那个进程已经退出、这一页只属于当前进程，那就把 `PTE_W` 加回去，一个字节也不必拷。少了它，shell 每跑完一条命令后第一次写自己的每个页都会白拷一次，恰恰是 COW 想省掉的开销。读取计数与改写 PTE 不是原子的，**也不需要是**：一个页只可能在**本进程被 fork** 时增加引用，而 xv6 的进程是单线程的，此刻不可能发生；并发的退出只能让计数下降，而要释放一页必须经过 0，我们还持有映射，它到不了 0。

**可观测性 / What it looks like from user space**

`struct sysinfo` 追加 5 个字段（它没有页大小约束，加字段是安全的；批次 5 的那张字段表只覆盖当时交付的 9 个）：

| 字段 / Field | 来源 / Source |
| --- | --- |
| `pages_shared` | O(1) 计数器：引用计数 ≥ 2 的页数 |
| `pages_shared_ref` | **同一个量的 O(N) 参考实现**，在**同一个** `sysinfo()` 调用里扫一遍状态表 |
| `kref_calls` | `kref()` 累计调用次数（fork 带来的引用总数）|
| `cow_faults` | 被 `cowfault()` 解决的写缺页数 |
| `cow_copies` | 其中真正拷了一页的次数 |

`pages_shared` 与 `pages_shared_ref` 是本项目第三组"O(1) 计数器 vs O(N) 参考实现"（前两组是 `freemem`/`freemem_walk`、`fsinfo`/`fscount_walk`），区别在于这两个数由**同一次系统调用**返回，因而可以互相比较 —— 分成两次调用会引入时间窗。代价是每次 `sysinfo()` 都要在持 `kmem.lock` 时扫 32 KiB 状态表，只有 `neofetch`、`kmemtest`、`cowtest` 三个程序付这个代价。`cow_faults` 与 `cow_copies` 的差值本身即观测量：它统计"因对方已退出而省掉的拷贝次数"。

**自检 / Self-check**

`cowtest` 用已有的观测设施验证四条断言，而不是靠肉眼：

1. **fork 的代价**：先把 32 页读起来，再 fork 一个子进程；子进程报告自己已就绪后**阻塞**在管道上，父进程在"子进程确实活着"的时刻测 `mem_free`。复制式的 fork 会掉 32 页以上，实测应在 16 页以内（页表 + 内核栈 + trapframe）。
2. **写隔离**：子进程写满 32 页后，父进程必须仍读到自己的字节；子进程侧 `cow_copies` 至少增加 32。
3. **copyout 也走 COW**：子进程用 `read()` 把 4 KiB 读进一个与父进程**共享**的页 —— 若 `copyout()` 漏了这条路径，父进程的页会被写脏。
4. **三代共享**：孙进程写满全部页，父与子都必须看到自己的字节。

每一步都断言 `pages_shared == pages_shared_ref`，并在子进程退出后断言共享页数回到基线 —— 引用计数漏减会比内存泄漏更隐蔽。

三个值得写下来的工程决定 / Three engineering points:

1. **失败路径比成功路径难写**：`uvmcopy()` 中途失败时不能简单回滚。已经 `kref()` 过的页要 `kfree()` 回去，而父进程的 `PTE_W` 只有在该页计数确实回到 `1` 时才能恢复 —— 若它正被更早的一次 fork 共享着，恢复成可写就等于把那个孩子的页也交了出去。
2. **`PTE_COW` 留在单引用的页上是合法状态**：子进程退出后父进程的页仍是"只读 + COW"标记，直到下一次写被 `cowfault()` 的快捷路径修好。这个状态不产生任何拷贝，所以不是代价，但读 PTE 的代码不该假设"只读页一定没有 `PTE_COW`"。
3. **守恒律需要重新对齐口径**：把 `pages_live` 从"`kfree()` 调用次数"改成"真正回链表的次数"不是美化，而是让第二章那套校验继续成立的必要条件；计数器的定义跟着语义走。

---
## 已知限制 / Known limitations

- **`PTE_COW` 可以在引用计数为 1 时仍然挂着**：子进程退出后父进程的页保持"只读 + COW"标记，下一次写由 `cowfault()` 的快捷路径就地修好、不产生拷贝。因此任何查看 PTE 的代码都不该假设"只读页一定没有 `PTE_COW`"，而 `psinfo()`/`vm_rss()` 这类只读遍历不受影响。
  A page can keep `PTE_COW` after its reference count falls back to one: the next store heals it in place without copying. Code that inspects PTEs must therefore not assume that a read-only page has no `PTE_COW`; read-only walks such as `vm_rss()` are unaffected.
- **`sysinfo()` 多了一次 O(NPAGE) 的扫描**：`pages_shared_ref` 为了与 O(1) 计数器可比，必须在同一次调用里扫完 32 KiB 状态表，且全程持 `kmem.lock`。这是跨校验的代价，只有 `neofetch` / `kmemtest` / `cowtest` 三个调用者承担。
  `sysinfo()` now scans the 32 KiB table under `kmem.lock` so that `pages_shared_ref` can be compared with the O(1) counter. Only the three callers of `sysinfo()` pay for it.
- **`neofetch` 的 `sharing` 行在常规 shell 下通常读数为 0**：xv6 的 shell 走 fork + exec，子进程一 exec 就放弃了共享页。它非零的时候意味着**此刻有子进程正共享着父进程的页**；想看它动起来，请跑 `cowtest`。相比之下 `refs` 是累计值，会随每次 fork 单调增长。
  The `sharing` line reads 0 under the usual fork+exec shell: the child gives up the shared pages the moment it execs. A non-zero value means a forked child is sharing its parent's pages right now; run `cowtest` to watch it move. `refs` is cumulative and grows with every fork.
- **页引用计数只有 1 字节**：上限 255，`kref()` 里用一次 `panic` 守住回绕。`NPROC = 64` 意味着上限远够用，但这是一个被写死的耦合 —— 若把 `NPROC` 提到 255 以上就必须换宽度。
  The reference count is one byte wide, capped at 255 and guarded by a `panic`; `NPROC = 64` keeps that far out of reach, but widening `NPROC` past 255 would need a wider count.- **页状态表占用 32 KiB 静态内存**，且只在 `kalloc`/`kfree` 的热路径上增加两次数组访问。它是调试设施：若需要，可改成每页 2 位的位图（省一半）或用编译开关关掉。
  The state table costs 32 KiB of static memory plus two array touches per allocator call. It is a debug facility; a 2-bit-per-page bitmap would halve it, and a build flag could disable it.
- **`pages_total` 不等于 `kalloc` 管理的页数**：它含从不进入空闲链表的内核映像页，而内核映像占多少页并未导出。任何一致性检查都必须用增量。
- **双重释放检测只能靠审查验证**：从用户态触发它会 `panic` 并停机，无法写进自检程序。
- **调度器每 tick 做一次 O(NPROC) 全表扫描**：择优与老化合并后仍是一次完整遍历。`NPROC = 64` 下成本可忽略，但它确实比原先"遇到第一个 `RUNNABLE` 就走"更贵。
  The scheduler walks the whole table every tick. At `NPROC = 64` the cost is negligible, but it is strictly more work than the old "run the first RUNNABLE slot found".
- **优先级不继承**：`kfork()` 逐字段复制，子进程从 `PRIO_DEFAULT` 开始。若希望子进程继承父进程的优先级，需要显式调用 `setprio`。
- **老化速率由优先级范围宽度决定**：低优先级进程每 `PRIO_LOWEST - PRIO_HIGHEST + 1` 轮被选中一次。这个比例不是独立参数 —— 改范围就等于改比例。
- **`setprio` 无权限检查**：任何进程可以改任何进程的优先级（xv6 没有 uid/gid）。
- **优先级范围刻意压窄（0..9）**：不同于 nice(1) 的 -20..19，窄范围让老化在可观测的时间内生效；代价是"优先级"只能表达一档粗略的差别。
- **`psinfo()` 的成本随进程规模增长**：快照期间会对每个进程持 `p->lock` 遍历页表树，因此映射页很多的进程会让 `psinfo()` 变慢，而 `top` 每轮都要做一次。这里不能改成「先释放锁再遍历」——那样页表可能被并发释放；这是必要权衡，不是疏漏。
  `psinfo()` walks each process's page table while holding `p->lock`, so its cost grows with what is mapped and `top` pays it every refresh. It cannot drop the lock first, since the table could be torn down underneath it.

- **RSS 是即时快照，且不区分共享页**：`rss` 统计的是快照瞬间低于 `sz` 的已映射页，批次 8 的共享页会被计入**每个**映射它的进程 —— 与传统 `top` 一致，但意味着 `ps` 里两个进程的 `rss` 相加并不等于它们实际占用的物理内存。要看真实占用，请用 `sysinfo` 的 `pages_shared`。
  RSS is an instantaneous snapshot and does not deduplicate shared pages: if shared mappings are ever added (COW parent pages, say), a shared page counts in every process's RSS, same as traditional `top`.
- **`struct psinfo` 已无剩余空间**：加入 `rss` 后 sizeof 恰好 64，`64 × NPROC = PGSIZE`。再增字段会直接编译失败，届时快照须跨两页。
  There is no room left in `struct psinfo`: with `rss` its size is exactly 64 and `64 * NPROC == PGSIZE`. Adding a field fails to compile by design.
- **`waitx` 的账目可能少一格 tick**：冻结发生在退出路径末尾，此后到 `sched()` 之间若有定时器中断，那一格不会被任何人记录。这是 tick 级采样精度的固有边界。
  `waitx` accounting can be one tick short: the freeze happens at the end of the exit path, and a timer interrupt after it is charged to nobody. This is inherent to tick-granularity sampling.
- CPU 时间是**采样**得到：内核把 tick 记给被中断的那个进程，因此单次连续占用不足 1 tick（100 ms）的进程可能显示为 0。
  CPU time is **sampled**: a process that burns less than one tick (100 ms) at a stretch may show up as 0.
- `uptime()`（全局 `ticks`）只在 hart 0 上递增，而被统计的进程可能运行在任意 hart 上，因此两者之间存在少量偏斜；`cputest` 的容差 `TOLERANCE = 4` 即为此设置。
  `uptime()` only advances on hart 0 while the measured process may run on any hart, so a small skew exists; `cputest` uses `TOLERANCE = 4`.
- 日志是诊断输出，不是可靠通道：环形缓冲区在拷贝过程中若发生回绕，输出的尾部可能是新旧文本的混合。
  The log is diagnostic output, not a reliable channel: if the ring wraps during a copy, the tail may mix old and new text.

---

## 路线图 / Roadmap

已完成批次 1–8。批次 6 的调度器优先级与批次 8 的写时复制 fork 见其各自的小节；批次 5 的选型讨论保留在 `docs/batch5-plan.md` 第 7 章。

Batches 1-8 are complete; see the batch 6 and batch 8 sections above.

- 批次 9+：信号投递（唯一能解锁"可被中断的用户程序"的机制）、`/proc` 伪文件系统，以及把页引用计数换成 `int` 之类的可选清理

---

## 许可证与致谢 / License & Attribution

本项目基于 **xv6-riscv**，其版权与许可证文本见 [`LICENSE.xv6`](LICENSE.xv6)：

> The xv6 software is Copyright (c) 2006-2024 Frans Kaashoek, Robert Morris, Russ Cox, Massachusetts Institute of Technology.

xv6 受 John Lions 的 *Commentary on UNIX 6th Edition* 启发，是 MIT 6.1810 的教学操作系统，参见 <https://pdos.csail.mit.edu/6.1810/>。

This project is based on **xv6-riscv**; its copyright and license text is retained in [`LICENSE.xv6`](LICENSE.xv6). xv6 is inspired by John Lions's *Commentary on UNIX 6th Edition* and is the teaching operating system for MIT 6.1810.

本项目自身新增的代码以 MIT 许可证发布，见 [`LICENSE`](LICENSE)。

The code added by this project is released under the MIT License; see [`LICENSE`](LICENSE).
