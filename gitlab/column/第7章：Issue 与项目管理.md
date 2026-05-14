# 第7章：Issue 与项目管理

## 1. 项目背景

> **业务场景**：一家从传统外包转型的软件公司，项目管理和代码开发长期分离——产品经理用 Excel 管需求，开发用 GitLab 管代码，测试用 Redmine 提 Bug。结果是：一个功能从需求到上线，需要在三个系统间反复同步状态，信息丢失和沟通成本巨大。

有一次，产品经理在 Excel 里标记了一个需求为"已完成"，但开发实际上只完成了前端部分，后端接口还没做完。项目经理在周会上以为功能已交付，直接给客户做了演示——结果翻车了。追查原因时发现：Excel 里的状态是全凭产品经理手动更新的，没有和代码仓库、CI/CD 状态做任何关联。

更糟的是测试团队——他们发现 Bug 后在 Redmine 里建单，开发修完后在 GitLab commit 里写 "fixed #bug-1234"，但 Redmine 和 GitLab 没有集成，Bug 单永远停留在"待验证"状态。

**痛点放大**：GitLab 的 Issue 系统不是简单的"待办事项列表"，它天然与代码仓库、MR、CI Pipeline 联动——一个 Issue 可以直接关联到修复它的 MR，合并后 Issue 自动关闭。Issue Board 提供看板视图，Epic（EE）支持多项目里程碑跟踪。这些功能如果善加利用，完全可以把 Excel 和 Redmine 替换掉，实现"一个平台管全部"。

## 2. 项目设计——剧本式交锋对话

**场景**：周一 Sprint Planning 会议后，项目经理焦虑地翻着 Excel 表格。

---

**项目经理**："各位，我实在受不了了——需求在 Excel，Bug 在 Redmine，代码在 GitLab，每次同步都要在三个系统来回切换。有没有办法把项目管理和代码开发合到一起？"

**大师**："GitLab 的 Issue 系统就是为了解决这个痛点的。一个 Issue 可以由任意人创建——产品经理提需求、测试提 Bug、开发提技术债务。关键的是，Issue 可以关联到修复它的 MR，MR 合并后 Issue 自动关闭。"

**小胖**："那不就是 TODO List 吗？跟 Excel 有啥区别？"

**大师**："区别大了。Excel 里的任务是孤立的，但 GitLab Issue 可以关联到代码提交——当开发在 commit message 里写 'Fixes #42' 时，GitLab 会自动把这个 commit 链接到 Issue #42。MR 合并后，如果 MR 描述里写了 'Closes #42'，Issue #42 会自动关闭。技术映射——Excel 是照片，拍完就定格了；GitLab Issue 是监控摄像，全程记录需求的变化过程。"

**小白**："那 Issue Board 看板呢？我看它跟 Jira 的看板很像。"

**大师**："看板的核心是可视化工作流。你可以把 Issue 按标签状态映射到不同的列——'待开发'、'开发中'、'待测试'、'测试中'、'已完成'。当开发者创建 MR 并关联 Issue 后，MR 的合并会自动把 Issue 的标签从'开发中'改为'待测试'。完全不需要手动拖拽。"

**小胖**："那我们一个需求可能涉及前端和后端两个仓库，一个 Issue 能跨多个仓库吗？"

**大师**："这就是 Epic（EE 版功能）的作用。Epic 是跨项目的 Issue 容器——你可以创建一个 Epic '用户积分系统'，然后在这个 Epic 下创建多个 Issue，分别关联到前端仓库和后端仓库。Epic 有自己的看板和进度跟踪，就像一个高级版的 Milestone。"

**小白**："Quick Actions 是什么？我看有人评论 Issue 的时候打 `/assign` 之类的命令。"

