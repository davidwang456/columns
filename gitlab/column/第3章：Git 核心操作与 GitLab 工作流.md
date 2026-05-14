# 第3章：Git 核心操作与 GitLab 工作流

## 1. 项目背景

> **业务场景**：一家公司的开发团队刚从 SVN 迁移到 GitLab。原 SVN 用户习惯了集中式版本控制，对 Git 的分布式模型始终一知半解——"为什么我 commit 了同事看不到？"、"rebase 和 merge 到底有什么区别？"、"为什么我 pull 的时候总是冲突？"

最惨烈的一次事故：小张在 feature 分支上做了两周的开发，准备合并到 main 时发现与同事的代码有 50+ 个文件冲突。为了"快速解决"，他用 `git merge main` 生成了一个大 merge commit，但里面混入了错误的冲突解决代码。合入 main 后，生产环境的支付模块直接挂了 3 小时。回滚时又因为不了解 `git revert` 和 `git reset` 的区别，直接把 main 分支 reset 到了两天前，导致中间其他同事的 commit 全部丢失……

**痛点放大**：很多人把 Git 当成"高级版 SVN"使用——只知道 commit、push、pull 三板斧，但不理解 Git 的对象模型和 DAG 版本图。遇到问题时只会搜索凑答案，不会从根本上分析。GitLab 的 Merge Request、CI Pipeline 等高级功能都建立在扎实的 Git 理解之上——如果连 rebase 和 merge 都分不清，Code Review 的质量就无从保证。

## 2. 项目设计——剧本式交锋对话

**场景**：午后，小胖一脸沮丧地从工位走过来。

---

**小胖**："大师救命！我刚刚 `git pull` 结果出来一堆 CONFLICT，我随便改了几行文件，然后 `git push`，现在 CI 全红了！"

**大师**："别急，我们先搞清楚你的 Git 仓库现在处于什么状态。`git log --oneline --graph --all` 看一下。"

**小胖**：（输入命令，看着满屏的星号线）"哇，这图也太复杂了，像地铁线路图一样……"

**大师**："这就是 Git 的版本图（DAG），每一个 commit 都是图上的一个节点，分支就是指向节点的指针。你这个 graph 看起来乱，是因为你在 push 之前没有先 `fetch` + `rebase`，而是直接 `pull`（等同于 fetch + merge），产生了一个毫无意义的 merge commit。"

**小白**："说到 rebase，我一直有个疑问——merge 和 rebase 到底有什么区别？为什么有些团队禁止用 rebase？"

**大师**："用一个生活场景来理解。假设你在抄一本小说的手稿（main 分支），你觉得开头不够精彩，于是自己撕下第一章（创建 feature 分支）开始改写。与此同时，原书作者也在修改其他章节并更新了手稿。" 

**小胖**："那 merge 就是——把我改好的第一章和我拿到的原书新章节，用订书机订在一起？"

**大师**："对，merge 会产生一个'合并 commit'，记录了你合并的时间点和两个人分别做了什么——历史完整，但有订书机的痕迹。rebase 则是——你偷偷把原书的最新章节拿过来，然后把你的改动重新写在最新版的基础之上。看起来就像你从一开始就在最新版上写的一样——历史干净，但你改写了时间线。"

**小白**："所以 rebase 会修改 commit hash？因为它把 commit 的 parent 改了？"

**大师**："技术映射——没错。`git rebase` 的本质是'变基'：把你的 commit 摘下来（cherry-pick），放到目标分支的最新 commit 之后重新提交。因为 parent 变了，commit hash 就变了。这就是为什么'不要 rebase 已经推送的分支'——别人已经基于你原来的 commit 做了工作，你改写历史后他们的基础就没了。"

**小胖**："那 GitLab Flow 又是什么？跟 Git Flow、GitHub Flow 有啥区别？"

**大师**："Git Flow 是最复杂的：有 main、develop、feature、release、hotfix 五类分支。适合有固定发布周期的传统软件，比如一个月发布一个版本。GitHub Flow 是最简单的：只有 main 和 feature 分支，feature 通过 PR 合入 main 后立即部署。适合持续部署的 SaaS 产品。"

**大师**："GitLab Flow 取了两者的折中——在 main 分支之外加了环境分支（如 staging、production）。feature 合入 main → 部署到 staging → 验证通过后合并到 production 分支。技术映射：GitLab Flow 用环境分支替代了 Git Flow 的 release 分支，更符合 GitLab CI/CD 环境的触发模型。"

