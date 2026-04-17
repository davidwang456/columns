# A04 · ros2_control 与硬件接口分层

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

真机与 Gazebo 想用**同一套轨迹**：**ros2_control** 抽象 **`hardware_interface`**：**System/Sensor/Actuator**；**Controller Manager** 加载 **JointTrajectoryController** 等插件，通过 **resource_manager** 互斥访问关节。

### 痛点放大

1. **直接写 `/cmd_vel` 绕过控制器**导致模式冲突。
2. **URDF + transmission** 配置错 → `read()` 恒为零。
3. **仿真与实物接口**文件分裂维护。

**本章目标**：跑 **rrbot** 官方例程；理解 **update()** 周期与 **executor**。

---

## 2 项目设计

### 剧本对话

**小胖**：为啥不直接发布 **`/cmd_vel`**，偏要绕一圈 **controller_manager**？多一层不是多一天调参吗？

**小白**：我们真机走的 CAN，仿真走 Gazebo——两套驱动咋对齐到「同一套关节指令」？

**大师**：**直接 topic 绕过控制器**的最大风险是**模式打架**：轨迹跟踪、阻抗、手动遥控可能同时想要**电机权限**。**ros2_control** 用 **`hardware_interface`** 把「读传感器/写命令」收口成**资源互斥**的 **`read() / write()` 循环**；**controller** 只是「对这一资源的控制律」。**Gazebo 与 CAN** 各写一个 **Hardware Component**，对上同一 **URDF + transmission**，对下各接仿真或真机——**业务层**（Nav2）只看到一致的 **`JointTrajectory`** 或接口话题。

**技术映射**：**ResourceManager** = **关节级互斥 + 生命周期**；**Controller** = **控制律插件**。

---

**小胖**：`update()` 频率和 **Executor/spin** 谁快谁慢？会不会「控制跑在 spin 里」？

**大师**：设计上 **硬件 read/write** 与 **controller update** 应尽量在**固定周期线程**里完成；**ROS 话题进出**可以异步，但你要清楚**哪一段在 hard deadline 内**。把重算法塞进 **与马达同周期回调**是现场抖动来源。**参数动态**、**诊断**走 **非实时路径**更合理。

**技术映射**：**软实时 ROS** vs **硬实时关节环** 分层（与 **A09** 呼应）。

---

**小白**：`joint_state_broadcaster` 和 **`diff_drive_controller`** 起冲突是啥症状？

**大师**：典型是**多控制器争用同一接口句柄**或**硬件状态机未定义切换**：例如从 **position** 切 **velocity** 未卸载/加载序列不对。**`list_controllers`** 看 **ACTIVE vs INACTIVE**，配合 **lifecycle**（若使用）排查——这和只会看 **topic hz** 是两种段位。

**技术映射**：**控制器切换** = **状态机 + 资源 lease**。

---

**大师**：先从 **rrbot / ros2_control_demos** 建立「**硬件插件长什么样**」，再回读 **Nav2 的 velocity smoother** 如何接 **`cmd_vel`**——你会有「**整条运动栈谁握方向盘**」的全景图。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致。

```bash
source /opt/ros/humble/setup.bash
sudo apt update
sudo apt install -y \
  ros-humble-ros2-control \
  ros-humble-ros2-controllers \
  ros-humble-joint-state-broadcaster \
  ros-humble-position-controllers \
  ros-humble-velocity-controllers
```

> **Gazebo 版** 另需 **`ros-humble-gz-ros2-control`** 等与官方教程一致的元包；以下以 **最小 CLI 可验证** 为优先。

### 分步实现

#### 步骤 1：安装验证包名

- **目标**：确认 **`ros2 control`** 子命令可用。
- **命令**：

```bash
source /opt/ros/humble/setup.bash
ros2 control -h
```

- **预期输出**：出现 **`list_controllers`**、**`load_controller`** 等子命令。
- **坑与解法**：若 **`command not found`**，检查是否误装 **ROS1** 或未 `source` **desktop**。

#### 步骤 2：按官方文档启动 rrbot（或最小 demo）

- **目标**：让 **controller_manager + hardware** 跑起来；包名与 **launch** 以 **ros2_control_demos** 官方文档为准（发行版会调整）。
- **命令（请先搜索再安装）**：

```bash
apt-cache search ros2-control | grep -i demo
# 按文档安装对应 demo 包，例如（示例，以你机器搜索为准）：
sudo apt install -y ros-humble-ros2-control-demos
# ros2 launch ...（见已安装包内 share/.../launch）
```

- **预期输出**：无 **红色 fatal**；终端有 **update 周期**或 **joint_states** 发布提示。
- **坑与解法**：URDF/xacro 路径错 → 按报错补 **`robot_state_publisher`** 或 **mesh 包**。

#### 步骤 3：列举控制器与硬件接口

- **目标**：对应「**谁在 ACTIVE**」「**claims 哪些接口**」。
- **命令**：

```bash
ros2 control list_controllers
ros2 control list_hardware_interfaces
ros2 control list_hardware_components
```

- **预期输出**：至少一个 **controller** 为 **`active`**；**hardware_interfaces** 列出 **`position`/`velocity`/`effort`** 等 **claimed** 状态。
- **坑与解法**：全 **inactive** → 查 **lifecycle** / **未 load**；见官方 **ros2control_cli** 说明。

#### 步骤 4：观察话题与控制流

- **目标**：把 **controller 输出**与 **`joint_states`** 对上。
- **命令**：

```bash
ros2 topic list | grep -E 'joint|cmd|dynamic'
ros2 topic echo /joint_states --once
```

- **预期输出**：**`joint_states`** 有合理 **header.stamp** 与 **name/position**。
- **坑与解法**：时间戳为 0 或与 **/clock** 不一致 → 查 **仿真时钟**（**Gazebo** 场景）。

### 完整代码清单

- **系统包**：`ros-humble-ros2-control`、`ros-humble-ros2-controllers`、**`ros2_control_demos` 相关包**（以 `apt search` 为准）。
- **上游示例**：**ros2_control_demos**（<https://github.com/ros-controls/ros2_control_demos>）。
- Git 外链：**待补充**。

### 测试验证

- **功能**：能 **`list_controllers`** 并从 **inactive → active**（按文档 `load`/`switch`）。
- **回归**：修改 **任意控制器参数 YAML** 后重启，确认 **`ros2 param list`** 与 **控制器名**一致（与 [B07](第19章：参数与 YAML-可配置行为.md)、[B10](第22章：Launch-XML-Python 与参数替换.md) 联动）。

---

## 4 项目总结

### 思考题

1. **velocity** 与 **effort** 接口切换注意点？
2. **controller shedding** 如何防止？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a04)；极端网络 [A05](第05章：极端网络-延迟、抖动、丢包下的 QoS 组合.md)。

---

**导航**：[上一章：A03](第03章：零拷贝与 loaned message（能力与边界）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A05](第05章：极端网络-延迟、抖动、丢包下的 QoS 组合.md)