**大师**："Quick Actions 是 GitLab 的快捷命令，可以在 Issue/MR 的评论框中直接执行操作，不需要去右侧栏点按钮。常用命令有：`/assign @someone`（指派）、`/label ~bug ~critical`（加标签）、`/estimate 2h`（预估工时）、`/spend 30m`（记录实际工时）、`/due tomorrow`（设置截止日期）。技术映射——Quick Actions 相当于 GitLab 的命令行界面，熟练后比鼠标点击快 5 倍。"

---

## 3. 项目实战

### 环境准备

> **目标**：为一个虚拟的电商项目搭建完整的项目管理工作流——创建 Issue、配置 Board、使用 Quick Actions 和 Milestone。

**前置条件**：GitLab 项目（参考第4章），有 Developer 以上权限。

### 分步实现

#### 步骤1：创建项目和 Issue 模板

**目标**：用 API 批量创建标准化的 Issue，并为不同类型的 Issue 创建模板。

**创建 Issue 模板**：

```bash
# 创建 Bug Report 模板
mkdir -p .gitlab/issue_templates
cat > .gitlab/issue_templates/Bug_Report.md << 'EOF'
## Bug 描述
<!-- 发生了什么问题？ -->

## 复现步骤
1. 
2. 
3. 

## 预期行为
<!-- 应该发生什么？ -->

## 实际行为
<!-- 实际发生了什么？ -->

## 截图/日志
<!-- 如有 -->

## 环境信息
- GitLab 版本: 
- 浏览器: 
- 操作系统: 

/label ~bug
/assign @tech-lead
EOF

# 创建 Feature Request 模板
cat > .gitlab/issue_templates/Feature_Request.md << 'EOF'
## 需求背景
<!-- 为什么需要这个功能？解决什么问题？ -->

## 功能描述
<!-- 具体要做什么？ -->

## 验收标准
- [ ] 
- [ ] 

## 涉及模块
<!-- 前端/后端/数据库等 -->

/label ~feature ~needs-priority
EOF

# 创建技术债务模板
cat > .gitlab/issue_templates/Tech_Debt.md << 'EOF'
## 问题描述
<!-- 当前代码的什么问题？ -->

## 影响范围
<!-- 影响了哪些功能？ -->

## 建议方案
<!-- 如何修复？ -->

## 紧急程度
- [ ] 低（暂不影响业务）
- [ ] 中（影响开发效率）
- [ ] 高（已造成线上问题）

/label ~tech-debt
EOF

git add .gitlab/issue_templates/
git commit -m "chore: add issue templates for bug/feature/tech-debt"
git push origin main
```

#### 步骤2：通过 API 批量创建 Sprint Issues

**目标**：模拟一次 Sprint Planning，用 API 批量创建 5 个 Issue。

```bash
export GITLAB_URL="http://gitlab.local:8929"
export GITLAB_TOKEN="glpat-xxxx"
export PROJECT_ID="<project-id>"

# 先创建 Milestone
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "title": "Sprint 1 - 基础功能上线",
    "description": "第一个 Sprint，完成商品展示和购物车的核心功能",
    "start_date": "2026-05-12",
    "due_date": "2026-05-26"
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/milestones"

# 批量创建 Issue（用关联数组定义每个 Issue 的属性）
declare -A issues=(
  ['商品列表页']='feature|high|实现商品列表的展示、搜索和分类筛选功能'
  ['商品详情页']='feature|medium|展示商品详情、价格、库存、用户评价'
  ['购物车-添加商品']='feature|high|实现购物车的添加、删除、修改数量功能'
  ['购物车-价格计算']='feature|high|根据商品价格、数量、优惠券计算购物车总价'
  ['支付接口对接']='feature|high|对接第三方支付，完成下单支付的闭环'
)

MILESTONE_ID=$(curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/milestones" | \
  python3 -c "import json,sys; m=json.load(sys.stdin); print(m[0]['id'])")

for title in "${!issues[@]}"; do
  IFS='|' read -r label priority desc <<< "${issues[$title]}"

  curl --request POST \
    --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    --header "Content-Type: application/json" \
    --data "{
      \"title\": \"$title\",
      \"description\": \"$desc\",
      \"labels\": \"$label,$priority\",
      \"milestone_id\": $MILESTONE_ID,
      \"weight\": $(( RANDOM % 5 + 1 ))
    }" \
    "$GITLAB_URL/api/v4/projects/$PROJECT_ID/issues"

  echo "创建 Issue: $title"
done
```

