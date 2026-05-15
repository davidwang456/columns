# 第8章：Quality Profile：打造团队规则集

## 1. 项目背景

**业务场景**：某互联网金融团队在接入 SonarQube 两周后，出现了一个意料之外的问题——开发和测试在"什么算 Bug"上产生了激烈争吵。测试同学认为所有 Major 及以上级别的问题都应该修，但开发同学发现其中 40% 的问题要么是误报，要么是"框架设计导致的必然写法"。

例如，SonarQube 默认规则 `java:S1186`（空方法体）把 Spring Data JPA 的 Repository 接口方法全部标记为 Code Smell——但这些方法由框架在运行时动态实现，空方法是设计意图，不是代码缺陷。类似的问题还有：Lombok 生成的代码被标记为未使用、React 的 `useEffect` 空依赖数组被标记为"缺少依赖"、单例模式的路由守卫被标记为"字符串字面量硬编码"。

团队需要一套自己的规则集——既要覆盖关键质量检查，又要排除团队公认的"误报规则"。

**痛点放大**：

- **默认规则不适用**：内置的 "Sonar way" Profile 是通用基准，不可能完全匹配每个团队的技术栈和编码风格。
- **规则冲突**：不同开发者对"好的代码"有不同理解，如果不统一规则标准，争吵永无止境。
- **规则变更无记录**：谁来改规则？改了什么？为什么改？没有记录的话，规则集就会变成"神秘黑盒"。
- **回滚困难**：改了规则后发现误杀了一大批代码，但不知道改之前的状态是什么。

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（盯着 SonarQube 页面上一堆"空方法体"的 Code Smell）："大师，Spring Data JPA 的 Repository 接口全是空方法，这很明显不是 Bug 啊。我就是按 Spring 官方文档写的，凭什么说我错了？"

**大师**："你说得对。这不是你写错了，是规则配置需要调整。SonarQube 的规则是'普适'的——它不知道你用的是什么框架、什么设计模式。所以你需要定制 Quality Profile，把不适合团队的规则关掉，把适合团队的阈值调到合理位置。"

**小白**："那 Quality Profile 到底是什么？我理解它是一组规则的集合，但我不清楚它和 Quality Gate、和具体的编程语言是什么关系。"

**大师**："打个比方：公司要组织一次员工体检（SonarQube 扫描）。**Quality Profile** 是体检项目清单——你要检查血常规、心电图、视力。不同工种的项目不同：程序员要查颈椎腰椎，厨师要查皮肤病。**Quality Gate** 是体检合格标准——血压不能高于 140、血糖不能高于 6.1。**Profile 决定查什么，Gate 决定过不过得了**。"

**小胖**："那 Profile 和语言的关系呢？我 Java 项目的规则和 JavaScript 项目的是不是分开的？"

**大师**："完全分开。每种语言有独立的 Profile。Java 有一套规则（600+ 条），JavaScript 有一套（200+ 条），Python 有一套（200+ 条）。你创建 Profile 时要指定语言——不指定语言的话，不能添加规则。"

**小白**："内置 Profile 有好几个，除了 'Sonar way'，还有 'Sonar way (Strict)'、'FindBugs' 等。我们该用哪个作为基础？"

**大师**："'Sonar way' 是推荐起点——它涵盖了最重要的规则，误报率最低。'Sonar way (Strict)' 包含了更多严格规则，适合追求极致质量的团队。'FindBugs' 是历史遗留的规则集（已被 'Sonar way' 取代）。

我的建议：基于 'Sonar way' 创建自定义 Profile。理由是：
1. 'Sonar way' 是 SonarSource 官方维护的"最佳实践基准"，活跃度最高
2. 它的规则经过大量项目验证，误报率控制得好
3. 你可以在此基础上叠加或删减，而不是从零开始

操作上，不要直接修改 'Sonar way'。而是复制一份，命名如 'Team Java Profile v1'，在副本上修改。"

**小胖**："那如果公司有 20 个 Java 项目，应该共用 1 个 Profile 还是每个项目 1 个？"

**大师**："共用。Profile 是团队级别的资产，不是项目级别的。理由很简单：如果每个项目都有自己的规则集，那就失去了'统一代码标准'的意义。20 个 Profile 意味着 20 套标准——和没装 SonarQube 有什么区别？

但是，如果公司有不同的技术线（如核心交易系统 vs 内部管理工具），可以为不同等级的系统创建不同的 Profile。核心系统用严格的 Profile，内部工具用宽松的。但你设置了 3 套 Profile 就够了，不能每个项目一套。"

**小白**："Profile 还有继承关系——这是什么意思？什么场景下用继承？"

**大师**："继承是 Profile 层级设计的基础。举个例子：

```
Parent: 'Company Java Baseline v1' （公司通用规则，150 条）
  └── Child: 'Core Trading Java Profile' （继承父规则 + 30 条金融级严格规则）
  └── Child: 'Internal Tool Java Profile' （继承父规则 - 20 条过于严格的规则）
```

