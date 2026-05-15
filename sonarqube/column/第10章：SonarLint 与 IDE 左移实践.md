# 第10章：SonarLint 与 IDE 左移实践

## 1. 项目背景

**业务场景**：某公司研发团队接入 SonarQube 三个月后，发现一个令人沮丧的统计：QA 阶段发现的 Bug 减少了 40%，但开发者对 SonarQube 的满意度反而下降了。调查发现原因：开发者每次提交 PR 后，CI 需要跑 8 分钟才能反馈扫描结果。如果 Quality Gate 不通过，开发者需要等 8 分钟才知道要修什么，修完再等 8 分钟——一个简单的修复可能消耗半小时等待时间。

更糟糕的是，一些开发者开始"赌博式提交"——先提交再看结果，违反了"Commit => CI Gate => Merge"本该有的流畅体验。团队 Leader 反思：SonarQube 的质量反馈不能只在 CI 阶段，应该**前置到编码阶段**——让开发者在保存文件时就知道自己写的代码有什么问题。

这就是"左移"（Shift Left）的核心思想：把质量检查从"提测后"（右）移动到"编码时"（左），越早发现问题，修复成本越低。

**痛点放大**：

- **反馈延迟**：CI 扫描需要 5-15 分钟，开发者等待成本高
- **上下文丢失**：等 CI 反馈回来，开发者可能已经切换到另一个任务，再回头修 Bug 成本翻倍
- **质量门禁的"惊吓效应"**：开发者提交时信心满满，结果 CI 报出 15 个新 Issue，从"以为写完了"变成"又要修一堆"
- **SonarQube 和 IDE 脱节**：SonarQube 的规则和本地 IDE 的 Lint 规则不一致，开发者困惑"我 IDE 里没报错，为什么 CI 上红了？"

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（在 IDE 里写完代码，兴冲冲提交 PR，10 分钟后收到 CI 失败邮件）："啊！又是 Quality Gate Failed！而且这次的 Issue 是我方法复杂度过高——我在写的时候 IDE 里没有提醒我啊！如果早知道复杂度过高，我写的时候就拆分了！"

**大师**："小胖，你需要装上 SonarLint——SonarQube 的 IDE 插件。它就像你车上的倒车雷达，在停车时（写代码时）就哔哔哔提醒你——不用等到撞上了才后悔。"

**小白**："我装过 SonarLint，但它和 SonarQube 服务器是两套规则——我本地通过了，CI 上还是报错。这两个怎么同步？"

**大师**："你需要开启 Connected Mode。SonarLint 有两种工作模式：

1. **Standalone Mode（独立模式）**：使用本地内置的默认规则集。这些规则是 SonarSource 预设的"基线规则"，但不是你在服务器上定制的团队规则集。
2. **Connected Mode（连接模式）**：SonarLint 连接到你团队的 SonarQube 服务器，同步服务器的 Quality Profile 和规则配置。你开启 Connected Mode 后，IDE 里显示的 Issue 和 CI 上显示的应该是同一个标准。

简单说：独立模式是默认规则，连接模式是团队规则。**团队的 SonarLint 必须用 Connected Mode**。"

**小胖**："我设置了 Connected Mode，但它提示我不支持某些规则？"

**大师**："对。不是所有服务器规则都能在 IDE 中实时检查。有些规则需要跨文件分析（如检测未使用的 public 方法）、需要字节码分析（如检测废弃 API 的使用）、或者依赖项目完整 classpath——这些在 IDE 单文件编辑场景下无法执行。这类规则的本地点火只会在你打开文件时执行，而不是实时按字符触发。

不过好消息是，SonarLint 支持检查的规则正在不断增多——目前 Java 大约 70% 的规则支持本地检查。"

**小白**："还有一个问题：我在 IDE 中看到了几百个 SonarLint 警告——但其中很多是历史代码的问题，不是我这次修改引入的。怎么让它只显示我改动的代码的问题？"

**大师**："这正是 SonarLint 最新版本的'焦点模式'。有几个设置可以帮助你：

1. **只显示新增 Issue**：在 SonarLint 设置中开启 "Focus on New Code"，它只会检查你当前修改的文件中新增的代码行——和 SonarQube 的 New Code 概念一致。
2. **按文件过滤**：只开启当前打开文件的检查，历史文件不做全量扫描。
3. **绑定到特定 Quality Gate 条件**：只显示那些会触发 Quality Gate 失败的 Issue 类型（Bug、Vulnerability、Security Hotspot）——而 Code Smell 可以先不管。

其中第三点特别有用——如果你只关心'会不会被门禁拦截'，就只看 Blocker/Critical 的 Bug 和 Vulnerability。"

