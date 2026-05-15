# 第9章：Quality Gate：把质量要求变成流水线红线

## 1. 项目背景

**业务场景**：某电商公司支付网关团队在连续三个月线上零事故后，突然在"双十一"大促前夕遭遇了一次严重故障——一笔 500 万元的对账差异。根因追溯发现，一位开发者在修复一个"Minor Code Smell"时，"顺便"修改了对账逻辑中的一个除零保护，将 `if (divisor == 0) return 0` 改成了 `if (divisor == 0) return -1`，导致下游消费方把 `-1` 当成了正常的负数金额。

更讽刺的是，这次修改完美通过了 Quality Gate——因为 Quality Gate 只检查了 New Code 的 Bug 和 Vulnerability，没有检查 Coverage。修改覆盖率为 0%（修改的代码路径没有测试用例），但门禁没有拦截。

这个事故暴露了一个核心问题：**Quality Gate 不是"越严越好"或"越松越好"，而是需要精确匹配业务系统的风险等级**。支付系统的门禁必须覆盖覆盖率检查，而内部报表系统的门禁可以宽松一些。

**痛点放大**：

- **门禁过严**：开发者为了通过门禁，开始"刷覆盖率"——写无效测试、标记误报、降低复杂度阈值。Quality Gate 变成了"数字游戏"。
- **门禁过松**：像支付系统的案例，历史 Bug 被"顺便"引入，但门禁没有拦截。
- **New Code vs Overall Code**：老项目有 5000 个历史 Issue，用 Overall Code 门禁会导致永远过不了——团队直接放弃。
- **门禁条件组合模糊**：是"所有条件都要通过"还是"任一条件通过"？条件之间的逻辑关系是什么？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（收到第 15 次 Quality Gate Failed 通知，抓狂）："大师！我又没过门禁！这次是因为'New Code Coverage < 80%'——但我这次只改了 3 行日志代码，我难道还要写单元测试测日志吗？这也太形式主义了吧！"

**大师**："小胖，你说的对——只改日志不写测试是合理的。问题不出在你身上，出在门禁设计上。一个通用的 80% 覆盖率门禁对所有类型的修改都一视同仁，这显然不合理。我们需要一个分级的门禁体系。"

**小白**："我看到 Quality Gate 页面里可以添加很多条件——Coverage、Duplicated Lines、Security Rating、Reliability Rating……这些条件之前是什么关系？是 AND 还是 OR？"

**大师**："是 **AND** 关系。Quality Gate 中所有条件都必须通过，只要有一个 Fail，整个 Gate 就是 Failed。这就像高考——你必须所有科目都及格，不能有一科拖后腿。

条件分为两大类：
- **On New Code**：只检查本次扫描中新增/修改的代码
- **On Overall Code**：检查整个项目的所有代码

这是 SonarQube 最核心的设计决策。你去看 Quality Gate 的默认条件列表，几乎全都是 'On New Code'——为什么？因为如果检查 Overall Code，遗留项目几乎不可能通过。"

**小胖**："那 New Code 怎么定义？我昨天改了一个老方法里的 if 条件，这算 New Code 吗？"

**大师**："算。New Code 的定义由 **New Code Period** 决定。这是一个时间窗口。常见的设置方式有三种：

1. **Previous Version**：和上一个分析版本比较。最常用。当你在 `sonar-project.properties` 中设置了 `sonar.projectVersion=1.0.1`，SonarQube 会把上一版本（1.0.0）分析后的所有代码标记为 New Code。
2. **Number of Days**：指定一个天数（如 30 天）。SonarQube 把你设置的时间点之后的修改都算 New Code。这个选项适合版本号不规律的项目。
3. **Reference Branch**：以某个参考分支为基准。适合多分支开发场景。

一般来说，推荐使用 'Previous Version'，版本号随着每次发布递增。"

**小白**："我看到默认 Quality Gate（Sonar way）有 7 个条件：

1. Coverage on New Code < 80% → Fail
2. Duplicated Lines on New Code > 3% → Fail
3. Maintainability Rating on New Code > A → Fail
4. Reliability Rating on New Code > A → Fail
5. Security Rating on New Code > A → Fail
6. Security Hotspots Reviewed < 100% → Fail
7. Security Review Rating on New Code > A → Fail

这些条件能不能按项目调整？比如支付系统的覆盖率要求 90%，内部工具只要 50%？"

**大师**："当然可以，而且应该这样做。你需要为不同风险等级的项目创建不同的 Quality Gate：

- **核心系统 Gate**：Coverage >= 90%，New Bug = 0，New Vulnerability = 0，Security Hotspot Reviewed = 100%
- **业务系统 Gate**：Coverage >= 70%，New Blocker/Critical Bug = 0
- **内部工具 Gate**：Coverage >= 50%，New Blocker Bug = 0

