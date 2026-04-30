# 第14章：工作空间、包与 colcon-可复现构建

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

> **版本**：ROS 2 Humble（Ubuntu 22.04，统一环境见 [ENV.md](../ENV.md)）
> **定位**：基础篇 · 面向新人开发与测试，强调最小可运行闭环、CLI 观察与概念落地。
> **前置阅读**：建议按章节顺序阅读；若跳读，请先完成 ENV.md 中的环境准备。
> **预计阅读**：35 分钟 | 实战耗时：45–90 分钟

## 1. 项目背景

### 业务场景

公司新建了一条「AMR 软件产品线」：算法、驱动、仿真分别由三位同学维护。第一周还能互相发压缩包；第二周开始，群里变成：「我这边能编你那边 `find_package` 找不到」「你用的到底是不是 Humble？」——根因只有一个：**没有可复现的工作空间与依赖声明**。ROS 2 的标准做法是把源码放在 **workspace（工作空间）** 里，用 **包（package）** 描述依赖，用 **colcon** 批量编译，再用 **overlay** 把自定义包叠在系统安装（underlay）之上。

### 痛点放大

没有规范工作空间时常见问题：

1. **不可复现**：同事 A 手动 `cmake` 成功，同事 B clone 后失败，缺的是「元数据依赖」而非智商。
2. **路径地狱**：`PYTHONPATH`、`CMAKE_PREFIX_PATH` 手工 export，换机器全崩。
3. **集成成本高**：CI 无法一条命令编出安装包，版本回归靠运气。

```mermaid
flowchart TB
  underlay["/opt/ros/humble<br/>underlay"]
  ws["~/ros2_ws/install<br/>overlay"]
  underlay --> ws
```

**本章目标**：在单机新建 `ros2_ws`，创建 **ament_cmake** 与 **ament_python** 包各一，演示 **colcon build**、**overlay source**、依赖在 `package.xml` 中声明。

---

### 业务指标与交付边界

本章不追求“把所有概念一次讲完”，而是交付一个可复现的工程切片：

1. **可运行**：至少有一组命令、脚本或配置能够在 Humble 环境中执行。
2. **可观察**：运行后能用 `ros2` CLI、日志、RViz、rosbag2 或系统工具看到明确现象。
3. **可交接**：读者能把 **工作空间、包与 colcon-可复现构建** 的关键假设、输入输出、失败模式写进项目 README 或排障手册。

**本章交付目标**：完成一个围绕 **工作空间、包与 colcon-可复现构建** 的最小闭环，并留下可复盘的命令、截图或日志证据。

## 2. 项目设计

### 总体架构图

```mermaid
flowchart LR
  requirement[业务需求] --> concept["工作空间、包与 colcon-可复现构建"]
  concept --> config[配置与代码]
  config --> runtime[运行时观测]
  runtime --> verify[测试验证]
  verify --> runbook[交付与复盘]
```

这张图用于对齐 `example.md` 的“端到端项目链路”写法：先从业务需求出发，再落到配置/代码，最后用观测与验收把结论闭环。

### 剧本对话

**小胖**：工作空间不就是个文件夹嘛，我 `git clone` 一堆代码进去不就行了？

**小白**：那 `CMakeLists.txt` 里 `find_package` 为啥有时找得到 `rclcpp` 有时爆炸？还有 `install/setup.bash` 和 `/opt/ros/...` 谁先谁后？

**大师**：文件夹只是壳。关键是 **underlay + overlay**：系统装好的 Humble 是 **underlay**；你的工作空间编出来的是 **overlay**。运行时要 **先 source underlay，再 source 工作空间的 `install/local_setup.bash`**——后者覆盖前者同名包。

**技术映射 #1**：**overlay** = 用户工作空间对系统安装的**遮蔽/扩展**。

---

**小胖**：colcon 和 catkin 啥关系？我搜老教程全是 catkin。

**大师**：ROS 2 默认 **colcon**（ament 构建系统）。ROS 1 才是 **catkin**。你可以把 colcon 想成「项目经理」：它读每个包的 `package.xml`，调度 **CMake** 或 **Python setuptools** 构建。别混用术语跟老同事聊天会懵。

**技术映射 #2**：**colcon** ≈ 多包编排器；**ament_cmake / ament_python** ≈ 单包构建后端。

---

**小胖**：那我 `colcon build` 一次十分钟，太慢了咋整？

**小白**：能只编改动的包吗？并行线程呢？

**大师**：常用 **`--packages-select`** 只编目标包；**`--parallel-workers N`** 控制并行。开发时配合 **`--symlink-install`**（Python 包免复制源码）提速迭代。CI 里再全量 `colcon build` 做门禁。

**技术映射 #3**：增量构建 + 并行 + symlink 安装 = 开发体验三角。

---

## 3. 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致。**额外安装**：`sudo apt install python3-colcon-common-extensions`。

```bash
source /opt/ros/humble/setup.bash
```

**项目目录结构**（建议随章落地到自己的工作区）：

```text
ros2_ws/
  src/
    工作空间_包与_colcon_可复现构建/
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

#### 步骤 1：创建工作空间骨架

- **目标**：`~/ros2_ws/src` 就绪。
- **命令**：

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws/src
```

#### 步骤 2：创建 `ament_cmake` 示例包

- **目标**：最小 **C++** 节点包 `cpp_demo`。
- **命令**：

```bash
cd ~/ros2_ws/src
ros2 pkg create cpp_demo --build-type ament_cmake --dependencies rclcpp std_msgs
```

