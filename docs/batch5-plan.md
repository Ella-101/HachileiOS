# 批次 5 实施计划 / Batch 5 Implementation Plan

> 本文档针对 `README.md` 所声明的批次 1–4 能力做一次逐项核对，并把核对中发现的三处落差整理为批次 5 的可执行范围。
>
> 状态：**批次 5 全部完成，已在 WSL 验证通过。** 本机无 RISC-V 工具链，所有改动均由用户在 WSL 中 `make` 编译并跑通 `usertests`。
>
> **批次 6（调度器优先级 + `setprio`）已在分支 `batch6-priority-scheduling` 上落地**，设计要点、三个工程取舍与自检方法写在 `README.md` 的批次 6 小节。本文档 §7.1–7.2 的选型分析（为什么选优先级而不是 COW/`/proc`）与关键发现（`_pad` 是免费的 4 字节）已据此实现，保留作为决策记录。
>
> | 项 | 内容 | 状态 |
> | --- | --- | --- |
> | 5.1 | `fsstat` 补 `nmeta`，`df` 输出整盘/数据块双口径 | ✅ 已在 WSL 验证通过 |
> | 5.2 | `SYS_sysinfo = 28`，暴露运行时在线 hart 数 | ✅ 已在 WSL 验证通过 |
> | 5.3 | 磁盘 I/O 与 bcache 命中率计数 | ✅ 已在 WSL 验证通过 |
> | 5.4 | `vmfault` 缺页计数 | ✅ 已在 WSL 验证通过 |
>
> 实施说明：5.2–5.4 共享同一个 `struct sysinfo` 共享头，拆开做会出现"字段恒为 0"的中间态，因此一次交付。

---

## 1. 核对结论：文档声明 vs 代码实证

核对方式为直接读取工作区源码，逐项定位到文件与行号。**未发现文档虚报**。

| 文档声明 | 代码实证 | 结论 |
| --- | --- | --- |
| 新增 syscall 编号 23–27 | `kernel/syscall.h` 尾部 5 条定义；`kernel/syscall.c` 分发表 5 项 | ✅ |
| 新增 9 个用户程序 | `Makefile:153-161` 的 `UPROGS` 尾部 9 项，共 29 项 | ✅ |
| `sizeof(struct psinfo) == 64` | `kernel/psinfo.h`：`_pad` + 4 个 uint64 + `name[16]`，无尾部填充 | ✅ |
| 编译期页面断言 | `kernel/proc.c:14-15` `psinfo_fits_one_page[]` | ✅ |
| RSS 走页表树 | `kernel/vm.c:497 rsswalk()` / `:549 vm_rss()`，`psinfo()` 持 `p->lock` 调用 | ✅ |
| `waitx` 在 `kexit()` 冻结账目 | `kernel/proc.c:391-392` 写 `xutime`/`xktime`；`kwait()` 已是三参数版本 | ✅ |
| 空闲内存 O(1) + O(N) 对照 | `defs.h:67-68` 同时导出 `freemem_walk()` 与 `freemem()` | ✅ |
| 块/inode 用量 O(1) + O(N) 对照 | `defs.h:59-61` 导出 `fscount_scan()` / `fscount_walk()` / `fsinfo()` | ✅ |
| `df` 元数据为 47 块 | 见 §3.1 推导，`nmeta = 2 + 31 + 13 + 1 = 47` | ✅ |

---

## 2. 当前能力盘点

四个批次交付的是**同一套模式的复用**：内核加一个 O(1) 计数器或一次性快照 → 一个只读 syscall 导出 → 一个用户程序格式化打印。项目仍处于"只读观测"阶段，没有任何机制类改造。

| 层 | 内容 |
| --- | --- |
| 内核数据源 | `kmem.nfree`（kalloc/kfree）、`p->u_ticks`/`k_ticks`（`clockintr()` 按 hart 计费，`sstatus.SPP` 拆分）、16 KiB 无锁日志环（`kputc()` tee）、`vm_rss()`、`fs_nused_blocks`/`fs_nused_inodes` |
| 系统调用 | 23 `psinfo` / 24 `freemem` / 25 `klog` / 26 `waitx` / 27 `fsinfo` |
| 用户程序 | `ps` `free` `dmesg` `top` `neofetch` `cputest` `df` `waitxtest` `help` |
| 共享头 | `kernel/psinfo.h` / `kernel/minios.h` / `kernel/fsstat.h` |

**贯穿性工程习惯**（后续批次应延续）：每个 O(1) 计数器配一个 O(N) 参考实现作对照（`freemem_walk()`、`fscount_walk()`），导出到 `defs.h` 但不被生产路径调用；并发取舍一律写进注释并说明为何用 relaxed atomic 而非自旋锁。

