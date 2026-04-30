# 第08章：构建系统深入-ament、依赖与私有仓库

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

> **版本**：ROS 2 Humble（Ubuntu 22.04，统一环境见 [ENV.md](../ENV.md)）
> **定位**：高级篇 · 面向架构师与资深开发，强调源码边界、极端场景与生产取舍。
> **前置阅读**：建议先完成基础篇与中级篇相关章节，尤其关注执行器、QoS、Launch、观测性与 SRE。
> **预计阅读**：45 分钟 | 实战耗时：90–150 分钟

## 1. 项目背景

### 业务场景

公司内部 **Fork** 上游 **nav2**，同时_vendor 第三方 **非公开 deb** ——需要 **ament** 下 **`ament_target_dependencies`** + **private Git URL** + **`vcs import`** 管理多仓。CI 里 **`rosdep`** 与 **内部 apt mirror** 对齐。

### 痛点放大

1. **`find_package` 顺序**错误。
2. **`BUILD_TESTING` OFF** 跳过必要检查。
3. **`package.xml` `<depend>` 与 CMake 不一致**。

**本章目标**：`rosdep` 工作流；**`repomixins`**；**私有** `package.xml` **doc + url** 标签规范。

---

### 业务指标与交付边界

本章不追求“把所有概念一次讲完”，而是交付一个可复现的工程切片：

1. **可运行**：至少有一组命令、脚本或配置能够在 Humble 环境中执行。
2. **可观察**：运行后能用 `ros2` CLI、日志、RViz、rosbag2 或系统工具看到明确现象。
3. **可交接**：读者能把 **构建系统深入-ament、依赖与私有仓库** 的关键假设、输入输出、失败模式写进项目 README 或排障手册。

**本章交付目标**：完成一个围绕 **构建系统深入-ament、依赖与私有仓库** 的最小闭环，并留下可复盘的命令、截图或日志证据。

## 2. 项目设计

### 总体架构图

```mermaid
flowchart LR
  requirement[业务需求] --> concept["构建系统深入-ament、依赖与私有仓库"]
  concept --> config[配置与代码]
  config --> runtime[运行时观测]
  runtime --> verify[测试验证]
  verify --> runbook[交付与复盘]
```

这张图用于对齐 `example.md` 的“端到端项目链路”写法：先从业务需求出发，再落到配置/代码，最后用观测与验收把结论闭环。

### 剧本对话

**小胖**：**package.xml** 和 **CMakeLists** 都要写依赖，是不是有手滑一遍就炸？

**小白**：我们内部 **apt 私服** 和 **Git 子模块**混用，`rosdep` 到底信谁？

**大师**：**ament** 的灵魂是「**单一事实来源尽量落在 package.xml**」，CMake 侧用 **`ament_target_dependencies`** 对齐——否则 **CI 与本地** 各成功一半。**私有仓库**常见模式：**`vcs import` 锁版本** + **`.repos` 文件**进评审；**rosdep** 规则里 **yaml 映射**到内部 key。社会工程部分：**OWNER**、**升级窗口**、**谁有权限 merge 到 release 分支**。

**技术映射 #1**：**package.xml** = **依赖声明**；**rosdep** = **键→安装命令** 的可配置解析器。

---

**小胖**：**`BUILD_TESTING=OFF`** 能加快编一夜，为啥不默认全关？

**大师**：关掉的是 **ament_lint / unit tests**，短期快，长期负债。**导航安全关键模块**应把 **lint+单测**视作**合并门槛**，而不是「发布前补」。

**技术映射 #2**：**技术债利息** vs **迭代速度**。

---

**大师**：**Fork nav2** 时显式记录 **upstream tag** 与**补丁原因**（ADR），否则半年后没人敢合并上游。

---

## 3. 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；准备一个 **可写入的 `~/ros2_ws/src`**，内含 **至少一个** ament 包（可用 [B02](第14章：工作空间、包与 colcon-可复现构建.md) 的 **`cpp_demo`**）。

