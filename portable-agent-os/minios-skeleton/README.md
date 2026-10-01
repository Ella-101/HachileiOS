# minios-skeleton

便携式 Agent OS 的可运行骨架。
**这些文件不是在描述设计，它们就是可以跑的东西。**

| 文件 | 是什么 | 状态 |
| --- | --- | --- |
| `minios.py` | 命令注册表、参数解析、分发（薄层） | 完整，41 条命令 |
| `minioscore.py` | 全部行为：策略、沙箱、审计、会话、密钥库、网关、口令、命令实现 | 完整 |
| `firstboot.py` | 首次启动：建立可写层里的布局，播种默认配置 | 完整，幂等 |
| `image.py` | 镜像落点清单 + 暂存到 `mkosi.skeleton/` | 完整 |
| `hwprofil.py` | 硬件画像生成器 | 完整，与开机单元同一份代码 |
| `profile.schema.json` | 画像的 JSON Schema | 完整 |
| `hwprofild.service` / `minios-firstboot.service` | 开机单元 | 完整 |
| `mkosi.conf` | 镜像组成 | **键名未经验证**（见文件内注释） |
| `providers.yaml` / `egress.yaml` | 默认配置（也作为文档） | 完整 |
| `test_cli.py` | 命令行端到端测试，100 项 | 全部通过 |
| `test_image.py` | 首次启动与镜像层测试，67 项 | 全部通过 |
| `test_skeleton.py` | 骨架文件测试，32 项 | 全部通过 |
| `demo.py` | 搭临时环境，把几条命令的真实输出打出来 | 可运行 |
| `gen_spec.py` | 从代码生成命令表并重写规格文档 | 可运行 |

规格文档在上一级：`../portable-agent-os-cli.md`（命令行 v0.3）、
`../portable-agent-os.md`（路线）、`../portable-agent-os-checklist.md`（43 项基线）。

## 三条核心设计规则

**一、一个只读层，一个可写层。**

```
/usr/lib/minios/defaults/   不可变的默认值，在只读镜像上
/etc/minios/                工作副本，在可写层上
```

默认值**只在目标不存在时才复制过去**——用户改过的配置永远不被覆盖。
"每次开机都跑"是这事唯一不会被人忘掉的版本，所以 `firstboot` 必须幂等。
`--root PREFIX` 让它能在一个临时目录上完整跑一遍（包括机器 ID 生成与配置播种），
所以它测得动。

**二、规则只有一份，被三个调用者共用。**
`minios.py` 只有注册表与解析，行为全在 `minioscore.py`。
同一条规则要被用户敲的命令、`doctor` 里的自检、测试三方使用；
只活在其中两个里的规则，在第三个里一定是错的。

**三、镜像里没有 `/etc/minios`。**
只读层上写什么都不代表什么，而一个期待用户去编辑的文件本就不该待在只读层。

## 跑起来

```sh
python3 minios.py help          # 命令索引
python3 minios.py doctor        # 自检：按清单编号逐条汇报
python3 minios.py hw            # 硬件画像
python3 minios.py status --json # 给程序看的那一份

python3 firstboot.py --root /tmp/stage --dry-run   # 看看首次启动会做什么
python3 image.py list           # 镜像落点清单
python3 image.py stage          # 生成 mkosi.skeleton/
python3 image.py check          # 暂存树是否还和清单一致

python3 test_cli.py             # 100 项（含一个假的 provider 服务器）
python3 test_image.py           # 67 项（首次启动、镜像层、管理口令）
python3 test_skeleton.py        # 32 项
python3 demo.py                 # 几条命令的真实输出，给人看
python3 gen_spec.py             # 重新生成 ../portable-agent-os-cli.md
```

三套测试需要 PyYAML（命令本身也要）。

## 四条读代码时要注意的边界

**一、`doctor` 会回答 `unknown`，那不是通过。**
在开发机上大部分检查无法判定——没有 `/proc`、没有目标镜像。`unknown` 意思是"这个环境里判不了"，
而**自检一旦开始猜，它就变成了安慰剂**。真正的验收在目标镜像上跑。

**二、`mkosi.conf` 的键名没有被验证过。**
mkosi 在 14→19 之间几乎重写了整套配置键名，而写这份文件时本机没有 mkosi。
**构成**（装哪些包、放哪些文件、启哪些服务、产出哪些格式）可信，**键名**要对着那个版本文档核一遍。

**三、`MINIOS_ALLOW_PLAINTEXT_KEYS=1` 与 `MINIOS_UNSAFE_ALLOW_ADMIN=1` 只给开发用。**
前者让密钥库退化成明文（`doctor` 的 E2b 会因此报 fail），后者跳过 root 门槛。
两个都必须显式设置才生效——默认不放行，也默认不留后门。

**四、路径分隔符。** 策略规则写作 `**/*.pem`（用 `/`），而 `os.path.realpath` 在 Windows 上返回 `\`。
两者直接比较的后果是**规则全部落空**：不报错、不警告，deny 列表只是不再 deny。
修法是在**比较点归一化**（`minioscore.norm()`），而不是在写盘时改路径——
这样同一套逻辑在 Windows 上也测得动，否则测试永远只能覆盖 Linux 那一半。
