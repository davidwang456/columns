# A05 · 极端网络：延迟、抖动、丢包下的 QoS 组合

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

**车间 Wi‑Fi** 丢包 3%，遥控指令仍能工作吗？需要 **BEST_EFFORT + 应用层重试**、或 **可靠 + 限频**；**tf** 与 **sensor_data** profile 协同。本章给出**实验矩阵**：`tc netem` 人为损伤网络，观察 **Nav2** 行为。

### 痛点放大

1. **默认 RELIABLE** 造成**队头阻塞**。
2. **跨 AP 漫游** IP 不变但 DDS 可能重发现耗时。

**本章目标**：Linux **`tc qdisc netem`** 命令模板；记录 **ros2 topic delay**（若有）或自定义 timestamp skew 脚本思路。

---

## 2 项目设计

### 剧本对话

**小胖**：弱网下我直接把 **QoS 全改 RELIABLE**，不是更不容易丢包吗？

**小白**：Nav2 开始**卡顿螺旋**，是不是 **global planner** 坏了？还是网络？

**大师**：**RELIABLE** 在拥塞链路可能放大**队头阻塞与重传延迟**——对你以为是「导航傻」，其实是**控制回路看到的观测更陈旧**。对策常是**分层**：**高频传感**允许 **BEST_EFFORT + 应用层滤波**；**关键指令**小载荷 **RELIABLE + 超时重发**；**状态估计**侧对丢包做**界内预测**而不是无限等完整队列。先 **`tc netem`** 量化 **RTT 与丢包率**，再调 QoS，别反着来。

**技术映射**：**QoS** 与 **控制稳定性**耦合；需**系统辨识式**调参。

---

**小胖**：**多播**和 **AP 漫游**到底怎么坑 DDS？

**大师**：漫游时可能只是 **DDS 参与者重新匹配** 的几百毫秒级「空窗」，对 **TF 与传感器**却是可见的 glitch。要从 **发现与 locator** 层理解，而不是只怪 **Wi‑Fi 信号格**。有时需要 **unicast discovery server** 或 **固定网段部署**（**M01**）。

**技术映射**：**移动网络** = **链路层间歇** + **发现层重组成本**。

---

**大师**：做一张 **实验矩阵**：`delay × loss × rate` × **`Reliability` 档位**，记录 **Nav2 失败模式**（震荡、重规划风暴、原地转），比改十个 yaml 更有价值。

**技术映射**：**经验表** > **体感调参**。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；需 **root** 权限操作 **`tc netem`**。准备 **两张网卡** 或 **虚拟机** 做对比更安全：**切勿对 SSH 唯一链路盲目加延迟**导致断连。

先查看接口名：

```bash
ip -br link
```

下文以 **`eth0`** 为例，请替换为你的**实际接口**。

### 分步实现

#### 步骤 1：基线 — 无损网络下的 topic 统计

- **目标**：记录 **无 netem** 时的 **hz / 延迟体感**。
- **命令**（**Talker/Listener** 或 Nav2 场景任选其一）：

```bash
source /opt/ros/humble/setup.bash
ros2 run demo_nodes_cpp talker &
ros2 topic hz /chatter
# Ctrl+C 停 hz；再 kill talker
```

- **预期输出**：**Hz** 接近 demo 设计值；抖动小。
- **坑与解法**：多终端记得同一 **`ROS_DOMAIN_ID`**。

#### 步骤 2：注入延迟 + 丢包

- **目标**：人为创造 **A05 背景**中的弱网，可复现 **队头阻塞** 类现象。
- **命令**（**需 root**）：

```bash
sudo tc qdisc add dev eth0 root netem delay 100ms 20ms loss 5%
```

- **预期输出**：再上 **步骤 1**，**`hz`** 统计方差增大；**RELIABLE** 话题可能出现 **延迟飙升**。
- **坑与解法**：**SSH 卡死** → 用 **带外 console** 或 **`del`** 规则；先在 **VM 二网卡实验**。

#### 步骤 3：对比 QoS — 同 Topic 不同 profile（概念）

- **目标**：体会 **RELIABLE vs BEST_EFFORT** 在损伤网络下的差异（与 [M02](第27章：QoS 深度-history、deadline、durability.md) 联动）。
- **操作**：用 [B05](第17章：QoS 入门-可靠与尽力而为.md) 中 **pub/sub**，一端 **RELIABLE**、一端 **BEST_EFFORT** 先观察 **`ros2 topic info -v` 兼容性**；再在 **netem 开/关** 下各录一段 **`echo` 延迟**。
- **预期输出**：**不兼容**时可能无数据；**兼容**时 **RELIABLE** 在 **高丢包**下延迟尾部更肥。
- **坑与解法**：只改一端 QoS 不够 —— 成对设计。

#### 步骤 4：拆除规则

- **目标**：避免忘记 **qdisc** 影响后续实验。

```bash
sudo tc qdisc del dev eth0 root
```

- **预期输出**：`tc qdisc show dev eth0` 无 **netem**。
- **坑与解法**：若提示 **RTNETLINK**，可加 **`sudo tc qdisc show`** 查是否已删。

### 完整代码清单

- **系统工具**：`iproute2`（**`tc`**）。
- **ROS**：与 [B04](第16章：话题与消息-发布订阅第一印象.md)/[B05](第17章：QoS 入门-可靠与尽力而为.md) 示例包共用即可。
- **无独立 Git 仓库要求**。

### 测试验证

- **记录表**：`delay(ms)` × **`loss(%)`** × **`RMW`**（[A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md)）× **Reliability**，每种组合打勾 **「可运行 / 延迟可接受」**。
- **安全**：GameDay 仅在 **测试网络**执行；生产车辆需 **变更审批**。

---

## 4 项目总结

### 思考题

1. **DDS reliability + netem 高丢包**会怎样？
2. **双链路冗余**在 ROS 层的常见 pattern？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a05)；Action [A06](第06章：自定义 Action-复杂状态机与容错.md)。

---

**导航**：[上一章：A04](第04章：ros2_control 与硬件接口分层.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A06](第06章：自定义 Action-复杂状态机与容错.md)
