"""Regenerate the CLI specification, with the command table built from the code."""
import os
import subprocess
import sys

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, 'minios.py')
OUT = os.path.join(os.path.dirname(HERE), 'portable-agent-os-cli.md')

table = subprocess.run([PY, CLI, 'help', '--format', 'markdown'],
                       capture_output=True, text=True, encoding='utf-8').stdout.strip()

DOC = '''# 便携式 Agent OS：命令行接口规格 v0.3

> 配套：`portable-agent-os.md`（技术路线）、`portable-agent-os-checklist.md`（43 项基线）。
> 可运行骨架：`minios-skeleton/`（41 条命令，38 条已实现；另含首次启动、镜像落点与管理口令）。
>
> **本文的命令表是从代码里生成的**（`minios help --format markdown`），不是手抄的。
> 表格与代码不一致时以代码为准，重新生成即可。

---

## 0. 三条贯穿全表的约定

**一、单入口两级：`minios <object> <verb>`。**
便携设备不该往 PATH 里加二十个名字，只该加一个；而且**一份命令注册表**才能让 `help`、
这份文档和以后的 man 页从同一处渲染。代价是打字长一点，用裸形式补回来：`minios hw` = `minios hw show`。

**二、人读默认，`--json` 到处都有。**
以后的验收脚本与 agent 框架适配层走**同一条代码路径**，所以谁都不会和谁漂移。
`--json` 在每一级解析器上都注册，且用 `argparse.SUPPRESS` 作默认值（见 §7.3）。

**三、退出码有约定，脚本能区分"没准备好"和"坏了"。**

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 执行了，但结果不对（例如自检有 fail） |
| 2 | 用法错误 |
| 3 | **尚未实现**，并说明计划批次 |
| 4 | **前置条件未满足**：未解锁 / 无 provider / 离线 / 无可比对画像 |

第 3 和第 4 条最实用：一个正在施工的系统里，"这条命令还没做"和"这条命令做了但你现在没条件用"
是完全不同的事，混成同一个非零码，脚本就没法写。

---

## 1. 命令索引（''' + str(len(table.split(chr(10))) - 2) + ''' 条）

''' + table + '''

---

## 2. 六条值得单独说明的命令

### `minios status` —— 屏幕最上方那几行

它从**两条独立的轴**回答"现在能干什么"，并且**绝不猜**。一轮 `net probe` 之前：

```
minios   : dev on 9f2c...
hardware : AMD Ryzen 7 7840U, 8 cores, 30.6 GiB
network  : not probed            (run `minios net probe`)
models   : 2 provider(s) configured, reachability not probed
```

探测之后同一屏变成"网络状态 + 哪些 provider 可达"两行。

**"有网"与"能对话"是两件事**，这是纯云端架构里最容易糊在一起的一对状态：网络通但两个 provider
都在冷却，用户该看到的是"网络正常，模型都不可用"，而不是一个转圈或一句"请检查网络"。
`status` 自己不探测（探测是 `net probe` 的事），所以它快、可以随便调、不会因为网络卡住而卡住。

### `minios doctor` —— 可执行的验收清单

那份 43 项清单的**可执行形式**：每条检查用清单编号汇报，`--only A5,C2` 只跑一部分，
`--json` 可以直接进 CI。目前 13 条：

```
id    status   check                                            detail
C2    pass     at least two providers are configured            2 providers configured
D4    pass     the file sandbox refuses to leave the workspace  /etc/shadow is refused with E_PATH_OUTSIDE_WORKSPACE
E2b   fail     the key store is not plaintext                   keys would be stored in plaintext (development only)
F1    unknown  the system partition is read-only                no /proc/mounts on this machine
fail 3, pass 3, skip 1, unknown 4
```

**`unknown` 是这个命令里最重要的设计。** 判不了就说判不了——自检一旦开始猜，
它就从工具变成了安慰剂，而真正的验收要在目标镜像上跑。有 `fail` 时退出码为 1。

其中 **D4 是自检里最值得留意的**：它真的去问一次沙箱"能不能读 `/etc/shadow`"，
并检查答案是不是 `E_PATH_OUTSIDE_WORKSPACE`。**没被测试过的沙箱等于没有沙箱**，
把它做成每次自检都跑的一条，比写一份沙箱设计文档有用。

### `minios policy check <path>` —— 事前回答"这个文件会被发出去吗"

在没有本地模型的架构里，每个被工具读到的文件都可能离开本机。这条命令让用户在
**不花一次请求**的情况下问清楚：

```
$ minios policy check ~/work/id_ed25519
verdict   : would NOT be sent
because   : matches deny rule `**/id_ed25519`
workspace : inside
```

注意它回答的是**出网**问题；`workspace` 一行是另一条更强的规则（沙箱边界），
输出里把两者分开写，因为它们由不同的机制保证。

### `minios tool run` —— 与 Agent 走同一条代码路径

同一个 `run_tool()`，所以这是**对沙箱的真实测试**，不是模拟。失败返回结构化错误码
（`E_PATH_OUTSIDE_WORKSPACE` / `E_PATH_DENIED_BY_POLICY` / `E_TOO_LARGE` / `E_SANDBOX_UNAVAILABLE`…），
模型可以据此自我修正，而不是收到一个异常。

### `minios hw` / `minios hw diff` —— 换机时真正有用的那条

`hw diff` 不带参数时自动选对比对象，顺序是：**同一台机器的上一份画像** →
否则**上一次启动时用的那台机器**。第二条才是便携场景的常态，也是 `last` 要单独记的原因。
实现上 `last` 是**单行文本文件而不是符号链接**——U 盘大多是 exFAT/FAT32，那里没有符号链接。

### `minios ask` / `chat` —— 一个给脚本，一个给人

`ask` 一次一问一答，`--stdin` 可管道输入，`--json` 输出答案+用量+估算费用；
`chat` 是流式交互，`/exit` 退出、`/model NAME` 换模型、`/session` 打印会话号。

---

## 3. `doctor` 与那份 43 项清单的关系

目前 11 条可判定或可诚实标注，其余随批次加入。映射规则：

- 一项检查对应一个清单编号；一个编号可以有多条检查（A2 既看固件类型也看 SecureBoot 变量，
  E2 与 E2b 分别看环境变量与密钥库后端）；
- 检查的**实现进度跟着批次走**（A 域 P1、B 域 P2、C 域 P3，依此类推）；
- 所以 `--only` 是分批次验收的入口：P2 收尾时跑 `--only A1,A3,A4,A5,B1,B3,B5`。

好处是**清单不再是一张静态表**：每加一批功能就同时加一组检查，`doctor` 直接成为回归测试的宿主。

---

## 4. `[admin]` 与 `[network]` 标注不是装饰

- **`[admin]`**：需要 root（`prov add`、`key set`、`persist unlock`…）。便携介质容易丢，
  凡是改配置、碰密钥、动系统的命令都挡一道。开发机上用 `MINIOS_UNSAFE_ALLOW_ADMIN=1` 放行。
- **`[network]`**：没网就没意义（`chat`、`ask`、`prov test`、`update check`…）。
  纯云端下这个标注标出的是一批**在网络不可用时必须明确降级**的能力（对应清单 B6）。

---

## 5. 环境变量（都是给测试与开发用的，不是配置接口）

| 变量 | 作用 |
| --- | --- |
| `MINIOS_CONFIG_DIR` `MINIOS_STATE_DIR` `MINIOS_RUN_DIR` `MINIOS_WORKSPACE` | 改四个根目录 |
| `MINIOS_ALLOW_PLAINTEXT_KEYS=1` | 允许明文密钥库，**仅供开发**；`doctor` 的 E2b 会因此报 fail |
| `MINIOS_UNSAFE_ALLOW_ADMIN=1` | 跳过 root 门槛，**仅供开发** |
| `MINIOS_KEY_BACKEND` | 强制指定密钥库后端 |
| `MINIOS_PERSIST_DEVICE` | 持久层的设备路径 |
| `MINIOS_MAX_READ_BYTES` `MINIOS_MAX_WRITE_BYTES` `MINIOS_MAX_LIST_ENTRIES` | 工具限额 |
| `MINIOS_PROBE_TTL_S` | 探测结果多久算过期（默认 300 秒） |

---

## 5b. 镜像层：首次启动与落点

命令行之外，P1 还差的那块是**让"能开机"这条路径成立**。两个程序负责它：

**`firstboot.py`** —— 把其余代码一直在假设的布局变成事实。
它跑在只读镜像上，而**需要变化的东西只能住在可写层**，所以规则是：

```
/usr/lib/minios/defaults/   不可变的默认值，在只读镜像上
/etc/minios/                工作副本，在可写层上
```

默认值**只在目标不存在时才复制过去**——用户改过的配置永远不被覆盖。
"每次开机都跑"是这事唯一不会被人忘掉的版本，所以它必须是幂等的。
`--root PREFIX` 让它能在一个临时目录上完整跑一遍，包括机器 ID 的生成与配置播种。

**`image.py`** —— 内置一份**落点清单**（11 个文件），`stage` 按它生成 `mkosi.skeleton/`，
`check` 验证暂存树仍与清单一致。用程序而不是手工副本维护这张表，
是为了"`/usr/lib/minios/minios.py` 不在它该在的地方"这类故障有一个单一的查看处。

镜像里**不包含 `/etc/minios`**：只读层上写什么都不代表什么，而一个期待用户去编辑的文件
本来也不该待在只读层。

---

## 6. 实现状态

| 状态 | 命令 |
| --- | --- |
| **已实现** | 38 条 |
| **桩：返回 3 并说明计划批次** | `update check` / `update apply` / `update rollback` |

`update.*` 留桩是刻意的：它需要一张**签过名的第二镜像**和一个能切过去的引导器，
而一条无法验签的"检查更新"比诚实的"还没做"更糟。

**验证**：三套测试共 **199 项检查全部通过**（`test_cli.py` 100、`test_image.py` 67、
`test_skeleton.py` 32），覆盖：

- 41 条命令的 `--help` 能否建立解析器、五类退出码、根/子两处 `--json` 输出逐字节相同；
- 策略匹配的语义（`**` 跨目录、`*` 不跨、无斜杠的模式补 `**/` 前缀、allowlist 反向）；
- 沙箱边界（越界、`..` 穿越后被策略拦下、读取时脱敏、写文件、未知工具、shell 被拒、出网白名单）；
- 密钥库（明文后端告警、存在性、四位提示、真的落盘）；
- **一个假 provider 服务器**：`prov test`、`ask`、`ask --stream`、`chat` 全部真实往返，
  并断言密钥是以 `Bearer` 头送出的；
- 会话（列表/查看/导出/删除）、审计（按 id、参数只存摘要、`--since`）、
  网络（探测后 `status` 不再说 "not probed"）、自检（`unknown` 不等于 `pass`）、
  无痕与持久化、以及 `support-bundle`（清单、脱敏、不含密钥、含会话记录）；
- **镜像层**：firstboot 的幂等性、用户改过的配置不被覆盖、`--dry-run` 不写任何东西、
  **种子配置能被真实加载器（不是 YAML 解析器）读进去**、`image.py check` 能发现
  "暂存树落后于源码"与"清单里已经没有、却还留在树上的文件"。

另有一份 `demo.py`，搭一个临时环境把上面几条命令的真实输出打出来——那是给人看的证据。

---

## 7. 写这套 CLI 踩到的五个 argparse 陷阱

都不是语法错误，而是**能跑、但行为和你想的不一样**：

**7.1 `add_parser` 只存在于 `add_subparsers()` 的返回对象上。**
`parser.add_parser(...)` 不存在——组解析器本身没有这个方法，二级命令必须在组内**再建一层**
`add_subparsers()`。

**7.2 把 `set_defaults(path=...)` 放在组上，会遮蔽叶子自己的默认值。**
argparse 在解析开始时为尚未存在于 namespace 的属性填默认值，而子解析器拿到的是**同一个
namespace**，所以组先写的值挡住了叶子想写的那个——**所有子命令都会解析成组默认值，且不报错**。
解法：不在解析器里存路径，改存两个 dest（`root` / `verb`），在 `main()` 里合成。

**7.3 子解析器上的 `--json` 默认值会覆盖根解析器解析到的值。**
`store_true` 的默认值是 `False`，子解析器会把根刚设好的 `True` 覆盖掉，于是
`minios --json status` 静默打印人类输出。解法是子级用 `default=argparse.SUPPRESS`。

**7.4 把命令路径存进 `args.path`，会覆盖同名位置参数。**
`minios policy check <path>` 的位置参数就叫 `path`。`main()` 里一行 `args.path = 命令路径`
把它换成了字符串 `"policy.check"`，于是这条命令**去检查一个名叫 policy.check 的文件**，
并且如实汇报"允许发送"。改用保留名 `cmd_path`。

**7.5 裸 `minios hw` 走组内第一条动词，因此裸形式不接受该动词独有的选项。**
`--raw` 只在 `hw show` 上声明，裸形式下要用 `minios hw show --raw`。这是刻意取舍：
让它在裸形式下也生效，就得把选项复制到组上，help 里同一件事会出现两次。

---

## 8. 一条**不属于** argparse、但同样安静地坏掉的坑：路径分隔符

策略里的规则写作 `**/*.pem`（用 `/`），而 `os.path.realpath` 在 Windows 上返回 `\\`。
两者直接比较的结果是：**规则全部落空，什么都不再被拦**——不报错、不警告，
只是 deny 列表突然不再 deny。工作区边界同理，`C:\\work` 与 `C:\\work/` 前缀比较失败，
**每个路径都被判成"在工作区外"**（这个是 fail-closed，方向安全，但工具变得不可用）。

修法是**在比较点归一化**，而不是在写盘时改路径：新增一个 `norm()`，把 `os.sep` 换成 `/`
后再做匹配与前缀比较。附带的收益是这套逻辑在 Windows 上可测——否则测试永远只能覆盖
Linux 那一半。

---

## 9. 下一步

1. **A1（UEFI/Legacy 双模式）检查**：它需要一次真实的双模式启动，只能人工判定。
   要在支持矩阵里如实标注，而不是假装 `doctor` 覆盖了它。
2. **`update.*`**：等 P6 的签名与 A/B 分区就绪后再实现；在那之前保持桩。
3. **P2 的网络域**：`net probe` 有了，还缺 B2（Wi-Fi 连接）与 B4（桥接后局域网可直连）
   的验收路径。后者在虚拟机形态下由**宿主**承担，客机侧只需要"不对模式做任何假设"——
   这一点已经在 B3/B4 的验收标准里写死。
4. **首次运行向导的串联（E1）**：两个动作已经有了（`admin passwd`、`key set`），
   还缺一个把它们按顺序串起来的向导，以及"已设置就跳过"的判断。在那之前，
   `status` 用两行提示代替它。
'''

with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write(DOC)
print('wrote %s  %d bytes  %d lines' % (OUT, len(DOC.encode('utf-8')), DOC.count('\n') + 1))
print('table rows embedded:', table.count('\n') - 1)