---

## 3. 已确认的三处落差

### G1 — `df` 的数据块口径无法计算

`README.md` 的「已知限制」写道："想看数据块余量，应从 total 中扣掉 `nmeta`"。但 `kernel/fsstat.h` 只有 `blocksize` / `blocks` / `blocksfree` / `inodes` / `inodesfree` 五个字段，用户态拿不到 `nmeta`，**这条建议当前无法执行**。

### G2 — 运行时在线 hart 数缺失

`user/neofetch.c:36` 打印的是编译期常量 `NCPU`（8），而实际启动参数是 `-smp 3`。内核中确实没有在线 hart 计数器：`grep ncpu` 仅命中 `struct cpu cpus[NCPU]` 的定义与声明。

### G3 — `struct psinfo` 字段余量为零

`64 × NPROC(64) = 4096 = PGSIZE`，断言已踩满。**任何"给 `ps` 加一列"的需求都必须先让快照跨两页**，否则编译失败。这是有意的护栏，但它是批次 5 及以后所有"扩展 `ps`"类需求的前置约束。

---

## 4. 批次 5 范围

四项，全部属于"沿用既有骨架、每项可独立验证"的模式，**不引入任何新的内核机制**。建议按 5.1 → 5.4 的顺序实现，每项一个 commit。

### 5.1 `fsstat` 补 `nmeta`，`df` 输出双口径

**改动清单**

| 文件 | 改动 |
| --- | --- |
| `kernel/fsstat.h` | 增加 `uint64 nmeta;`（共享头无页大小约束，可直接加字段） |
| `kernel/fs.c` | `fsinfo()` 填 `st->nmeta` |
| `user/df.c` | 增加一行"数据块口径"输出 |

**实现要点：`nmeta` 不必重算，一个减法即可**

`mkfs/mkfs.c:96-97` 定义 `nmeta = 2 + nlog + ninodeblocks + nbitmap`，`nblocks = FSSIZE - nmeta`，因此

```
nmeta = sb.size - sb.nblocks
```

这比在核心里重算 inode 块数与位图块数更可靠——它复用 mkfs 自己算出的结果，避免两侧常量漂移。代入默认配置验算：

```
nlog         = LOGBLOCKS + 1 = 30 + 1 = 31
ninodeblocks = NINODES / IPB + 1 = 200 / 16 + 1 = 13
nbitmap      = FSSIZE / BPB + 1 = 2000 / 8192 + 1 = 1
nmeta        = 2 + 31 + 13 + 1 = 47   ✓ 与 README 记录的 47 块一致
nblocks      = 2000 - 47 = 1953
```

**验证标准**

- `df` 新增行的总量应为 **1953** 块，而非 2000；
- 出厂镜像首次启动后 `used` 应为 **1281 块**（1328 − 47，与 README 记录的"1281 块存放 README 与 29 个程序"精确吻合）;
- 不变式：`nmeta + nblocks == blocks`，可在 `df` 内断言。

**取舍**：`nmeta` 是只读的布局常量，不是计数器，因此它不需要 relaxed atomic，也不破坏"O(1) 计数器"的一致性模型。另需注意共享头加字段后内核与用户态必须同时重编译——这在 `make` 流程中是自动的，但若只单独重编 `user/` 会出现布局不一致。

### 5.2 新增 `SYS_sysinfo = 28`：运行时系统信息

**为什么另起一个 syscall**：`psinfo` 无字段余量（G3），`fsstat` 的语义是"文件系统用量"，塞入 hart 数会破坏其单一职责。因此新建 `kernel/sysinfo.h` 共享头，与 `psinfo.h` / `fsstat.h` 形成第四个同类文件。

**建议的布局**（一次定型，避免后续反复加字段）

```c
struct sysinfo {
  uint64 ncpu_online;   // harts that have reached scheduler()
  uint64 ncpu_max;      // NCPU, the compile-time cap
  uint64 mem_total;     // MINIOS_MEM_TOTAL
  uint64 mem_free;      // freemem() at the same instant (consistent snapshot)
  uint64 disk_reads;    // physical block reads
  uint64 disk_writes;   // physical block writes
  uint64 bcache_hits;   // bget found the block already cached
  uint64 bcache_misses; // bget had to take a fresh slot
  uint64 vmfaults;      // vmfault() calls (demand paging)
};
```

**在线 hart 计数器：加在 `main()` 进入 `scheduler()` 之前**

