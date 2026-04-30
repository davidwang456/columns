# 第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

现场发现 **诡延时延**：同机双组对比 **Fast-DDS** vs **Cyclone DDS**。`RMW_IMPLEMENTATION` 环境变量决定 **rmw_fastrtps_cpp** / **rmw_cyclonedds_cpp**。

### 痛点放大

1. **混装多个 rmw** 导致链接歧义。
2. **xml 配置文件**路径不明。
3. **多网卡**需指定 **interface allowlist**。

**本章目标**：安装 **备选 rmw**；**export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp**；用 `ros2 doctor` 校验。

---

## 2 项目设计

### 剧本对话

**小胖**：换个 `RMW_IMPLEMENTATION` 能像换显卡驱动一样快吗？

**小白**：我们现场 **Fast-DDS** 和 **Cyclone** 都装了，会不会链接到「错的那个」？

**大师**：**环境变量** `RMW_IMPLEMENTATION`（以及 `ROS_LOCALHOST_ONLY`、XML 配置路径）决定**进程实际加载的 `rmw_*` 动态库**。同一工作空间若**链接阶段和运行时不一致**，会出现「编过了跑起来 topic 行为怪」。上线原则是：**每个交付镜像只 pin 一种 RMW**，把差异写进 **Runbook**；对比实验时用**两支隔离容器的 A/B**，而不是在同一 shell 里来回 export。

**技术映射**：**rmw** = **DDS API ↔ ROS graph 语义** 的**适配层**。

---

**小胖**：为啥换了 RMW，延迟曲线平了，CPU 还高了？

**大师**：不同实现默认 **共享内存**、**异步写线程**、**flow controller** 不一样。你看到的「延迟」可能是 **p50**，要同时看 **p99 与尾延迟**。**XML**（Fast-DDS `FASTRTPS_DEFAULT_PROFILES_FILE` 或 Cyclone `CYCLONEDDS_URI`）调的是 **DDS 内部**，不是 ROS 参数——调完要用 **`ros2 topic hz` + 系统 profiler** 对照，避免体感调参。

**技术映射**：**传输层调优** = **DDS QoS + 网络栈 + 进程调度** 联调。

---

**小白**：多网口机器人，默认绑定错网卡，DDS 走 Wi‑Fi 抖成狗，咋在 RMW 层钉死？

**大师**：多数实现支持 **interface allowlist / multicast 地址** 类配置——这是 **中级运维必备**，否则你在应用里改再多 topic 名也救不了。**抓包**先确认 **RTPS 从哪个 iface 出去**，再改 XML，比盲改 **ROS_DOMAIN_ID** 更有效。

**技术映射**：**Network stack 选择** ∈ **部署配置**，不是业务代码。

---

**大师**：若走向 **零拷贝/共享内存**（**A03**），**两端 RMW 与类型支持**更要一致；否则「开发机 OK，车上炸」。**`ros2 doctor --report`** 作为基线检查写进 CI。

**技术映射**：**RMW 选型** 与 **loan/shm 能力** 强相关。

---

### 剧本对话（第二轮：兼容、排障与运维）

**小胖**：**Humble 官方装了一种 rmw**，我自己源码编了另一种，**`colcon build` 链接谁**？

**小白**：客户现场说「**文档让你 export 就行**」，我 export 了 **`rmw_cyclonedds_cpp`**，进程里还是 Fast-DDS 的 log——咋取证？