**小胖**："那 SonarLint 发现的 Issue，和我在 SonarQube 网站上报的 Issue，能关联在一起吗？比如我修了一个 SonarLint 报的问题，提交后 CI 上也显示这个 Issue 被修复了？"

**大师**："不能直接关联。SonarLint 和 SonarQube 之间没有 Issue ID 的映射——它们各自独立分析，各自产生 Issue。但效果上是一致的：你在 SonarLint 中修复了一个除零 Bug，提交后 CI 扫描也不会再报这个 Bug，因为代码已经不存在这个问题了。

可以把 SonarLint 理解为'本地预检'，SonarQube 是'正式体检'。本地预检合格，正式体检大概率合格。"

---

## 3. 项目实战

### 3.1 环境准备

- IntelliJ IDEA 2023.3+（Community 或 Ultimate）
- 或 VS Code 1.85+
- SonarQube 实例（10.x+），已登录账号

### 3.2 IntelliJ IDEA 安装与配置

**步骤 1：安装 SonarLint 插件**

1. 打开 IntelliJ IDEA → Settings → Plugins
2. 搜索 "SonarLint"
3. 点击 Install → 重启 IDE

**步骤 2：配置 Connected Mode**

1. 打开 Settings → Tools → SonarLint
2. 点击 "+" 添加连接
3. 选择 "SonarQube"，填写：
   - Connection Name: `Company SonarQube`
   - SonarQube URL: `http://localhost:9000`
4. 认证方式选择 "Token"，粘贴你的 SonarQube Token
5. 点击 Next → 选择要绑定的项目（如 `com.example:order-service`）
6. 点击 Finish

绑定成功后，SonarLint 会自动下载该项目的 Quality Profile 和规则配置。

**步骤 3：验证 Connected Mode**

1. 打开任意 Java 文件
2. 故意写一段问题代码，如：

```java
public void buggyMethod() {
    String password = "admin123"; // 硬编码密码
    System.out.println(password);
}
```

3. SonarLint 应在行号左侧显示 🔴 或 🟡 标记
4. 将鼠标悬停在标记上，查看 Issue 详情——应显示来自服务器同步的规则信息

**步骤 4：使用 SonarLint 分析面板**

打开 SonarLint 工具窗口（View → Tool Windows → SonarLint）：

- **Current File** 页签：显示当前打开文件的所有 Issue
- **Report** 页签：显示当前项目的 Issue 汇总
- **Security Hotspots** 页签：本地安全热点（Connected Mode 下可见）
- **Rules** 页签：查看当前激活的规则集

**步骤 5：设置焦点模式（Focus on New Code）**

Settings → Tools → SonarLint → 勾选 "Focus on New Code"。

然后配置 New Code 的判定基准：
- 选择 "Since the last analysis on SonarQube/SonarCloud"
- SonarLint 会自动和服务器同步 New Code Period 的设置

### 3.3 VS Code 安装与配置

**步骤 1：安装扩展**

1. 打开 VS Code → Extensions (Ctrl+Shift+X)
2. 搜索 "SonarLint"（发布者：SonarSource）
3. 点击 Install

**步骤 2：配置 Connected Mode**

1. 按 Ctrl+Shift+P → 输入 "SonarLint: Add SonarQube Connection"
2. 输入 SonarQube URL: `http://localhost:9000`
3. 选择认证方式：Token
4. 输入 Token
5. 选择要绑定的项目

绑定后，VS Code 的 SonarLint 面板会自动更新，显示服务器同步的规则。

### 3.4 验证 Connected Mode 同步效果

创建一个符合服务器规则的测试文件：

```java
public class ConnectedModeTest {
    // 如果有硬编码密码规则 → Vulnerability
    private String apiKey = "prod-secret-key-2024";

    // 如果有规则禁止 printStackTrace → Code Smell
    public void riskyCode() {
        try {
            int result = 100 / 0;
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
```

验证步骤：

1. 本地 SonarLint 显示上述 Issue
2. 提交代码 → CI 触发 SonarQube 扫描
3. CI 扫描结果中的 Issue 与本地 SonarLint 提示的 Issue **类型一致**
4. 修复后重新扫描，本地和 CI 的 Issue 都消失

### 3.5 SonarLint 高级使用技巧

**技巧 1：快速修复建议**

SonarLint 支持 Auto-Fix 的规则会在 Issue 详情中显示 "Quick Fix" 按钮——点击即可自动应用修复（如将 `==` 替换为 `equals()`）。

**技巧 2：抑制误报**

如果 SonarLint 对某行报误报，在行末添加注释：

