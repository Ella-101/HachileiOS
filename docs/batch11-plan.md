# 批次 11 计划：用户身份与权限（阶段三）

> 状态：第一、二批**已实施**（第一批已推送并通过 CI，第二批含磁盘格式变更，待在 WSL 验证）；第三至五批未开工。
> 若需改选，见每条的"影响面"。
> 基线：上游 `56b9bf7`（阶段一、二已完成并合并）。分支：`batch11-identity-permissions`。
> 依据：`docs/minios-roadmap.md` §5 列出的六项要求；本文是它的落地设计与风险清单。

---

## 1. 这一批和前面九批的性质不同

前面九批都是「加能力」：新系统调用、新计数器、新调度策略、新内存语义。**这一批是第一次改磁盘格式** —— inode 要记住属主与权限位，而 `fs.img` 是持久状态，改错了不是重新编译能修的。

它同时改的是**安全语义**。权限检查漏一处，症状不是崩溃，而是**静默的越权**：程序照常运行，只是不该读的读到了。因此每一批的验收都必须包含**负向用例**（该拒绝的必须被拒绝），只测「正向能用」等于没测。

第三个不同点：它会给 CI 带来一个**真实的破坏风险**（见 §7.5）—— 如果启动路径改成先登录，`test-xv6.py` 往 shell 里发命令的方式会全部失效。这一点必须在设计阶段就解决，不能留到实现时才发现。

---

## 2. 现况调研（逐项对着代码，已核对）

### 2.1 进程侧

| 事实 | 位置 |
| --- | --- |
| `struct proc` **没有任何身份字段**（只有 pid、prio、pgid、信号相关） | `kernel/proc.h` |
| `kfork()` 是**逐字段复制**，所以加字段后必须显式决定继承规则 | `kernel/proc.c` |
| `kexec()` 只换地址空间，不清空 proc 主体 ⇒ uid 天然跨 exec 保留 | `kernel/exec.c` |
| `kkill()` **只比对 pid**，不检查调用者与目标的关系 | `kernel/proc.c` |
| `ksetprio()` 同样不检查 | `kernel/proc.c` |
| `init` 直接 fork + exec `sh`，**没有登录环节** | `user/init.c` |

### 2.2 文件系统侧

| 事实 | 位置 |
| --- | --- |
| `struct dinode` = type/major/minor/nlink(4×short) + size(4) + addrs[13](52) = **恰好 64 字节** | `kernel/fs.h` |
| **硬约束**：`assert((BSIZE % sizeof(struct dinode)) == 0)` ⇒ sizeof(dinode) 必须整除 1024，只能是 64、128…**不能是 68/72** | `mkfs/mkfs.c:88` |
| `IPB = BSIZE / sizeof(dinode)` = 16（方案 A 下不变） | `kernel/fs.h` |
| 内存中的 `struct inode` 是 dinode 的副本，加字段要同步 | `kernel/file.h` |
| **权限检查点目前一个都没有**：`namei` 只按路径查找 | `kernel/fs.c` |
| `namei`/`nameiparent` 共 **8 个调用点**（exec、allocproc、link×2、unlink、create、open、chdir） | 见 §5.3 |
| `create()` 是唯一创建 inode 的地方，属主应在此确定 | `kernel/sysfile.c` |
| `mkfs` 用 `ialloc(T_FILE)` 写初始文件，属主与 mode 需在此确定 | `mkfs/mkfs.c:156` |
| 格式标识只有 `FSMAGIC`(0x10203040)，`fsinit()` 里不匹配即 `panic("invalid file system")` | `kernel/fs.h`、`kernel/fs.c` |
| `MAXFILE = (NDIRECT + NINDIRECT)` **从 NDIRECT 推导**，`usertests` 用的是符号 | `kernel/fs.h`、`user/usertests.c:593,613,3208` |

最后一条很重要：**调整 NDIRECT 不需要改动测试套件**。已逐行核对 `usertests.c`，它引用的是 `MAXFILE` 符号而非字面量。

### 2.3 系统调用与用户态

