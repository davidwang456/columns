# A01 · rcl/rclcpp 执行模型与源码导读

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

当 **Nav2** 表现异常，文档与参数都排查完，需要下到 **rcl/rclcpp** 层：理解 **`Executor::spin` 如何拉取 wait set**、`rcl_take` 如何与 **DDS loaned message** 交互。资深开发能读源码定位「**为何此处阻塞**」。

### 痛点放大

1. **只会在应用层打日志**，看不到 **rcl 层返回码**。
2. **误用 MultithreadedExecutor** 导致 UB。
3. **银河麒麟/定制内核**上 `rmw` 行为差异。

**本章目标**：本地 clone **rclcpp**，走读 **`executors/`** 与 **`node.cpp`**；结合 **B03** 建立深度认知。

---

## 2 项目设计

### 剧本对话

**小胖**：都说要看 **rclcpp** 源码，我一打开全是 `Executor`，跟咱们 `spin()` 到底啥关系？

**小白**：我关心的是：**回调为啥饿死**？还有 **`spin_once` 和 `spin` 在源码里差哪一行？

**大师**：可以这么读分层：**最底下 `rmw`** 向 **DDS** 要样本；**`rcl`** 把 **rcl 节点/waitset/计时器** 包成 C API；**`rclcpp::Executor`** 才是「**把哪些 waitable 在Which 线程里执行**」的策略。`spin()` 本质是**循环**：`executor.spin()` → **collect entities** → **wait_for_work（rmw_wait）** → **取出就绪的 executable** → **执行回调**。所谓「饿死」，往往是**互斥回调组里长回调占坑**，或 **wait 策略/timeout** 配置不当——先别急着怪 DDS。

**技术映射**：**Executor** = **Scheduling**；**Callback Group** = **可重入/互斥约束**。

---

**小胖**：那 `Waitable` 是个啥？我搜代码老看见 `add_waitable`。

**大师**：不仅是 sub/timer，**service/action** 也可被抽象为**可被 wait 的对象**。Executor 维护 **waitset**，底层 **`rmw_wait`** 阻塞到**至少一类就绪**或超时。读 `executors/*` 时重点看：**如何把 rclcpp::Waitable 映射成「可执行单元」**，以及 **MultiThreadedExecutor** 里**线程池与回调组**如何相交。

**技术映射**：**Waitable** = **统一的就绪源抽象**；**SingleThreadedExecutor** = **串行 drain**。

---

**小白**：**Loaned message** 和 executor 有关系吗？还是只和 **Publisher** 有关？

**大师**：**loan** 的归还在 **发布路径**更耀眼；但**回调侧**若处理极慢，一样会**反压**到 **DDS 队列/QoS**。**Executor 只做调度，不替你降算法复杂度**。若你要追**端到端延迟**，应 **tracing** 同时打 **take 时刻 vs 回调结束时刻**（**M10**）。

**技术映射**：**调度层（rclcpp）** 与 **传输层（rmw/DDS）** 责任边界要分清。

---

**大师**：建议阅读顺序：**`rmw_implementation` 接口** → **`rcl` waitset 语义** → **`rclcpp::Executor::spin_some/ spin`** → **具体 `Node::create_*` 注册的 waitable**。对比 **Humble** 与 **Rolling** 的 PR，能看到 **executor 公平性**的演进——适合作为「资深向」个案追踪。

**技术映射**：**读源码 = 建立可证伪的心智模型**，而不是背类名。

---

### 剧本对话（第二轮：排障与边界）

**小胖**：我 **`gdb` 断在 `rmw_wait`**，线程全卡住——这是 **ROS 的 bug** 吧？

**小白**：**`spin_some(0)`** 和死循环 **`spin()`** 对**别的节点**有啥副作用？单测里我写哪个？

**大师**：`rmw_wait` **无就绪 + 无限等**时看起来像「全挂」，多半是**没有唤醒源**或**shutdown 未触发 guard condition**。先确认 **executor 是否加进了 Node**、`timer` 是否还在跑、**rclcpp 是否收到退出信号**。**`spin_some`** 让出时间片，适合**嵌套在自家循环里**做「**合作式调度**」；**`spin()`** 占用线程直到上下文销毁。单测里常 **`spin_some` + 断言** mock 消息，全线程 **`spin`** 容易和 **gtest 主线程**争。

**技术映射**：**`Executor::cancel()` / guard_condition** = **从阻塞中唤醒 wait**；**spin_some** = **可控步进的仿真/测试友好**。

---

**小胖**：**Timers** 和 **Subscription** 谁更「优先」被执行？我总怀疑定时器饿死 topic。

**大师**：在 **SingleThreadedExecutor** 里本质是**就绪队列/顺序**问题，不是「硬优先级调度」。若 **timer 回调极长**，后面的 **take 数据**确实可能**堆积或丢**（还受 **QoS** 约束，见 **M02**）。多线程模型下又引入**锁与组**——读 **`add_callback_group` 与 executor 的绑定**那段实现，比记口诀可靠。

**技术映射**：**就绪顺序 ≠ Linux SCHED_FIFO**；是 **executor 内部队列策略**。

---

**小白**：从 **`Subscription::callback`** 往下追，**哪一行真正 `take` 了 DDS 样本**？