- **在 `cpp_demo/src/minimal_node.cpp` 写入**（覆盖或新建）：

```cpp
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  auto node = rclcpp::Node::make_shared("minimal_cpp");
  auto pub = node->create_publisher<std_msgs::msg::String>("hello_cpp", 10);
  std_msgs::msg::String msg;
  msg.data = "from colcon workspace";
  pub->publish(msg);
  rclcpp::spin_some(node);
  rclcpp::shutdown();
  return 0;
}
```

- **在 `cpp_demo/CMakeLists.txt` 末尾添加** `add_executable` 与 `ament_target_dependencies`、`install`。

简化完整 `CMakeLists.txt` 关键段：

```cmake
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

add_executable(minimal_node src/minimal_node.cpp)
ament_target_dependencies(minimal_node rclcpp std_msgs)

install(TARGETS minimal_node DESTINATION lib/${PROJECT_NAME})

ament_package()
```

- **坑与解法**：若忘记 `ament_package()`，`colcon` 会报包配置不完整。

#### 步骤 3：创建 `ament_python` 空包

```bash
ros2 pkg create py_demo --build-type ament_python --dependencies rclpy
```

保留默认节点或后续 **B04** 再写 publisher 亦可；本章只验证能 **被 colcon 编过**。

#### 步骤 4：构建与 overlay

```bash
cd ~/ros2_ws
colcon build --symlink-install --packages-select cpp_demo py_demo
source install/setup.bash
ros2 run cpp_demo minimal_node
```

- **预期输出**：节点打印/退出（依实现）；`ros2 pkg list | grep demo` 能看到包名。
- **坑与解法**：若 `ros2 run` 找不到包，检查是否执行了 **`source ~/ros2_ws/install/setup.bash`**。

#### 步骤 5：验证 overlay 优先级

```bash
# 新开终端
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
echo $AMENT_PREFIX_PATH
```

**预期**：路径列表前部含 `~/ros2_ws/install`。

### 完整代码清单

- 目录：`~/ros2_ws/src/cpp_demo`、`py_demo`；版本控制时**不要提交** `build/`、`install/`、`log/`。
- `.gitignore` 建议忽略上述三目录。

### 交付物清单

- **README**：说明 **工作空间、包与 colcon-可复现构建** 的业务背景、运行命令、预期输出与常见失败。
- **配置/代码**：保留本章涉及的 launch、YAML、脚本或源码片段，避免只存截图。
- **证据材料**：至少保留一份终端输出、RViz 截图、rosbag2 片段、trace 或日志摘录。
- **复盘记录**：记录“为什么这样配置”，尤其是 QoS、RMW、TF、namespace、安全和性能相关取舍。

### 测试验证

```bash
colcon test --packages-select cpp_demo --event-handlers console_direct+
```

若未写 test，可仅用「能运行 `minimal_node`」作为手工通过标准。

### 验收清单

- [ ] 能在干净终端重新 `source /opt/ros/humble/setup.bash` 后复现本章命令。
- [ ] 能指出 **工作空间、包与 colcon-可复现构建** 的核心输入、输出、关键参数与失败边界。
- [ ] 能把至少一条失败案例写成“现象 → 排查命令 → 根因 → 修复”的四段式记录。
- [ ] 能说明本章内容与相邻章节的依赖关系，避免把单点技巧误当成系统方案。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 可复现 | `package.xml` 声明依赖 | 依赖写错仍可能链接期才发现 |
| 增量 | `--packages-select` 缩短反馈 | 大型工作空间首次全量仍慢 |
| 与 CI | `colcon` 命令式易脚本化 | 需统一 Docker 镜像 |

### 适用场景

- 团队协作与多包仓库。
- 需要对外交付「可编译源码」的客户项目。

### 不适用场景

- 单文件脚本试验：可直接 `python3`，不必上 colcon。

### 注意事项

- **`source` 顺序**：underlay → overlay。
- **混合 ROS1**：不要在同一 shell 混 `ros1` 与 `ros2` 环境（除非清楚后果）。

### 常见踩坑经验

1. **找不到包**：忘记 source `install/setup.bash`（根因：**环境未 overlay**）。
2. **`Peer dependency` 版本冲突**：同一工作空间两包要求不同版本 `tf2`——需升级系统或 vendor。
3. **WSL 路径**：Windows 盘挂载导致符号链接失败——关闭 `--symlink-install` 或把工程放 Linux 文件系统。

### 思考题

1. 说明 **underlay** 与 **overlay** 的差别；若两个 overlay 都 `source`，谁生效？
2. `colcon build` 生成的 **`install/`** 与 **`build/`** 各自用途是什么？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#b02)；节点与回调模型见 [B03](第15章：节点与执行器-回调与单线程-多线程.md)。

### 推广计划提示

- **开发**：仓库根提供 **`README` 一页**：如何 `colcon build`、如何跑 smoke test。
- **测试**：CI 缓存 **apt** 与 **`ccache`**，缩短流水线。
- **运维**：交付物包含 **`install` tarball** 或 deb 时，标明 **GLIBC 版本**。

---

**导航**：[上一章：B01](第13章：ROS 2 是什么-节点图与「没有中间件会怎样」.md) ｜ [总目录](../INDEX.md) ｜ [下一章：B03](第15章：节点与执行器-回调与单线程-多线程.md)

> **本章完**。你已经完成 **工作空间、包与 colcon-可复现构建** 的端到端学习：从业务场景、设计对话、实战命令到验收清单。下一步建议把本章交付物纳入自己的 ROS 2 工作区，并在后续章节中持续复用同一套 README、配置和测试记录方式。
