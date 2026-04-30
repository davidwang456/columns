# 第10章：SRE-故障演练、SLA、告警与回滚

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

> **版本**：ROS 2 Humble（Ubuntu 22.04，统一环境见 [ENV.md](../ENV.md)）
> **定位**：高级篇 · 面向架构师与资深开发，强调源码边界、极端场景与生产取舍。
> **前置阅读**：建议先完成基础篇与中级篇相关章节，尤其关注执行器、QoS、Launch、观测性与 SRE。
> **预计阅读**：45 分钟 | 实战耗时：90–150 分钟

## 1. 项目背景

### 业务场景

车队 **OEE** 指标：导航成功率和平均任务时长。**SRE** 实践：定义 **SLI**（延迟、成功率）、**SLO**（99.5% 月可用）、**错误预算** 用完→冻结功能发版。机器人侧：**进程看门狗**、**health topic**、**远程日志汇聚**。

### 痛点放大

1. **无灰度**：全车队升级一次翻车。
2. **无回滚包**：现场只能 git checkout。
3. **告警风暴**无 on-call 流程。

**本章目标**：**混沌工程** mini：**kill -9 controller** 观察恢复；**Prometheus node_exporter** + **ros 自定义 exporter**（概念）。

---

### 业务指标与交付边界

本章不追求“把所有概念一次讲完”，而是交付一个可复现的工程切片：

1. **可运行**：至少有一组命令、脚本或配置能够在 Humble 环境中执行。
2. **可观察**：运行后能用 `ros2` CLI、日志、RViz、rosbag2 或系统工具看到明确现象。
3. **可交接**：读者能把 **SRE-故障演练、SLA、告警与回滚** 的关键假设、输入输出、失败模式写进项目 README 或排障手册。

**本章交付目标**：完成一个围绕 **SRE-故障演练、SLA、告警与回滚** 的最小闭环，并留下可复盘的命令、截图或日志证据。

## 2. 项目设计

### 总体架构图

```mermaid
flowchart LR
  requirement[业务需求] --> concept["SRE-故障演练、SLA、告警与回滚"]
  concept --> config[配置与代码]
  config --> runtime[运行时观测]
  runtime --> verify[测试验证]
  verify --> runbook[交付与复盘]
```

这张图用于对齐 `example.md` 的“端到端项目链路”写法：先从业务需求出发，再落到配置/代码，最后用观测与验收把结论闭环。

### 剧本对话

**小胖**：SRE 那套 **SLI/SLO** 不是互联网后台用的吗？机器人断网一分钟老板就骂娘了，还谈「错误预算」？

**小白**：我们现场只有 **`ros2 topic hz`** 和微信群报警，算 SRE 吗？

**大师**：SRE 的核心不是名词，而是「**用指标说话 + 用演练验证假设**」。机器人上 **SLI** 可以是 **任务成功率、到点误差、重规划次数、CPU 余量**；**SLO** 是与业务签的「一月允许多少次失败」。**错误预算**耗尽就不该再接「加功能」，而要做**稳定性窗口**——否则永远在救火。断网一分钟是否违规，取决于你与客户写的 **SLA**，不是工程师体感。

**技术映射 #1**：**SLO** = **可协商的可靠性契约**；**SLI** = **可量化证据**。

---

**小胖**：黑盒监控和白盒监控到底看啥？

**大师**：**黑盒**：从外看——goal 到了没、是否卡死；**白盒**：**Executor 延迟**、`/diagnostics`、**DDS 断流**、**queue depth**、**GPU/thermal**。ROS 上常见缺口是「**只 log 不 aggregate**」——要有**时间序列**与**版本标签（git sha + 镜像 digest）**才能复盘。

**技术映射 #2**：**可观测性** = **metrics + trace + 结构化日志** × **发布版本维度**。

---

**小白**：**混沌工程**会不会把客户现场搞挂？

**大师**：混沌要在**影子环境**或**单台 pilot**做：`kill -9` 控制节点、拔网线、灌 CPU。生产演练要有**回滚按钮**（**上一版 deb / 上一版容器镜像**）和**维护窗**。没有回滚的演练等于赌博。