- 编号到 **39**（`SYS_jobstate`），**下一个可用号是 40**。
- 39 个调用是：`fork exit wait pipe read kill exec fstat chdir dup getpid sbrk pause uptime open write mknod unlink link mkdir close sync psinfo freemem klog waitx fsinfo sysinfo setprio sigaction sigmask sigreturn signal killpg setpgid getpgid tcsetpgrp waitpg jobstate`。
- 新增共享头/结构要**同时登记三处**：`kernel/defs.h` 的 tag 前向声明、`user/user.h` 的 include、`user/usys.pl` 的 entry。前两处已经各踩过一次。
- `struct stat`（`kernel/stat.h`）是内核/用户共享结构，加字段会改**用户可见 ABI**。
- `struct psinfo` 目前是 **64 字节 × NPROC(64) = PGSIZE，零余量**；上次给 `ps` 加 prio 列用的是它原来的 `_pad`，**现在没有余量了**。

---

## 3. 身份模型

### 3.1 候选

| 模型 | 内容 | 评价 |
| --- | --- | --- |
| A | 只有 uid，权限只分 owner / other | 代码最少，但 `ls -l` 的 group 段没有意义，教学价值低 |
| **B（推荐）** | `uid` + `gid` + **9 位权限**，判定按 owner → group → other | 与 `ls -l` 的直觉一致；只多约 15 行判定代码 |
| C | 再加 setuid 位、补充组（supplementary groups）、`/etc/group` | **明确不做**：setuid 位是提权面，教学系统里收益低风险高；补充组要维护集合，复杂度不成比例 |

### 3.2 模型 B 的精确边界（写清楚，避免实现时膨胀）

- **uid**：进程的实际身份，`fork` 继承，`exec` 保留。uid 0 = 特权（root）。
- **gid**：进程的主组。由 `login` 在降权时设置，之后普通进程改不动它；没有 setgid 位、没有补充组。
- **inode**：`uid`、`gid`、`mode`（9 个权限位有效）。
- **判定**：按 owner → group → other 选**唯一**一类，不做回退（见 §5.2 的说明，这是最容易写错的地方）。
- **没有** setuid/setgid 位、没有 sticky 位、没有 ACL、没有 capabilities、没有文件时间戳。
- **降权不可逆**：本模型只有「实际 uid」一个字段，没有 saved-uid，所以 setuid 到非 0 之后**无法再回到 root**。这是简化，也是安全的默认。

---

## 4. 磁盘格式变更（本文最关键的决定）

给 inode 加身份需要额外 6 字节（uid + gid + mode 各 2 字节）。但 §2.2 那条 `assert` 决定了 `sizeof(dinode)` 必须整除 1024，**不能只加 6 字节**。两条可行路线：

### 方案 A（推荐）：dinode 保持 64 字节，NDIRECT 12 → 10

```c
struct dinode {
  ushort type;               // 文件类型
  ushort mode;               // 权限位（低 9 位有效）      ← 新增
  ushort uid;                // 属主                      ← 新增
  ushort gid;                // 属组                      ← 新增
  ushort major;              // 设备号（仅 T_DEVICE）
  ushort minor;
  ushort nlink;
  ushort _pad;               // 显式填充：不用隐式对齐字节
  uint   size;
  uint   addrs[NDIRECT + 1]; // NDIRECT = 10 ⇒ 11×4 = 44
};
```

8 个 short = 16 字节，`size` 占 4 → 20，`addrs` 占 44 → **合计恰好 64**，`1024 % 64 == 0` 成立。

| 影响 | 变化 |
| --- | --- |
| `IPB` | 16（**不变**） |
| `ninodeblocks` | 13（**不变**） |
| `nmeta` | 47（**不变**） |
| 数据块 | 2353（**不变**） |
| 单文件上限 | 268 → 266 块，即 274,432 → 272,384 字节（**少 2 KB**） |

**推荐它的理由**：这一批真正的风险是「格式变更」本身，而方案 A 把变更限制在 **inode 的内容**，**磁盘布局一个字节都不动**。于是 `nmeta = sb.size − sb.nblocks` 的推导、`IPB`、`IBLOCK`、以及 README 里 `df` 的口径全部继续成立，对照与回滚都容易得多。2 KB 的上限损失由 `MAXFILE` 自动吸收，不改测试。

### 方案 B：dinode 扩到 128 字节，NDIRECT 保持 12