**小胖**："那我们现在一个 5 人小团队，用哪个最合适？"

**大师**："5 人团队用简化版 GitLab Flow 就够了：main + feature 分支 + 一个 production 环境分支。feature 合入 main 自动部署到 staging 环境（如果有的话），手动或自动合并 main 到 production 完成上线。等你团队扩展到 20 人以上，再考虑引入类似 Git Flow 的 release 分支。"

---

## 3. 项目实战

### 环境准备

> **目标**：在本地模拟一个 3 人团队使用 GitLab Flow 完成一次完整的特性开发到合并上线流程。

**前置条件**：

| 工具 | 版本 | 用途 |
|------|------|------|
| Git | 2.40+ | 版本控制客户端 |
| GitLab CE | 17.x（参考第2章部署） | 远程仓库 |
| GitLab 账号 | 至少 3 个 | 模拟多人协作 |

### 分步实现

#### 步骤1：初始化项目仓库与分支保护规则

**目标**：在 GitLab 上创建项目，设置 main 分支保护规则。

```bash
# 在 GitLab UI 上创建项目
# 1. 登录 GitLab → New Project → Create blank project
# 2. Project name: "shop-api", Visibility: Private
# 3. 勾选 "Initialize repository with a README"
# 4. Default branch: main

# 克隆项目到本地
git clone http://gitlab.local:8929/root/shop-api.git
cd shop-api

# 初始化项目结构
mkdir -p src tests
echo "# Shop API" > README.md

# 添加 .gitignore
cat > .gitignore << 'EOF'
# Node
node_modules/
dist/
.env
*.log

# IDE
.vscode/
.idea/
*.swp
EOF

# 提交初始结构
git add .
git commit -m "chore: initialize project structure"
git push origin main
```

**在 GitLab UI 上配置分支保护**：
```
Settings → Repository → Protected branches
  - Branch: main
  - Allowed to merge: Maintainers only
  - Allowed to push: No one (禁止直接 push)
  - Code owner approval: 启用（需要 .gitlab/CODEOWNERS 文件）
```

#### 步骤2：创建 feature 分支并开发功能

**目标**：模拟开发者创建 feature 分支，开发一个"获取商品列表"的 API。

```bash
# 开发者A：创建 feature 分支
git checkout -b feature/product-list

# 开发代码
mkdir -p src/api
cat > src/api/products.js << 'EOF'
// 商品列表 API
module.exports = function getProducts(category) {
  const products = [
    { id: 1, name: '商品A', price: 99.9, category: 'electronics' },
    { id: 2, name: '商品B', price: 199.0, category: 'books' },
    { id: 3, name: '商品C', price: 29.9, category: 'electronics' },
  ];

  if (category) {
    return products.filter(p => p.category === category);
  }
  return products;
};
EOF

# 编写测试
cat > tests/products.test.js << 'EOF'
const getProducts = require('../src/api/products');

function runTests() {
  // 测试1：获取全部商品
  const all = getProducts();
  console.assert(all.length === 3, '应返回3个商品');

  // 测试2：按分类筛选
  const electronics = getProducts('electronics');
  console.assert(electronics.length === 2, '应返回2个电子产品');

  console.log('所有测试通过！');
}

runTests();
EOF

# 提交代码
git add src/api/products.js tests/products.test.js
git commit -m "feat: add product list API with category filter"

# 补充测试到 package.json（模拟真实项目）
cat > package.json << 'EOF'
{
  "name": "shop-api",
  "version": "1.0.0",
  "scripts": {
    "test": "node tests/products.test.js"
  }
}
EOF

git add package.json
git commit -m "chore: add test script"

# 查看 commit 历史（应该看到 2 个 commit）
git log --oneline -3

# 推送到远程
git push origin feature/product-list
```

#### 步骤3：模拟 main 分支更新——处理冲突（merge 方式）

**目标**：另一个开发者同时在 main 上提交了更改，模拟同步 main 的过程。