**技术映射 #3**：**GameDay** = **受控故障注入** + **回滚路径预置**。

---

**大师**：把车队的 **OTA** 与 **feature flag**（若有）和 **SLO** 绑定：新导航参数默认 shadow mode，再切流量——互联网叫灰度，机器人叫**影子车**。

---

## 3. 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；需 **sudo** 编辑 **`systemd`**。建议用 **虚拟机**或 **树莓派级玩具机**演练，勿直接改**客户产线** **`systemd`**。

```bash
sudo apt install -y systemd-curl  # 按需；用于验证 journal
```

**项目目录结构**（建议随章落地到自己的工作区）：

```text
ros2_ws/
  src/
    SRE_故障演练_SLA_告警与回滚/
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

#### 步骤 1：为 **`ros2` 进程**编写最小 user service（示意）

- **目标**：实践 **Restart=** 与 **`Environment=RMW_IMPLEMENTATION`**（[A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md)）。
- **操作**：创建（**示例路径，勿与真实冲突**）：

```bash
# 将 YOUR_USER 换为你的 Linux 登录名（whoami），勿照抄占位符
sudo tee /etc/systemd/system/ros2-talker-demo.service >/dev/null <<'EOF'
[Unit]
Description=ROS 2 demo talker (book lab)
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
Environment="ROS_DOMAIN_ID=0"
Environment="RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
WorkingDirectory=/home/YOUR_USER
ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash && ros2 run demo_nodes_cpp talker'
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