子 Profile 继承父 Profile 的所有规则，可以额外开放更多规则（激活），也可以关闭父 Profile 的规则（停用）。父 Profile 更新（如新增一条安全规则），所有子 Profile 自动继承——这就是集中管理的优势。"

**小胖**："那我想把 '空方法体' 这个规则关了，是应该停用还是修改参数？"

**大师**："先看规则是否有参数可调。'空方法体'（java:S1186）有一个 `excludedAnnotations` 参数——你可以在这里添加 `@Override` 注解，让标注了 `@Override` 的空方法不被标记。这比直接停用规则更精确——你保留了'其他空方法'的检查。

如果规则没有合适的参数，而且确实不适合你的团队，那就停用它。但停用前要在规则描述中添加 Comment（需要商业版）或额外记录原因，否则以后没人知道为什么关了。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例，管理员权限
- 至少已有 Java 项目完成一次扫描

### 3.2 分步实现

**步骤 1：创建自定义 Profile**

1. 进入 **Quality Profiles** 页面
2. 在 Java 语言卡片上点击 "Create"
3. 填写：
   - Name: `Team Java Profile v1.0`
   - Language: Java
   - Parent: Sonar way
4. 点击 Create

现在你有了一个和 "Sonar way" 完全一样的 Profile。

**步骤 2：调整关键规则**

**场景 A：停用不适用的规则**

进入新 Profile 的规则列表，搜索 `java:S1186`（空方法体）。

1. 点击规则名进入详情页
2. 查看 "Parameters" 区域
3. 发现没有适合 Spring Data JPA 的参数
4. 回到 Profile 规则列表，点击规则旁的 "Deactivate"（停用）
5. 在确认弹窗中选择 "Note: Spring Data JPA repository methods are implemented by runtime proxy"（商业版支持添加 Note）

**场景 B：修改规则参数**

搜索 `java:S3776`（Cognitive Complexity 认知复杂度）。

此规则默认阈值是 15（认知复杂度超过 15 就报告 Issue）。

1. 进入规则详情
2. 在 Parameters 区域，将 `Threshold` 从 15 修改为 20
3. 点击 Save

这样只有认知复杂度超过 20 的方法才会被标记。

**场景 C：激活额外规则**

有些规则在 "Sonar way" 中默认未开启，但你的团队可能需要。例如 `java:S2065`（transient 字段未序列化时不应使用）。

1. 在规则列表中，将 "Inactive" 筛选器打开
2. 搜索 `java:S2065`
3. 点击 "Activate"
4. 设置 Severity 为 Major

**步骤 3：规则变更的影响评估**

每次修改规则后，SonarQube 会提示 "X projects are using this profile. Changes will take effect on next analysis"。但这不够——你需要主动评估影响。

**(a) 查看活跃 Issue 受影响情况：**

```bash
# 找出受规则 S1186 影响的历史 Issue
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?rules=java:S1186&statuses=OPEN&ps=1" \
  | python3 -m json.tool | grep '"total"'
```

如果返回 `"total": 0`，说明没有历史 Issue 受影响，可以安全修改。

**(b) 在测试项目上先验证**：如果有条件，先在一个测试项目上应用新 Profile，验证扫描结果是否符合预期。

**步骤 4：批量激活/停用规则**

通过 API 批量操作规则：

```bash
# 列出 Java 所有活跃规则
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/rules/search?languages=java&activation=true&qprofile=AXet_UyGxjZKF8FlHmOb&ps=500" \
  | python3 -m json.tool | grep '"key"' | head -20

# 批量停用规则（通过 CSV 文件或脚本）
for rule in java:S1175 java:S1176 java:S1177; do
  curl -X POST -u admin:Sonar@2024Admin \
    "http://localhost:9000/api/qualityprofiles/deactivate_rule" \
    -d "qualityProfile=Team Java Profile v1.0" \
    -d "rule=$rule"
done
```

**步骤 5：Profile 导出与版本管理**

导出 Profile（用于备份和版本控制）：

```bash
# 导出 Profile 的所有规则配置
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualityprofiles/export?language=java&qualityProfile=Team+Java+Profile+v1.0" \
  > team-java-profile-v1.0.xml
```

导入 Profile（恢复到某个版本）：

```bash
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualityprofiles/restore" \
  -F "backup=@team-java-profile-v1.0.xml"
```

**步骤 6：比较 Profile 差异**

```bash
# 比较两个 Profile 的规则差异
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualityprofiles/compare" \
  -d "left=Team+Java+Profile+v1.0" \
  -d "right=Sonar+way" \
  | python3 -m json.tool
```

输出会列出：left 有而 right 没有的规则、right 有而 left 没有的规则、两边参数不同的规则。

**步骤 7：将 Profile 分配给项目**

