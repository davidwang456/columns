# 第36章：容器化（Docker）与最小 CI

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

研发 Mac/Win，目标部署 Ubuntu ARM——**Docker 镜像**固化 **ROS Humble** + 依赖 deb；**GitHub Actions / GitLab CI** `docker run` 执行 `colcon build && colcon test`。

### 痛点放大

1. **GPU / GLX** 绑定显示复杂。
2. **设备 /dev/* ** 进容器映射。
3. **缓存层** bust 导致 CI 慢。

**本章目标**：`Dockerfile` 样例；**multi-stage build**；CI 缓存 apt。

---

## 2 项目设计

### 剧本对话

**小胖**：Docker 里跑 **Gazebo + GPU** 为啥永远比同事本机卡？

**小白**：CI 里 **`docker build` 七分钟**、人家 **ccache** 为啥一秒过？

**大师**：容器解决「**依赖一致**」，不自动解决「**性能等同**」。**GUI/GL/GPU** 需要 **nvidia-container-toolkit**、**X11/WSLg** 等一堆绑定；**实时性**在容器里常被打折扣（cgroup、额外层）。CI 侧：**分层镜像** + **apt/ccache 挂载** + **不每次从源编译整个 universe**。**不要把容器当实时RTOS**（**A09**）。

**技术映射**：**容器** = **交付与测试环境的冻结**。

---

**小胖**：`/dev/ttyUSB0` 映射进去了，还是读不到激光？

**大师**：除了设备节点，还要看 **udev 权限**、**用户组**、有时 **UVC/串口**在热插拔后被改名——这些是**运维脚本**层面，与 ROS 无直接关系。**privileged** 一时爽，**安全风险**要评估。

**技术映射**：**设备直通** = **内核权限模型** + **命名稳定策略**。

---

**大师**：**镜像 digest** 打进发布说明里，才有**可复盘事故现场**的价值。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致：**Ubuntu 22.04** 作 **Docker 宿主机**；安装 **Docker Engine**（或 **Podman**），用户加入 **`docker` 组**（或等效 rootless）。

本章额外依赖：**基础镜像** 选用 **`ros:humble-ros-base-jammy`** 或 **OSRF** 维护镜像（与团队策略一致；**digest** 固定见本章正文）。

### 分步实现

#### 步骤 1：编写最小 `Dockerfile`

- **目标**：**可复现构建**：**同一 Dockerfile** 在 **同事机/CI** 产出 **相同依赖层**。
- **内容**：

```dockerfile
FROM ros:humble-ros-base-jammy
RUN apt-get update && apt-get install -y python3-colcon-common-extensions
WORKDIR /ws
COPY . /ws/src
RUN /bin/bash -c "source /opt/ros/humble/setup.bash && colcon build"
```

- **预期输出**：`docker build` **exit 0**；镜像内 **`/ws/install`** 存在。
- **坑与解法**：**COPY 过大** → **.dockerignore** 排除 **`build/`、`install/`、`.git`**；**网络** 失败 → **重试/代理**。

#### 步骤 2：本地验证运行

- **目标**：确认 **运行时** `source install/setup.bash` **与线上一致**。
- **命令**：

```bash
docker run --rm -it YOUR_IMAGE bash
# 容器内: source /ws/install/setup.bash && ros2 pkg list | head
```

- **预期输出**：**包列表** 含你的工作空间包。
- **坑与解法**：**无显示/GPU** → **`--device` / `nvidia-container-toolkit`**（若需要）；**privileged** 安全风险（思考题）。

#### 步骤 3：CI（GitHub Actions 示意）

- **目标**：**PR 即构建**，**镜像 tag** 或 **构建日志** 可审计。
- **配置**：

```yaml
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t robostack .
```

- **预期输出**：CI **绿色**；**Artifacts** 可选 **镜像 digest**。
- **坑与解法**：**Runner 磁盘** 满 → **缓存策略**；**多架构** → **buildx**（超出本章可链官方文档）。

### 完整代码清单

- **`Dockerfile`** + **`.dockerignore`**。
- **`.github/workflows/build.yml`**（占位）或 **GitLab CI** 等价物。
- **发布说明模板**：**镜像 digest**、**基础镜像版本**（本章剧本）。

### 测试验证

- **本地**：`docker build` **连续两次** 第二次 **命中缓存**（合理分层时）。
- **手工验收**：**新同事** 仅按 **README 三步** 可 **build + run**（与 [ENV.md](../ENV.md) 交叉引用）。

---

## 4 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 工程价值 | **容器化（Docker）与最小 CI** 能把隐性经验显式化，便于新人复现与团队协作。 | 需要配套环境、日志与记录表，否则容易停留在概念层。 |
| 可维护性 | 通过标准命名、参数、Launch 或测试约束降低沟通成本。 | 规则一多会增加初期学习曲线。 |
| 可观测性 | 便于用 CLI、日志、bag 或监控指标定位问题。 | 指标若没有业务阈值，仍可能变成“看起来很多但不能决策”。 |
| 扩展性 | 可与前后章节串联，逐步走向真实系统。 | 跨 RMW、跨发行版或跨硬件时需要重新验证边界。 |

### 适用场景

- 团队需要把 **容器化（Docker）与最小 CI** 从个人经验沉淀为可复用流程。
- 新人、测试与运维需要用同一套命令与术语对齐问题现象。
- 项目进入联调阶段，需要记录参数、话题、日志与验收结果。
- 需要为后续源码阅读、性能优化或生产复盘提供上下文。

### 不适用场景

- 只做一次性演示且不需要交接、回归或复盘的临时脚本。
- 现场约束尚未明确时，不宜把 **容器化（Docker）与最小 CI** 的示例参数直接当作生产标准。

### 注意事项

- **版本兼容**：所有命令以 Humble 与 [ENV.md](../ENV.md) 为基线，其他发行版需查 `--help` 与官方文档。
- **配置边界**：不要把实验参数直接带入生产；先记录硬件、RMW、QoS、网络与时钟条件。
- **安全边界**：涉及远程调试、容器权限、证书或硬件接口时，先按最小权限原则收敛。

### 常见踩坑经验

1. **只看现象不记录环境**：同一命令在不同 RMW、Domain、QoS 或硬件上结果不同。根因通常是缺少版本与环境快照。
2. **一次改多个变量**：参数、Launch、网络与代码同时变化，导致无法归因。解决方法是每次只改一项并保存日志或 bag。
3. **忽略跨角色交接**：开发能跑通但测试/运维无法复现。根因是缺少最小验收命令、预期输出与失败处理路径。

### 思考题

1. **privileged** 容器安全风险？
2. 为何常选 **`osrf/ros`** vs 官方 **`library/ros`**？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#m11)；多机 [M12](第37章：多机器人协同与通信隔离.md)。

### 推广计划提示

- **开发**：把 **容器化（Docker）与最小 CI** 的最小 demo、关键参数与失败日志写入项目 README。
- **测试**：抽取 1–2 条可重复的 smoke 用例，记录输入、预期输出与回归频率。
- **运维**：整理运行环境、启动命令、日志位置与告警阈值，便于现场排障。

---

**导航**：[上一章：M10](第35章：可观测性-tracing、诊断话题与仪表盘.md) ｜ [总目录](../INDEX.md) ｜ [下一章：M12](第37章：多机器人协同与通信隔离.md)
