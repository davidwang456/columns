# 第6章：Merge Request 基础——代码审查入门

## 1. 项目背景

> **业务场景**：一家 30 人的 SaaS 公司，技术团队使用了 GitLab 半年，但 Code Review 一直流于形式——MR 创建后五分钟就有人点 Approve，Reviewer 只扫一眼标题就通过，线上 Bug 数量居高不下。更有甚者，有人为了"快速上线"，直接用 Maintainer 权限绕过 MR 往 main 分支 push 代码。

一次事故让管理层彻底坐不住了：某开发在周五下午 merge 了一个"看起来没问题"的 MR，该 MR 修改了一个订单金额计算的浮点数精度问题。Reviewer 只看了代码风格，没有注意到计算逻辑错误——原本应该是 `price * quantity * (1 - discount)`，被写成了 `price * quantity / (1 + discount)`。这个 Bug 在线上运行了整整一个周末，导致所有订单少收了 8% 的金额，直接损失超过 50 万。

事后复盘，Reviewer 辩解："这个 MR 有 500 行改动，我根本没时间逐行看。" 而提交者也委屈："我明明写了详细的 MR 描述，但 Reviewer 根本没看。"

**痛点放大**：Code Review 不是"有人点 Approve"就完事了。一个高质量的 Code Review 流程包括：MR 的描述规范、commit 历史的清晰度、代码 diff 的可读性、Review 评论的有效性、自动化检查（CI/SAST）的拦截。GitLab 的 MR 提供了所有这些功能，但团队需要一个规范的流程来使用它们。

## 2. 项目设计——剧本式交锋对话

**场景**：Code Review 规范讨论会，白板上写着"MR = Merge Request ≠ Merge Regardless"。

---

**小胖**："说实话，我们的 Code Review 确实很水。但我也不知道怎么才算一个好的 Review——难道要逐行看吗？500 行的 MR 我一天都看不完。"

**大师**："问题不在'看不看完'，而在于 MR 本身就不该有 500 行。一个好的 MR 应该控制在 200-300 行以内，专注做一件事。如果你收到一个 500 行的 MR，你的第一反应不应该是硬着头皮看，而是直接在 MR 下评论：'请拆分成 2-3 个更小的 MR。'"

**小白**："我同意小 MR 更容易 review，但 commit 历史也很重要吧？我经常看到 MR 里有这种 commit：`fix`、`fix again`、`really fix this time`——这种历史合并进去后，以后回看根本看不懂代码为什么这么写。"

**大师**："这就是 Squash and Merge 的意义。当你合并 MR 时选择 Squash，所有零碎的 commit 会被压缩成一个干净的 commit，这个 commit 的 message 就是 MR 的标题。技术映射——你的 feature 分支是你个人的草稿本，可以随意修改；但 main 分支是团队的正式文档，每个 commit 都要有清晰的意义。"

**小胖**："那 Draft MR 又是干嘛的？"

**大师**："Draft MR（以前叫 WIP MR）是'还没做完，先给大家看看'的意思。比如你开发一个复杂功能，写到一半想让同事提前看一下方向对不对——这时候就可以创建 Draft MR。它不会触发合并提示，Reviewer 可以提前给意见但不会'误合并'。"

**小白**："对了，我注意到 MR 有 3 种合并模式——Merge Commit、Squash and Merge、Rebase and Merge。什么时候用哪种？"

**大师**："Merge Commit 保留了完整的分支历史和合并点，适合大型开源项目的贡献流程——你可以看到'这个功能是由某个 PR 引入的'。Squash and Merge 把所有 commit 压缩为一个，main 分支历史最干净——适合小团队的快速迭代。Rebase and Merge 保持线性历史但不压缩 commit，适合每个 commit 都有独立意义的场景——比如一个 commit 是重构，另一个 commit 是新增功能。技术映射：Merge Commit 是日记（大事小事都记），Squash and Merge 是周报（总结摘要），Rebase and Merge 是分类笔记（按主题分条但保持原始记录）。"

**小胖**："那我们团队用什么？"

**大师**："对于 30 人的团队，我推荐 Squash and Merge 作为默认策略——main 分支干净、每个 MR 有且只有一个 commit、回溯和回滚都非常方便。特殊情况下可以用 Rebase and Merge（比如大型重构需要保留多个逻辑独立的 commit）。"

---