```bash
sudo apt install -y python3-rosdep python3-vcstool
sudo rosdep init 2>/dev/null || true
rosdep update
```

**项目目录结构**（建议随章落地到自己的工作区）：

```text
ros2_ws/
  src/
    构建系统深入_ament_依赖与私有仓库/
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

#### 步骤 1：仓库级依赖安装（公开键）

- **目标**：跑通 **`rosdep install --from-paths`** 默认路径。
- **命令**：

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws
rosdep install --from-paths src --ignore-src -r -y
```

- **预期输出**：终端显示 **`All required rosdeps`** 或明确 **`MISSING`** 列表。
- **坑与解法**：**private deb** 会失败 —— 进入步骤 2。

#### 步骤 2：模拟私有映射 — `rosdep` 规则文件（示意）

- **目标**：理解 **`yaml rule`** 如何把 **`my_private_lib`** 映射到 **`apt` 命令**。
- **操作**：在 **可写目录** 建 **`local_rosdep.yaml`**（**示例，勿用于生产密钥**）：

```yaml
# 示意：实际 key 名需与 package.xml  rosdep 键一致
my_fake_vendor_sdk:
  ubuntu:
    focal: [curl]
```

- **命令**：

```bash
export ROSDEP_SOURCES_PATH="$HOME/rosdep_private:$ROSDEP_SOURCES_PATH"
mkdir -p ~/rosdep_private
echo "yaml file://$HOME/local_rosdep.yaml" > ~/rosdep_private/50-private.list
rosdep update --include-eol-distros
rosdep resolve my_fake_vendor_sdk
```

- **预期输出**：`rosdep resolve` **找到映射**（若键存在）；否则仅作语法演示。
- **坑与解法**：团队应在 **内网 git** 维护 **`rosdep`** 规则 + **评审流程**。

#### 步骤 3：`vcs import` 锁多仓版本

- **目标**：对应 **`.repos` 文件**协作（剧本中的社会工程部分）。
- **操作**：自建 **`deps.repos`**（**示例**：只拉 **一个** 小型公共包，勿用整仓 **ros2.repos** 以免海量下载）：

```yaml
repositories:
  turtle_tutorial_substitute:
    type: git
    url: https://github.com/ros2/examples.git
    version: humble
```

- **命令**：

```bash
cd ~/ros2_ws
cat > deps.repos <<'EOF'
repositories:
  examples:
    type: git
    url: https://github.com/ros2/examples.git
    version: humble
EOF
vcs import src --input deps.repos
```

- **预期输出**：`~/ros2_ws/src/examples` **clone** 成功（或已存在则更新策略见 `vcs` 文档）。
- **坑与解法**：生产应使用 **你们自己的 `.repos`** + **tag/hash 锁死**；勿在弱网下 **递归整桌**。

#### 步骤 4：**ament** Lint（建议）

- **目标**：把 **`BUILD_TESTING=ON`** 的价值落到一行命令。
- **命令**：

```bash
cd ~/ros2_ws
colcon test --packages-select cpp_demo --event-handlers console_direct+ 2>/dev/null || true
```

- **预期输出**：若无测试则跳过；若有 **ament_lint** 则输出报告。
- **坑与解法**：`BUILD_TESTING=OFF` 会跳过 —— **与剧本反思对照**。

### 完整代码清单

- **`rosdep` 私有规则**：建议 **独立 git 仓** + **CI 校验 YAML 语法**。
- **`vcs` `.repos`**：与 **产品 BOM** 同步（版本、分支、hash）。
- Git 外链：**待补充**。

### 交付物清单

- **README**：说明 **构建系统深入-ament、依赖与私有仓库** 的业务背景、运行命令、预期输出与常见失败。
- **配置/代码**：保留本章涉及的 launch、YAML、脚本或源码片段，避免只存截图。
- **证据材料**：至少保留一份终端输出、RViz 截图、rosbag2 片段、trace 或日志摘录。
- **复盘记录**：记录“为什么这样配置”，尤其是 QoS、RMW、TF、namespace、安全和性能相关取舍。

