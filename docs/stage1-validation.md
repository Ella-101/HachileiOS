# 阶段一进度与验证记录

## 当前进度（2026-09-30）

阶段一的实现和本地验收已完成，本次交付包含测试程序、宿主执行入口、
Linux 单核/三核 CI、失败日志及文档。README 的当前进度、测试命令和后续
路线已同步；本批不包含信号、终端作业控制和用户权限。

| 交付项 | 状态 |
| --- | --- |
| 6 个专用测试及退出状态校验 | 已完成 |
| 多核调度竞争与组合压力测试 | 已完成 |
| 超时、错误路径回归、日志与 JSON 汇总 | 已完成 |
| Linux 1/3 核 CI、macOS 构建及任务超时 | 已配置 |
| 本地连续验收、完整回归与崩溃恢复 | 全部通过，详见下表 |
| README 与路线图同步 | 已完成 |

代码交付目标为 `Miusdy/HachileiOS` 的 `main` 分支（远端 `hachi`）；远端
CI 结果见 [GitHub Actions](https://github.com/Miusdy/HachileiOS/actions/workflows/test.yml)，
本文的通过记录均为本地验收结果。

## 验证接口

本批不改变系统调用或磁盘格式。`testrun PROGRAM [ARG...]` fork/exec 一个
测试并 wait，输出 `TEST RESULT PROGRAM STATUS`；exec/wait 失败和子进程
非零退出均失败。宿主还要求程序自己的 OK 行，拒绝 panic、FAIL、MISMATCH。
结果标记必须在本次命令之后出现，并等到 Shell 返回；超时和 QEMU 提前退出
直接失败，不重试。每项专用测试使用新镜像，崩溃恢复重启复用被测镜像；
不要并行运行共享同一工作目录的测试。

`--cpus` 显式传给 make；`--repeat` 是连续验收次数，任意失败即停止。
每个 QEMU 会话保存串口及启动参数、来宾命令、CPU 数和 Git 提交/脏状态；
每次宿主运行另存 JSON 汇总。异常退出在 finally 中终止 make/QEMU 进程组。

调度测试单核使用一对竞争者，多核使用每核一对（工作进程多于核心数），
通过管道屏障准备好所有工作进程，以同一个 uptime 截止时间竞争。
比较两组总 CPU 时间，每个低优先级进程必须获得时间；不要求固定倍率。
统计测试保留 tick 容差，不使用宿主运行速度作为断言。

mixstress 每轮并发运行 6 个工作进程，各自反复 fork 子进程，通过超过管道
容量的数据传输触发阻塞和 copyout/COW，写入并读回独立文件，然后 unlink。
父进程检查 COW 原始内容、子进程状态；最外层回收全部工作进程并验证文件系统
块/inode 回到基线。每轮文件名互不冲突，结束前 sync；任何资源获取失败
立即报告失败，由宿主关闭整个虚拟机，避免错误路径后台进程影响下一用例。


## 本地验收记录（2026-09-30）

基线 `31342e4` 加本批工作区修改；RISC-V GCC 14.2.0、QEMU 10.2.1。

| 验证项 | 结果 |
| --- | --- |
| `python3 test-harness.py` | 19 项通过，含真实宿主进程的失败/挂起注入 |
| `./test-xv6.py dedicated --cpus 1 --repeat 10` | 60/60 通过，无失败重试 |
| `./test-xv6.py dedicated --cpus 3 --repeat 10` | 60/60 通过，无失败重试 |
| `./test-xv6.py usertests --cpus 1` | 完整套件通过，347.225 秒 |
| `./test-xv6.py usertests --cpus 3` | 完整套件通过，347.529 秒 |
| `./test-xv6.py crash --cpus 1` | 日志、孤儿文件、孤儿目录恢复均通过 |
| `./test-xv6.py crash --cpus 3` | 日志、孤儿文件、孤儿目录恢复均通过 |
| 真实 QEMU 错误路径 | `testrun missingtest` 返回 127 被判失败；`testrun cat` 阻塞被判超时；两次退出后进程组均不存在 |
| 构建与静态检查 | `-Wall -Werror` 构建、clang-format 21.1.8 全树检查、`git diff --check` 通过 |

宿主错误路径测试同时覆盖“OK 后非零退出”、缺少 OK、缺少 Shell 返回、
过期成功行、panic/FAIL/MISMATCH、QEMU 提前退出、失败日志和汇总、首次失败
停止重复运行。`cputest`、`waitxtest` 现在以非零状态报告失败；`kmemtest`
检查每次子进程的回收结果。

验收摘要保存在本地 `test-results/`（已由 `.gitignore` 排除，不随代码提交；
CI 的串口日志和汇总由工作流上传为构建产物）：

- 单核专用：`1790772246245209651-summary.json`
- 三核专用：`1790772507602385513-summary.json`
- 单核完整：`1790772742831213681-summary.json`
- 三核完整：`1790773090093774861-summary.json`
- 单核恢复：`1790773462536992656-summary.json`
- 三核恢复：`1790773465297687454-summary.json`
- 真实 QEMU 错误路径：`injection/results.json`，同目录保留串口和命令元数据。

最初在受限沙箱中执行恢复测试时，本地 QMP 套接字被禁止创建，QEMU 未能启动。
该环境失败保存在 `1790773437659300196-summary.json`；获得权限后重新执行
两种配置的恢复测试并通过。没有把该环境失败删除或当作测试通过。

Linux CI 已配置单核/三核矩阵、30 分钟任务超时和 always 上传日志；macOS
保持构建验证并设置 15 分钟上限。推送会触发远端工作流；其结果需单独核对，
不能由本地验收推断。信号、终端作业控制和用户权限仍属后续批次。