```bash
# 开发者B（在另一个目录模拟）：直接在 main 上修改了 products 模块
# （我们通过直接在本地 main 上模拟）
git checkout main
git pull origin main

# 模拟有人在 main 上修改了同一个文件
cat > src/api/products.js << 'EOF'
// 商品列表 API（main 分支版本 - 增加了库存字段）
module.exports = function getProducts(category) {
  const products = [
    { id: 1, name: '商品A', price: 99.9, category: 'electronics', stock: 10 },
    { id: 2, name: '商品B', price: 199.0, category: 'books', stock: 5 },
    { id: 3, name: '商品C', price: 29.9, category: 'electronics', stock: 20 },
    { id: 4, name: '商品D', price: 59.9, category: 'food', stock: 100 },
  ];

  if (category) {
    return products.filter(p => p.category === category);
  }
  return products;
};
EOF

git add src/api/products.js
git commit -m "feat: add stock field and new product D"
git push origin main

# 切回 feature 分支
git checkout feature/product-list

# ===== 场景A：使用 merge 方式同步 main =====
# 将 main 的最新代码合并到 feature
git merge main
# 此时可能会产生冲突——因为两个分支都修改了 products.js

# 如果有冲突，手动解决：
# 1. 打开 src/api/products.js，找到 <<<<< HEAD 标记
# 2. 手动合并两边的改动（保留 feature 的逻辑 + main 的 stock 字段）
# 3. git add src/api/products.js
# 4. git commit -m "merge: resolve conflict with main"

git log --oneline --graph -5
# 你会看到一个 merge commit，这不是最优雅的方式
```

#### 步骤4：模拟 main 分支更新——处理冲突（rebase 方式）

**目标**：用 rebase 方式替代 merge，保持分支历史整洁。

```bash
# 撤销刚才的 merge（假设还没 push）
git merge --abort  # 如果 merge 还没完成
# 或
git reset --hard HEAD~1  # 如果 merge commit 已生成但未 push

# ===== 场景B：使用 rebase 方式同步 main =====
# 1. 先 fetch 远程最新状态
git fetch origin

# 2. 将 feature 分支 rebase 到最新 main 之上
git rebase origin/main

# 如果产生冲突：
# - Git 会暂停 rebase，让你逐个解决冲突
# - 解决冲突后：git add <file> 然后 git rebase --continue
# - 如果想放弃：git rebase --abort

# 冲突解决示例：
# 编辑 src/api/products.js，保留两边的改动：
cat > src/api/products.js << 'EOF'
module.exports = function getProducts(category) {
  const products = [
    { id: 1, name: '商品A', price: 99.9, category: 'electronics', stock: 10 },
    { id: 2, name: '商品B', price: 199.0, category: 'books', stock: 5 },
    { id: 3, name: '商品C', price: 29.9, category: 'electronics', stock: 20 },
    { id: 4, name: '商品D', price: 59.9, category: 'food', stock: 100 },
  ];

  if (category) {
    return products.filter(p => p.category === category);
  }
  return products;
};
EOF

git add src/api/products.js
git rebase --continue

# 3. 查看 rebase 后的历史（应该在 main 最新提交之后，无 merge commit）
git log --oneline --graph -5
# 历史是一条直线！

# 4. force push（因为 rebase 改变了 commit hash）
git push --force-with-lease origin feature/product-list
# 注意：--force-with-lease 比 --force 更安全，会检查远程是否有他人的新提交
```

#### 步骤5：创建 Merge Request 并完成合并

**目标**：在 GitLab 上发起 MR，完成 Code Review 流程。

```bash
# 方法A：通过 GitLab UI 创建 MR
# 访问 http://gitlab.local:8929/root/shop-api/-/merge_requests/new
# Source branch: feature/product-list
# Target branch: main
# Title: "feat: add product list API with category filter"
# Description: 填写变更说明
# Assignee: 指定 reviewer
# 勾选 "Delete source branch when merge request is accepted"

# 方法B：通过命令行创建 MR
# 使用 glab CLI（需要先安装：brew install glab / apt install glab）
glab auth login --hostname gitlab.local:8929
glab mr create \
  --title "feat: add product list API with category filter" \
  --description "实现了商品列表接口，支持按分类筛选" \
  --target-branch main \
  --remove-source-branch

# 方法C：通过 git push 选项自动创建 MR（GitLab 17.x 支持）
git push origin feature/product-list \
  -o merge_request.create \
  -o merge_request.target=main \
  -o merge_request.title="feat: product list API" \
  -o merge_request.remove_source_branch
```

**在 GitLab UI 中完成 Review 流程**：
1. Reviewer 查看 Changes 选项卡，对具体行添加评论
2. 开发者回复评论或修改代码后 force push 更新 MR
3. 所有 threads resolved 后，Reviewer 点击 Approve
4. 满足合并条件后点击 Merge（选择 Squash and Merge 将 3 个 commit 压缩为 1 个）

### 完整代码清单

