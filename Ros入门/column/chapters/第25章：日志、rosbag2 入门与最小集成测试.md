# 第25章：日志、rosbag2 入门与最小集成测试

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

> **版本**：ROS 2 Humble（Ubuntu 22.04，统一环境见 [ENV.md](../ENV.md)）
> **定位**：基础篇 · 面向新人开发与测试，强调最小可运行闭环、CLI 观察与概念落地。
> **前置阅读**：建议按章节顺序阅读；若跳读，请先完成 ENV.md 中的环境准备。
> **预计阅读**：35 分钟 | 实战耗时：45–90 分钟

## 1. 项目背景

### 业务场景

现场机器人「昨天好好的今天撞了」——没有**时间戳对齐的日志**与**传感器录制**，复盘只能靠猜。**ROS 2 Logger** 分级输出；**rosbag2** 按话题录制**类型化消息**，回放时驱动算法复现，是最低成本的「**飞行记录仪**」。再配合 **`launch_testing`** 做**自动化冒烟**，基础篇收束到**可验证**闭环。

### 痛点放大

1. **print 调试**：无法与节点名、文件行号对齐。
2. **只录图像不落盘标定**：无法重放。
3. **手测回归**：人力不可伸缩。

**本章目标**：配置 **logger 级别**；录制/回放 bag；给出 **`launch_test`** 最小骨架思路。

---

### 业务指标与交付边界

本章不追求“把所有概念一次讲完”，而是交付一个可复现的工程切片：

1. **可运行**：至少有一组命令、脚本或配置能够在 Humble 环境中执行。
2. **可观察**：运行后能用 `ros2` CLI、日志、RViz、rosbag2 或系统工具看到明确现象。
3. **可交接**：读者能把 **日志、rosbag2 入门与最小集成测试** 的关键假设、输入输出、失败模式写进项目 README 或排障手册。

**本章交付目标**：完成一个围绕 **日志、rosbag2 入门与最小集成测试** 的最小闭环，并留下可复盘的命令、截图或日志证据。

## 2. 项目设计

### 总体架构图

```mermaid
flowchart LR
  requirement[业务需求] --> concept["日志、rosbag2 入门与最小集成测试"]
  concept --> config[配置与代码]
  config --> runtime[运行时观测]
  runtime --> verify[测试验证]
  verify --> runbook[交付与复盘]
```

这张图用于对齐 `example.md` 的“端到端项目链路”写法：先从业务需求出发，再落到配置/代码，最后用观测与验收把结论闭环。

### 剧本对话

**小胖**：日志不就 Python `logging` 吗？

**小白**：`RCLCPP_INFO` 和 `get_logger().info` 差别？能 JSON 吗？

**大师**：ROS logger 统一了**节点上下文**、**节流**与**远程订阅**（可用 `rqt_console`）。导出 JSON 需自建 sink 或接 **ros2_tracing**（中级篇）。

**技术映射 #1**：**rcutils logging** + **Logger name = 节点层级**。

---

**小胖**：bag 文件会不会把硬盘塞爆？

**大师**：按话题选择、**压缩**、**分割文件**、**采样**；只录问题窗口（事件触发录制）。

---

## 3. 项目实战

### 环境准备

```bash
sudo apt install ros-humble-rosbag2 ros-humble-launch-testing ros-humble-launch-testing-ament-cmake
```

**项目目录结构**（建议随章落地到自己的工作区）：

```text
ros2_ws/
  src/
    日志_rosbag2_入门与最小集成测试/
      package.xml
      launch/
      config/
      scripts/
      test/
  docs/
    runbook.md      # 记录命令、预期输出、截图或日志
```

说明：若本章以阅读源码、配置或运维演练为主，可以把 `scripts/` 换成 `notes/`，但仍建议保留 `config/` 与 `test/`，方便后续复盘。

### 分步实现

#### 步骤 1：日志

```python
self.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
self.get_logger().debug('fine detail')
```

环境变量：

```bash
export RCUTILS_CONSOLE_OUTPUT_FORMAT="[{severity}] [{name}]: {message}"
```

#### 步骤 2：录制

```bash
ros2 bag record /chatter -o my_bag
```

#### 步骤 3：回放

```bash
ros2 bag play my_bag
```

#### 步骤 4：`launch_test` 概念

在 `CMakeLists.txt`：

```cmake
find_package(launch_testing_ament_cmake REQUIRED)
add_launch_test(test/smoke.launch.py)
```

`smoke.launch.py` 启动 talker 并 assert topic 出现（示例略，依项目）。

### 完整代码清单

- `ros2 bag` CLI + 最小 launch_test 目录。
- 外链待补充。

### 交付物清单

- **README**：说明 **日志、rosbag2 入门与最小集成测试** 的业务背景、运行命令、预期输出与常见失败。
- **配置/代码**：保留本章涉及的 launch、YAML、脚本或源码片段，避免只存截图。
- **证据材料**：至少保留一份终端输出、RViz 截图、rosbag2 片段、trace 或日志摘录。
- **复盘记录**：记录“为什么这样配置”，尤其是 QoS、RMW、TF、namespace、安全和性能相关取舍。

### 测试验证

- 录制→删 talker→回放→listener 仍能收到（若时间戳/sim time 配置允许）。

### 验收清单

- [ ] 能在干净终端重新 `source /opt/ros/humble/setup.bash` 后复现本章命令。
- [ ] 能指出 **日志、rosbag2 入门与最小集成测试** 的核心输入、输出、关键参数与失败边界。
- [ ] 能把至少一条失败案例写成“现象 → 排查命令 → 根因 → 修复”的四段式记录。
- [ ] 能说明本章内容与相邻章节的依赖关系，避免把单点技巧误当成系统方案。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 复现 | bag 黄金标准 | 体积 |
| 日志 | 统一接口 | 高级聚合另需方案 |
| 自动化 | launch_test 可 CI | 编写成本 |

### 适用场景

- 故障分析、回归测试、数据集构建。

### 不适用场景

- 秘密环境不可导出数据：需脱敏或现场只读。

### 常见踩坑经验

1. **Sim time `/clock` 未同步**导致回放错位。
2. **QoS 与录时不同**导致订阅失败。
3. **类型变更** bag 与代码不一致。

### 注意事项

- **版本兼容**：所有命令以 Humble 与 [ENV.md](../ENV.md) 为基线，其他发行版需查 `--help` 与官方文档。
- **配置边界**：不要把实验参数直接带入生产；先记录硬件、RMW、QoS、网络与时钟条件。
- **安全边界**：涉及远程调试、容器权限、证书或硬件接口时，先按最小权限原则收敛。

### 思考题

1. 为何 bag 回放常用于 **CI** 仍具挑战？
2. 日志级别 **DEBUG** 在生产环境默认打开的风险？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#b13)；DDS 域见 [M01](第26章：DDS 发现、域（Domain）与跨机通信.md)。

### 推广计划提示

- **开发**：关键算法提交必须附**最小复现 bag**（可截断）。
- **测试**：夜间 job 回放黄金 bag 做 KPI。
- **运维**：磁盘配额与 **日志轮转**。

---

**导航**：[上一章：B12](第24章：Action-长时间任务与可取消.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M01](第26章：DDS 发现、域（Domain）与跨机通信.md)

> **本章完**。你已经完成 **日志、rosbag2 入门与最小集成测试** 的端到端学习：从业务场景、设计对话、实战命令到验收清单。下一步建议把本章交付物纳入自己的 ROS 2 工作区，并在后续章节中持续复用同一套 README、配置和测试记录方式。