`addrs[13]` 占 72 字节，再补 56 字节保留区到 128。`IPB` → 8，`ninodeblocks` 13 → **26**，`nmeta` 47 → **60**，数据块 2353 → **2340**。

好处是单文件上限不变，且留 64 字节余量给将来（时间戳、扩展字段）。代价是每个 inode 浪费 60 字节（200 个共 12 KB），且**布局真的变了**：README 的 `df` 口径与 `nmeta` 推导都要跟着改。

### 兼容性策略（两个方案共用）

1. **改 `FSMAGIC`**（`0x10203040` → 新值），`mkfs` 写新值。
2. **`fsinit()` 的 panic 信息写清楚**，从 `panic("invalid file system")` 改成能一眼看出原因的话，例如「this image was built with an older inode format; rebuild with mkfs」。
3. **不提供自动迁移路径**。理由要说明白：本项目的唯一持久文件系统是每次构建由 `mkfs` 重新生成的 `fs.img`，没有需要跨格式保留的用户数据。迁移代码的复杂度换不来任何收益 —— 这是**明确的取舍，不是遗漏**。

---

## 5. 接口定义（实施前冻结）

### 5.1 新增系统调用（编号 40 起）

在原有 39 个之后**追加**，不重排任何既有编号。

| 号 | 名称 | 语义 | 权限 |
| --- | --- | --- | --- |
| 40 | `getuid()` | 返回调用者的 uid | 无 |
| 41 | `getgid()` | 返回调用者的 gid | 无 |
| 42 | `setuid(uid)` | 设置调用者的 uid | 仅当 uid 为 0，或目标值等于当前 uid；否则返回 -1 |
| 43 | `setgid(gid)` | 设置调用者的主组 | 同上 |
| 44 | `chmod(path, mode)` | 改权限位，返回前清掉非权限位 | 属主或 uid 0 |
| 45 | `chown(path, uid, gid)` | 改属主与属组 | **仅 uid 0**（普通用户不得赠送文件） |

`setgid` 单独成一个调用，而不是"组跟随用户"：若 `gid` 恒等于 `uid`，权限模型的 group 一档
就只会是 owner 档的副本，永远不可能授出 owner 没授出的权限 —— 那一档等于死代码。
两者都要改时**顺序是先 `setgid` 后 `setuid`**：`setuid` 到非 0 之后就再也改不动组了。

**认证不进内核**：`login` 以 root 运行，在用户态读账户文件、校验口令，然后调 `setuid()` 降权。内核只提供"降权"这一个原语，不理解口令。这样口令的存储与策略都在用户态，内核攻击面不增加。

### 5.2 权限判定（放在 `kernel/fs.c`，只有一份）

```c
// acc: ACC_R(4) / ACC_W(2) / ACC_X(1)
static int
perm_ok(struct inode *ip, ushort uid, ushort gid, int acc)
{
  uint shift;

  if (uid == 0)
    return acc == ACC_X ? (ip->mode & 0111) != 0 : 1;

  if (uid == ip->uid)
    shift = 6;
  else if (gid == ip->gid)
    shift = 3;
  else
    shift = 0;

  return (ip->mode >> shift) & acc;
}
```

两处语义要点，都必须写进注释：

1. **不回退**。只匹配最具体的那一类：属主命中就只看属主位。若写成「属主不通过再看 group、group 不通过再看 other」，一个被属主故意设成 `0600` 的文件会被同组或其他人读到 —— 这是经典错误，而且是**静默提权**。
2. **root 也要过执行位**。root 绕过读写，但执行一个没有任何 x 位的文件仍然应当被拒绝。这一条在本项目里有实际意义：`exec` 走的就是这条判定。

### 5.3 目录搜索权限与 `namei` 的签名

**目录语义**：读目录表（`ls`）要 `r`；**穿过目录**要 `x`。`namex()` 走每一级都要检查 `x`，这是最容易漏的一处 —— 漏了它，一个 `0700` 的目录仍然能被别人穿过并读到里面的文件。

`namei`/`nameiparent` 目前不接受身份参数，**8 个调用点**：

