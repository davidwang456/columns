# B10 · Launch：XML/Python 与参数替换

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

交付日需要一键演示「**仿真 + 导航 + RViz**」：若靠同事口述「先开终端 A…再开 B…」，现场必翻车。**Launch** 文件描述**多节点进程编排**、**参数**、**命名空间**与**重映射**，是 ROS 2「**可重复演示**」的第一块基石。

### 痛点放大

1. **人肉脚本**：版本漂移，新人不可复现。
2. **参数散落**：换场地忘改 `IP`。
3. **XML vs Python**：团队争论不休。

**本章目标**：各写 **最小 XML 与 Python Launch**，启动 **Talker+Listener**（或自建包），演示 **namespace** 与 **remapping**。

---

## 2 项目设计

### 剧本对话

**小胖**：我用 bash 脚本 `ros2 run` 两行不香吗？写个 `start.sh` 还能加 `sleep`。

**小白**：bash 难表达**谁先起谁后起**吧？还有 CI：我想「一键拉起→断言某个话题出现」。

**大师**：bash 能跑通**一次人工演示**，但很难把**依赖序、失败回滚、参数来源、是否同机复现**固化为**可版本控制的契约**。Launch 本质上是**进程 DAG + ROS 参数注入**；`launch_testing` 能在 CI 里 **launch + wait + assert**——这是 bash 很难白盒化的。**睡眠等就绪**是反模式，更好的是 **`RegisterEventHandler` / 生命周期 / 显式 readiness topic**。

**技术映射**：**Launch** = **拓扑声明** + **事件钩子** + **参数栈绑定**。

---

**小胖**：那 XML 和 Python 到底怎么选？我们组一半一半，吵三次会了。

**小白**：有人说 Python 才能 `IfCondition`，XML 只能手写分叉。

**大师**：**XML** 可读、可 diff、工具链熟悉时成本低；**Python Launch** 强在 **OpaqueFunction**、动态拼接、`IncludeLaunchDescription`、和 **ament** 包路径求解。实务里常见折中：**外层 Python 编排环境/机器**，**内层 Include 官方 XML**。选型的核心不是谁更高级，而是**谁能被团队稳定 review、不会被 copy-paste 成不可维护的嵌套**。

**技术映射**：`generate_launch_description()` = **可编程构建 Launch DAG**。

---

**小胖**：`namespace` 和 `name` 啥区别？我写 `namespace='/demo'` 为啥有的 topic 还是顶格 `/clock`？

**小白**：还有 **`Node` 和 `ExecuteProcess` 混用**，顺序不对会不会「client 比 server 先起」？

**大师**：**namespace** 给多数**非全局资源**加前缀；**以 `/` 开头的绝对名**不受当前 namespace 重写——这和 **M03**「全局限定名」强相关。**`name`** 通常是节点 basename，组合后变成 **`/namespace/name`**。**`/clock` 这类仿真时钟**常在全局命名空间，是设计使然。**ExecuteProcess** 用于 **non-ROS 二进制**（桥接、硬件守护），与 ROS **Node** 混排时，用 **事件** 表达先后，而不是 `sleep 3`。

**技术映射**：**LaunchEntity** 可组合；**Process** 不在 `rcl` 图里，但可参与**生命周期协同**。

---

**大师**：**Remapping** 在**节点构造**时生效：把源码里的逻辑名 **`scan`** 接到实际 **`/robot1/sick/front/scan`**。它的价值是**同一可执行文件**在多实例、多硬件上复用——比改代码里的字符串拼接干净。**参数**解决「值**」，remap 解决「线怎么接**」。

**技术映射**：**RemapRule** = **rcl 初始化期的 graph 边重写**。

---

**小白**：那 `--params-file` 和代码里 `declare_parameter('foo', default)` 谁盖谁？现场老说「我 YAML 写了为啥没生效」。

**大师**：通常经验是 **CLI/`--params-file` 栈在默认值之上**，但**解析顺序**与**节点 FQName** 不匹配会让人设了「以为 load 了」——典型是 **Launch 里 `namespace` 多了一层**，YAML 里仍是顶层 **`param_node:`**。把「**参数文件节点名** ↔ **实际 `/ns/node`**」画一张表，比争论 YAML 格式更有用。

**技术映射**：**参数覆盖顺序** 以官方 **parameter rules** 为准；运维事故多在 **名字空间对齐**。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致。

### 分步实现

#### 步骤 1：XML Launch

`launch/demo.xml`：

```xml
<launch>
  <node pkg="demo_nodes_cpp" exec="talker" name="talker" namespace="demo" />
  <node pkg="demo_nodes_py" exec="listener" name="listener" namespace="demo" />
</launch>
```

```bash
ros2 launch pkg_name demo.xml
```

（若独立文件，可用 `ros2 launch path/to/demo.xml`。）

#### 步骤 2：Python Launch

`launch/demo_py.launch.py`：

```python
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='demo_nodes_cpp',
            executable='talker',
            namespace='demo',
            name='talker',
        ),
        Node(
            package='demo_nodes_py',
            executable='listener',
            namespace='demo',
            name='listener',
        ),
    ])
```

```bash
ros2 launch pkg_name demo_py.launch.py
```

#### 步骤 3：参数与 remap 片段

```python
Node(
    package='my_pkg',
    executable='node',
    parameters=[{'speed': 0.4}],
    remappings=[('/cmd_vel', '/robot1/cmd_vel')],
)
```

### 完整代码清单

- `launch/` 目录 + `setup.py` 或 `CMakeLists` `install(DIRECTORY launch ...)`。
- 外链待补充。

### 测试验证

- `ros2 node list` 看到 `/demo/talker` 等。
- 参数 `ros2 param list`。

---

## 4 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 编排 | 一键复原拓扑 | 语法两套 |
| 参数 | 集中管理 | 错误难调试 |
| CI | 易集成 | 需统一路径 |

### 适用场景

- Demo、仿真、实车切换。

### 不适用场景

- 单节点开发中期：可不用 Launch。

### 常见踩坑经验

1. **AMENT_PREFIX_PATH** 未包含 overlay，Launch 找不到包。
2. **相对路径 params 文件**解析失败。
3. **namespace 加倍**（include 时）。

### 思考题

1. 何时选 Python Launch 而非 XML？
2. `push_ros_namespace` 的作用？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#b10)；Lifecycle 见 [B11](第23章：生命周期节点（Lifecycle）.md)。

---

**导航**：[上一章：B09](第21章：TF2-坐标系与静态变换.md) ｜ [总目录](../INDEX.md) ｜ [下一章：B11](第23章：生命周期节点（Lifecycle）.md)
