# A06 · 自定义 Action / 复杂状态机与容错

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

**复合任务**：「巡检 → 抓取 → 回充」若硬编码 Service 链，**失败恢复**难维护。用 **行为树 + Action** 或 **SMACC2** 状态机，把**长事务**拆成可组合单元，配合 **cancel/preempt** 语义。

### 痛点放大

1. **多处 Future 回调地狱**。
2. **重复 goal** 未去重。
3. **状态落盘**审计缺失。

**本章目标**：设计 **`DockRobot.action`**（自定）字段含 **failure_code**；Server 侧 **thread-safe goal handle** 样板描述。

---

## 2 项目设计

### 剧本对话

**小胖**：我用 **一串 Service** 也能拼「巡检→抓取」啊，为啥折腾 **Action + 行为树**？

**小白**：**cancel** 点了没反应算谁的？客户盯着进度条，我们只能 `rostopic echo` 看有没有 feedback 吗？

**大师**：**Service** 适合**原子事务**；**长事务**需要 **goal id、反馈序列、抢占/cancel、terminal state**，这正是 **Action** 协议关心的。**把 Action 当 RPC** 会低估它：好的实现会把 **execute** 写成**可中断的阶段机**，并在 **result** 里给 **业务错误码**。**行为树 / SMACC2** 解决的是「**组合爆炸**」——哪一步失败回到哪一步重试、哪一步不可恢复。

**技术映射**：**Action** = **有状态 IPC**；**BT** = **组合与恢复策略的可视化载体**。

---

**小胖**：**多个 goal** 同时塞进来，Server 单线程咋办？

**大师**：要定义 **并行策略**：**拒绝**、**排队**、**抢占**——并在文档写清。**`rclcpp_action` 的 goal handle** 需要**线程安全**；若 execute 里再 **同步 call 自己的 service**，又会和 **B03** 的**死锁**面相遇。

**技术映射**：**Goal 生命周期** ∈ **并发模型** 的一部分。

---

**大师**：审计需求强时，把**关键阶段**记 **rosbag** + **结构化日志**（带 **goal_id**），比在回调里 `print` 更像产品线。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致。

```bash
source /opt/ros/humble/setup.bash
sudo apt install -y ros-humble-action-tutorials-py ros-humble-action-tutorials-cpp
```

工作空间可选；本章基于 **`action_tutorials`** 官方包。

### 分步实现

#### 步骤 1：跑通默认 Fibonacci Server / Client

- **目标**：确认 **Action 管线**无改装时行为正常。
- **命令**：

```bash
# 终端 A
source /opt/ros/humble/setup.bash
ros2 run action_tutorials_py fibonacci_action_server

# 终端 B
source /opt/ros/humble/setup.bash
ros2 run action_tutorials_py fibonacci_action_client
```

- **预期输出**：Client 打印 **sequence/final result**；Server 打印 **goal 处理**日志。
- **坑与解法**：**无响应** → **域不一致** / **包未装**。

#### 步骤 2：观察 Action 图

- **目标**：熟悉 **CLI** 与 **goal 名**。
- **命令**：

```bash
ros2 action list
ros2 action info /fibonacci
```

- **预期输出**：`/fibonacci` 存在，类型 **`action_tutorials/action/Fibonacci`**（以 `ros2 interface show` 为准）。
- **坑与解法**：**Server 未起** 时 list 仍可能有残留信息 —— 以 **实际 server 跑否**为准。

#### 步骤 3：故意延迟 execute + 中途 cancel

- **目标**：复现 **长事务**与 **取消**语义（对应剧本）。
- **操作**：
  1. 在 **clone 到本地的** `fibonacci_action_server.py`（或自建包）**execute 内 `time.sleep(5)`**（**仅实验环境**）。
  2. **`colcon build`** 后启动 Server；Client 发起 goal；
  3. **数秒内**在 Client 终端 **Ctrl+C** 或按教程调用 **cancel API**。
- **预期输出**：Server 侧进入 **cancel 处理分支**（依实现打印 **canceled**）；Client **不再 block**。
- **坑与解法**：**死锁**常来自 **在 execute 里同步 call 自己的 service**（[B03](第15章：节点与执行器-回调与单线程-多线程.md)）。

#### 步骤 4：并发 Client（可选）

- **目标**：观察 **多 goal** 行为（拒收/排队/抢占 —— 依你的 Server 实现）。
- **操作**：开 **三终端** 各跑 **`fibonacci_action_client`**，记录 **日志顺序**。
- **预期输出**：至少能回答「**第二个 goal 怎么样了**」。
- **坑与解法**：若未定义策略，行为**未定义** —— 倒逼产品设计。

### 完整代码清单

- **上游**：**action_tutorials**（apt 安装路径 `/opt/ros/humble/share/action_tutorials`）。
- **魔改版**：建议 **`~/ros2_ws/src/action_tutorials_fork`** 自行维护 diff。
- Git 外链：**待补充**。

### 测试验证

- **验收**：修改后仍能 **`colcon test`**（若包内带 test）或 **手工 checklist**：**send → feedback → result | canceled**。
- **埋点**：在 **feedback** 中打 **`goal_id`** 日志，便于与 **rosbag** 对齐（[B13](第25章：日志、rosbag2 入门与最小集成测试.md)）。

---

## 4 项目总结

### 思考题

1. **BT** 与 **SMACC2** 选型维度？
2. **幂等 goal id** 如何实现？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a06)；交叉编译 [A07](第07章：交叉编译与嵌入式部署（Yocto-板级）.md)。

---

**导航**：[上一章：A05](第05章：极端网络-延迟、抖动、丢包下的 QoS 组合.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A07](第07章：交叉编译与嵌入式部署（Yocto-板级）.md)