```c
  // Every hart arrives here after its own initialisation is complete, so
  // the counter measures "harts that are actually scheduling", not the
  // compile-time cap NCPU.  hart 0 arrives last because it does all the
  // boot-time device setup first.
  __atomic_fetch_add(&ncpu_online, 1, __ATOMIC_RELAXED);
  scheduler();
```

**语义细节（必须写进注释）**：该计数是"已进入调度器的 hart 数"，不是 QEMU 配置的 `-smp` 值。若某 hart 尚未完成初始化，读到的是偏小的值；这是有意的——观测"真实在线"比观测"配置上限"更有价值。计数单调递增、单次写入，用 relaxed atomic 与 `started` 标志的 release/acquire 发布语义区分开：后者是同步，前者是统计。

**为什么 `mem_free` 放进 `sysinfo`**：单次 syscall 返回同一时刻的一致快照。分别调用 `freemem()` 与 `sysinfo()` 之间内存数量会变化，前者作为独立 syscall 仍保留（语义单一、O(1)）。

**验证标准**

- `neofetch` 的 cpus 行改为读 `sysinfo().ncpu_online`，默认 `-smp 3` 下应显示 **3**；
- 用 `make qemu CPUS=1` 与 `CPUS=4` 各跑一次，读数应为 1 与 4；
- 同时撤掉 `README.md` 中"`neofetch` 报告的是编译期上限"这条限制。

### 5.3 磁盘 I/O 与 bcache 命中率

**两个互补的计数层**，刻意分开以便区分"逻辑访问"与"物理 I/O"：

| 层 | 计数点 | 含义 |
| --- | --- | --- |
| 缓存层 | `kernel/bio.c` 的 `bget()` 两个返回分支 | `bcache_hits` / `bcache_misses`，即逻辑块访问是否命中 |
| 物理层 | `kernel/virtio_disk.c` 的 `virtio_disk_rw()`，按 `write` 参数分流 | `disk_reads` / `disk_writes`，即真实设备 I/O 次数 |

**取舍**：`bget()` 的两处递增位于 `bcache.lock` 临界区之内，`virtio_disk_rw()` 则不在任何锁内——两处一律用 relaxed atomic，**绝不为计数加新锁**。理由与批次 4 的 `fs_nused_blocks` 相同：计数点已在既有临界区中，再加锁只引入锁序反转风险而无精度收益。

**命中率的口径必须写清楚**：`bcache_misses` 计的是"未命中缓存、需要从 LRU 取一个槽位"，**不等于**磁盘读次数。新取的槽 `valid = 0`，只有后续 `bread()` 才会真正读盘；`bwrite()` 路径不会。因此跨层只能给出不等式 `disk_reads ≥ (由 bread 触发的 miss)`，不构成可断言的等式。

**验证方法（与批次 1/4 不同，这里没有可扫描的 O(N) 真值）**

1. **同层恒真不变式**：`bcache_hits + bcache_misses == bget()` 总调用次数；
2. **量级对照**：连续执行两次 `ls`，第二次的 `bcache_hits` 增量应远大于 `disk_reads` 增量；
3. **负载对照**：`stressfs` 前后取差值，`disk_writes` 应显著增长（日志提交走 `bwrite`）；
4. **外部真值（进阶，可选）**：在 QEMU 的 `-nographic` 下按 `Ctrl-a c` 进入 monitor 执行 `info blockstats`，其 `rd_bytes` / `wr_bytes` 除以 1024 应与 `disk_reads` / `disk_writes` 同量级。**注意这只是近似对照**：QEMU 可能做预取与对齐，量级一致即可，不要求逐块相等。

这是本批次唯一一项**无法沿用"O(1) 计数器 vs O(N) 遍历"校验套路**的能力，需要在 README 中明确说明其校验方式不同，避免后来者误以为漏了参考实现。

### 5.4 `vmfault` 缺页计数

一次 `__atomic_fetch_add` 加在 `kernel/vm.c:459 vmfault()` 入口，随 `sysinfo` 一起导出。

**价值**：这是唯一能直接观测惰性 `sbrk` 实际效果的指标——`rss` 告诉用户"现在占了多少"，`vmfaults` 告诉用户"分配是逐步发生的"。两者配对才能解释为什么 `vsz` 可以远大于 `rss`。

**口径说明**：`vmfault()` 也会被 `copyin`/`copyout` 路径间接触发，因此它统计的是"按需分配页的次数"，不严格等于"用户态缺页陷入次数"。注释中需写明，避免与 `trap.c` 中 `r_scause()==13/15` 的分支混淆。