```
kernel/exec.c:42        namei(path)            调用者的身份
kernel/proc.c:297       namei("/")             allocproc 中，尚无身份 ⇒ 用系统身份(root)
kernel/sysfile.c:134    namei(old)             link
kernel/sysfile.c:156    nameiparent(new,...)   link
kernel/sysfile.c:214    nameiparent(path,...)  unlink
kernel/sysfile.c:265    nameiparent(path,...)  create
kernel/sysfile.c:351    namei(path)            open
kernel/sysfile.c:442    namei(path)            chdir
```

**推荐：加一个 `struct cred { ushort uid, gid; }` 参数**，调用点用 `mycred()` 取。理由是「规则只有一份」这条项目原则 —— 把身份显式传进去，判定点在哪里、用的是谁的身份，读代码时一眼可见；而在函数内部偷偷 `myproc()` 会让 `namei` 变成一个隐式依赖全局状态、也因此难以单独验证的函数。代价就是上面这 8 处。

### 5.4 用户可见的结构改动

| 结构 | 改动 | 影响面 |
| --- | --- | --- |
| `struct stat`（`kernel/stat.h`） | 加 `uint mode; uint uid; uint gid;` | 所有用 `stat` 的用户程序；`ls -l` 需要它 |
| `struct psinfo` | 加 `ushort uid; ushort gid;` | **零余量，必须改成跨两页的快照**（见下） |

`struct psinfo` 这一项要单独说明：它现在是 64×64 = 4096，**恰好一页，没有余量**，而上次加 prio 列已经用掉了原来的 `_pad`。加字段就必须把快照改成两页（`NPROC × 128 = 8192`）。这是一处已知护栏，改动本身很干净，但**必须成对修改内核侧与 `user/ps.c`**，否则用户态读到的会错位。

---

## 6. 并发与不变式

| 项 | 规则 |
| --- | --- |
| `ip->uid/gid/mode` 的读写 | 归 `ip->lock`（sleeplock）。`perm_ok()` 在 `ilock` 保护下调用，不额外加锁 |
| 进程身份 | 归 `p->lock`。`setuid` 在 `p->lock` 下写；判定时读调用者自己的字段，不跨进程读 |
| `kkill`/`ksetprio` 的权限检查 | **必须在 `acquire(&p->lock)` 之后读目标 uid**，不能先放锁再比 —— 否则检查与动作之间目标可能已被 `freeproc()` 回收并复用给新进程 |
| 已打开的文件描述符 | **不在每次 read/write 时重新检查权限**。`open` 时的判定就是最终判定，之后改 mode 不影响已打开的描述符。这是 POSIX 行为，也是唯一能避免"检查与使用之间变量被改"的做法。要写进注释，否则看起来像漏了检查 |
| 锁序 | 不引入新的锁，不改变既有锁序 |

---

## 7. 分批实施（每批可独立验证）

拆成五批，**每批结束时整个系统都必须能跑通既有回归**。关键在第一批只是加字段、第二批只是"所有人仍是 root"，所以到第三批权限真正生效之前，行为变化为零。

### 7.1 批次 1：身份字段与身份系统调用（**不改磁盘格式**）

`struct proc` 加 `uid`/`gid`（默认 0）；`kfork()` 显式继承；新增 `getuid`/`getgid`/`setuid`/`setgid`；`user/id.c` 打印当前身份；`user/idtest.c` 自检下列规则。

**验收**：`idtest` 末行 `idtest: OK`；`id` 输出 `uid=0 gid=0`；完整 `usertests` 全过
（这一批不改行为，必须全过）。`idtest` 以 root 启动后 fork：子进程降权、父进程保持 root，
因为降权不可逆（§3.2），一个进程没法既降权又验权。它覆盖 12 条断言 —— fork 继承身份、
exec 保留身份（fork 一个孙进程去 `exec id`，把输出经管道读回后解析）、降权后无法回到 root、
无法转成别的身份、no-op 形式仍合法、越界值被拒、父进程不受子进程降权影响。