## 3. 项目实战

### 环境准备

> **目标**：完整走一遍规范化的 MR 流程——从创建到合并，包含 Commit Lint、Draft 标记、Squash 合并和代码审查。

**前置条件**：GitLab 项目（参考第4章），main 分支受保护（参考第5章）。

### 分步实现

#### 步骤1：创建符合规范的 feature 分支和 commit

**目标**：用 Conventional Commits 规范写 commit message，保持提交历史清晰。

```bash
# 克隆项目
git clone http://gitlab.local:8929/acme-corp/ecommerce/order-service.git
cd order-service

# 创建 feature 分支（命名规范：feature/<简短描述>）
git checkout -b feature/add-discount-calculator

# ===== 写代码 =====
# 创建 discount 计算模块
mkdir -p src/services
cat > src/services/discount.js << 'EOF'
/**
 * 折扣计算服务
 * 支持：百分比折扣、满减折扣、阶梯折扣
 */
class DiscountCalculator {
  /**
   * 计算百分比折扣
   * @param {number} price - 原价
   * @param {number} discountRate - 折扣率 0-1
   * @returns {number} 折后价格
   */
  static percentageDiscount(price, discountRate) {
    if (discountRate < 0 || discountRate > 1) {
      throw new Error('折扣率必须在 0-1 之间');
    }
    return price * (1 - discountRate);
  }

  /**
   * 满减折扣
   * @param {number} price - 原价
   * @param {number} threshold - 满减门槛
   * @param {number} reduction - 减免金额
   */
  static thresholdDiscount(price, threshold, reduction) {
    if (price >= threshold) {
      return Math.max(0, price - reduction);
    }
    return price;
  }

  /**
   * 阶梯折扣（根据消费总额自动匹配折扣档位）
   * @param {number} price - 原价
   * @param {Array} tiers - 折扣档位 [{min, rate}]
   */
  static tieredDiscount(price, tiers) {
    const sorted = [...tiers].sort((a, b) => b.min - a.min);
    for (const tier of sorted) {
      if (price >= tier.min) {
        return price * (1 - tier.rate);
      }
    }
    return price;
  }
}

module.exports = DiscountCalculator;
EOF

# 第一次 commit: 核心功能
git add src/services/discount.js
git commit -m "feat(discount): add percentage and threshold discount calculator

- 实现百分比折扣计算
- 实现满减折扣计算
- 添加边界值校验（折扣率 0-1，满减后不低于 0）
- 为后续阶梯折扣功能预留接口"

# ===== 编写测试 =====
mkdir -p tests
cat > tests/discount.test.js << 'EOF'
const DiscountCalculator = require('../src/services/discount');

function runTests() {
  let passed = 0;
  let failed = 0;

  function assert(condition, msg) {
    if (condition) { passed++; }
    else { console.error(`FAIL: ${msg}`); failed++; }
  }

  // 百分比折扣测试
  assert(
    DiscountCalculator.percentageDiscount(100, 0.2) === 80,
    '20% off 100 should be 80'
  );
  assert(
    DiscountCalculator.percentageDiscount(100, 0) === 100,
    '0% off should return original'
  );

  // 满减折扣测试
  assert(
    DiscountCalculator.thresholdDiscount(200, 100, 20) === 180,
    '200 - 20 when threshold 100'
  );
  assert(
    DiscountCalculator.thresholdDiscount(50, 100, 20) === 50,
    'No discount below threshold'
  );

  // 边界测试
  try {
    DiscountCalculator.percentageDiscount(100, 1.5);
    console.error('FAIL: Should throw for invalid rate');
    failed++;
  } catch (e) {
    passed++;
  }

  console.log(`\n结果: ${passed} 通过, ${failed} 失败`);
  process.exit(failed > 0 ? 1 : 0);
}

runTests();
EOF

# 第二次 commit: 测试
git add tests/discount.test.js
git commit -m "test(discount): add unit tests for discount calculator

覆盖场景：
- 正常百分比折扣
- 零折扣边界
- 满减触发与不触发
- 异常折扣率校验"

# 查看 commit 历史
git log --oneline -3
# a1b2c3d test(discount): add unit tests for discount calculator
# e4f5g6h feat(discount): add percentage and threshold discount calculator
```

#### 步骤2：创建 Merge Request

**目标**：推送分支并在 GitLab 上创建规范化的 MR。