**实现时的一处调整**：计数点从函数入口移到了 `mappages()` 成功之后。入口计数会把 `va >= psz` 与 `ismapped()` 这两种无效调用一并计入，而移到成功路径后，该数字直接等于"新映射的页数"，可以与 `rss` 的增长直接对照。

**验证**：用 `usertests` 的惰性 sbrk 用例，观察 `vmfaults` 在访问新页区间时增长，且增量与访问的页数同量级。

---

## 5. 交付物清单

| 类别 | 文件 |
| --- | --- |
| 新增 | `kernel/sysinfo.h`、`user/sysinfo.c`（或并入 `neofetch`，见下） |
| 修改 | `kernel/fsstat.h`、`kernel/fs.c`、`kernel/bio.c`、`kernel/virtio_disk.c`、`kernel/vm.c`、`kernel/main.c`、`kernel/proc.c`（`ncpu_online` 定义）、`kernel/syscall.h`、`kernel/syscall.c`、`kernel/sysproc.c`、`kernel/defs.h`、`user/df.c`、`user/neofetch.c`、`user/user.h`、`user/usys.pl`、`Makefile`、`README.md` |

**未决**：是否新增 `iostat` 用户程序，或让 `neofetch` 与 `top` 直接展示 `sysinfo` 字段。倾向**不新增程序**——批次 5 的四项都不构成"值得独立成命令"的复杂度，塞进 `neofetch` 即可保持镜像精简（当前 29 个程序，`help` 索引已承载 28 条）。若后续 `sysinfo` 字段继续增长，再考虑独立 `iostat`。

---

## 6. 风险与硬约束

1. **`struct psinfo` 零余量（G3）**：批次 5 的四项都刻意绕开了它。若将来必须给 `ps` 加列，需先把快照改为跨两页（两次 `copyout` 或一次性 `kalloc` 两页），这是一次独立的、值得单独成 commit 的改造。
2. **共享头加字段的耦合**：`fsstat.h` 与 `sysinfo.h` 被内核和用户态同时包含，任一侧单独重编都会导致布局不一致。`make` 会正确处理，但**不要手工只编 `user/`**。
3. **`fsstat` 加字段后 `df` 的输出宽度**：现有打印用固定空格对齐，新行需重新对齐，否则破坏可读性。
4. **`neofetch` 移除 `NCPU` 后 `kernel/param.h` 的包含关系**：`neofetch.c` 为使用 `NPROC` 引入了 `param.h`，改造后需确认仍被 `psinfo.h` 间接满足或显式保留。
5. **本机无工具链**：全部改动只能做静态审查。特别需要自查 `defs.h` 的 tag 前向声明块是否登记了新 struct（`struct sysinfo;`），否则 `void f(struct sysinfo*)` 会触发 `declared inside parameter list [-Werror]`。
6. **`-Wall -Werror` 零诊断**是项目基线，任何未使用变量（例如新计数器若只写不读）都会中断编译。

---

## 7. 批次 6 及以后展望

### 7.1 开发者视角：批次 6 选优先级调度，而不是继续加观测

**三条理由**

1. **观测已接近饱和**。4 个批次、5 个 syscall、9 个程序全部是只读的；再加 I/O 计数、缺页计数、锁争用计数，本质是同一套"计数器 + syscall + 打印程序"模式的第 N 次复用，边际学习价值已经很低。观测设施应该开始被当作**验证工具**使用。
2. **它是唯一能让已有设施从"展品"变成"仪器"的候选**。`top` 的 `dcpu` / `busy%` 恰好是验证调度策略的天然度量：高优先级进程应获得显著更大的 `busy%`。相比之下 COW 需要人为构造内存压力才能证明自己生效（本机没有），`/proc` 需要靠 `cat` 之外的间接手段观察。**"新机制必须能被已有观测设施直接验证"是本项目最有价值的筛选标准。**
3. **改动局部且不撞 G3**。只涉及 `proc.c` 的 `scheduler()`、`struct proc` 一个字段、一个 syscall，不触碰内存语义，也不会与 `struct psinfo` 的零余量冲突（见下）。

**关键技术发现：`_pad` 是免费的 4 字节**

`kernel/psinfo.h:28` 的 `int _pad` 是显式填充位，全仓库只在 `kernel/proc.c:757` 被赋值一次（`e->_pad = 0;`）。把它改名为 `int prio` 并填入真实值，**`sizeof(struct psinfo)` 仍然是 64 字节**，编译期断言不受影响。因此"给 `ps` 加一列优先级"**不需要**先做快照跨两页改造——该选项的最大成本被消除了。§6 第 1 条的风险可以推迟到真正需要第 10 个字段时再处理。

### 7.2 实施要点

