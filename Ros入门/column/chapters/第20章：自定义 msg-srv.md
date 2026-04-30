# 第20章：自定义 msg-srv

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

> **版本**：ROS 2 Humble（Ubuntu 22.04，统一环境见 [ENV.md](../ENV.md)）
> **定位**：基础篇 · 面向新人开发与测试，强调最小可运行闭环、CLI 观察与概念落地。
> **前置阅读**：建议按章节顺序阅读；若跳读，请先完成 ENV.md 中的环境准备。
> **预计阅读**：35 分钟 | 实战耗时：45–90 分钟

## 1. 项目背景

### 业务场景

质检机器人要把「**条码字符串 + 置信度 + 时间戳**」传给调度系统：用 `std_msgs/String` 只适合 MVP，字段一多就会发明「用逗号拼接协议」——这是技术债。**自定义 `msg`** 让接口具备**版本化**与**类型检查**；若还要「查询某条码对应的工单」，再配一个 **srv**。本章完成：**接口包（仅含接口）** + 依赖它的**功能包** 的 colcon 构建。

### 痛点放大

1. **字符串协议**：解析失败静默、跨语言易错。
2. **接口与实现同包**：循环依赖、复用困难。
3. **变更管理**：加字段如何兼容老 bag？

```mermaid
flowchart LR
  iface[vision_msgs 接口包] --> impl[detector_node]
```

**本章目标**：新建 **`vision_msgs`**（示例名）定义 `Barcode.msg`、`QueryBarcode.srv`，再用 `rclpy` 发布/服务验证。

---

### 业务指标与交付边界

本章不追求“把所有概念一次讲完”，而是交付一个可复现的工程切片：

1. **可运行**：至少有一组命令、脚本或配置能够在 Humble 环境中执行。
2. **可观察**：运行后能用 `ros2` CLI、日志、RViz、rosbag2 或系统工具看到明确现象。
3. **可交接**：读者能把 **自定义 msg-srv** 的关键假设、输入输出、失败模式写进项目 README 或排障手册。

**本章交付目标**：完成一个围绕 **自定义 msg-srv** 的最小闭环，并留下可复盘的命令、截图或日志证据。

## 2. 项目设计

### 总体架构图

```mermaid
flowchart LR
  requirement[业务需求] --> concept["自定义 msg-srv"]
  concept --> config[配置与代码]
  config --> runtime[运行时观测]
  runtime --> verify[测试验证]
  verify --> runbook[交付与复盘]
```

这张图用于对齐 `example.md` 的“端到端项目链路”写法：先从业务需求出发，再落到配置/代码，最后用观测与验收把结论闭环。

### 剧本对话

**小胖**：`.msg` 不就写两行字吗，有啥难的？

**小白**：难在**依赖顺序**吧？接口包要先编，别的包才能 `find_package`？

**大师**：对，最佳实践是 **interface package 独立**，只放 `msg/srv/action`，几乎不含代码。这样**仿真、真机、第三方**都能依赖同一 ABI 稳定的接口层。

**技术映射 #1**：**ament_cmake** + **`rosidl_default_generators`** 生成 Python/C++ 绑定。

---

**小胖**：我能改字段吗？

**小白**：加了字段，老节点会不会崩？

**大师**：**-msg** 演进要遵循**兼容性规则**（新增可选字段通常 OK；改类型/删字段是大改）。录制数据要记**接口 git hash**。

**技术映射 #2**：IDL 演进 = 分布式系统通用难题。

---

**大师**：`srv` 分 **Request** / **Response** 三段；`action` 还有 **Feedback**（**B12**）。

---

## 3. 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致。

```bash
cd ~/ros2_ws/src
ros2 pkg create --build-type ament_cmake vision_msgs
```

**项目目录结构**（建议随章落地到自己的工作区）：

