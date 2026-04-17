# M08 · rosbag2 进阶：录制策略与回放测试

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

CI 需要 nightly **回放黄金场景 bag** 复算 KPI；磁盘却不够。**存储格式**（sqlite3 / mcap）、**压缩**、**按 QoS 录制**以及 **play 速率**共同决定 pipeline。

### 痛点放大

1. **回放与线控时间不同步**。
2. **类型升级**旧 bag 不能播。
3. **选择性录制**遗漏关键 topic。

**本章目标**：`ros2 bag record` 的高级参数；**mcap**；**play --clock** 与 **`use_sim_time`**。

---

## 2 项目设计

### 剧本对话

**小胖**：bag 不就是**录像**吗？我硬盘大，全录不行吗？

**小白**：**QoS 不一致**导致回放和设计时不一致，是谁的锅？

**大师**：**rosbag2** 的价值是 **time-scrubbable 的可复现输入**，不是冷备。**全 topic 录制**在带宽爆炸时会把 **存储、I/O、时钟** 一起拖垮——要 **按场景筛选** + **压缩格式**（如 **mcap**）+ **split**。回放时要关心 **`use_sim_time` / `/clock`** 与 **录制时 QoS** 是否对齐，否则「算法昨天好好的」其实是**输入分布变了**。

**技术映射**：**Bag** = **可版本化测试向量**；**元数据**（版本、分支、参数哈希）和 bag 一样重要。

---

**小胖**：**CI 回放**为啥老是 flaky？

**大师**：常见是 **`wall time` 假设**、**非确定性线程**、**外部服务依赖**。要把回放测试设计成：**固定随机种子**（若 applicable）、**确定性的仿真配置**、对 **SLO** 设合理容差。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，`source /opt/ros/humble/setup.bash`。

本章额外依赖：**`ros-humble-rosbag2`**（通常随 desktop）；**mcap** 存储需 **`ros2 bag record -s mcap`** 可用（发行版说明为准）。磁盘预留 **≥ 录制预估 × 2**。

### 分步实现

#### 步骤 1：准备 QoS 覆盖文件

- **目标**：**回放与线上一致**，避免 **能 record 不能 play**（[M02](第27章：QoS 深度-history、deadline、durability.md)）。
- **命令**：编写 **`qos.yaml`**（格式见 `ros2 bag record -h` / 官方文档），对齐 **`/scan`、`/odom`** 等 **Reliability/Durability**。
- **预期输出**：`ros2 bag info` 显示 **存储格式** 与 **话题列表** 正确。
- **坑与解法**：**默认 QoS 与线人不符** → **无数据**；以 **`ros2 topic info -v`** 线下为准。

#### 步骤 2：mcap 录制

- **目标**：高吞吐场景用 **mcap** 降低开销（与 **sqlite3** 对比见思考题）。
- **命令**：

```bash
ros2 bag record -s mcap --qos-profile-overrides-path qos.yaml /scan /odom
```

- **预期输出**：生成 **`.mcap`**；`ros2 bag info` 可读。
- **坑与解法**：**磁盘满**、**权限**、**话题名写错** —— 先 **`ros2 topic list`** 再录。

#### 步骤 3：受控回放

- **目标**：**可复现**调试（**降速**、**仿真时钟**）。
- **命令**：

```bash
ros2 bag play my.mcap --rate 0.5 --clock 100
```

（参数以 **`ros2 bag play -h`** 为准。）

- **预期输出**：订阅端 **`use_sim_time:=true`** 时 **时间连续**；算法行为与线速对照可解释。
- **坑与解法**：**混用 sim time 与 wall time** → **TF / message filter** 错乱（[B10](第22章：Launch-XML-Python 与参数替换.md)）。

### 完整代码清单

- **`qos.yaml`**（占位）：与线上一致的 **profile overrides**。
- **脚本** `record_nav_debug.sh`：**话题列表** + **输出目录**。
- Git 占位：**大文件 `.mcap` 勿提交**；**`.gitignore`** 说明。

### 测试验证

- **录制后** `ros2 bag info` **话题类型/条数**合理；**回放** 时 **`ros2 topic hz`** 符合 **`--rate`**。
- **手工验收**：同一 bag **两台机** 回放 **Nav2/感知** 结论可复现（与 CI 断言思路衔接）。

---

## 4 项目总结

### 思考题

1. **mcap** 相对 sqlite 的收益？
2. 如何在 CI 断言 **导航成功**？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m08)；性能 [M09](第34章：性能与带宽-topic hz-bw、系统剖析入门.md)。

---

**导航**：[上一章：M07](第32章：pluginlib-算法可替换.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M09](第34章：性能与带宽-topic hz-bw、系统剖析入门.md)
