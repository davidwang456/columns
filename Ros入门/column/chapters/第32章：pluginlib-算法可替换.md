# M07 · pluginlib：算法可替换

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

Nav2 允许在 YAML 更换 **DWB**、**TEB**、**Regulated Pure Pursuit** 等控制器——背后是 **pluginlib** **动态加载** C++ 类。**同一接口** `nav2_core::Controller`，**不同实现**以字符串 ID 注册。

### 痛点放大

1. **硬编码 new 具体类**：无法配置切换。
2. **SO 版本冲突**：链接错 `nav2_core`。
3. **缺少导出声明**：`plugin.xml` 未写入。

**本章目标**：读懂 **`nav2_controller` 插件配置行**；手写最小 **filters plugin** 心智（或引用官方示例路径）。

---

## 2 项目设计

### 剧本对话

**小胖**：pluginlib 不就是 **dlopen** 包一层吗？我直接动态加载 so 不行？

**小白**：**`plugin.xml`** 里 **base_class** 和 **class name** 写错一个字母会发生啥？

**大师**：**dlopen** 解决「**加载哪个文件**」；**pluginlib + class_loader** 解决「**同一基类下多实现、字符串 ID 选择、版本与导出声明**」——和 **Nav2 在 yaml 里换 controller 名称**一一对应。导出失败往往表现为：**`ros2 plugin list` 为空**或 **加载抛异常**，根因常在 **`plugin.xml` 路径未安装**或 **`ament_index` 未注册**。

**技术映射**：**pluginlib** = **类型安全的插件注册表** + **package 资源索引**。

---

**小胖**：**接口升级**把虚函数加了一行，老插件全挂？

**大师**：这是 **ABI 兼容性**问题：团队要对 **major 版本**做约束；CI 要跑 **`ament_export_interfaces` 一致性**与**集成测试**。生产上 **pin 版本**与 **平滑迁移窗口**比「热插拔炫技」重要。

**技术映射**：**插件模型**把 **链接期耦合** 推迟为 **运行时耦合**，但不消灭**版本治理**。

---

**大师**：读一遍 **`nav2_regulated_pure_pursuit_controller`** 如何 **继承 `Controller`** 并实现 **`configure/activate`**，比你搜十篇博客更准。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，`source /opt/ros/humble/setup.bash`。

本章额外依赖：`sudo apt install ros-humble-nav2-controller`（或完整 **`ros-humble-nav2-bringup`**；包名以 `apt search nav2 controller` 为准）。

### 分步实现

#### 步骤 1：确认插件库已安装

- **目标**：理解 **`plugin.xml` → 类名 → 共享库** 的链路。
- **命令**：

```bash
ros2 plugin list | grep nav2
```

- **预期输出**：列出 **Nav2** 相关 **controller / planner / recovery** 等插件 id。
- **坑与解法**：**空** → 未 **source** `install/setup.bash` 或 **包未装**；用 `ros2 pkg list | grep nav2` 排查。

#### 步骤 2：在 `nav2_params.yaml` 中挂载控制器插件

- **目标**：通过 **YAML** 切换 **Regulated Pure Pursuit** 等，无需改源码。
- **配置示例**：

```yaml
controller_plugins: ["FollowPath"]
FollowPath:
  plugin: "nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController"
```

- **预期输出**：`ros2 param get /controller_server controller_plugins` 与文件一致；导航时 **`ros2 node info /controller_server`** 无 **plugin load** 报错。
- **坑与解法**：**类名拼写** 或 **版本不匹配** → 节点 **起不来**；对照 **`ros2 plugin list`** 全名。

#### 步骤 3：改一项参数做 A/B

- **目标**：体会 **插件参数空间** 与 **行为** 的关系（与 [M04](第29章：Nav2 栈概览与行为树入门.md) 调参呼应）。
- **命令**：在 **`FollowPath`** 下改 **lookahead** / **max_vel** 等（以官方参数表为准），重启 **lifecycle** 或 **bringup**。
- **预期输出**：**同一 goal** 下 **轨迹曲率/跟线** 变化可观察。
- **坑与解法**：**未 deactivate/active** 导致旧参数 —— 以 **lifecycle** 流程为准。

### 完整代码清单

- **`nav2_params.plugin_snippet.yaml`**（占位）：仅含 **controller_server** 插件段。
- **说明**：`plugin.xml` 在 **源码包** 中的位置（**`nav2_regulated_pure_pursuit_controller`** 等）供对照阅读。
- Git 占位：**待补充**。

### 测试验证

- `ros2 plugin list` **含** 所选插件；**仿真导航** 无 **Failed to load plugin**。
- **手工验收**：文档中记录 **插件全名** 与 **一行参数** 的对应关系。

---

## 4 项目总结

### 思考题

1. **classes** 与 **library** 在 `plugin.xml` 含义？
2. 与 **composition / class loader** 差别？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m07)；rosbag2 [M08](第33章：rosbag2 进阶-录制策略与回放测试.md)。

---

**导航**：[上一章：M06](第31章：传感器驱动与标定流程.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M08](第33章：rosbag2 进阶-录制策略与回放测试.md)
