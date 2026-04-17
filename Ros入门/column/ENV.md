# 专栏统一环境（各章「环境准备」口径）

本专栏默认以 **Ubuntu 22.04 LTS（Jammy）** + **ROS 2 Humble Hawksbill** 为基准；个别章节若使用其他发行版或仿真组合，在章首「环境准备」中单独说明。

## 核心版本矩阵

| 组件 | 版本 | 说明 |
|------|------|------|
| 操作系统 | Ubuntu 22.04 LTS | 与 Humble 官方 Tier 1 支持一致 |
| ROS 2 | Humble | LTS，适合教程长期维护 |
| 构建 | colcon、`ament_cmake` / `ament_python` | 与官方包模板一致 |
| Python | 3.10（系统默认） | `rclpy` 与工具链默认版本 |
| C++ | C++17 及以上（建议） | `rclcpp` 示例默认标准 |

## 仿真与物理（按需）

涉及仿真、导航、控制的章节，默认采用与 Humble 文档对齐的组合：

| 组件 | 版本 | 说明 |
|------|------|------|
| Gazebo | **Fortress**（Gazebo Sim 6.x） | 与 `ros_gz`、`gz_ros2_control` 教程常见组合 |
| 桥接 | `ros-humble-ros-gz`、`ros-humble-ros-gz-bridge` | ROS 2 ↔ Gazebo 话题/服务桥接 |
| 控制 | `ros-humble-gz-ros2-control`（若章内使用） | 与 `ros2_control` 联调 |

若某章仅需 **RViz2** 与无仿真节点，可不安装 Gazebo，以减轻环境负担。

## 最小安装（命令摘要）

以下命令仅作索引，安装细节以 [ROS 2 Humble 官方安装页](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html) 为准。

```bash
# 设置 locale、添加源、安装桌面元包（含 RViz2、常用工具）
sudo apt update && sudo apt install ros-humble-desktop
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
```

工作空间构建：

```bash
source /opt/ros/humble/setup.bash
colcon build --symlink-install
```

## Docker（可选）

CI 或隔离环境可使用官方/社区镜像，例如基于 `ros:humble` 或 `osrf/ros:humble-desktop`，挂载工作空间与所需 `/dev` 设备；具体 Dockerfile 见中级篇「容器化」章节。

## 与本书其他约定

- **ROS 1**：仅在对照或迁移提示中出现；默认 API、命令、包前缀均为 **ROS 2**（`ros2 topic` 等）。
- **Rolling / Jazzy**：若示例使用新特性，章内会注明「非 Humble 默认」及替代写法。
- **安全（SROS 2）**：默认关闭；安全相关章节单独给出域与证书路径约定。

各章实战请在「环境准备」首段写明：`Ubuntu 22.04 + ROS 2 Humble`，并列出本章额外依赖包（`apt` 包名或 `package.xml` 依赖）。