#### 步骤3：配置 Issue Board 看板

**目标**：配置与团队工作流匹配的看板视图。

**通过 UI 配置**：

```
项目 → Issues → Boards
→ 点击 "Create board" → 选择 "Development" 模板
```

**看板列设计**：

| 列名 | 过滤条件 | 含义 |
|------|---------|------|
| Open | 无 `~workflow::*` 标签 | 新建的、未被认领的 Issue |
| 待开发 | `~workflow::ready` | 已规划好、等待开发认领 |
| 开发中 | `~workflow::in-progress` | 正在进行开发 |
| 待测试 | `~workflow::in-review` | 开发完成，等待测试验证 |
| 已完成 | `~workflow::done` | 已合并、已验证 |
| Closed | Closed issues | 已关闭的 Issue |

**通过 API 创建 Board（带泳道）**：

```bash
# 创建自定义 Board
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "name": "Sprint 1 看板",
    "milestone_id": '"$MILESTONE_ID"',
    "labels": ["workflow::ready", "workflow::in-progress", "workflow::in-review", "workflow::done"]
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/boards"
```

#### 步骤4：使用 Quick Actions 和关联 MR

**目标**：用 Quick Actions 高效操作 Issue，并通过 MR 自动化 Issue 状态流转。

**Quick Actions 实操**：

```bash
# 在 Issue 评论框中使用以下命令：

# 认领 Issue
/assign @zhangsan
/label ~workflow::in-progress
/spend 1h  # 记录已花费 1 小时

# 提交 MR 后关联 Issue
# 在 Commit message 中：
git commit -m "feat: add product list page

Implement product listing with search and filter.
- Add ProductList component
- Add search API integration
- Add category filter dropdown

Related to #1"  # #1 是 Issue 编号（不是 iid）

# 在 MR 描述中：
# Closes #1  ← 合并后自动关闭 Issue #1

# Issue 自动流转状态
# 在 Issue 评论中：
/reopen                     # 重新打开已关闭的 Issue
/due 2026-05-20            # 设置截止日期
/remove_due_date            # 移除截止日期
/label ~high ~blocked      # 添加/替换标签（用 ~ 前缀）
/unlabel ~blocked           # 移除标签
/weight 3                   # 设置权重（优先级）
/copy_metadata #2          # 从 Issue #2 复制元数据（标签/指派人/里程碑）
```

**演示完整的 Issue 流转**：

```bash
# 1. 开发者在 Issue #1 评论框中输入：
# /assign @zhangsan
# /label ~workflow::in-progress
# /estimate 4h

# 2. 开发者在代码提交中关联：
git commit -m "feat: add product list page

Implements the main product listing with filter support.
Refs #1"

# 3. 开发者创建 MR 时在描述中填写：
# Closes #1

# 4. MR 合并后：
# - Issue #1 自动关闭
# - Issue #1 自动关联到合并的 commit
# - Issue #1 的 MR 列表中显示该 MR
```

### 完整代码清单

- `.gitlab/issue_templates/Bug_Report.md`：Bug 报告模板
- `.gitlab/issue_templates/Feature_Request.md`：功能需求模板
- `.gitlab/issue_templates/Tech_Debt.md`：技术债务模板
- `create-sprint-issues.sh`：批量创建 Issue 脚本（步骤2）

### 测试验证