### 测试验证

- **验收**：**新 clone 机器**上 **`rosdep install` + `colcon build` 无人工步骤**（或仅一次 **`apt` mirror 指印`**）。
- **回归**：升级 **上游 nav2 fork** 时，**`.repos` diff** 可 review（剧本 ADR）。

### 验收清单

- [ ] 能在干净终端重新 `source /opt/ros/humble/setup.bash` 后复现本章命令。
- [ ] 能指出 **构建系统深入-ament、依赖与私有仓库** 的核心输入、输出、关键参数与失败边界。
- [ ] 能把至少一条失败案例写成“现象 → 排查命令 → 根因 → 修复”的四段式记录。
- [ ] 能说明本章内容与相邻章节的依赖关系，避免把单点技巧误当成系统方案。

---

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 工程价值 | **构建系统深入：ament、依赖与私有仓库** 能把隐性经验显式化，便于新人复现与团队协作。 | 需要配套环境、日志与记录表，否则容易停留在概念层。 |
| 可维护性 | 通过标准命名、参数、Launch 或测试约束降低沟通成本。 | 规则一多会增加初期学习曲线。 |
| 可观测性 | 便于用 CLI、日志、bag 或监控指标定位问题。 | 指标若没有业务阈值，仍可能变成“看起来很多但不能决策”。 |
| 扩展性 | 可与前后章节串联，逐步走向真实系统。 | 跨 RMW、跨发行版或跨硬件时需要重新验证边界。 |

### 适用场景

- 团队需要把 **构建系统深入：ament、依赖与私有仓库** 从个人经验沉淀为可复用流程。
- 新人、测试与运维需要用同一套命令与术语对齐问题现象。
- 项目进入联调阶段，需要记录参数、话题、日志与验收结果。
- 需要为后续源码阅读、性能优化或生产复盘提供上下文。

### 不适用场景

- 只做一次性演示且不需要交接、回归或复盘的临时脚本。
- 现场约束尚未明确时，不宜把 **构建系统深入：ament、依赖与私有仓库** 的示例参数直接当作生产标准。

### 注意事项

- **版本兼容**：所有命令以 Humble 与 [ENV.md](../ENV.md) 为基线，其他发行版需查 `--help` 与官方文档。
- **配置边界**：不要把实验参数直接带入生产；先记录硬件、RMW、QoS、网络与时钟条件。
- **安全边界**：涉及远程调试、容器权限、证书或硬件接口时，先按最小权限原则收敛。

### 常见踩坑经验

1. **只看现象不记录环境**：同一命令在不同 RMW、Domain、QoS 或硬件上结果不同。根因通常是缺少版本与环境快照。
2. **一次改多个变量**：参数、Launch、网络与代码同时变化，导致无法归因。解决方法是每次只改一项并保存日志或 bag。
3. **忽略跨角色交接**：开发能跑通但测试/运维无法复现。根因是缺少最小验收命令、预期输出与失败处理路径。

### 思考题

1. **`test_depend` 泄漏**到生产 install？
2. **`ament_lint`** 在 CI 的必跑集？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a08)；实时 [A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md)。

### 推广计划提示

- **开发**：把 **构建系统深入：ament、依赖与私有仓库** 的最小 demo、关键参数与失败日志写入项目 README。
- **测试**：抽取 1–2 条可重复的 smoke 用例，记录输入、预期输出与回归频率。
- **运维**：整理运行环境、启动命令、日志位置与告警阈值，便于现场排障。

---

**导航**：[上一章：A07](第07章：交叉编译与嵌入式部署（Yocto-板级）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md)

> **本章完**。你已经完成 **构建系统深入-ament、依赖与私有仓库** 的端到端学习：从业务场景、设计对话、实战命令到验收清单。下一步建议把本章交付物纳入自己的 ROS 2 工作区，并在后续章节中持续复用同一套 README、配置和测试记录方式。
