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

镜像里共有 **29** 个用户程序（即 `UPROGS` 的 29 项）。不带参数运行 `help` 会按类别列出其中 **28 条**——除 `help` 自身以外的全部命令——并把 miniOS 新增的 8 条用 `*` 标出；`help ps` 则只显示该命令的用法与补充细节。`help` 本身是 miniOS 的第 9 个新增程序，没有把自己列进索引（一个刻意的取舍），但它与其他命令一样只是根目录里的普通程序，`ls` 能看到它。xv6 的 shell 没有内建命令，因此命令索引本身也只能是一个普通程序。

The image holds **29** user programs (the 29 `UPROGS` entries). Run with no arguments, `help` lists **28** of them grouped by category — every command except `help` itself — marking the 8 added by this project with `*`; `help ps` shows just that entry. `help` is itself the 9th program this project added; it deliberately does not index itself, but it is otherwise an ordinary program in the root directory, visible to `ls`. xv6's shell has no built-ins, so the index has to be an ordinary program too.

```
$ ps
pid  ppid state  vsz  rss  usr sys name
1  0  sleep  16  16  0  0  init
2  1  sleep  20  20  0  0  sh
3  2  run  20  20  0  0  ps
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


## 新增系统调用 / New system calls

| 编号 | 名称 | 用户态原型 | 返回 |
| --- | --- | --- | --- |
| 23 | `SYS_psinfo` | `int psinfo(struct psinfo *buf, int max)` | 写入条目数，或 `-1` |
| 24 | `SYS_freemem` | `uint64 freemem(void)` | 空闲字节数 |
| 25 | `SYS_klog` | `int klog(char *buf, int max, uint64 *seq, uint64 *lost)` | 复制字节数，或 `-1` |
| 26 | `SYS_waitx` | `int waitx(int *status, uint64 *utime, uint64 *ktime)` | 子进程 pid，或 `-1` |
| 27 | `SYS_fsinfo` | `int fsinfo(struct fsstat *st)` | `0`，或 `-1` |
| 28 | `SYS_sysinfo` | `int sysinfo(struct sysinfo *info)` | `0`，或 `-1` |

编号定义在 `kernel/syscall.h`，分发表在 `kernel/syscall.c`，实现在 `kernel/sysproc.c`，用户桩由 `user/usys.pl` 生成。

Numbers live in `kernel/syscall.h`, the dispatch table in `kernel/syscall.c`, the handlers in `kernel/sysproc.c`, and the user stubs are generated from `user/usys.pl`.

---

## 文件清单 / File inventory

### 新增 / New

| 文件 | 说明 |
| --- | --- |
| `kernel/psinfo.h` | `struct psinfo` 布局 + `PSTATE_*` 常量，内核/用户共享 |
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

### 修改 / Modified

| 文件 | 改动 |
| --- | --- |
| `kernel/kalloc.c` | `kmem.nfree` O(1) 计数器；`freemem()`、`freemem_walk()` |
| `kernel/vm.c` | `vm_rss()` 驻留页统计（递归遍历三级页表）；`vmfaults()` 按需分页计数 |
| `kernel/fs.c` | O(1) 块/inode 计数器；`fscount_scan()`、`fscount_walk()`、`fsinfo()`（含 `nmeta` 推导） |
| `kernel/printk.c` | 无锁日志环形缓冲区；`kputc()` tee；`klog_read()` |
| `kernel/proc.h` | `struct proc` 增加 `u_ticks` / `k_ticks` / `xutime` / `xktime` |
| `kernel/proc.c` | `allocproc()` 清零计数；`psinfo()` 快照 + 编译期页大小断言；`cpu_online_inc()`/`ncpu_online()` |
| `kernel/trap.c` | `clockintr()` 按 hart 计费并拆分为用户/内核态 |
| `kernel/sysproc.c` | `sys_psinfo` / `sys_freemem` / `sys_klog` / `sys_sysinfo` |
| `kernel/main.c` | 每个 hart 进入 `scheduler()` 前调用 `cpu_online_inc()` |
| `kernel/bio.c` | `bcache_hits` / `bcache_misses` 计数器与 `bio_stats()` |
| `kernel/virtio_disk.c` | `disk_reads_cnt` / `disk_writes_cnt` 计数器与 `disk_stats()` |
| `kernel/syscall.h` / `.c` | 新系统调用编号与分发表 |
| `kernel/defs.h` | 新函数原型 |
| `user/user.h` / `usys.pl` | 新系统调用声明与桩 |
| `Makefile` | `UPROGS` 增加 9 个用户程序（`ps` / `free` / `dmesg` / `top` / `neofetch` / `cputest` / `df` / `waitxtest` / `help`） |

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

**手动检查 / Manual checks**

- `dmesg` 可回放内核启动日志；执行 `echo hello` 后再 `dmesg`，**不会**包含 `hello` —— 证明 tee 边界正确（用户态 `printf` 不属于内核日志）。实际上 `init: starting sh` 也不出现，因为那是用户程序输出。
  `dmesg` replays the boot log; after `echo hello` a subsequent `dmesg` does **not** contain `hello`, confirming the tee boundary. Notably `init: starting sh` is absent too, because it is user output.
- 环形缓冲区溢出路径：临时把 `KLOGSIZE` 改成 16，`dmesg` 会打印 `[dmesg: N bytes of older log were overwritten]`，数值与 `总字节数 − 保留字节数` 精确吻合。
  Overrun path: with `KLOGSIZE` temporarily set to 16, `dmesg` reports exactly `total log bytes − retained bytes` as overwritten.
- 后台跑 `cputest 200 &` 时 `top` 显示其 `dcpu` 接近满格、`busy% 100`，而 `init` / `sh` 为 0。

---

## 已知限制 / Known limitations

- **`psinfo()` 的成本随进程规模增长**：快照期间会对每个进程持 `p->lock` 遍历页表树，因此映射页很多的进程会让 `psinfo()` 变慢，而 `top` 每轮都要做一次。这里不能改成「先释放锁再遍历」——那样页表可能被并发释放；这是必要权衡，不是疏漏。
  `psinfo()` walks each process's page table while holding `p->lock`, so its cost grows with what is mapped and `top` pays it every refresh. It cannot drop the lock first, since the table could be torn down underneath it.

- **RSS 是即时快照，且不区分共享页**：`rss` 统计的是快照瞬间低于 `sz` 的已映射页。若将来引入共享映射（如 COW fork 的共享父页），同一页会被计入每个进程的 RSS，与传统 `top` 的行为一致。
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

已完成批次 1–5。批次 6 选定**调度器优先级**（含本项目第一个写型 syscall `setprio()`），选型理由与实施方案见 `docs/batch5-plan.md` 第 7 章。

Batches 1-5 are complete. Batch 6 is slated for **scheduler priorities** (including `setprio()`, the first syscall in this project that writes kernel state); see `docs/batch5-plan.md` §7 for the rationale and the plan.

- 批次 6：调度器优先级（带老化，避免饥饿）+ `setprio(pid, prio)`
- 批次 7：`kalloc` 页状态数组（双重释放 / 泄漏检测）
- 批次 8+：COW fork、信号投递、`/proc` 伪文件系统

---

## 许可证与致谢 / License & Attribution

本项目基于 **xv6-riscv**，其版权与许可证文本见 [`LICENSE.xv6`](LICENSE.xv6)：

> The xv6 software is Copyright (c) 2006-2024 Frans Kaashoek, Robert Morris, Russ Cox, Massachusetts Institute of Technology.

xv6 受 John Lions 的 *Commentary on UNIX 6th Edition* 启发，是 MIT 6.1810 的教学操作系统，参见 <https://pdos.csail.mit.edu/6.1810/>。

This project is based on **xv6-riscv**; its copyright and license text is retained in [`LICENSE.xv6`](LICENSE.xv6). xv6 is inspired by John Lions's *Commentary on UNIX 6th Edition* and is the teaching operating system for MIT 6.1810.

本项目自身新增的代码以 MIT 许可证发布，见 [`LICENSE`](LICENSE)。

The code added by this project is released under the MIT License; see [`LICENSE`](LICENSE).
