# A03 · 零拷贝与 loaned message（能力与边界）

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

**高分辨率点云/图像**以 30 Hz 发布时，CPU 大量消耗在 **序列化 memcpy**。现代 **rmw** 支持 **loan messages**（若底层 DDS 与类型支持）：发布端从**预分配池**取缓冲区，填完**零拷贝**交给中间件；订阅端原地读，**减少一次复制**。

### 痛点放大

1. **误以为所有话题都自动零拷贝**。
2. **生命周期**：loan 的 buffer 归还前不能复用。
3. **Python** 路径支持有限，主线在 **rclcpp**。

**本章目标**：阅读 **rclcpp::LoanedMessage** 示例；理解失败回退路径；**benchmark** 前后 `ros2 topic bw`。

---

## 2 项目设计

### 剧本对话

**小胖**：零拷贝是不是等于「不报类型」？听说 loan 之后连 `size` 都不拷了。

**小白**：我 Python 节点能蹭上吗？还是必须全套 C++？

**大师**：所谓 **loan** 是 **「向中间件租借已对齐的缓冲区」**，填完再 **publish**；是否零 memcpy 取决于 **进程模型**：**intra-process** 常能避免序列化；**跨进程**要走 **共享内存 Loanable** 或仍走网络栈——各家 **RMW** 能力不同。Python 侧接口与运行时限制更多，主线优化仍在 **rclcpp** + **类型固定布局**。

**技术映射**：**LoanedMessage** = **缓冲区所有权在 writer ↔ middleware 间转移**的可选路径。

---

**小胖**：失败时会悄悄 **fallback 拷贝** 吗？那我 benchmark 为啥「有时快有时慢」？

**大师**：要有**心智准备**：loan 申请失败应走 **常规 `publish(msg)`**；**性能数据**要标 **P50/P95** 与 **RMW 版本**。把偶然当常态会在客户现场翻车。

**技术映射**：**Fallback** 路径 = **隐蔽的性能双峰分布**。

---

**小白**：**点云**动不动几十万点，pool 怎么设？

**大师**：要么**降采样再上 ROS**、要么**分块 topic**、要么**压缩字段**（**pcl** 常规套路）。loan 不是「**无限免费午餐**」，**池耗尽**时行为要定义清楚（阻塞？drop？）。

---

**大师**：把 **zero-copy** 与 **node 聚合成 component** 一起设计——分拆进程越多，越难共享地址空间（**A01**）。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；本章需 **C++ 编译链** 与 **`~/ros2_ws`**。

```bash
source /opt/ros/humble/setup.bash
sudo apt install -y ros-humble-image-tools ros-humble-cv-bridge
```

> **说明**：**零拷贝 / intra-process** 能力因 **类型、RMW、进程模型** 而异；下列步骤以官方 **`image_tools`** 为参照（请以 Humble 文档为准）。

### 分步实现

#### 步骤 1：定位并阅读上游示例

- **目标**：不手写大块 C++，先跑通**官方** `image_tools` **intra-process** 管线。
- **命令**：

```bash
source /opt/ros/humble/setup.bash
ros2 pkg prefix image_tools
# 记录 share 路径下 launch / source 说明；对照 docs.ros.org 「image_tools intra process」
```

- **预期输出**：包路径存在，文档中有 **`talker`/`listener` 或 composable** 启动方式。
- **坑与解法**：若包名不同，请 **`apt search ros-humble-image`** 安装正确发行版包。

#### 步骤 2：构建工作空间（若从源码跟踪）

- **目标**：可选 — 将 **`image_tools`** fork 到 **ws/src** 便于打日志/`borrow_loaned_message`。
- **命令**：

```bash
mkdir -p ~/ros2_ws/src && cd ~/ros2_ws/src
git clone --depth 1 -b humble https://github.com/ros-demos/image_tools.git
cd ~/ros2_ws && colcon build --packages-select image_tools --symlink-install
source install/setup.bash
```

- **预期输出**：`colcon` **Summary: 1 package finished**。
- **坑与解法**：依赖缺失时按报错 `sudo apt install ros-humble-...`。

#### 步骤 3：关键 API 对齐

- **目标**：在源码中搜索 **`borrow_loaned_message` / `LoanedMessage`**，理解 **发布路径**。
- **示例片段（语义说明，非完整可编译文件）**：

```cpp
auto loaned = pub->borrow_loaned_message();
// fill loaned.get()
pub->publish(std::move(loaned));
```

- **预期输出**：能指出 **`publish(std::move(loaned))`** 与 **拷贝 `publish(msg)`** 分支差异。
- **坑与解法**：**loan 失败**时应走 **拷贝路径** —— 对照实现里的 **if (!loaned)** 分支。

#### 步骤 4：带宽与 CPU 粗测

- **目标**：用 **B04** 同款工具观察 **负载**（与 loan 是否启用**分开记录**）。
- **命令**（话题名以实际为准）：

```bash
ros2 topic bw /image
ros2 topic hz /image
```

- **预期输出**：**Hz × 消息大小** 与 **`bw`** 输出同量级；优化前后各保存一段 **终端文字**。
- **坑与解法**：无相机时可用 **`image_tools` 合成图**或 **bag play**（见 [M08](第33章：rosbag2 进阶-录制策略与回放测试.md)）。

### 完整代码清单

- **上游**：<https://github.com/ros-demos/image_tools> ，分支 **`humble`**（或与发行版同步 tag）。
- **RMW**：与 [A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md) 一致 **pin** 一种做对比实验。
- Git 外链占位：**待补充**。

### 测试验证

- **对照矩阵**：同一图像话题，在 **（进程模型 A / B）×（RMW 默认 / 调优）** 下采 **P50/P95 CPU**（**`top`** 或 **`pidstat`**），记录是否出现 **双峰延迟**（第二轮对话）。
- **验收**：能解释 **intra-process** 与 **跨进程 loan** 哪一步 **仍可能拷贝**。

---

## 4 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| CPU | 显著降 memcpy | 约束多 |
| 延迟 | 更稳定 | Debug 更难 |

### 思考题

1. **Intra-process** 与 **loan** 是否冲突？
2. **qos** 对 loan 的影响？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a03)； ros2_control [A04](第04章：ros2_control 与硬件接口分层.md)。

---

**导航**：[上一章：A02](第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A04](第04章：ros2_control 与硬件接口分层.md)