```bash
# 推送分支
git push origin feature/add-discount-calculator

# GitLab 会在 push 后自动显示 "Create merge request" 链接
# 或者通过命令行创建：

# 方法A：git push 选项自动创建
git push origin feature/add-discount-calculator \
  -o merge_request.create \
  -o merge_request.target=main \
  -o merge_request.title="feat: add discount calculator service" \
  -o merge_request.description="## 概述
实现订单折扣计算服务，支持百分比折扣和满减折扣。

## 变更内容
- 新增 DiscountCalculator 类
- 实现 percentageDiscount、thresholdDiscount 两个方法
- 添加完整的边界条件校验
- 单元测试覆盖正常/边界/异常场景

## 测试方案
\`\`\`bash
npm test
\`\`\`

## 风险评估
- 低风险：新增独立模块，不影响现有订单流程
- 需要后续 PR 集成到 OrderService

## MR 类型
- [ ] Bug Fix
- [x] Feature
- [ ] Refactor
- [ ] Documentation" \
  -o merge_request.label="feature" \
  -o merge_request.assign="<reviewer-username>" \
  -o merge_request.remove_source_branch

# 方法B：使用 glab CLI
glab mr create \
  --title "feat: add discount calculator service" \
  --description-file mr-description.md \
  --label "feature,needs-review" \
  --assignee "@reviewer-username" \
  --target-branch main \
  --remove-source-branch \
  --squash-before-merge
```

#### 步骤3：进行 Code Review（作为 Reviewer）

**目标**：模拟 Review 流程，学习如何在 MR 中提出有效的 comments。

**Review 清单（Reviewer 视角）**：

```
□ 代码逻辑是否正确（核心关注点）
□ 边界条件是否处理（空值、负数、极大值）
□ 是否有潜在的性能问题（循环中的不必要的计算）
□ 是否有安全风险（注入、敏感数据泄露）
□ 单元测试是否覆盖了核心路径和边界
□ 命名是否清晰（变量名、函数名）
□ commit message 是否符合规范
```

**在 GitLab MR 页面上进行 Review**：

1. **Changes 选项卡** → 查看所有变更
2. **行内评论**：鼠标悬停在代码行号上 → "Add comment"
   - 例如：在 `price * (1 - discountRate)` 这行评论："这里如果是超大金额（如 1000 万），浮点数乘法会不会有精度问题？建议用 decimal.js 或改为整数计算"
3. **总结性评论**：在 Overview 选项卡底部写总体评价
4. **Approve**：确认所有问题已解决后点击 "Approve"

**如何提交 Review 后修改代码**：

```bash
# 开发者收到 Review 意见后修改代码
git checkout feature/add-discount-calculator

# 修改代码（例如添加精度处理）
# 编辑 src/services/discount.js，改用整数计算（分）避免浮点问题

git add src/services/discount.js
git commit -m "fix(discount): use integer arithmetic to avoid float precision issues

Review comment: 大金额场景下浮点数乘法可能有精度问题
解决方案: 改用分为单位进行整数计算"

# 直接 push 到同一分支，MR 会自动更新
git push origin feature/add-discount-calculator
```

#### 步骤4：配置 MR 模板与自动化

**目标**：创建 MR 模板，自动填充描述结构，减少重复工作。

```bash
# 创建 MR 模板文件
mkdir -p .gitlab/merge_request_templates
cat > .gitlab/merge_request_templates/Default.md << 'EOF'
## 概述
<!-- 用 1-2 句话描述这个 MR 做了什么 -->

## 变更内容
<!-- 列出主要的代码变更 -->
- 
- 

## 相关 Issue
Closes #

## 测试方案
<!-- 如何验证这个改动？贴出测试命令或步骤 -->
```bash

```

## 截图（如有 UI 变更）
<!-- 拖拽截图到此处 -->

## 风险评估
- [ ] 低风险（新增功能，不影响现有）
- [ ] 中风险（修改现有逻辑，需要回归测试）
- [ ] 高风险（修改核心模块，需要压测验证）

## MR 类型
- [ ] Bug Fix
- [ ] Feature
- [ ] Refactor
- [ ] Documentation
- [ ] Performance

## Checklist
- [ ] 代码已通过本地测试
- [ ] 已添加/更新单元测试
- [ ] commit message 符合 Conventional Commits 规范
- [ ] 已确认无调试代码/console.log 残留
EOF

git add .gitlab/merge_request_templates/
git commit -m "chore: add MR template for standardized reviews"
git push origin main
```