然后将三个 Gate 分别分配给对应的项目。这叫做'差异化的质量红线'——核心系统不能有一丝妥协，内部工具可以适当宽松。"

**小胖**："那门禁过严会不会导致开发者想法子'钻空子'？比如为了过覆盖率门槛，写一堆 `assertTrue(true)` 的无效测试？"

**大师**："这确实是最常见的'门禁副作用'。对抗措施有三条：

1. **Code Review 中审查测试质量**：机器检查覆盖率，人检查测试有效性。
2. **排除低价值文件**：在 SonarQube 中配置 `sonar.coverage.exclusions`，排除 POJO/DTO/配置类——这些文件不需要测试。
3. **设置合理的覆盖率目标**：80% 是行业通用基准，但有些系统（如核心交易）需要 90%+，有些（如批处理脚本）50% 就够了。

核心理念是：**Quality Gate 是安全网，不是绩效考核 KPI**。如果把 Gate 和绩效挂钩，钻空子几乎是必然的。"

---

## 3. 项目实战

### 3.1 环境准备

- SonarQube 管理员权限
- 至少已有 2 个项目完成扫描

### 3.2 分步实现

**步骤 1：查看默认 Quality Gate**

进入 **Quality Gates** 页面，点击 "Sonar way" 查看内置条件：

| 条件 | 维度 | 阈值 | 类型 |
|------|------|------|------|
| Coverage | < 80.0% | 低于 80% 则失败 | New Code |
| Duplicated Lines | > 3.0% | 高于 3% 则失败 | New Code |
| Maintainability Rating | worse than A | B/C/D/E 则失败 | New Code |
| Reliability Rating | worse than A | B/C/D/E 则失败 | New Code |
| Security Rating | worse than A | B/C/D/E 则失败 | New Code |
| Security Hotspots Reviewed | < 100% | 有未审核 Hotspot 则失败 | Overall |
| Security Review Rating | worse than A | B/C/D/E 则失败 | New Code |

**步骤 2：创建自定义 Quality Gate**

点击 "Create"，创建三个层级的 Gate：

**(a) "Core System Gate"（核心系统）**

| 条件 | 阈值 |
|------|------|
| Coverage on New Code | < 90.0% → Fail |
| Duplicated Lines on New Code | > 3.0% → Fail |
| Blocker Issues (Bug) on New Code | > 0 → Fail |
| Blocker/ Critical Issues (Vulnerability) on New Code | > 0 → Fail |
| Security Hotspots Reviewed | < 100% → Fail |

**(b) "Business System Gate"（业务系统）**

| 条件 | 阈值 |
|------|------|
| Coverage on New Code | < 70.0% → Fail |
| Duplicated Lines on New Code | > 5.0% → Fail |
| Blocker Issues (Bug + Vulnerability) on New Code | > 0 → Fail |
| Security Hotspots Reviewed | < 100% → Fail |

**(c) "Internal Tool Gate"（内部工具）**

| 条件 | 阈值 |
|------|------|
| Coverage on New Code | < 50.0% → Fail |
| Blocker Issues (Bug) on New Code | > 0 → Fail |

**步骤 3：分配 Quality Gate 到项目**

进入 **Project Settings → Quality Gate**（或以管理员身份进入 Administration → Projects → Management）：

1. 选中目标项目
2. 点击 "Quality Gate" 下拉菜单
3. 选择对应的 Gate

也可以通过 API 批量分配：

```bash
# 给核心系统项目分配 Gate
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/select" \
  -d "gateName=Core System Gate" \
  -d "projectKey=com.example:payment"

# 给内部工具项目分配 Gate
curl -X POST -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/select" \
  -d "gateName=Internal Tool Gate" \
  -d "projectKey=com.example:admin-tool"
```

**步骤 4：设置 New Code Period**

进入 **Project Settings → New Code**：

- 推荐选择 "Previous version"（以 `sonar.projectVersion` 为准）
- 或者选择 "Number of days"，设为 30（以最近 30 天的修改为 New Code）

**步骤 5：测试 Gate 效果**

**(a) 模拟通过场景**：修复所有 Blocker Issue，确保覆盖率达标，重新扫描。

```bash
sonar-scanner -Dsonar.projectVersion=1.0.1
```

查看 Quality Gate 状态：

```bash
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.example:payment" \
  | python3 -m json.tool
```

预期输出：
```json
{
    "projectStatus": {
        "status": "OK",
        "conditions": [
            {"metricKey": "new_coverage", "status": "OK", "actualValue": "87.5"},
            {"metricKey": "new_bugs", "status": "OK", "actualValue": "0"}
        ]
    }
}
```

**(b) 模拟失败场景**：故意引入一个除零 Bug，不写测试，重新扫描。

```java
public double riskyDivide(double a, double b) {
    return a / b; // 除零漏洞
}
```

预期 Gate 状态为 `ERROR`，具体是 New Bug > 0 和 Coverage 下降导致失败。