```bash
# 验证1：查看里程碑进度
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/milestones/$MILESTONE_ID/issues" | \
  python3 -c "
import json, sys
issues = json.load(sys.stdin)
total = len(issues)
closed = sum(1 for i in issues if i['state'] == 'closed')
print(f'里程碑进度: {closed}/{total} ({closed/total*100:.1f}%)')
"

# 验证2：查看 Board 列统计
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/boards" | \
  python3 -c "
import json, sys
boards = json.load(sys.stdin)
for b in boards:
    print(f'Board: {b[\"name\"]}')
    for lst in b.get('lists', []):
        label = lst.get('label', {}).get('name', 'Open')
        count = lst.get('issues_count', 0)
        print(f'  [{label}]: {count} issues')
"

# 验证3：验证 Issue 与 MR 的关联
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/issues/1/related_merge_requests"
# 应返回关联的 MR 列表
```

## 4. 项目总结

### 优点 & 缺点

| 功能 | 优点 | 缺点 |
|------|------|------|
| Issue | 与代码/CI天然联动，状态自动流转 | 过于灵活，缺乏 Jira 那样结构化的字段 |
| Board | 可视化看板，支持多 Board 视图 | 高级配置（泳道、WIP限制）需要 EE |
| Epic (EE) | 跨项目聚合，适合大型需求管理 | CE 不可用，开源团队只能用 Milestone 折中 |
| Quick Actions | 键盘操作效率极高 | 命令需要记忆，新人学习成本 |
| Templates | 标准化 Issue 格式，减少沟通成本 | 模板更新后旧 Issue 不自动同步 |

### 适用场景

- **敏捷团队**：Sprint 规划 + Board + Milestone 覆盖 Scrum 核心需求
- **开源项目**：Bug Report 模板 + Quick Actions 让贡献者轻松提交高质量 Issue
- **中小团队替代 Jira**：前端的简单团队可以完全用 GitLab 替代 Jira

**不适用场景**：
- 需要复杂工时统计和财务报表的大企业（Jira 在报表方面更强）
- 需要严格的审批流和权限管理的合规场景

### 注意事项

- **Issue 的 `iid` 是项目内编号，#1、#2……，但跨项目引用需用 `username/project#iid` 格式**
- **Milestone 和 Epic 的区别**：Milestone 是时间维度的聚合（一个 Sprint），Epic 是主题维度的聚合（一个大功能跨多个 Sprint）
- **不要把所有讨论都放在 Issue 评论区**：长讨论建议转移到 MR 或独立文档中，保持 Issue 的可读性

### 常见踩坑经验

1. **Issue 自动关闭不是即时的**：MR 描述中写了 "Closes #42"，但合并后 Issue 还在 Open 状态。根因：MR 的 Target Branch 不是默认分支（main），GitLab 只在合并到默认分支时才自动关闭。解决方法：确保 MR Target 是 main 分支。
2. **Board 列表中 Issue 不显示**：看板的过滤条件太严格，把 Issue 都过滤掉了。根因：Board 创建时指定了多个 AND 条件的 label，导致没有 Issue 同时满足。解决方法：检查 Board 的过滤条件，或改用 OR 条件（逗号分隔 label）。
3. **Quick Actions 不生效**：在 Issue 评论中写了 `/assign @user` 但没生效。根因：Quick Actions 必须是评论的唯一一行，如果和其他文字混在一行就不会执行。解决方法：确保 Quick Action 独占一行。

### 思考题

1. 如果你需要管理一个跨 5 个微服务的大功能，每个微服务有自己的仓库。用 Issue + Milestone + Epic 应该怎么组织？
2. GitLab Issue 的 `weight` 字段默认是 1-10 的数字，但团队想用 T-shirt size（S/M/L/XL）来做估点，怎么实现？

> 答案见附录 D。

### 推广计划提示

- **产品经理**：学会使用 Issue 模板和 Board 后，可以完全替代 Excel 做需求管理
- **开发**：重点掌握 Quick Actions 和 Issue/MR 的关联联动，这能节省大量手动操作时间
- **测试**：Bug Report 模板应成为测试团队的标配，Quick Actions 中的 `/label ~bug` 可以自动触发 CI 回归测试