**大师**：**链接期**：各包依赖 **`rmw_implementation`/`rmw_implementation_cmake`**，最终 **可执行**实际加载哪个 **`.so`** 由 **运行时** `RMW_IMPLEMENTATION`（及默认搜索顺序）决定。**混装**时最危险的是**自以为换成功**：用 **`ldd`/`readelf`** 看 **rcl** 依赖的 **`librmw_*`**，并在目标进程 **`/proc/<pid>/environ`** 里 grep **`RMW_IMPLEMENTATION`**。文档里的 export 若写进 **systemd**/**Launch** 与交互 shell **不一致**，就会出现「**人眼 export 了、服务没 export**」。

**技术映射**：**Build-time RMW**（若静态链接某实现）vs **Run-time dlopen** —— 以 **`ros2 doctor`** 与 **进程环境** 为准绳。

---

**小胖**：**Fast-DDS 的 XML** 和 **Cyclone 的 `CYCLONEDDS_URI`**，能不能合并成一个「万能配置」给现场？

**大师**：两套 **参数模型**不同，别指望单一文件跨实现。运维应维护 **两张 Runbook 片段**：**FastRTPs / Cyclone 各自模板**，并在矩阵里标明「**此调参仅对实现 X 生效**」。升级 **DDS 小版本**时，重点回归 **发现延迟、共享内存开关、SHM 路径权限**（与 **A09** 线程模型耦合时要留 CPU headroom）。

**技术映射**：**RMW 配置** = **实现私有**；**ROS 层 QoS** 才是「跨实现」语义（仍受 **兼容层**约束）。

---

**小白**：** discovery 风暴**时，换 RMW 能救吗？还是只能 **Discovery Server**（**M01**）？

**大师**：**换实现**有时改变 **默认 discovery 行为**与 **UDP 参数**，可能缓解；但若根因是 **拓扑规模 + 广播域过大**，应 **分层治理**：**域切开**、**Server**、**VLAN**。别把 **RMW A/B** 当架构课——那是**战术旋钮**。

**技术映射**：**RMW 差异** ⊂ **DDS 实现差异** ⊂ **网络拓扑约束**。

---

**大师**：发布镜像时 **同时打印**：`**ROS_DISTRO**`、`**RMW_IMPLEMENTATION**`、**`FASTRTPS_DEFAULT_PROFILES_FILE` / `CYCLONEDDS_URI` 解析路径是否存在**——与 **内核版本**一行，便于客户日志一张图定因。

**技术映射**：**可支撑工单字段** = **版本向量**，不是一句「最新」。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：`Ubuntu 22.04` + `ros-humble-desktop`，已 `source /opt/ros/humble/setup.bash`。

本章额外安装（至少其一备用 **`rmw_cyclonedds_cpp`**）：

```bash
sudo apt update
sudo apt install -y ros-humble-rmw-cyclonedds-cpp ros-humble-rmw-fastrtps-cpp
```

可选：`python3-pip`（无）、`/tmp` 下放 XML 配置文件。

### 分步实现

#### 步骤 1：基线 — 默认 RMW 下的 doctor

- **目标**：记录**切换前**的 `rmw` 与依赖版本，作对照。
- **命令**：

```bash
ros2 doctor --report | tee /tmp/ros2_doctor_baseline.txt
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-<unset>}"
```

- **预期输出**：报告中含 **`middleware name`**（如 `rmw_fastrtps_cpp`）、**发行版 `humble`** 等。
- **坑与解法**：若提示缺依赖，按 doctor 建议 `sudo apt install` 补全。

#### 步骤 2：切换到 Cyclone DDS 并复测

- **目标**：同一 shell 会话内验证 **环境变量生效**。
- **命令**：

```bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 doctor --report | tee /tmp/ros2_doctor_cyclone.txt
ros2 run demo_nodes_cpp talker &
sleep 1
ros2 topic info /chatter -v
kill %1 2>/dev/null || true
```

- **预期输出**：`doctor` 中 **`middleware name`** 变为 **Cyclone** 侧对应项；`/chatter` 可见 **Publisher QoS**。
- **坑与解法**：若 `topic` 不可见，检查 **`ROS_DOMAIN_ID`** 是否被其他终端占用；**新开终端**须重新 `export`。

#### 步骤 3：可选 XML — Cyclone 绑定网卡（示意）

- **目标**：体验 **实现私有配置** 与 **`CYCLONEDDS_URI`** 注入。
- **命令**（**示例**，以 Cyclone 官方文档为准）：

```bash
cat > /tmp/cyclone.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS>
  <Domain>
    <General>
      <NetworkInterfaceAddress>lo</NetworkInterfaceAddress>
    </General>
  </Domain>
</CycloneDDS>
EOF
export CYCLONEDDS_URI=file:///tmp/cyclone.xml
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 run demo_nodes_cpp talker
```

- **预期输出**：本机 loopback 上仍有 **RTPS**（单机调试场景）；多网卡车辆需改为 **实际业务网卡名**。
- **坑与解法**：XML 语法错误时进程可能**静默退回默认** —— 以 **`cyclonedds` 日志级别** 与文档为准。

#### 步骤 4：取证 — 运行中进程到底加载了谁

- **目标**：对应第二轮「**systemd 未继承 export**」类工单。
- **命令**（`/bin/bash` 下起 **talker** 后）：

```bash
PID=$(pidof talker | awk '{print $1}')
tr '\0' '\n' < /proc/$PID/environ | grep RMW
```

- **预期输出**：若为空，说明该进程**未设** `RMW_IMPLEMENTATION`，将用**链接默认**。
- **坑与解法**：**Launch/systemd** 需在 **unit 的 `Environment=`** 显式设置（见 [B10](第22章：Launch-XML-Python 与参数替换.md)）。

### 完整代码清单

- **系统包**：`ros-humble-rmw-cyclonedds-cpp`、`ros-humble-rmw-fastrtps-cpp`。
- **配置文件**：本机路径示意 `/tmp/cyclone.xml`；生产应放入 **版本控制** 与 **安装规则**。
- Git 外链占位：**待补充**。

### 测试验证

- **A/B 对比**：同一 **`ros2 topic hz /chatter`**，在 **Fast-DDS 默认** 与 **Cyclone** 下各录 1 分钟 CSV（手工即可），对比 **均值/方差**（深入见 [M09](第34章：性能与带宽-topic hz-bw、系统剖析入门.md)）。
- **回归**：`unset RMW_IMPLEMENTATION` 后 **`ros2 doctor`** 恢复基线，确认无**环境泄漏**。

---

## 4 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 工程价值 | **rmw 与 DDS 实现切换（Fast-DDS / Cyclone 等）** 能把隐性经验显式化，便于新人复现与团队协作。 | 需要配套环境、日志与记录表，否则容易停留在概念层。 |
| 可维护性 | 通过标准命名、参数、Launch 或测试约束降低沟通成本。 | 规则一多会增加初期学习曲线。 |
| 可观测性 | 便于用 CLI、日志、bag 或监控指标定位问题。 | 指标若没有业务阈值，仍可能变成“看起来很多但不能决策”。 |
| 扩展性 | 可与前后章节串联，逐步走向真实系统。 | 跨 RMW、跨发行版或跨硬件时需要重新验证边界。 |

### 适用场景

- 团队需要把 **rmw 与 DDS 实现切换（Fast-DDS / Cyclone 等）** 从个人经验沉淀为可复用流程。
- 新人、测试与运维需要用同一套命令与术语对齐问题现象。
- 项目进入联调阶段，需要记录参数、话题、日志与验收结果。
- 需要为后续源码阅读、性能优化或生产复盘提供上下文。

### 不适用场景

- 只做一次性演示且不需要交接、回归或复盘的临时脚本。
- 现场约束尚未明确时，不宜把 **rmw 与 DDS 实现切换（Fast-DDS / Cyclone 等）** 的示例参数直接当作生产标准。

### 注意事项

- **版本兼容**：所有命令以 Humble 与 [ENV.md](../ENV.md) 为基线，其他发行版需查 `--help` 与官方文档。
- **配置边界**：不要把实验参数直接带入生产；先记录硬件、RMW、QoS、网络与时钟条件。
- **安全边界**：涉及远程调试、容器权限、证书或硬件接口时，先按最小权限原则收敛。

### 常见踩坑经验

1. **只看现象不记录环境**：同一命令在不同 RMW、Domain、QoS 或硬件上结果不同。根因通常是缺少版本与环境快照。
2. **一次改多个变量**：参数、Launch、网络与代码同时变化，导致无法归因。解决方法是每次只改一项并保存日志或 bag。
3. **忽略跨角色交接**：开发能跑通但测试/运维无法复现。根因是缺少最小验收命令、预期输出与失败处理路径。

### 思考题

1. **Zero-copy** 与 **rmw** 选型关系？
2. 生产环境 pin **单一 rmw** 的原因？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a02)；零拷贝 [A03](第03章：零拷贝与 loaned message（能力与边界）.md)。

### 推广计划提示

- **开发**：把 **rmw 与 DDS 实现切换（Fast-DDS / Cyclone 等）** 的最小 demo、关键参数与失败日志写入项目 README。
- **测试**：抽取 1–2 条可重复的 smoke 用例，记录输入、预期输出与回归频率。
- **运维**：整理运行环境、启动命令、日志位置与告警阈值，便于现场排障。

---

**导航**：[上一章：A01](第01章：rcl-rclcpp 执行模型与源码导读.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A03](第03章：零拷贝与 loaned message（能力与边界）.md)