**步骤 6：通过 API 获取 Gate 详细数据**

```bash
# 获取 Gate 详细条件和实际值
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.example:payment&branch=main" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
status = data['projectStatus']
print(f'Overall: {status[\"status\"]}')
for cond in status['conditions']:
    print(f\"  {cond['metricKey']:30s} {cond['status']:5s}  threshold={cond.get('errorThreshold','N/A')}  actual={cond.get('actualValue','N/A')}\")
"
```

### 3.3 进阶：New Code 门禁 vs Overall Code 门禁

对比两种门禁方式的影响：

```bash
# 某个三年遗留项目，整体 Bug 数 500+
# 设置 Overall Code 门禁 → 永远 Failed → 团队放弃
# 设置 New Code 门禁 → 只要新增代码无 Bug → Passed → 团队有信心继续治理
```

| 维度 | New Code Only 门禁 | Overall Code 门禁 |
|------|-------------------|-------------------|
| 遗留项目友好度 | ✅ 高 | ❌ 低 |
| 增量治理效果 | ✅ 新增代码不腐烂 | ❌ 无法单独约束 |
| 历史债务可见性 | ❌ 无法量化历史债 | ✅ 全域可见 |
| 推荐使用场景 | 有历史债的活跃项目 | 新项目或已完成治理的项目 |

**推荐策略**：先用 New Code 门禁止血（保证新增代码质量），再逐步治理历史债。

### 3.4 验证

```bash
# 列出所有 Gate
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/list" \
  | python3 -m json.tool

# 查看某个 Gate 的条件
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/show?name=Core%20System%20Gate" \
  | python3 -m json.tool
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | Quality Gate（SonarQube） | 手动 Code Review 审批 |
|------|--------------------------|----------------------|
| 一致性 | ✅ 100% 客观，无人情因素 | ❌ 因人而异 |
| 速度 | ✅ 扫描后立刻出结果 | ❌ 等待审查者有空 |
| 覆盖维度 | ✅ 同时检查 7+ 个质量维度 | ❌ 审查者一般只关注 2-3 个 |
| 可配置性 | ✅ 阈值、条件可精细化配置 | 🟡 口头约定，难以追溯 |
| 上下文理解 | ❌ 不理解业务逻辑合理性 | ✅ 能理解设计意图 |
| 误杀风险 | 🟡 过严时会产生"形式主义" | ❌ 主观性强 |

### 4.2 适用场景

- **CI/CD 流水线**：门禁是 CI 中最核心的"质量关卡"
- **多项目差异化治理**：核心系统严格、辅助系统宽松
- **遗留项目质量止血**：New Code 门禁安全网

**不适用场景**：
- 实验性项目 / POC 原型（门禁会阻碍快速试错）
- 生成代码 / 第三方代码仓库

### 4.3 注意事项

1. **门禁条件不是越多越好**：每个条件都是一个"潜在阻断点"。条件越多，误杀的概率越大，团队绕过门禁的动机越强。
2. **Severity 的选择有技巧**：不要设置"New Code Smell > 0"这样的条件——Code Smell 太多太杂，容易误杀。至少要设 Bug 或 Vulnerability 级别。
3. **版本号管理**：New Code Period 依赖 `sonar.projectVersion`。每版本必须递增版本号，否则 New Code 判定会出问题。

### 4.4 常见踩坑经验

**故障 1：门禁突然 Failed，但团队说"我们没改代码啊"**

根因：New Code Period 到期后窗口前移，之前被标记为"New Code"的代码变成了"历史代码"，之前没触发的条件（如 Coverage）现在暴露出来了。

**故障 2：Coverage 条件始终 Failed，即使开发写了测试**

根因：覆盖率报告路径在不同环境不一致（如 Docker 中的绝对路径），SonarQube 无法匹配到源码。需要统一使用相对路径生成报告。

**故障 3：Quality Gate Failed 后 Jenkins Pipeline 没有中断**

根因：Pipeline 中的 `waitForQualityGate()` 步骤默认不阻塞。需要配合 Webhook 回调或设置 `sonar.qualitygate.wait=true`。

### 4.5 思考题

1. 如果你的团队同时维护一个新项目（1 万行代码）和一个三年历史项目（15 万行代码），你如何为它们设计不同的 Quality Gate？
2. "Coverage on New Code >= 80%" 这个条件，有没有可能被"合法绕过"？如何防止这种绕过？

> **答案提示**：第1题新项目用 Overall Code 门禁 + New Code 门禁双重护航，老项目仅用 New Code 门禁止血。第2题见本章正文"钻空子"部分。

---

> **推广计划提示**：Quality Gate 的设置必须和团队协商——不能由架构师或质量负责人一个人定。建议在质量周会上展示"如果设置这个门禁条件，当前有多少项目会 Fail"，让数据说话，再和团队一起投票决定。门禁是工程契约，不是行政命令。
