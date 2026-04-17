# A08 · 构建系统深入：ament、依赖与私有仓库

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

公司内部 **Fork** 上游 **nav2**，同时_vendor 第三方 **非公开 deb** ——需要 **ament** 下 **`ament_target_dependencies`** + **private Git URL** + **`vcs import`** 管理多仓。CI 里 **`rosdep`** 与 **内部 apt mirror** 对齐。

### 痛点放大

1. **`find_package` 顺序**错误。
2. **`BUILD_TESTING` OFF** 跳过必要检查。
3. **`package.xml` `<depend>` 与 CMake 不一致**。

**本章目标**：`rosdep` 工作流；**`repomixins`**；**私有** `package.xml` **doc + url** 标签规范。

---

## 2 项目设计

### 剧本对话

**小胖**：**package.xml** 和 **CMakeLists** 都要写依赖，是不是有手滑一遍就炸？

**小白**：我们内部 **apt 私服** 和 **Git 子模块**混用，`rosdep` 到底信谁？

**大师**：**ament** 的灵魂是「**单一事实来源尽量落在 package.xml**」，CMake 侧用 **`ament_target_dependencies`** 对齐——否则 **CI 与本地** 各成功一半。**私有仓库**常见模式：**`vcs import` 锁版本** + **`.repos` 文件**进评审；**rosdep** 规则里 **yaml 映射**到内部 key。社会工程部分：**OWNER**、**升级窗口**、**谁有权限 merge 到 release 分支**。

**技术映射**：**package.xml** = **依赖声明**；**rosdep** = **键→安装命令** 的可配置解析器。

---

**小胖**：**`BUILD_TESTING=OFF`** 能加快编一夜，为啥不默认全关？

**大师**：关掉的是 **ament_lint / unit tests**，短期快，长期负债。**导航安全关键模块**应把 **lint+单测**视作**合并门槛**，而不是「发布前补」。

**技术映射**：**技术债利息** vs **迭代速度**。

---

**大师**：**Fork nav2** 时显式记录 **upstream tag** 与**补丁原因**（ADR），否则半年后没人敢合并上游。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；准备一个 **可写入的 `~/ros2_ws/src`**，内含 **至少一个** ament 包（可用 [B02](第14章：工作空间、包与 colcon-可复现构建.md) 的 **`cpp_demo`**）。

```bash
sudo apt install -y python3-rosdep python3-vcstool
sudo rosdep init 2>/dev/null || true
rosdep update
```

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

### 测试验证

- **验收**：**新 clone 机器**上 **`rosdep install` + `colcon build` 无人工步骤**（或仅一次 **`apt` mirror 指印`**）。
- **回归**：升级 **上游 nav2 fork** 时，**`.repos` diff** 可 review（剧本 ADR）。

---

## 4 项目总结

### 思考题

1. **`test_depend` 泄漏**到生产 install？
2. **`ament_lint`** 在 CI 的必跑集？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a08)；实时 [A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md)。

---

**导航**：[上一章：A07](第07章：交叉编译与嵌入式部署（Yocto-板级）.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A09](第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md)