**这一节踩过的坑（写下来免得重犯）**：xv6 的 `&` 是**分隔符，不是连接符** —— `A & B` 是语法错误。
`parsecmd` 会把 `B` 判为 leftovers，打印 `leftovers: B` 然后 `panic("syntax")`；用户态 `panic` 就是
`fprintf + exit(1)`，于是 **shell 进程退出**，`init` 的循环再拉一个新的（屏幕上看起来像"系统重启"）。
要跑两条命令就写成两行，或用 `id ; id`。另外 shell **没有 `wait` 内建**（只有 `cd`/`jobs`/`fg`/`bg`），
`wait` 会被当作程序去 exec，报 `exec wait failed`。

### 7.2 批次 2：磁盘格式与属主/权限位（**改格式，但所有人仍是 root**）

§4 的方案落地；`mkfs` 以 uid 0 写初始文件并设置 mode（可执行文件 0755、数据文件 0644，`/etc` 目录 0755）；`create()` 以调用者身份与 `0644`/`0755` 建 inode；`stat` 暴露 `mode`/`uid`/`gid`；新增 `chmod`/`chown`；`user/ls.c` 加 `-l`；新增 `user/whoami.c`。

**验收**：**既有全部测试仍然通过**（因为每个进程都是 root，判定恒真）—— 这一条是本批最重要的验收，它证明格式变更本身没有破坏任何东西；`ls -l` 输出的 mode 与属主正确；新文件属主等于创建者。

**已实施（第二步）**：`struct dinode` 增加 `mode`/`uid`/`gid`，`NDIRECT` 12 → 10 把三个 16 位字段
从同一 64 字节里腾出来（`sizeof(dinode)` 仍是 64，`mkfs` 的整除断言与 `IPB = 16` 都不变）；
`FSMAGIC` 换成 `0x10203041`，并保留旧值 `0x10203040` —— `fsinit()` 认出旧镜像时给的是
"用了旧 inode 格式，请重跑 mkfs" 而不是一句无信息量的 "invalid file system"；
`mkfs` 写初始文件的属主 0 与 mode（**以主机名里的前导 `_` 判断是不是程序**：`_cat` → 0755，
`README` → 0644，目录 0755 —— 用文件名而不是主机 stat，因为 drvfs 挂载把所有文件都报成 0777）；
`create()` 把新 inode 的属主设为调用者、mode 设为 0644（目录 0755）；`stati()` 暴露三个字段；
新增 `chmod`(44) / `chown`(45)；`ls -l` 打印 10 字符权限列 + 链接数 + 属主 + 大小；新增 `user/chmod.c`。

与计划的**两处偏离，都记在这里**：

1. **`user/whoami.c` 推迟到第五批**。计划写它时 `id` 还不存在；在没有账户/用户名的状态下，`whoami` 只能打印 uid，
   与 `id` 完全重复，白占镜像空间。第五批有名字之后再让它输出名字才有意义。
2. **多了一个 `user/chmod.c`**（计划只写了系统调用）。理由是本项目的一条硬规矩：
   改文件属性的调用如果 shell 里没有程序能碰，那这个接口就**无法验收**。`mkdir`/`ln`/`rm` 都是"程序 + 系统调用"的
   成对形式，`chmod` 照办；`chown` 只有 root 能用、且要等第五批有非 root 进程才测得出，所以暂留系统调用，
   由第三批的 `permtest` 覆盖。

**本地静态审查抓到的一个真 bug**：`ls -l` 的权限列渲染循环起点写成了 `i = 8`，实际检查的是 bit 10..2 而不是 8..0，
会把 `0644` 显示成 `--xr-x--x`。编译器不会报这种错（表达式合法），所以用同样的表达式在本地对 8 种已知 mode 做了单元核对，
全部吻合后才修。`chmod.c` 的八进制解析同样做了 12 例单元核对 —— 原来的宽松写法会让 `chmod 8 f` 静默变成 0，
等于一个手滑就清掉全部权限位，现在非八进制字符一律拒绝。

**未编译**：本机没有 RISC-V 工具链，以上全部只经过静态审查（`ci-check.py`：87 文件格式与静态检查全过）
与上述单元核对。

### 7.3 批次 3：权限检查生效（**负向用例是重点**）

§5.2 的 `perm_ok()`、§5.3 的目录 `x` 检查、`open`/`exec`/`unlink`/`link`/`mkdir` 的检查；新增 `user/permtest.c`。