| 改动 | 要点 |
| --- | --- |
| `kernel/proc.h` | `struct proc` 增加 `int prio`。**必须放在 `p->lock` 保护的那一段**，而不是 "private to the process" 段——`setprio()` 写它、`scheduler()` 读它，存在真实的跨 hart 并发 |
| `kernel/proc.c` | `allocproc()` 给默认优先级；`scheduler()` 由"取第一个 RUNNABLE"改为"取 prio 最小者"；`psinfo()` 填 `prio` |
| `kernel/psinfo.h` | `_pad` 改名为 `prio`（sizeof 不变） |
| `kernel/syscall.h/.c`、`kernel/sysproc.c` | 新增 `SYS_setprio = 29`：`int setprio(int pid, int prio);`。**这是本项目第一个写型 syscall**，此前 23–27 全部只读 |
| `user/ps.c` | 增加 `prio` 列 |
| 新增 `user/priotest.c` | 自检程序，见下方验证 |

**三个必须显式写进注释的取舍**

1. **静态优先级一定会饥饿**。`kernel/trap.c:85-86` 与 `157-158` 表明每个 tick 都会调用 `yield()`，调度器每次都会重新择优；因此低优先级进程只在高优先级进程全部阻塞时才能运行。**必须配老化（aging）**，否则 `usertests` 里大量并发子进程的场景会间歇性失败——这不是理论风险。建议直接把优先级衰减做成 MLFQ 的形状。
2. **无权限模型**。xv6 没有 uid/gid，`setprio()` 无法做任何权限检查，任何进程可以修改任何进程的优先级。显式声明为教学简化。
3. **两遍扫描的 pid 复用竞态**。第一遍扫描选出候选后必须释放 `p->lock`（不能持锁跨越 `swtch`），第二遍重新持锁确认 `state == RUNNABLE`。若该 pid 在两次扫描之间被回收并复用，第二遍可能匹配到另一个进程——宽容处理即可（它同样是 runnable 的），但必须在注释中写明这是**设计选择**而非疏漏。

**验证**

- 新增 `priotest`：两个 CPU 密集子进程，一个设高优先级、一个设低优先级，跑固定 tick 数后用 `psinfo()` 读回两者 `u_ticks`，断言差距方向正确且比例合理；
- **老化验证**：让高优先级进程持续运行，低优先级进程的 tick 计数仍须推进（不为 0），否则老化没生效；
- 交互验证：后台跑一个低优先级 `cputest`，用 `top` 观察 `busy%` 是否被高优先级进程压制；
- **回归**：完整 `usertests` 必须全过（这是饥饿风险的守门人）。

### 7.3 其余候选与排序

| 档 | 内容 | 说明 |
| --- | --- | --- |
| 档 2（批次 7） | `kalloc` 页状态数组：双重释放检测、按调用点归类的泄漏视图 | 需要约 32 KB 的状态表（128 MiB / 4 KiB）。**与 COW fork 有耦合**：COW 引入共享页后引用计数与 RSS 语义都要重定义，合并做比分开做返工少。另有候选：自旋锁争用计数（`acquire()` 自旋次数），可与批次 5 的 I/O 计数同风格 |
| 档 3（批次 8+） | COW fork、信号投递、`/proc` 伪文件系统 | 逐项单独成批次 |

**明确不做（附理由）**

- **COW fork**：RSS 语义需重定义，且与 `struct psinfo` 零余量交互；更关键的是 miniOS 没有内存压力，收益无法被观测。等批次 7 的 kalloc 引用计数设施就绪后再做。
- **信号投递**：唯一收益是让 `top` 常驻，而有界刷新已经够用，收益单薄。
- **`/proc` 伪文件系统**：架构价值最高（把 5 个观测 syscall 统一到文件接口，学习主题是 VFS 与设备层），但改动面大、验证手段间接，建议排在机制类改造之后。

**建议顺序**：批次 5 收尾（§4）→ 批次 6 优先级调度 → 批次 7 `kalloc` 调试设施 → 批次 8 COW 或 `/proc`。

## 附：批次 5 完成后应新增的不变式

| 不变式 | 校验位置 |
| --- | --- |
| `nmeta + nblocks == blocks` | `df`，且 `nmeta == 47` |
| `bcache_hits + bcache_misses == bget()` 调用次数 | `sysinfo`，同层恒真 |
| `disk_reads + disk_writes == virtio_disk_rw()` 调用次数 | `sysinfo`，同层恒真 |
| `ncpu_online <= NCPU`，且等于 `-smp` 配置值 | `neofetch`，配合 `CPUS=` 变参验证 |
