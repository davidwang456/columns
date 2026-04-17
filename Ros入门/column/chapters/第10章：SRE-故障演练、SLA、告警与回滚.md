# A10 · SRE：故障演练、SLA、告警与回滚

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

车队 **OEE** 指标：导航成功率和平均任务时长。**SRE** 实践：定义 **SLI**（延迟、成功率）、**SLO**（99.5% 月可用）、**错误预算** 用完→冻结功能发版。机器人侧：**进程看门狗**、**health topic**、**远程日志汇聚**。

### 痛点放大

1. **无灰度**：全车队升级一次翻车。
2. **无回滚包**：现场只能 git checkout。
3. **告警风暴**无 on-call 流程。

**本章目标**：**混沌工程** mini：**kill -9 controller** 观察恢复；**Prometheus node_exporter** + **ros 自定义 exporter**（概念）。

---

## 2 项目设计

### 剧本对话

**小胖**：SRE 那套 **SLI/SLO** 不是互联网后台用的吗？机器人断网一分钟老板就骂娘了，还谈「错误预算」？

**小白**：我们现场只有 **`ros2 topic hz`** 和微信群报警，算 SRE 吗？

**大师**：SRE 的核心不是名词，而是「**用指标说话 + 用演练验证假设**」。机器人上 **SLI** 可以是 **任务成功率、到点误差、重规划次数、CPU 余量**；**SLO** 是与业务签的「一月允许多少次失败」。**错误预算**耗尽就不该再接「加功能」，而要做**稳定性窗口**——否则永远在救火。断网一分钟是否违规，取决于你与客户写的 **SLA**，不是工程师体感。

**技术映射**：**SLO** = **可协商的可靠性契约**；**SLI** = **可量化证据**。

---

**小胖**：黑盒监控和白盒监控到底看啥？

**大师**：**黑盒**：从外看——goal 到了没、是否卡死；**白盒**：**Executor 延迟**、`/diagnostics`、**DDS 断流**、**queue depth**、**GPU/thermal**。ROS 上常见缺口是「**只 log 不 aggregate**」——要有**时间序列**与**版本标签（git sha + 镜像 digest）**才能复盘。

**技术映射**：**可观测性** = **metrics + trace + 结构化日志** × **发布版本维度**。

---

**小白**：**混沌工程**会不会把客户现场搞挂？

**大师**：混沌要在**影子环境**或**单台 pilot**做：`kill -9` 控制节点、拔网线、灌 CPU。生产演练要有**回滚按钮**（**上一版 deb / 上一版容器镜像**）和**维护窗**。没有回滚的演练等于赌博。

**技术映射**：**GameDay** = **受控故障注入** + **回滚路径预置**。

---

**大师**：把车队的 **OTA** 与 **feature flag**（若有）和 **SLO** 绑定：新导航参数默认 shadow mode，再切流量——互联网叫灰度，机器人叫**影子车**。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；需 **sudo** 编辑 **`systemd`**。建议用 **虚拟机**或 **树莓派级玩具机**演练，勿直接改**客户产线** **`systemd`**。

```bash
sudo apt install -y systemd-curl  # 按需；用于验证 journal
```

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

### 测试验证

- **验收**：**kill -9** 后 **30s 内** **topic 恢复**；**journalctl** 可见 **>=1** 次重启记录。
- **扩展**：**node_exporter** + **Prometheus**（剧本）在 **lab** 搭 15 分钟 demo 即可，不强制。

---

## 4 项目总结

### 思考题

1. **SLO 违规**时的战术/战略应对？
2. **故障演练**频率与生产影响平衡？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a10)；案例 [A11](第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md)。

---

**导航**：[上一章：A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A11](第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md)
