# B13 · 日志、rosbag2 入门与最小集成测试

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

现场机器人「昨天好好的今天撞了」——没有**时间戳对齐的日志**与**传感器录制**，复盘只能靠猜。**ROS 2 Logger** 分级输出；**rosbag2** 按话题录制**类型化消息**，回放时驱动算法复现，是最低成本的「**飞行记录仪**」。再配合 **`launch_testing`** 做**自动化冒烟**，基础篇收束到**可验证**闭环。

### 痛点放大

1. **print 调试**：无法与节点名、文件行号对齐。
2. **只录图像不落盘标定**：无法重放。
3. **手测回归**：人力不可伸缩。

**本章目标**：配置 **logger 级别**；录制/回放 bag；给出 **`launch_test`** 最小骨架思路。

---

## 2 项目设计

### 剧本对话

**小胖**：日志不就 Python `logging` 吗？

**小白**：`RCLCPP_INFO` 和 `get_logger().info` 差别？能 JSON 吗？

**大师**：ROS logger 统一了**节点上下文**、**节流**与**远程订阅**（可用 `rqt_console`）。导出 JSON 需自建 sink 或接 **ros2_tracing**（中级篇）。

**技术映射**：**rcutils logging** + **Logger name = 节点层级**。

---

**小胖**：bag 文件会不会把硬盘塞爆？

**大师**：按话题选择、**压缩**、**分割文件**、**采样**；只录问题窗口（事件触发录制）。

---

## 3 项目实战

### 环境准备

```bash
sudo apt install ros-humble-rosbag2 ros-humble-launch-testing ros-humble-launch-testing-ament-cmake
```

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

### 测试验证

- 录制→删 talker→回放→listener 仍能收到（若时间戳/sim time 配置允许）。

---

## 4 项目总结

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