`permtest` 的形状值得先定下来：**它必须以 root 启动然后 fork** —— 子进程 `setuid(1001)` 降权后尝试越权操作，父进程保持 root 验证"该拒绝的确实被拒绝了"。降权不可逆（§3.2），所以测试不能在一个进程里既降权又验权。

**验收**：`permtest` 的每一项负向断言成立（读不到别人的 `0600` 文件、写不进别人的目录、穿不过 `0700` 的目录、执行不了没有 x 位的文件、不能 unlink 别人的文件）；`usertests` 在 root 下继续全过。

### 7.4 批次 4：特权操作与可见性边界

`kill`/`killpg`/`signal`/`setprio`/`setpgid`/`tcsetpgrp` 增加「同 uid 或 uid 0」检查；`mknod` 仅 uid 0；`klog`（`dmesg`）仅 uid 0；`psinfo` 加 uid（跨两页快照），`ps` 显示属主列。

**可见性边界的取法（需要拍板）**：本批推荐「**进程统计对所有身份可见，内核日志仅 root**」。理由是 `psinfo` 里只有 pid/sz/rss/次数这类数字，不含文件内容与命令行，公开它不泄漏隐私；而内核日志里有完整路径与设备信息，限制它是划算的。若要求「普通用户只看得到自己的进程」，那是同 uid 过滤，数据已经在了（uid 已进 `psinfo`），可以后续单独开一批。

**验收**：跨 uid 的 `kill`/`setprio` 被拒绝，同 uid 的允许；非 root 的 `dmesg` 被拒绝；`ps` 正确显示属主。

### 7.5 批次 5：账户与登录（**含 CI 兼容风险，见下**）

`mkfs` 建立 `/etc` 目录与初始账户文件（格式 `name:uid:gid:hash`，mode **0600，仅 root 可读**）；用户态实现口令哈希与失败延迟；新增 `user/login.c`；`user/init.c` 改为先 fork `login`；新增 `user/passwd.c`（仅 root 可改）。

**这里有一个必须先解决的 CI 兼容问题。** `test-xv6.py` 与 `test-harness.py` 都是往 shell 里直接发命令的；若启动路径变成"先出现登录提示"，**全部测试会立刻失效**，而且失败形态会是"超时"而不是"断言失败"，很难定位。

**推荐解法：`init` 默认经过 `login`，但 root 账户的默认状态是「免密自动登录」。** 即账户文件里 root 的口令字段为特殊值（如 `*`）时，`login` 不做提示、直接 `setuid(0)` + `exec sh`。这样：

- 默认镜像的启动路径**与现在完全一致**，既有测试与 CI 不受影响；
- 登录流程本身是真实存在、可验证的 —— `permtest` 或专门的用例可以用一个带口令的普通账户走完整流程；
- 把 root 也要求口令，只需改 `mkfs` 写入的内容，不改代码。

**口令存储**：用户态无加密库，需自写一个小实现（盐 + 多轮哈希）。**必须诚实标注它的强度**：它能阻止"直接读文件看到明文口令"，但**不抗有决心的离线破解**。同时，账户文件是 0600、只有 root 能读，这是第一道防线。失败处理：连续失败按次数递增延迟（例如 1/2/4 秒），避免在线暴力猜测。

**明确不做**：用户自助改口令（那需要提权助手，而本批不实现 setuid 位）；`/etc/group` 与补充组；账号增删的管理工具（初始账户写在 `mkfs` 里）。

**验收**：错误口令不进入且延迟递增；两个不同 uid 的登录会话互不越权（A 读不到 B 的 `0600` 文件）；默认镜像启动后 shell 立即可用（CI 兼容）；`usertests` 与 `crash` 继续通过。

---

## 8. 风险清单

