# M09 · 性能与带宽：topic hz/bw、系统剖析入门

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

整车 CPU 80%，需要知道 **哪条 topic 占带宽**、**哪个节点占 CPU**。ROS CLI + **linux perf** + **heaptrack**（按需）组合定位。

### 痛点放大

1. **盲目降分辨率**掩盖算法问题。
2. ** DDS 内建统计不足**需侧信道。

**本章目标**：熟练 `ros2 topic bw/hz/info`；`top -H` 对齐 **进程名**；了解 **component_container** 益处。

---

## 2 项目设计

### 剧本对话

**小胖**：是不是上 **zero-copy** 就完事？我们图像一打开 CPU 就红。

**小白**：**`ros2 topic bw`** 很大，但 **`top` 里进程 CPU 不高**，钱花在哪儿了？

**大师**：先**分层量测**：**网络/DDS** vs **解码** vs **算法**。**bw/hz** 告诉**比特率**；**`perf top`** / **tracing** 告诉**谁在吃周期**。 Zero-copy 只解决**一部分 memcpy**；若瓶颈在 **JPEG 解码 / 畸变矫正 / 深度学习**，换 RMW 也救不了。**盲目降分辨率**可能只是在隐藏**算法问题**。

**技术映射**：**性能工程** = **证据链**；**topic bw** 是输入侧指标之一。

---

**小胖**：**多线程 spin** 之后 CPU 上去延迟没降，是为啥？

**大师**：可能 **锁竞争**、**False sharing**、或 **Python GIL**——要用 **profiler** 证明，而不是再加线程。

---

**大师**：**component / intra-process** 能减少序列化成本，但引入**生命周期耦合**——是**权衡**，不是默认勾选。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，`source /opt/ros/humble/setup.bash`。

本章额外依赖：无硬性 apt；**Linux** 上 `perf` 来自 **`linux-tools-$(uname -r)`**（需 **sudo** 或 **权限配置**）。准备 **有负载的仿真或实机**（**`/points` 类话题** 可选 **PCL 演示**）。

### 分步实现

#### 步骤 1：`ros2 topic` 带宽与频率

- **目标**：建立 **「话题流量」** 基线，区分 **算法慢** vs **传输/序列化慢**。
- **命令**：

```bash
ros2 topic bw /points
ros2 topic hz /joint_states
```

（话题名以现场为准；无 `/points` 可换 **`/scan` 或 `/image`**。）

- **预期输出**：**bw** 给出 **B/s**；**hz** 给出 **均值与标准差**。
- **坑与解法**：**无输出** → 话题 **无发布** 或 **QoS 不匹配**（[M02](第27章：QoS 深度-history、deadline、durability.md)）。

#### 步骤 2：`perf` 热点（可选、需权限）

- **目标**：定位 **CPU 用户态** 热点函数（**C++ 节点**）。
- **命令**：

```bash
perf top -p $(pidof YOUR_NODE_BINARY)
```

（将 **`YOUR_NODE_BINARY`** 换为实际进程名；**pidof** 多进程时慎用。）

- **预期输出**：**符号级** 热点（需 **未 strip** 或 **debug 包**）。
- **坑与解法**：**容器内 perf** 受限 → **host** 上 **pid**；**权限** 不足 → **`kernel.perf_event_paranoid`**（仅实验环境）。

#### 步骤 3：与 **component / intra-process** 对照（概念）

- **目标**：记录 **「换通信方式前」** 的 **bw/hz/CPU%**，为 **架构决策** 留数据（本章正文）。
- **命令**：同一 workload 下对比 **多进程** vs **同进程 composable**（若项目已有）。
- **预期输出**：**intra-process** 可能 **降序列化成本**，但 **耦合** 增加 —— **写入结论一句**。
- **坑与解法**：**只测一次** → 无意义；**三次取中位数**。

### 完整代码清单

- **一页记录表**：话题名、**bw/hz**、**perf 截图**、**RMW** 版本、**机器型号**。
- **外链**：Linux **`perf`** 文档、ROS 2 **intra-process** 设计说明。
- Git 占位：**待补充**。

### 测试验证

- **手工验收**：**瓶颈** 在 **CPU / GPU / 网络 / 磁盘** 四类中可归因 **至少一类**（与 [M10](第35章：可观测性-tracing、诊断话题与仪表盘.md) 分层一致）。
- **回归**：优化后 **同一 bag 回放**（[M08](第33章：rosbag2 进阶-录制策略与回放测试.md)）**hz** 或 **延迟** 有 **可量化** 变化。

---

## 4 项目总结

### 思考题

1. **CPU profiler** 与 **ROS tracing** 分别回答什么问题？
2. 何时考虑 **intra-process communication**？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m09)；可观测性 [M10](第35章：可观测性-tracing、诊断话题与仪表盘.md)。

---

**导航**：[上一章：M08](第33章：rosbag2 进阶-录制策略与回放测试.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M10](第35章：可观测性-tracing、诊断话题与仪表盘.md)