- `src/api/products.js`：商品列表 API 实现
- `tests/products.test.js`：单元测试
- `.gitignore`：忽略规则
- 完整 Git 命令流（步骤1-5）

### 测试验证

```bash
# 验证1：确认分支历史整洁
git log --oneline --graph --all
# 应该看到 main 是一条直线，无多余 merge commit

# 验证2：确认测试通过
node tests/products.test.js
# 输出：所有测试通过！

# 验证3：验证 MR 合并后 main 包含 feature 代码
git checkout main
git pull origin main
grep "getProducts" src/api/products.js
# 应输出函数定义

# 验证4：验证合并后 feature 分支已自动删除
git branch -r | grep feature/product-list
# 应无输出（如果 MR 勾选了删除源分支）
```

### 常见问题与解决

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `git push --force` 被拒绝 | 分支受保护，不允许 force push | 在 Settings → Repository 中临时放开，或使用 protected branch 的 allowed to force push |
| rebase 后 push 提示 non-fast-forward | commit hash 已变，普通 push 被拒绝 | 使用 `git push --force-with-lease` |
| 解决冲突时改错了 | 手动合并时遗漏了关键代码 | `git diff --name-only --diff-filter=U` 查看冲突文件，重新解决 |
| squashed merge 后本地分支不同步 | 本地 main 还指向旧的 commit | `git checkout main && git pull --prune` |

## 4. 项目总结

### 优点 & 缺点

| 策略 | 优点 | 缺点 |
|------|------|------|
| Git Flow | 结构清晰，适合固定发布周期 | 分支太多（5类），小团队开销大 |
| GitHub Flow | 极致简单，适合持续部署 | 无环境分支，多环境管理靠基础设施 |
| GitLab Flow | 折中方案，环境分支清晰，与 CI/CD 天然集成 | 需要理解环境分支的合并时机 |
| merge 同步 | 保留完整历史，可追溯 | 产生 merge commit，历史图杂乱 |
| rebase 同步 | 历史干净线性，Code Review 友好 | 改写历史，团队协作需要规范 |

### 适用场景

- **GitLab Flow**：使用 GitLab CI/CD 的团队、有多套环境（staging/production）的项目
- **merge 同步**：多人协作的公共分支（如 main）、需要保留合并记录的审计场景
- **rebase 同步**：个人 feature 分支同步 main、Code Review 前整理 commit 历史

**不适用场景**：
- 无需 Code Review 的个人项目（直接 push 到 main 即可）
- 强合规要求需要保留每一次 merge 记录的场景（避免 rebase）

### 注意事项

- **绝对不要 rebase 已经推送且他人基于此开发的公共分支**
- **feature 分支合并到 main 时，推荐使用 Squash and Merge**——将所有 commit 压缩成一个，保持 main 历史干净
- **冲突解决后务必运行测试**，防止解决冲突时引入新 bug
- **`.gitignore` 要尽早创建**，否则后期清理大文件（如 node_modules）需要 `git filter-branch` 或 `BFG Repo-Cleaner`

### 常见踩坑经验

1. **在 main 上直接开发**：没有创建 feature 分支就在 main 上写代码，push 时被保护规则拒绝。根因：不了解分支策略。解决：`git stash` → `git checkout -b feature/xxx` → `git stash pop`。
2. **merge commit 堆叠**：每次 pull 都用默认的 merge 策略，导致 `Merged 'origin/main' into main` 的 commit 堆了 20 个。根因：不了解 rebase。解决：`git pull --rebase` 或配置 `git config --global pull.rebase true`。
3. **误用 `git push -f` 覆盖了同事的提交**：在 main 分支上执行 force push。根因：不理解 force push 的危险性。解决：永远使用 `--force-with-lease`，且仅在 feature 分支上使用。

### 思考题

1. 如果你在执行 `git rebase main` 的过程中遇到冲突并解决了 3 次，但在第 4 次冲突时发现前面有一次解决错了，你应该如何回退到 rebase 之前的状态？如果已经 `rebase --continue` 了怎么办？
2. GitLab Flow 中，如果 staging 环境分支和 production 环境分支分别有不同的 hotfix，应该如何处理才不会丢失彼此的修复？

> 答案见附录 D。

### 推广计划提示

- **开发**：本章是后续 MR Review、CI/CD 的基础，务必将 rebase/merge/squash 的差异理解透彻
- **运维**：了解分支保护规则的配置，这是 GitLab 安全管理的核心功能之一
- **测试**：理解 feature 分支的工作流后，后续可以为特定分支类型配置不同的 CI 触发策略