进入 **Project Settings → Quality Profiles**：
- 选择 Java 的 Profile 为 "Team Java Profile v1.0"
- 其他语言保持默认

或者在 **Administration → Configuration → Quality Profiles** 中设置默认 Profile，新项目会自动继承。

### 3.3 使用父子 Profile 实现分级治理

创建 Profile 层级体系：

```bash
# 1. 创建父 Profile（基座）
# 在 UI 中创建 "Company Java Baseline v1"，基于 "Sonar way"
# 包含 150 条通用规则

# 2. 创建核心系统子 Profile
# 在 UI 中创建 "Core Trading Java v1"，Parent 选 "Company Java Baseline v1"
# 额外激活 30 条严格规则（安全、并发、异常处理）

# 3. 创建内部工具子 Profile
# 在 UI 中创建 "Internal Tool Java v1"，Parent 选 "Company Java Baseline v1"
# 停用 20 条过于严格的规则（如要求所有 public 方法有 Javadoc）

# 4. 分配 Profile 到项目
# 核心系统项目 → "Core Trading Java v1"
# 内部工具项目 → "Internal Tool Java v1"
# 其他项目 → "Company Java Baseline v1"（默认）
```

### 3.4 验证

```bash
# 验证 Profile 分配
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualityprofiles/search?projectKey=com.example:order-service" \
  | python3 -m json.tool

# 验证某个规则在 Profile 中的状态
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/rules/show?key=java:S1186&actives=true" \
  | python3 -m json.tool | grep -E '"qProfileName"|"severity"'
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | SonarQube Quality Profile | Checkstyle/PMD 配置文件 |
|------|--------------------------|------------------------|
| 集中管理 | ✅ Web UI 管理，团队共享 | ❌ 配置文件散落在各项目 |
| 版本管理 | 🟡 需手动导出 XML 备份 | ✅ 天然是 Git 文本文件 |
| 规则变更审核 | ❌ 无内置审核流程 | ✅ 通过 PR Review |
| 父子继承 | ✅ 支持多级继承 | ❌ 不支持 |
| 变更影响评估 | 🟡 需通过 API 人工评估 | ❌ 无 |

### 4.2 适用场景

- **多项目统一治理**：通过 Profile 继承实现公司级基座 + 团队级定制
- **技术栈特定的规则优化**：如 Spring Boot 项目的注解感知、Lombok 代码排除
- **渐进式规则推广**：先从一个宽松的 Profile 开始，逐步收紧

**不适用场景**：
- 单项目小团队（直接用 "Sonar way" 即可，定制成本高于收益）
- 需要 GitOps 管理规则配置的团队（SonarQube 不原生支持 Git 驱动的规则管理）

### 4.3 注意事项

1. **停用规则前先检查参数**：很多规则有可调参数，先尝试调整参数而非直接关闭。
2. **不要直接修改 "Sonar way"**：SonarQube 升级时可能会重置内置 Profile。始终在副本上修改。
3. **Profile 变更需要通知团队**：新激活的规则可能导致下次扫描出现大量新 Issue，提前通知开发团队。
4. **导出备份**：每次规则变更后导出 XML 备份，存到 Git 仓库，形成变更历史。

### 4.4 常见踩坑经验

**故障 1：修改了 Profile 但项目扫描结果没变化**

根因：Profile 变更后需要重新扫描才能生效。已有 Issue 不会自动关闭——需要修复代码后重扫。如果是停用规则，历史 Issue 需要手动标记为 Closed。

**故障 2：子 Profile 中停用父 Profile 的规则不生效**

根因：子 Profile 可以"停用"父 Profile 的规则，但需要确认操作界面选择的是 "Inherited" 规则（带父图标）并执行 "Deactivate"。

**故障 3：Profile 导入后规则参数丢失或回退**

根因：导入 XML 时如果与服务器上已有规则冲突，使用 `-F "strategy=REPLACE"` 参数强制全量替换。

### 4.5 思考题

1. 如果公司有 5 个 Java 团队，其中 1 个做核心交易系统（要求极严格），2 个做业务系统（中等要求），2 个做内部工具（宽松）。你如何设计 Profile 层级结构？
2. Quality Profile 的 "继承" 和面向对象编程中的 "继承" 有什么相似和不同？为什么 SonarQube 选择用继承而不是组合？

> **答案提示**：第1题设计 1 个父 Profile（基准规则）+ 3 个子 Profile（分级定制）。第2题继承实现了"默认统一 + 例外覆盖"的治理模式。

---

> **推广计划提示**：Quality Profile 定制是团队沟通的产物——不是技术 Leader 一个人说了算。建议组织一次"规则评审会"，邀请开发、测试、架构师各 2 人，一起审阅 "Sonar way" 中的规则，投票决定哪些保留、哪些停用、哪些调参。评审结果形成文档，作为团队的"代码规范宪法"。
