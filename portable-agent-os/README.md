# 便携式 Agent OS（设计 + 可运行骨架）

> 本目录与仓库根的 xv6 内核代码**没有关系**。它是对另一个想法的设计工作：
> 一个 8 GB 镜像 / 6 GB 内存、作为宿主机的子系统运行、集成 agent 框架、
> 可调用多家大模型 API 的便携式系统。放在同一个仓库里只是因为还没有另开仓库。

## 状态

| 部分 | 状态 |
| --- | --- |
| 技术路线 `portable-agent-os.md` | 完成：尺寸与内存预算、选型对照、风险清单、决策记录 |
| 基线清单 `portable-agent-os-checklist.md` | 完成：43 项，每项一个可验证的验收标准 |
| 命令行规格 `portable-agent-os-cli.md` | 完成：命令表**由代码生成**，不是手抄 |
| CLI 骨架 `minios-skeleton/` | **可运行**：41 条命令中 38 条已实现 |
| 首次启动 / 镜像落点 | 完成：幂等，可在临时目录完整演练 |
| 三套测试 | 199 项检查，全过（100 / 67 / 32） |
| **真正构建镜像、启动、跑 agent** | **未做**：`mkosi.conf` 的键名未验证，也没有构建环境 |

## 三条已定决策

| # | 决策 |
| --- | --- |
| `ADR-1` | 不内置本地模型，纯云端（省下约 2.5 GB 镜像与 3 GB 内存） |
| `ADR-5` | **不做任何 UI**：无图形桌面、无 TUI、无 Web；命令行是唯一界面 |
| `ADR-6` | **Agent 框架用现成的**，不自研 |

决策全文与理由在 `portable-agent-os.md` §8。编号写成 `ADR-n`，与清单里的条目编号
（`A1`–`F7`）是**两套命名空间** —— 曾经撞过号，所以特意分开。

用现成框架时有一条硬约束：**框架拿不到 provider 的 URL 与密钥**，它只能指向本机网关端点
（`127.0.0.1:4000/v1`）。否则出网策略、密钥库、provider 冷却与用量账本会被系统里自由度
最大的那个组件绕过，审计日志也就不再是"什么离开了这台机器"的记录。

## 目录

```
portable-agent-os.md              技术路线（预算、选型、风险、ADR）
portable-agent-os-checklist.md    43 项基线功能清单
portable-agent-os-cli.md          命令行规格（命令表由代码生成）
minios-skeleton/
    minios.py                     命令注册表、参数解析、分发（薄层）
    minioscore.py                 全部行为：策略、沙箱、审计、会话、密钥库、网关
    firstboot.py                  首次启动：建立可写层布局、播种默认配置
    image.py                      镜像落点清单 + 暂存 + 校验
    hwprofil.py                   硬件画像（与开机单元同一份代码）
    providers.yaml / egress.yaml  默认配置，同时也是文档
    mkosi.conf                    镜像组成（**键名未经验证**）
    test_cli.py / test_image.py / test_skeleton.py    三套测试
    demo.py / gen_spec.py         演示脚本、规格文档生成器
```

## 跑起来

```sh
cd minios-skeleton
python3 minios.py help            # 命令索引
python3 minios.py doctor          # 自检，按清单编号逐条汇报
python3 minios.py status --json   # 给程序看的那一份

python3 firstboot.py --root /tmp/stage --dry-run    # 看看首次启动会做什么
python3 image.py list             # 镜像落点清单
python3 image.py stage --check    # 生成 mkosi.skeleton/ 并校验一致性

python3 test_cli.py               # 100 项，含一个假 provider 服务器
python3 test_image.py             # 67 项
python3 test_skeleton.py          # 32 项
python3 demo.py                   # 几条命令的真实输出
```

需要 PyYAML（命令本身也要）。

## 两条很容易看错的边界

**一、`doctor` 会回答 `unknown`，那不是通过。** 在开发机上大部分检查判不了（没有 `/proc`、
没有目标镜像）。真正的验收要在目标镜像上跑 —— 自检一旦开始猜，它就变成了安慰剂。

**二、`mkosi.conf` 的键名没有被验证过。** mkosi 在 14→19 之间几乎重写了整套配置键名，
而写它的时候手边没有 mkosi。**构成**（装哪些包、放哪些文件、启哪些服务、产出哪些格式）
可信，**键名**要对着那个版本核一遍。