**大师**：路径大致是：**回调被触发** ← **`AnyExecutable` 取出** ← **`execute_subscription` / timer**；更底层在 **`rcl_take` / `loan_maybe`**（与 **A03** 衔接）。建议在 IDE 里对 **`rcl_take`** 打断点，看 **返回码**（**timeout / no data / bad alloc**），比 printk 管用。

**技术映射**：**`RCL_RET_*`** = **rcl 层可枚举故障面**；应对照 **rmw 文档**解释。

---

**大师**：若在 **银河麒麟/定制内核**上行为异常，**交叉比对同一套例程在 Ubuntu 官方镜像** ——先分离 **内核/GLIBC** 与 **ROS 本身**。再记录 **`rclcpp` commit** 与 **发行版补丁**（vendor fork），否则 issue 没法 upstream。

**技术映射**：**可复现 issue 包** = **docker + 最小节点 + `ros2 doctor` 输出**。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，已 `source /opt/ros/humble/setup.bash`。

本章额外依赖：`git`、`build-essential`；建议安装 **`ripgrep`（`rg`）** 或 IDE 全局搜索；可选 **`bear`** 生成 `compile_commands.json` 便于符号跳转。

工作目录任选，例如 `~/study/rclcpp_src`（**只读学习**即可，不必与业务 `ros2_ws` 混编）。

### 分步实现

#### 步骤 1：只读克隆与发行版对齐

- **目标**：获取与 **Humble** 一致的 **`rclcpp`** 树，便于对照本文与官方 changelog。
- **命令**：

```bash
mkdir -p ~/study && cd ~/study
git clone --depth 1 -b humble https://github.com/ros2/rclcpp.git
cd rclcpp && git rev-parse HEAD
```

- **预期输出**：最后一行为 **commit sha**，可写入你的学习笔记/工单「对照版本」。
- **坑与解法**：若需对比 **Rolling**，另建目录 clone，**勿在同一 build 目录混用**。

#### 步骤 2：用搜索建立「入口地图」

- **目标**：快速定位 **Executor / CallbackGroup** 实现文件。
- **命令**：

```bash
cd ~/study/rclcpp
rg -n "void Executor::spin" rclcpp/src 2>/dev/null || rg -n "spin()" rclcpp/src/rclcpp/executor*
rg -n "SingleThreadedExecutor" rclcpp/src
rg -n "MultiThreadedExecutor" rclcpp/src
rg -n "MutuallyExclusiveCallbackGroup" rclcpp/src/rclcpp/callback_group
```

- **预期输出**：列出 **`executors/*.cpp`**、**`callback_group.cpp`** 等待读文件路径。
- **坑与解法**：不同小版本路径可能微调 —— 以 **符号名** 为锚，不背行号。

#### 步骤 3：精读切片与时序草图

- **目标**：把「**spin → wait → execute**」落到函数名上，并手绘一页时序。
- **操作**：在 IDE 中打开（具体文件名以仓库为准）：
  - `rclcpp/src/rclcpp/executors/multi_threaded_executor.cpp`
  - 同目录下 **`single_threaded_executor.cpp`** 或集中 **`executor.cpp`**
  - `rclcpp/src/rclcpp/callback_group.cpp`
- **预期输出**：能口述 **谁发起 `rmw_wait`**、**`AnyExecutable`** 从哪来、**互斥组**如何限制并行。
- **坑与解法**：模板/内联导致跳转困难时，先搜 **`execute_any_executable`**、**`wait_for_work`** 字符串。

#### 步骤 4（可选）：与运行中进程对照

- **目标**：将「卡死」现象与栈帧关联（对应第二轮对话）。
- **终端 A**：`ros2 run demo_nodes_cpp talker`  
- **终端 B**：

```bash
sudo apt install gdb -y   # 若无
gdb -p $(pidof talker)
# (gdb) thread apply all bt
```

- **预期输出**：栈中可见 **`rclcpp::Executor::`** / **`rmw_wait`** 方向帧（**Release** 可能 mangled）。
- **坑与解法**：符号不全时安装 **`dbgsym`** 或接受「只见地址」——学习目的已达可停。

### 完整代码清单

- **上游仓库**：<https://github.com/ros2/rclcpp> ，分支 **`humble`**。
- 本书不附带独立功能包；若自行 **overlay 全量编译 rclcpp**，请参考官方 Developer Guide（耗时长，非本章必需）。

### 测试验证

- **自学验收**：在纸上画出 **`spin` → `wait_for_work` → `execute_*`** 闭环，并能说明 **`CallbackGroup` 互斥** 与 **topic 饿死**关系（详见 [B03](第15章：节点与执行器-回调与单线程-多线程.md)）。
- **可选**：另 clone **rcl** 仓库，`rg "rcl_take"` 粗追 **take** 路径，与 [A03](第03章：零拷贝与 loaned message（能力与边界）.md) 衔接。

---

## 4 项目总结

### 思考题

1. **Waitable** 对象有哪些实现方？
2. **Reentrant group** 在源码层级如何实现互斥排除？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a01)；rmw [A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md)。

---

**导航**：[上一章：M13](第38章：安全与 SROS 2-权限边界（实践向）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md)