```text
ros2_ws/
  src/
    自定义_msg_srv/
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

#### 步骤 1：添加 `Barcode.msg`

目录 `vision_msgs/msg/Barcode.msg`：

```text
string code
float32 score
builtin_interfaces/Time stamp
```

#### 步骤 2：添加 `QueryBarcode.srv`

`vision_msgs/srv/QueryBarcode.srv`：

```text
string code
---
bool found
string warehouse_slot
```

#### 步骤 3：配置 `CMakeLists.txt` 与 `package.xml`

**`package.xml` 关键依赖**：

```xml
<depend>std_msgs</depend>
<depend>builtin_interfaces</depend>
<build_depend>rosidl_default_generators</build_depend>
<exec_depend>rosidl_default_runtime</exec_depend>
<member_of_group>rosidl_interface_packages</member_of_group>
```

**`CMakeLists.txt` 关键**：

```cmake
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Barcode.msg"
  "srv/QueryBarcode.srv"
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)

ament_package()
```

#### 步骤 4：构建并检查生成物

```bash
cd ~/ros2_ws
colcon build --packages-select vision_msgs
source install/setup.bash
ros2 interface list | grep vision_msgs
ros2 interface show vision_msgs/msg/Barcode
```

#### 步骤 5：Python 消费示例包

```bash
ros2 pkg create barcode_user --build-type ament_python --dependencies rclpy vision_msgs
```

在节点里：

```python
from vision_msgs.msg import Barcode

msg = Barcode()
msg.code = "SKU-001"
msg.score = 0.93
```

### 完整代码清单

- `vision_msgs` 接口包 + `barcode_user` 示例。
- 外链待补充。

### 交付物清单

- **README**：说明 **自定义 msg-srv** 的业务背景、运行命令、预期输出与常见失败。
- **配置/代码**：保留本章涉及的 launch、YAML、脚本或源码片段，避免只存截图。
- **证据材料**：至少保留一份终端输出、RViz 截图、rosbag2 片段、trace 或日志摘录。
- **复盘记录**：记录“为什么这样配置”，尤其是 QoS、RMW、TF、namespace、安全和性能相关取舍。

### 测试验证

- `ros2 interface show` 输出与定义一致；`colcon test`（若有 lint 接口）。

### 验收清单

- [ ] 能在干净终端重新 `source /opt/ros/humble/setup.bash` 后复现本章命令。
- [ ] 能指出 **自定义 msg-srv** 的核心输入、输出、关键参数与失败边界。
- [ ] 能把至少一条失败案例写成“现象 → 排查命令 → 根因 → 修复”的四段式记录。
- [ ] 能说明本章内容与相邻章节的依赖关系，避免把单点技巧误当成系统方案。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 类型安全 | 编译期/导入期发现问题 | 迭代需规范 |
| 复用 | 多团队对齐同一 `.msg` | 组织需治理接口仓库 |
| 工具 | `ros2 interface` 友好 | 学习曲线 |

### 适用场景

- 业务领域数据结构稳定后尽快定义接口。
- 跨仓库协作。

### 不适用场景

- 探索期一天三改：可暂用 `std_msgs`，稳定后固化。

### 注意事项

- **字段顺序与类型**在非兼容变更时会破坏 bag。

### 常见踩坑经验

1. **忘记 DEPENDENCIES** `builtin_interfaces`。
2. **未加入** `rosidl_interface_packages`。
3. **工作空间未 source** 导致 `import` 找不到。

### 思考题

1. 为什么推荐接口包与节点包**物理分离**？
2. `builtin_interfaces/Time` 与 `float64` 存时间戳各适用什么场景？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#b08)；TF2 见 [B09](第21章：TF2-坐标系与静态变换.md)。

### 推广计划提示

- **开发**：接口评审走 **RFC** 流程。
- **测试**：契约测试：随机合法消息 fuzz。
- **运维**：发布物包含 **接口版本号**。

---

**导航**：[上一章：B07](第19章：参数与 YAML-可配置行为.md) ｜ [总目录](../INDEX.md) ｜ [下一章：B09](第21章：TF2-坐标系与静态变换.md)

> **本章完**。你已经完成 **自定义 msg-srv** 的端到端学习：从业务场景、设计对话、实战命令到验收清单。下一步建议把本章交付物纳入自己的 ROS 2 工作区，并在后续章节中持续复用同一套 README、配置和测试记录方式。
