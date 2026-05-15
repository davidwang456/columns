# 第20章：分支分析与 New Code 策略设计

## 1. 项目背景

**业务场景**：某金融系统是一个三年历史的遗留项目，代码量 20 万行，SonarQube 首次扫描发现 3,200 个 Issue。团队 Leader 设定了 Quality Gate（Overall Code 要求 Bug < 10）——结果门禁自打开启后就从未通过过。开发者无奈之下将门禁标记为 "Advisory"（仅告警不阻断），把它变成了"挂在墙上的装饰品"。

三个月后，一个新来的开发者写道："既然门禁永远不通过，那我也不用管扫描结果了。"——这个心理防线一旦崩溃，历史债务只会继续膨胀。

这个场景揭示了一个核心问题：**遗留项目不能直接用 Overall Code 门禁**。SonarQube 的设计哲学通过 "New Code"机制解决了这个矛盾——你可以接受历史代码的现状，但必须确保新增代码是干净的。这就是"止血"策略。

**痛点放大**：

- **历史债务 VS 新代码**：3,200 个历史 Issue 让人绝望，但每周新增的 15 个 Issue 才是持续恶化的根因
- **长生命周期分支**：Release 分支和长期维护分支如何与主分支协同治理
- **多版本策略**：同一个 Service 的 3 个版本分支，各自的质量基线如何设置
- **New Code Period 选择**：Previous Version / Number of Days / Reference Branch 各有什么适用场景？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（看着 SonarQube 页面上 3,200 个 Issues 的数字，瘫坐在椅子上）："大师……3200 个 Issue。我们团队一个月能修 50 个。按这个速度，需要 64 个月——5 年！5 年才能把门禁搞绿？这工具根本是在羞辱我们。"

**大师**："你又陷入 Overall Code 的陷阱了。我问你：你们每个月新增的 Issue 有多少？"

**小胖**："上个月新引入了大概 80 个——其中有 5 个是 Bug，剩下的都是 Code Smell。等等……新增了 80 个？！那我们修了 50 个，Issue 总数反而增加了？！"

**大师**："对。问题的核心不是历史 3200 个，而是每个月净增 30 个。如果你做不到'新增代码是干净的'，历史债务永远追不上新增速度——这就像水龙头不关就去拖地，水永远拖不完。

SonarQube 的 New Code 机制就是专门为这个场景设计的。它允许你定义什么是'新增代码'，然后只对新增代码执行 Quality Gate。你的 3200 个历史 Issue 可以先放一放，但从今天开始写的每一行新代码都不许出问题。"

**小白**："New Code Period 的定义方式有三种——Previous Version、Number of Days、Reference Branch。什么时候用哪种？"

**大师**：

| 方式 | 适用场景 | 优缺点 |
|------|---------|--------|
| **Previous Version** | 版本号规律递增的项目（如 1.0 → 1.1 → 2.0） | ✅ 精确匹配发布周期；❌ 版本号必须递增 |
| **Number of Days** | 快速迭代、版本号不规律的项目 | ✅ 灵活，设置一次就不管了；❌ 修改时间超过窗口后会被归为历史代码 |
| **Reference Branch** | 多分支协作项目（如主分支和 Release 分支） | ✅ 精确对比两个分支差异；❌ 分支必须存在于 SonarQube |

**小胖**："那我们该用哪种？我们项目版本号很乱——有的是按日期命名（2024.01），有的是按语义版本（2.1.3），还有的直接用 commit hash。"

**大师**："那就用 **Number of Days**，设为 30 天。这个配置的意思是：比当前时间早 30 天（或更早）的修改被视为历史代码，30 天内的修改被视为 New Code。SonarQube 会根据 Git blame 信息判断每行代码的时间戳。

不需要改版本号，不需要改 CI 配置——设置一次就持续生效。"

**小白**："那长期分支怎么处理？我们有 main 分支（主开发）、release-2.x（已发布的 LTS 版本）、还有 3 个 feature 分支。这些分支上的 New Code 判断逻辑是什么？"

**大师**："分支分析（Branch Analysis）是中级篇的核心话题。关键理解：

1. **Main Branch**：这是项目的主线。New Code Period 应设为 'Previous Version' 或 'Number of Days'。所有的 Quality Gate 以此分支为基准。

2. **Long-lived Branch（如 release-2.x）**：这是长期维护的分支，可能有自己的发布周期。你可以为它设置独立的 New Code Period（用 'Reference Branch' 指向它的上一个 release tag）。

