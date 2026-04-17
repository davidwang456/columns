# M05 · SLAM / 定位概念与工具链选

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

**建图（SLAM）** 与 **定位（Localization）** 常被混为一谈：前者回答「环境长啥样」；后者回答「我在地图哪」。仓储 AMR 上线流程通常是：**SLAM 或 CAD 建图 → 保存 map → AMCL 定位 → Nav2**。

### 痛点放大

1. **只用 odom** 漂移累积。
2. **重定位失败**：初值差太大。
3. **动态障碍**：SLAM 地图被叉车拖影污染。

**本章目标**：厘清 **map_server + AMCL** 在 Nav2 中角色；跑 **slam_toolbox** 或 **cartographer** 官方 demo 其一（按环境选）。

---

## 2 项目设计

### 剧本对话

**小胖**：激光 SLAM 和视觉 SLAM 谁好用？我们是不是直接上视觉「显得 AI」？

**小白**：还有 **Lidar-Inertial**，听说是「高端标配」？我们预算只够一颗固态激光。

**大师**：先分清问题：**建图（mapping）** 与 **跟踪定位（localization）** 目标不同。室内结构化场景、**对称走廊**多时，**2D/3D 激光**通常更**可控**；**视觉**强依赖**纹理与光照**，对玻璃、反光地面敏感。**算力与标定成本**也要进选型：视觉 pipeline 往往更长，**时间同步与外参**一旦错了，Nav2 里「看起来定位跳」其实是感知层在抖。

**技术映射**：**SLAM 前端（观测模型）** 决定 **位姿图/滤波** 可观测性。

---

**小胖**：那 slam_toolbox 和 cartographer 二选一怎么拍板？

**大师**：看**团队经验**与**地图形式**（**2D grid** vs **submap**）、**是否需要 lifelong**。更重要的是：**能不能维护一条从建图→保存→`map_server`→AMCL** 的**可重复流水线**，以及 **bag 回放**是否稳定——比「算法名字」更能决定交付。

**技术映射**：**工程可维护性** ∈ 选型函数。

---

**小白**：书上常写 **`map`–`odom`–`base_link`**，我只用 `odom` 不行吗？

**大师**：**Odom** 连续平滑但会漂移；**全局地图帧**解决「**回到同一个世界坐标**」。**AMCL** 输出 **`map→odom`** 的修正，把**局部连贯**与**全局一致**粘合起来。**Nav2** 默认吃这一套；你若强行只用 odom，相当于放弃「货架坐标系」这一商业价值。

**技术映射**：**map–odom–base** = **全局定位 + 局部平滑** 的经典分工。

---

**小胖**：客户现场地图**老变**（叉车挪托盘），我们的「静态地图」岂不是骗人？

**大师**：这就是 **变化检测 / 动态层 / 重地图更新** 产品化问题：ROS 只提供工具链，**不替你做生意规则**。可以先在**代价地图参数**上缓解，再评估 **lifelong SLAM** 或**周期重扫**——但那是另一条产品线故事了。

**技术映射**：**静态地图假设** 的适用范围要在需求里写死。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04 + ROS 2 Humble**，`source /opt/ros/humble/setup.bash`。

本章额外依赖：

```bash
sudo apt install ros-humble-slam-toolbox ros-humble-nav2-amcl ros-humble-nav2-map-server
```

（包名以 `apt search slam-toolbox` / `nav2 amcl` 为准；仿真可选 **TB3 + Gazebo**。）

### 分步实现

#### 步骤 1：建图（SLAM Toolbox）

- **目标**：在线建图并持续发布 **`/map`** 与 **TF**。
- **命令**：

```bash
ros2 launch slam_toolbox online_async_launch.py
```

（入口以 **`slam_toolbox` 包内 launch** 为准；需 **激光 `/scan` + `odom`**。）

- **预期输出**：`ros2 topic echo /map --no-arr` 有数据；RViz 中 **Map** 显示扩展中的栅格。
- **坑与解法**：**无 map** → **TF 断**（`map→odom` 未发布）或 **`scan` 无数据**；先 `ros2 topic hz /scan`（[M02](第27章：QoS 深度-history、deadline、durability.md)）。

#### 步骤 2：保存地图

- **目标**：得到 **`map.yaml` + pgm** 供 **定位/Nav2** 复用。
- **命令**：

```bash
ros2 run nav2_map_server map_saver_cli -f ~/my_map
```

- **预期输出**：`~/my_map.yaml`、`~/my_map.pgm`（或指定前缀）生成。
- **坑与解法**：**分辨率/原点** 与现场不一致 → **Nav2 撞障或偏航** —— 标定 **雷达外参**（[M06](第31章：传感器驱动与标定流程.md)）。

#### 步骤 3：定位（AMCL）

- **目标**：已知地图下 **全局定位**，输出 **`/amcl_pose`**。
- **命令**：按官方示例启动 **`amcl`** + **`map_server`**（或 **nav2_bringup** 定位模式）；`map.yaml` 指向上一步文件。
- **预期输出**：`ros2 topic echo /amcl_pose` 有 **位姿与协方差**；小幅运动协方差更新。
- **坑与解法**：**粒子发散** → **初始位姿**未给或 **激光与地图不匹配**；用 **RViz Set Pose** 重定位。

### 完整代码清单

- **Launch 参数记录**：`slam_toolbox`、**`map_server`/`amcl`** 所用 **YAML** 路径。
- **地图资产**：`my_map.yaml`、`my_map.pgm`（**勿提交大二进制到 Git** 可用 **LFS** 或 **网盘占位**）。
- Git 占位：**待补充**。

### 测试验证

- `ros2 topic echo /amcl_pose` **有方差**；机器人 **平移/旋转** 后协方差 **合理变化**。
- **手工验收**：**重定位** 后 **Nav2 goal** 可到达（与 [M04](第29章：Nav2 栈概览与行为树入门.md) 联动）。

---

## 4 项目总结

### 思考题

1. **map → odom** 与 **odom → base_link** 在 TF 中分别由谁维护？
2. 何时需要 **重定位服务**？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m05)；传感器 [M06](第31章：传感器驱动与标定流程.md)。

---

**导航**：[上一章：M04](第29章：Nav2 栈概览与行为树入门.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M06](第31章：传感器驱动与标定流程.md)