```java
@SuppressWarnings("java:S2065") // 抑制 transient 规则
private transient Object cache;
```

或在 SonarLint 面板中右键 Issue → "Suppress for this line"。

**技巧 3：团队同步通知**

Connected Mode 会在 SonarLint 面板中显示来自服务器的通知——如 Quality Profile 更新、新增规则、Severity 调整。

### 3.6 验证

```bash
# 确认绑定成功（通过 API 检查项目连接状态）
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/project_links/search?projectKey=com.example:order-service" \
  | python3 -m json.tool
```

在 IDE 中，SonarLint 工具面板底部的连接状态应显示 "Connected to SonarQube"。

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | SonarLint（Connected Mode） | 仅 SonarQube CI 扫描 |
|------|---------------------------|---------------------|
| 反馈速度 | 秒级（保存即检测） | 分钟级（CI 流水线完成） |
| 规则一致性 | ✅ 与服务器规则同步 | ✅ 自身一致 |
| 离线可用性 | ✅ 无需网络 | ❌ 必须连接 CI |
| 修复成本 | 低（编码时即修） | 高（切换任务再修） |
| 规则覆盖率 | 🟡 ~70%（有些规则不支持本地） | ✅ 100% |
| 团队学习成本 | 中（需配置 Connected Mode） | 低（CI 自动跑） |
| 干扰程度 | 高（一直在眼前提醒） | 低（提交后才知） |

### 4.2 适用场景

- **所有使用 IDE 的开发者**：SonarLint 是基本配置，应纳入"新员工入职必装工具"清单
- **前后端全栈团队**：同时安装 IntelliJ（后端）+ VS Code（前端）的 SonarLint
- **TDD/敏捷团队**：编码-反馈循环极短的开发模式
- **合规要求严格的团队**：安全规则在编码时即检查

**不适用场景**：
- 非 IDE 环境（如使用 vim/emacs 裸编辑的开发者）→ 考虑 SonarLint CLI
- CI 环境（CI 应使用完整的 SonarScanner）

### 4.3 注意事项

1. **Connected Mode 需要 Token**：Token 不能硬编码在 IDE 配置中（会被 Git 泄露）。使用 IDE 的密码管理器或环境变量注入。
2. **首次同步规则可能较慢**：Quality Profile 中的数百条规则都需要下载，首次连接可能需要 1-2 分钟。
3. **不要过度依赖 Auto-Fix**：自动修复只适用于简单、确定性的规则（如命名规范）。涉及逻辑变更的 Issue 必须人工判断。
4. **SonarLint 不替代 Code Review**：机器能检查规则问题，但不能替代人类对业务逻辑正确性的判断。

### 4.4 常见踩坑经验

**故障 1：Connected Mode 绑定失败，提示"Invalid token"或"Project not found"**

根因：Token 权限不足（需要 Browse 权限）或项目 Key 不存在。确认 Token 有 "Browse" 权限，确认 `projectKey` 准确无误。

**故障 2：绑定了 Connected Mode，但 SonarLint 仍使用本地规则**

根因：Connected Mode 绑定后需要手动在项目上选择 "Enable binding"。在 SonarLint 面板中选择项目，点击 "Bind to SonarQube/SonarCloud"。

**故障 3：SonarLint 显示大量历史 Issue，干扰当前开发**

根因：未开启 "Focus on New Code"。在 SonarLint 设置中开启后，它会像 SonarQube 一样只检查变更范围内的代码。

### 4.5 思考题

1. 如果团队中有人使用 IntelliJ IDEA，有人使用 VS Code，有人使用 vim，如何确保所有开发者都能获得一致的本地检查体验？
2. 在 Connected Mode 下，SonarLint 同步了服务器的 500 条规则。其中 20 条在本地产生了明显误报。你该如何处理这些本地误报而不影响服务器上的扫描结果？

> **答案提示**：第1题对有 IDE 的用 SonarLint Connected Mode，对无 IDE 的建议配置 SonarScanner 本地执行脚本。第2题可通过 SonarLint 本地设置排除特定规则（不影响服务器），或在代码中添加 `@SuppressWarnings`。

---

> **推广计划提示**：SonarLint 是推广 SonarQube 的"润滑剂"——开发者安装 SonarLint 后，提交 PR 前的恐惧感会大幅下降。建议将 SonarLint 的安装和 Connected Mode 配置写入团队的 "开发环境初始化脚本"（如 setup.sh / dev onboarding checklist），新成员入职时即可完成配置。质量负责人应在团队会议上演示一次 Connected Mode 的配置流程，消除安装门槛。