| # | 风险 | 处理 |
| --- | --- | --- |
| 1 | 权限检查漏一处 ⇒ 静默越权 | 每个被检查的入口都要有**成对的负向用例**；判定收敛到唯一函数 `perm_ok()` |
| 2 | 磁盘格式改错 ⇒ 镜像读不出来 | §4 方案 A 保持布局不变；`FSMAGIC` 变更使旧镜像被明确拒绝而非误读 |
| 3 | `struct dinode` 大小不整除 1024 ⇒ `mkfs` 断言失败 | 已核算：方案 A 恰好 64 字节；实施后**必须**跑一次 `mkfs` 确认断言通过 |
| 4 | 改成登录后 CI 全部超时 | §7.5 的 root 免密默认；实施后必须实跑 `./test-xv6.py usertests` 与 `crash` |
| 5 | `psinfo` 跨页改造漏改用户侧 ⇒ `ps` 读到错位数据 | 内核与 `user/ps.c` 成对修改，并在 `psinfo.h` 里写死一句注释说明页数 |
| 6 | `-Werror` 打回（尤其 macOS 的 riscv64-elf-gcc） | 新增的未使用变量、只写不读的变量会挂 macOS job；这是已踩过的坑 |
| 7 | 本机无编译器、无 qemu | **所有实现只能静态审查 + 交给用户在 WSL 验证**；`ci-check.py` 与 `ccheck.py` 是唯一防线，每被真实编译器打回一类错误就补一条检查 |

---

## 9. 需要拍板的事项

| # | 事项 | 推荐取值 | 影响面 |
| --- | --- | --- | --- |
| D1 | 磁盘格式走方案 A 还是 B | **方案 A**（dinode 保持 64 字节，NDIRECT 12→10，布局不变，单文件上限 −2 KB） | 格式与文档口径；A 的回滚与对照成本明显更低 |
| D2 | 是否实现组语义 | **实现**（9 位 × owner/group/other），但只有一个主组、无补充组、无 setuid 位 | 约 15 行判定代码；不实现则 `ls -l` 的 group 段失去意义 |
| D3 | 启动是否默认经过 `login` | **经过，但 root 默认免密** | 直接决定 CI 是否继续可用；见 §7.5 |
| D4 | 进程统计的可见性边界 | **统计全公开 + 内核日志仅 root** | 若要"只看自己的进程"，数据已在 `psinfo` 里，可后续单开一批 |

---

## 10. 明确不做（避免范围失控）

- setuid / setgid 位、sticky 位
- 补充组（supplementary groups）、`/etc/group`
- 文件时间戳、ACL、扩展属性
- 用户自助改口令（需要提权助手）
- 账号增删工具（初始账户写在 `mkfs`）
- 磁盘格式的自动迁移（见 §4，理由已说明）
- 用「输入用户名即切换身份」的任何形式的认证

---

## 11. 实施中发现：2 MiB 镜像已经装不下

第一批新增的两个程序（`_id` 35 块 + `_idtest` 45 块 = 80 块）把镜像推过了临界点：CI 上 `make fs.img`
报 `balloc: first 1768 blocks have been allocated`，随后 `usertests` 的 `writebig` 需要
`MAXFILE`(268) + 1 个间接块 = **269 块**，而只剩 **232 块** ⇒ `balloc: out of blocks`、
`write big file failed i=231`。

按块算账（`FSSIZE 2000`，`nmeta 47` ⇒ 数据区 1953）：

| 项 | 块 |
| --- | --- |
| 37 个用户程序（含每个 >12 KB 文件的 1 个间接块） | 1647 |
| 镜像内 `README`（纯文本，不是 `README.md`） | 3 |
| 根目录 | 1 |
| 合计 / 余量 | 1651 / **302** |

本地余量 302 > 269，所以 WSL 全过；**CI 的编译器生成的二进制胖了 70 块**，余量掉到 232 < 269 才失败。
也就是说这一批**没有写错任何东西**，只是把原先仅 **43 块**的余量吃掉了 —— 而"能不能过"取决于编译器
产出的体积，这本身就是不安全的配置。

修法：`FSSIZE` 2000 → **2400**（数据区 2353，余量 702，约 2.6× `writebig` 的需求）。代价只是 `fs.img`
从 2.0 MB 变成 2.34 MB；`nbitmap`、日志块数、缓冲缓存都与 `FSSIZE` 无关，`fs.img` 也不进内存，
所以 README 里"扩大容量需同时评估日志、缓存和内存"那条约束这次不构成负担。

遗留：`README.md` 与 `docs/minios-roadmap.md` 里"约 2 MiB 文件系统"这句现在偏小（实际 2.34 MiB），
两份都是上游文件，未改。