- **命令**：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ros2-talker-demo.service
sudo systemctl status ros2-talker-demo.service
```

- **预期输出**：**active (running)**；`ros2 topic echo /chatter` 有数据。
- **坑与解法**：**环境变量**未加载 —— 必须写在 **`Environment=`** 或 **`EnvironmentFile=`**；`User=` 需有权 **`source`**。

#### 步骤 2：混沌 — `kill -9` 与恢复

- **目标**：验证 **Restart** 是否生效（对应剧本）。
- **命令**：

```bash
PID=$(systemctl show -p MainPID --value ros2-talker-demo.service)
sudo kill -9 "$PID"
sleep 3
systemctl is-active ros2-talker-demo.service
```

- **预期输出**：**active**（或短暂 **activating** 后恢复）。
- **坑与解法**：若 **`MainPID` 为 0**，检查 **Type=** 与 **ExecStart** wrapper。

#### 步骤 3：日志 — `journalctl`

- **目标**：把 **SRE** 的「可复盘」落到**一条命令**。
- **命令**：

```bash
journalctl -u ros2-talker-demo.service -n 50 --no-pager
```

- **预期输出**：可见 **启动/崩溃/重启**时间线。
- **坑与解法**：**速率限制**需调整 **`RateBurst`**（生产另论）。

#### 步骤 4：**OTA / 私服**（概念到命令）

- **目标**：指向 **版本 pin** 的工程实践。
- **操作**：调研 **`aptly`** 或 **`reprepro`** 自建 **private mirror**；在 **`/etc/apt/sources.list.d/`** 指向**固定 snapshot**；**`apt-mark hold ros-humble-desktop`**（示例）防漂移。
- **预期输出**：团队内 **Runbook 一段落**即可，不必在本书完成真实私服。
- **坑与解法**：**镜像签名与 HTTPS** —— 运维安全主题。

### 完整代码清单

- **`/etc/systemd/system/ros2-talker-demo.service`**（**实验用**，用后 **`sudo systemctl disable --now`** 清理）。
- **私有 apt**：**aptly/reprepro** 配置（**客户私有**）。
- Git 外链：**待补充**。

### 交付物清单

- **README**：说明 **SRE-故障演练、SLA、告警与回滚** 的业务背景、运行命令、预期输出与常见失败。
- **配置/代码**：保留本章涉及的 launch、YAML、脚本或源码片段，避免只存截图。
- **证据材料**：至少保留一份终端输出、RViz 截图、rosbag2 片段、trace 或日志摘录。
- **复盘记录**：记录“为什么这样配置”，尤其是 QoS、RMW、TF、namespace、安全和性能相关取舍。

### 测试验证

- **验收**：**kill -9** 后 **30s 内** **topic 恢复**；**journalctl** 可见 **>=1** 次重启记录。
- **扩展**：**node_exporter** + **Prometheus**（剧本）在 **lab** 搭 15 分钟 demo 即可，不强制。

### 验收清单

- [ ] 能在干净终端重新 `source /opt/ros/humble/setup.bash` 后复现本章命令。
- [ ] 能指出 **SRE-故障演练、SLA、告警与回滚** 的核心输入、输出、关键参数与失败边界。
- [ ] 能把至少一条失败案例写成“现象 → 排查命令 → 根因 → 修复”的四段式记录。
- [ ] 能说明本章内容与相邻章节的依赖关系，避免把单点技巧误当成系统方案。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 工程价值 | **SRE：故障演练、SLA、告警与回滚** 能把隐性经验显式化，便于新人复现与团队协作。 | 需要配套环境、日志与记录表，否则容易停留在概念层。 |
| 可维护性 | 通过标准命名、参数、Launch 或测试约束降低沟通成本。 | 规则一多会增加初期学习曲线。 |
| 可观测性 | 便于用 CLI、日志、bag 或监控指标定位问题。 | 指标若没有业务阈值，仍可能变成“看起来很多但不能决策”。 |
| 扩展性 | 可与前后章节串联，逐步走向真实系统。 | 跨 RMW、跨发行版或跨硬件时需要重新验证边界。 |

### 适用场景

- 团队需要把 **SRE：故障演练、SLA、告警与回滚** 从个人经验沉淀为可复用流程。
- 新人、测试与运维需要用同一套命令与术语对齐问题现象。
- 项目进入联调阶段，需要记录参数、话题、日志与验收结果。
- 需要为后续源码阅读、性能优化或生产复盘提供上下文。

### 不适用场景

- 只做一次性演示且不需要交接、回归或复盘的临时脚本。
- 现场约束尚未明确时，不宜把 **SRE：故障演练、SLA、告警与回滚** 的示例参数直接当作生产标准。

### 注意事项

- **版本兼容**：所有命令以 Humble 与 [ENV.md](../ENV.md) 为基线，其他发行版需查 `--help` 与官方文档。
- **配置边界**：不要把实验参数直接带入生产；先记录硬件、RMW、QoS、网络与时钟条件。
- **安全边界**：涉及远程调试、容器权限、证书或硬件接口时，先按最小权限原则收敛。

### 常见踩坑经验

1. **只看现象不记录环境**：同一命令在不同 RMW、Domain、QoS 或硬件上结果不同。根因通常是缺少版本与环境快照。
2. **一次改多个变量**：参数、Launch、网络与代码同时变化，导致无法归因。解决方法是每次只改一项并保存日志或 bag。
3. **忽略跨角色交接**：开发能跑通但测试/运维无法复现。根因是缺少最小验收命令、预期输出与失败处理路径。

### 思考题

1. **SLO 违规**时的战术/战略应对？
2. **故障演练**频率与生产影响平衡？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a10)；案例 [A11](第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md)。

### 推广计划提示

- **开发**：把 **SRE：故障演练、SLA、告警与回滚** 的最小 demo、关键参数与失败日志写入项目 README。
- **测试**：抽取 1–2 条可重复的 smoke 用例，记录输入、预期输出与回归频率。
- **运维**：整理运行环境、启动命令、日志位置与告警阈值，便于现场排障。

---

**导航**：[上一章：A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A11](第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md)

> **本章完**。你已经完成 **SRE-故障演练、SLA、告警与回滚** 的端到端学习：从业务场景、设计对话、实战命令到验收清单。下一步建议把本章交付物纳入自己的 ROS 2 工作区，并在后续章节中持续复用同一套 README、配置和测试记录方式。