3. **Short-lived Branch（如 feature/*）**：这些分支的生命周期通常 < 1 周，而且它们的代码最终会合并到 main。建议：不需要为它们单独设置复杂的 New Code 策略——直接继承 main 的 New Code Period，重点检查 PR 差异。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 实例，多个分支共存的项目
- 项目有 Git 历史（需要 Git blame 信息计算 New Code）

### 3.2 分步实现

**步骤 1：查看和设置 New Code Period**

进入 **Project Settings → New Code**：

**(a) 选择 "Number of days"**：

```
使用场景：版本号不规律的项目
设置值：30 天
效果：分析日期往前推 30 天，这 30 天内的修改为 New Code
```

```bash
# 通过 API 设置
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:legacy-system" \
  -d "type=NUMBER_OF_DAYS" \
  -d "value=30"
```

**(b) 选择 "Previous Version"**：

```bash
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:legacy-system" \
  -d "type=PREVIOUS_VERSION"
```

需要配合在扫描时指定版本号：

```bash
sonar-scanner -Dsonar.projectVersion=2.3.0
```

**(c) 选择 "Reference Branch"**：

```bash
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:legacy-system" \
  -d "type=REFERENCE_BRANCH" \
  -d "value=main"
```

**步骤 2：验证 New Code 生效**

```bash
# 查看某个项目的 New Code Period
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/show?project=com.company:legacy-system" \
  | python3 -m json.tool
```

输出示例：
```json
{
  "projectKey": "com.company:legacy-system",
  "type": "NUMBER_OF_DAYS",
  "value": "30",
  "effectiveValue": "90d"
}
```

**步骤 3：实战——遗留项目的止血方案**

假设项目 `com.company:legacy-system` 有 3200 个历史 Issue。

**(a) 设置 New Code Period 为 30 天**：

```bash
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:legacy-system" \
  -d "type=NUMBER_OF_DAYS" \
  -d "value=30"
```

**(b) 修改 Quality Gate 为 New Code Only**：

确保所有条件都使用 `on New Code` 而非 `on Overall Code`。

进入 Quality Gate 页面，编辑 "Sonar way" 或自定义 Gate：

| 条件 | 类型 | 阈值 |
|------|------|------|
| Coverage | on New Code | < 80% → Fail |
| Duplicated Lines | on New Code | > 3% → Fail |
| Blocker Issues (Bug) | on New Code | > 0 → Fail |
| Critical Issues (Vuln) | on New Code | > 0 → Fail |

**(c) 验证效果**：

- 修改一行旧代码（不在 30 天 New Code 窗口内）→ 旧 Issue 保持不变 → Quality Gate 不变
- 修改一行存在 30 天内的代码 → 如果有 Blocker Bug → Quality Gate Failed
- 新增一个文件 → 所有问题都是 New Code → Quality Gate 严格检查

**(d) 数据佐证**：

```bash
# 查看整体和 New Code 的差异
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.company:legacy-system&metricKeys=bugs,new_bugs,vulnerabilities,new_vulnerabilities" \
  | python3 -m json.tool
```

输出：
```json
{
  "measures": [
    {"metric": "bugs", "value": "320"},           // 整体 320 个 Bug
    {"metric": "new_bugs", "value": "0"},         // 新增 0 个 Bug ✓
    {"metric": "vulnerabilities", "value": "45"}, // 整体 45 个漏洞
    {"metric": "new_vulnerabilities", "value": "2"} // 新增 2 个漏洞 ✗
  ]
}
```

**步骤 4：多分支 New Code 治理实战**

**(a) Main Branch — 主干策略**：

```bash
# main 分支使用 PREVIOUS_VERSION，版本号 v2.4.0
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:my-service" \
  -d "branch=main" \
  -d "type=PREVIOUS_VERSION"
```

扫描时指定版本号：

```yaml
# CI 配置
script:
  - mvn sonar:sonar -Dsonar.projectVersion=2.5.0 -Dsonar.branch.name=main
```

**(b) Release Branch — 发布分支策略**：

```bash
# release-2.4.x 分支使用 REFERENCE_BRANCH，参考 main 分支 v2.4.0
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/new_code_periods/set" \
  -d "project=com.company:my-service" \
  -d "branch=release-2.4.x" \
  -d "type=REFERENCE_BRANCH" \
  -d "value=main"
```

**(c) Feature Branch — PR 差异策略**：

Feature 分支不需要自定义 New Code——PR 扫描时 SonarQube 自动将目标分支的代码视为 New Code 基线。

```bash
mvn sonar:sonar \
  -Dsonar.branch.name=feature/payment-fix \
  -Dsonar.branch.target=main
```

**步骤 5：批量设置团队所有项目的 New Code Period**

```bash
#!/bin/bash
# 为所有项目设置统一的 New Code Period (30 days)

SONAR_URL="http://localhost:9000"
TOKEN="squ_xxx"

# 获取所有项目 Key
PROJECTS=$(curl -s -u "$TOKEN:" \
  "$SONAR_URL/api/projects/search?ps=500" \
  | python3 -c "import sys,json; data=json.load(sys.stdin); print('\n'.join([c['key'] for c in data['components']]))")

for PROJ in $PROJECTS; do
  echo "Setting New Code Period for: $PROJ"
  curl -s -X POST -u "$TOKEN:" \
    "$SONAR_URL/api/new_code_periods/set" \
    -d "project=$PROJ" \
    -d "type=NUMBER_OF_DAYS" \
    -d "value=30" \
    > /dev/null
done

echo "Done!"
```

### 3.3 验证

```bash
# 查看 New Code 的指标
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.company:legacy-system&metricKeys=new_bugs,new_vulnerabilities,new_coverage,new_duplicated_lines_density" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data['component']['measures']:
    print(f'{m[\"metric\"]}: {m.get(\"value\", \"N/A\")}')"
```

---

## 4. 项目总结

### 4.1 New Code Period 选择指南

| 项目特征 | 推荐方式 | 原因 |
|---------|---------|------|
| 版本号规律递增 | Previous Version | 精准对齐发布周期 |
| 快速迭代/版本号不规律 | Number of Days (30) | 配置最简单，一劳永逸 |
| 长期分支对标主分支 | Reference Branch | 精确对比分支差异 |
| 全新项目（无历史债） | 可选 Overall Code | 从零开始的干净项目 |

### 4.2 适用场景

- **所有有历史债务的项目**：New Code 是唯一可行的落地策略
- **多分支协作**：Main + Release + Feature 的分支治理
- **敏捷团队**：快速迭代要求增量质量，而非一步到位

### 4.3 注意事项

1. **Git Blame 信息必需**：New Code 判断依赖 `git blame`。如果 `.git` 目录不可访问或使用了 shallow clone（`depth=1`），New Code 计算会失败。CI 中使用 `fetch-depth: 0`。
2. **`sonar.projectVersion` 必须递增**：使用 Previous Version 方式时，版本号必须大于上次扫描。如果版本号回退，New Code 会异常。
3. **旧 Issue 不会自动关闭**：开启 New Code 门禁后，历史 Issue 仍然存在。需要团队手动标记或逐步修复。建议在 Sprint Backlog 中预留修复历史 Issue 的时间。

### 4.4 常见踩坑经验

**故障 1：New Code 始终为 0，即使修改了很多代码**

根因：Git blame 无法获取时间戳信息。通常是因为 CI 中 clone 深度不够（`depth=1`）或 `fetch-depth` 未设置。解决：`actions/checkout@v4` 中设置 `fetch-depth: 0`。

**故障 2：整体 Bug 从 100 降到 90（少了 10 个），但 New Bug 显示增加了 1 个**

根因：这是合理行为。你修复了 10 个历史 Bug，但修改过程中引入了 1 个新 Bug。New Bug 统计的是"新增代码"上的 Bug，不是"新增的 Issue 总数"。"净增 Bug = 1 个" ≠ "Issue 减少"，这两个是不同的指标。

### 4.5 思考题

1. 一个遗留项目有 5000 个历史 Issue，New Code Period 设为 30 天。团队修复了 200 个历史 Issue，但当天写的 50 行新代码引入了 1 个 Blocker Bug。Quality Gate 应该通过还是失败？为什么？
2. 如果团队有 2 个长期并行维护的 Release 分支（v1.x 和 v2.x），各自的 New Code 门槛应该如何设置才能既保证质量又不造成混乱？

> **答案提示**：第1题 Quality Gate 应该失败——因为 New Code 有 Blocker Bug。New Code 门禁的唯一标准是"新增代码是否干净"，与历史 Issue 修复量无关。历史 Issue 修复是加分的，但不影响门禁判定。

---

> **推广计划提示**：New Code 策略是说服团队接受 SonarQube 的关键论据。"我们不需要修完 5000 个历史问题，只需要保证今天写的代码是干净的"——这句话能消除 90% 的抵触情绪。质量负责人应在推广材料的首页就用大号字体强调"仅检查新增代码"，这是改变开发者认知的"一句话说服术"。
