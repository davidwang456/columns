# M10 · 可观测性：tracing、诊断话题与仪表盘

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

线上「**偶发卡顿 200 ms**」无法靠 print 复现：**ros2_tracing** / **LTTng** 抓取 **rcl 层事件**；**diagnostic_updater** 把传感器健康发布到 **`/diagnostics`**，`rqt_robot_monitor` 红绿展示。

### 痛点放大

1. **只看平均值**忽略长尾延迟。
2. **无统一健康语义**：每队自定 JSON。
3. **tracing 开销**未评估就开全局 trace。

**本章目标**：列出 **ros2_tracing** 基本使用；**diagnostic_msgs** 结构；Grafana 外接（概念）。

---

## 2 项目设计

### 剧本对话

**小胖**：`INFO` 日志已经刷屏了，还要 **tracing** 干啥？

**小白**：**diagnostics** 一堆 **WARN**，客户问「影不影响走货」，我们咋回答？

**大师**：**日志**擅长叙事，不擅长**统计分布**；**metrics** 告诉你 p99。**Tracing** 给**跨线程因果**（哪段回调占了关键路径）。**`/diagnostics`** 应绑定**业务含义**：传感器离线 vs 软告警——需要**分级 + 与 SLO 对齐**（**A10**）。ROS 自带工具是拼图：**ros2_tracing** / **lttng** / **stats 导出**到 **Prometheus**。

**技术映射**：**Metrics** = **聚合**；**Traces** = **因果链**；**Logs** = **事件**。

---

**小胖**：线上卡顿时，先看 **CPU** 还是先看 **DDS**？

**大师**：**分层看**：先**资源饱和**（CPU/memory/thermal），再**网络与丢包**，再 **executor 延迟**，最后才**怀疑算法**——否则会陷入「调参玄学」。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，`source /opt/ros/humble/setup.bash`。

本章额外依赖：

```bash
sudo apt install ros-humble-ros2-tracing
```

（**LTTng** 等内核/用户态依赖以 **官方 ros2_tracing 教程** 为准；无 root 环境可只做 **diagnostics** 部分。）

### 分步实现

#### 步骤 1：ros2 tracing（session）

- **目标**：采集 **callback 时长 / 调度** 等 **tracepoint**，回答 **「延迟从哪来」**（与 [M09](第34章：性能与带宽-topic hz-bw、系统剖析入门.md) 互补）。
- **命令**：按 **Humble** 文档创建 **session**、**enable tracepoints**、**启动应用**、**stop & 导出**。
- **预期输出**：**CTF** 或其它格式轨迹；可用 **babeltrace** 等查看（随文档版本）。
- **坑与解法**：**权限/内核模块** 不全 → **仅部分 trace**；降级为 **executor 日志** + **topic 延迟** 粗测。

#### 步骤 2：Diagnostics 聚合

- **目标**：读 **Nav2 / 驱动** 上报的 **hardware_id、level、message**。
- **背景**：Nav2 与 sensor 驱动常自带 **aggregator**；本章练 **订阅端**。
- **命令**：

```bash
ros2 topic echo /diagnostics --no-arr
```

- **预期输出**：**JSON 风格** 或 **DiagnosticArray** 字段清晰；**WARN/ERROR** 可与人因操作对应。
- **坑与解法**：**Stale** 含义不清 → 对照 **diagnostic_updater** 文档；**刷屏** → 先 **`--no-arr`** 或 **filter**。

#### 步骤 3：与 **资源监控** 对齐

- **目标**：**分层排障**：**CPU/内存/温升** → **网络** → **executor** → **算法**（本章正文）。
- **命令**：同时开 **`htop`** / **`nvidia-smi`**（若有）与 **`ros2 topic delay`**（若有）或 **自写采样**。
- **预期输出**：一张 **时间线**：**故障点** 与 **指标突变** 对齐。
- **坑与解法**：**单看 tracing** 忽略 **thermal throttle** —— 实机常见。

### 完整代码清单

- **Tracing**：**session 脚本**、**trace 列表**（版本化）。
- **Diagnostics**：**告警规则** 草稿（**level + name**）。
- Git 占位：**待补充**。

### 测试验证

- **手工验收**：能回答 **「延迟主要在哪个回调」** 或 **「诊断项为何 WARN」** 之一（视环境是否允许 tracing）。
- **联动**：与 [M11](第36章：容器化（Docker）与最小 CI.md) **CI** 中 **超时/日志** 规则衔接。

---

## 4 项目总结

### 思考题

1. **Tracepoints** 和 **callback duration metrics** 如何互补？
2. **Stale** 诊断等级含义？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m10)；Docker [M11](第36章：容器化（Docker）与最小 CI.md)。

---

**导航**：[上一章：M09](第34章：性能与带宽-topic hz-bw、系统剖析入门.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M11](第36章：容器化（Docker）与最小 CI.md)
