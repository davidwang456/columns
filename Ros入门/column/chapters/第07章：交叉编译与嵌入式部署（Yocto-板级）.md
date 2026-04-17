# A07 · 交叉编译与嵌入式部署（Yocto/板级）

> 本章目标字数：3000–5000。统一环境见 [ENV.md](../ENV.md)。

## 1 项目背景

### 业务场景

车载 **ARM64** Ubuntu 与研发 x86 CI 不同：**交叉编译工具链** + **`CMAKE_SYSTEM_NAME`** + **sysroot** 中安装 ros2 **underlay**。或使用 **Yocto meta-ros** 打镜像。

### 痛点放大

1. **`PYTHON_EXECUTABLE`** 指错。
2. **第三方非AMENT** 库链接失败。
3. **glibc 版本**运行时不匹配。

**本章目标**：列 **交叉编译 colcon** 的高层步骤；**不推荐**手搓，优先参考 **RoboStack** / 厂商 BSP。

---

## 2 项目设计

### 剧本对话

**小胖**：交叉编译不就是配个 **`aarch64-linux-gnu-gcc`** 吗？我把笔记本当 sysfs 挂载过去行不行？

**小白**：**sysroot** 里为啥还要再装一份 `/opt/ros/humble`？我在板子上 apt 装过一遍了啊。

**大师**：交叉链路的关键是：**编译器生成的 ABI** 与 **目标机运行时库**一致。常见深坑：**sysroot 里 ROS underlay** 的版本与板上 deb **不一致**、`PYTHON_EXECUTABLE` 指到宿主 Python、`CMAKE_FIND_ROOT_PATH` 漏项导致 **find_package 抓到宿主**。**在板上直接 colcon** 简单但不适合 CI；**交叉**适合大规模产线，但 upfront 成本高。**vendor BSP** 或 **meta-ros** 往往给出**已验证组合**。

**技术映射**：**sysroot** = **目标世界的根视图**；**toolchain file** = **隔离宿主泄漏**。

---

**小胖**：**Yocto** 和「**Ubuntu ARM 上源码编**」怎么选？

**大师**：前者胜在**可重现镜像与许可证治理**；后者胜在**与人类直觉最接近、资料多**。团队若没 **嵌入式发行版维护人员**，别强行上 Yocto；但要写清**安全更新**谁负责。

**技术映射**：**交付形态**（rootfs）决定**运维接口**。

---

**大师**：验证阶段用 **qemu-user**/**chroot** 跑测试比「祈祷上电」更接近工程。

---

## 3 项目实战

### 环境准备

与 [ENV.md](../ENV.md) 一致；**宿主编译机**为 **x86_64 Ubuntu 22.04**。本章以 **「文档化流程 + 最小验证」** 为主，**不强制**你在无 ARM 板场景下完成全链路交叉编译。

附加工具：

```bash
sudo apt install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
  qemu-user-static binfmt-support cmake
```

### 分步实现

#### 步骤 1：阅读官方交叉编译入口

- **目标**：建立 **权威步骤清单**，避免手搓踩坑。
- **操作**：浏览器打开 **ROS 2 文档** 中 **Building ROS 2 / Cross-compilation**（随发行版迁移的 URL，以 **docs.ros.org** 搜索 **cross compile** 为准），**打印或书签**一页 **Checklist**。
- **预期输出**：理解 **`CMAKE_TOOLCHAIN_FILE`**、**sysroot**、**COLCON_IGNORE** 等关键词在何步骤出现。
- **坑与解法**：Wiki 与 **Humble** 不完全一致时，**以你目标发行版文档为准**。

#### 步骤 2：准备最小 sysroot（示意）

- **目标**：体会 **`find_package` 为何抓到宿主**。
- **命令（概念演示，路径自定）**：

```bash
mkdir -p ~/cross/sysroot
# 实际工程常: debootstrap / rsync 根文件系统 / 使用厂商 SDK
```

- **预期输出**：**`sysroot/opt/ros/humble`** 若有，应与 **目标机 deb 版本**对齐。
- **坑与解法**：**版本漂移**是第一大雷 —— 锁 **apt snapshot** 或 **镜像 digest**。

#### 步骤 3：用 qemu-user 试跑目标 ELF（无板场景）

- **目标**：验证 **二进制 ABI** 与 **动态链接**。
- **命令**：

```bash
# 若你从厂商拿到 aarch64 可执行文件 hello_aarch64:
file hello_aarch64
qemu-aarch64-static -L ~/cross/sysroot ./hello_aarch64
```

- **预期输出**：程序正常输出；若 **`loader / libc` 找不到**，说明 **sysroot 不完整**。
- **坑与解法**：`qemu-user` **不等于**真实硬件实时性（**A09**）。

#### 步骤 4：交叉 `colcon`（选做 / 高风险）

- **目标**：仅适合有 **BSP** 支持读者 —— 在 **toolchain file** 就绪后：

```bash
# 示意；具体变量以官方为准
# colcon build --cmake-force-system-cmake \
#   --cmake-args -DCMAKE_TOOLCHAIN_FILE=/path/to/toolchain.cmake
```

- **预期输出**：`install/` 下生成 **aarch64** 前缀产物。
- **坑与解法**：失败时**优先对比** **PYTHON_EXECUTABLE**、**SYSROOT**、**OpenSSL** 路径。

### 完整代码清单

- **官方文档**：ROS 2 **Cross-compilation** 章节（URL 随版本变化）。
- **工具链**：`gcc-aarch64-linux-gnu`、厂商 **BSP**、或 **Yocto SDK environment**。
- **无统一 Git 仓库**；嵌入式交付常 **客户私有**。

### 测试验证

- **最低验收**：能解释 **`file`、`readelf -d`、`ldd`**（在目标机或 qemu 环境）三者之一输出含义。
- **进阶**：在 **qemu** 下跑通 **`ros2 doctor`**（若完整 rootfs）—— 证明 **sysroot 完整度**。

---

## 4 项目总结

### 思考题

1. **sysroot** 里需要哪些 **APT** 前缀？
2. **strip symbols** 对排障影响？

**答案**：见 [APPENDIX-answers.md](../APPENDIX-answers.md#a07)；ament [A08](第08章：构建系统深入-ament、依赖与私有仓库.md)。

---

**导航**：[上一章：A06](第06章：自定义 Action-复杂状态机与容错.md) ｜ [总目录](../INDEX.md) ｜ [下一章：A08](第08章：构建系统深入-ament、依赖与私有仓库.md)