### 完整代码清单

- `src/services/discount.js`：折扣计算服务
- `tests/discount.test.js`：单元测试
- `.gitlab/merge_request_templates/Default.md`：MR 模板

### 测试验证

```bash
# 验证1：MR 状态检查
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/merge_requests?state=opened" | \
  python3 -c "
import json, sys
mrs = json.load(sys.stdin)
for mr in mrs:
    print(f'MR !{mr[\"iid\"]}: {mr[\"title\"]}')
    print(f'  State: {mr[\"state\"]}')
    print(f'  Merge Status: {mr[\"merge_status\"]}')
    print(f'  Has Conflicts: {mr[\"has_conflicts\"]}')
"

# 验证2：查看 MR 的讨论
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/merge_requests/<mr-iid>/discussions" | \
  python3 -c "
import json, sys
discussions = json.load(sys.stdin)
for d in discussions:
    for note in d.get('notes', []):
        print(f'  [{note[\"author\"][\"name\"]}]: {note[\"body\"][:100]}')
"

# 验证3：确认 Squash Merge 结果
git checkout main
git pull origin main
git log --oneline -1
# 应该只看到一个 commit，message 是 MR 的标题
```

## 4. 项目总结

### 优点 & 缺点

| 模式 | 优点 | 缺点 |
|------|------|------|
| Squash and Merge | main 历史最干净，每个 MR 一个 commit | 丢失了分支内的 commit 细节 |
| Merge Commit | 保留完整分支历史，开源项目标准做法 | main 历史图杂乱，merge commit 大量堆叠 |
| Rebase and Merge | 线性历史 + 保留独立 commit | 有冲突时需要手动 rebase |
| Draft MR | 提前获得反馈，避免返工 | 过度使用可能导致 MR 长期处于草稿状态 |

### 适用场景

- **Squash and Merge**：内部团队的标准策略，main 分支保持干净
- **Merge Commit**：大型开源项目、长期维护的公共仓库
- **Draft MR**：复杂功能的早期设计讨论、架构变更的方案演示

**不适用场景**：
- 单人项目且不需要 Review（直接 push 到 main 即可）
- 超紧急 hotfix（可以考虑先创建 MR 再手动 Approve+Merge，但要事后补 Review）

### 注意事项

- **不要 review 后直接 push 新 commit 而不回复评论**：每个 Review 评论都应该被 resolve 或讨论
- **Squash commit message 默认是 MR 标题**：如果 MR 标题是 "fix bug"，squash 后的 commit 就是 "fix bug"——毫无信息量
- **分支删除**：合并后删除源分支保持仓库整洁，但确认 CI/CD 不再依赖该分支

### 常见踩坑经验

1. **MR 合并后再次自动创建新 MR**：因为源分支没删除，后续 push 又触发了新的 MR。根因：合并时忘记勾选 "Delete source branch"。解决：养成勾选习惯，或设置项目默认删除。
2. **MR 合并按钮灰色但不显示原因**：检查 GitLab UI 合并框下方的提示——可能是 CI 未通过、有未解决的讨论、Code Owner 未审批、该分支有冲突等。
3. **Squash merge 后本地 main 落后**：Squash 产生了新的 commit hash，本地 main 的 HEAD 与之不同。根因：习惯用 `git pull` 而非先 fetch 再了解状态。解决：`git checkout main && git pull --rebase origin main`。

### 思考题

1. 如果你在 feature 分支上同时做了重构和新功能开发，Reviewer 建议拆成两个 MR，你应该如何将已有的 commit 分配到两个不同分支？
2. 一个 MR 被合并后，有人发现其中有 Bug 需要立即回滚。你应该用 `git revert` 回滚 squash commit，还是重新提交一个修复 MR？各有什么利弊？

> 答案见附录 D。

### 推广计划提示

- **开发**：本章是日常工作中最高频的操作，MR 模板、Conventional Commits、Squash 合并应成为团队标准
- **运维**：理解 MR 合并检查与 CI/CD 的联动（Pipelines must succeed），后续配置 CI 时知道要覆盖哪些检查
- **测试**：建立"每个 MR 必须有对应测试"的规范，利用 MR 模板的 Checklist 强制提醒
