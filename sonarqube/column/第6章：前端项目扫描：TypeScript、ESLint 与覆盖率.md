# 第6章：前端项目扫描：TypeScript、ESLint 与覆盖率

## 1. 项目背景

**业务场景**：某电商平台的前端团队维护着一个 React + TypeScript 的单体仓库（monorepo），包含 12 个业务包和 3 个共享组件库。团队在过去半年积累了 347 个 ESLint 警告，但没有人去修——因为"ESLint 报的是警告，不阻塞构建"。

一次线上事故让团队意识到了问题的严重性：一个 TypeScript `any` 类型的滥用导致 API 响应字段名拼写错误（`userName` 写成了 `uesrName`），TypeScript 编译器未能捕获，最终在生产环境引发了白屏。复盘时发现，这个问题 ESLint 报了 3 个月但无人关注。

团队决定引入 SonarQube 对前端代码也进行质量检查，目标是让前端代码和 Java 后端代码遵守同样的质量门禁标准——新增代码不能引入 Bug、安全漏洞和未处理的代码异味。

**痛点放大**：前端项目接入 SonarQube 时面临的特有问题：

- **语言鸿沟**：后端开发者熟悉的 Maven/Gradle 配置在前端世界不存在，配置 sonar-project.properties 成了第一个拦路虎。
- **构建产物缺失**：前端没有 `.class` 文件，SonarQube 如何分析 TypeScript？答案是纯源码分析，但有些规则需要编译后的 AST。
- **工具重叠**：已有的 ESLint、Prettier、Stylelint 和 SonarQube 的规则大量重叠，团队困惑"到底该信谁"。
- **覆盖率路径地狱**：Jest/Vitest 生成的 lcov.info 文件路径在 monorepo 中深埋在 `packages/*/coverage/lcov.info`，配置容易出错。

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（抓狂地切换 ESLint 和 SonarQube 的两套 Issue 列表）："大师！ESLint 说我的 `console.log` 不行，SonarQube 也说不行。我把 ESLint 的改掉了，SonarQube 那里还是红的。这两个到底是什么关系？我到底修哪边的？"

**大师**："先理解分工。ESLint 是**编码阶段**的守卫——你在本地写代码时就告诉你哪里有问题。SonarQube 是**质量门禁**——在 CI 阶段告诉你代码能不能合并。核心区别在于：ESLint 规则由团队自定义（想多严就多严），SonarQube 规则由质量团队统一管理（全局一致）。"

**小白**："但我发现同一个问题，ESLint 和 SonarQube 给出的修复建议不一样。比如 `==` vs `===` 的问题，ESLint 让我改成 `===`，SonarQube 说这是 Minor Code Smell。我信谁？"

**大师**："这种情况下的处理原则是：**ESLint 管编码规范，SonarQube 管质量红线**。`==` vs `===` 属于编码规范，ESLint 是第一责任人。SonarQube 把它标记为 Code Smell 也没错——但你可以考虑在 SonarQube 的 Quality Profile 中把这类纯编码规范问题降级或关闭，让 SonarQube 专注于 ESLint 无法覆盖的领域（如安全漏洞检测、跨文件复杂度分析、污点分析）。"

**小胖**："那具体哪些事是 ESLint 做不了的？"

**大师**："三件事：第一，**安全漏洞检测**。ESLint 有个安全插件但不专业。SonarQube 对 XSS、注入、不安全的正则等有专业的安全规则库。第二，**跨文件分析**。ESLint 是单文件分析，无法检测'这个组件在 20 个地方用 but 没人写测试'。第三，**质量趋势**。ESLint 没有历史数据，SonarQube 能告诉你'上个月新增了 5 个 Bug，修复了 3 个'。"

**小白**："那 TypeScript 项目的扫描需要编译吗？Java 项目不是要 `sonar.java.binaries` 吗？前端应该不需要吧？"

**大师**："TypeScript 项目的 SonarQube 分析是纯源码级别的——它直接解析 `.ts` 和 `.tsx` 文件，不需要先编译成 JavaScript。但有一个前提：**SonarQube 服务器上必须安装了 SonarJS/SonarTS 插件**（社区版默认包含）。扫描时，SonarScanner 会下载对应语言的分析器 JAR，用它来解析 TypeScript AST。"

**小胖**："覆盖率呢？我项目用 Jest 跑的测试，生成了 `lcov.info`。SonarQube 能直接读吗？"

**大师**："能。`lcov` 是 SonarQube 原生支持的覆盖率格式。你需要配置两个关键参数：

```properties
sonar.javascript.lcov.reportPaths=coverage/lcov.info
sonar.testExecutionReportPaths=test-report.xml
```

前者告诉 SonarQube 覆盖率数据在哪，后者告诉它测试结果（哪些通过了、哪些失败了）。对 monorepo 来说，如果有多个包各自有覆盖率报告，可以用逗号分隔多个路径。"

**小胖**："那 CSS-in-JS 和 Vue SFC 这种非标准文件能扫吗？我们的样式写在 `styled-components` 里，不算独立的 CSS 文件。"

**大师**："`styled-components` 中的样式目前 SonarQube 不能独立分析（因为它是运行时的模板字符串）。但如果你有独立的 `.css` 或 `.scss` 文件，SonarQube 有 CSS 规则集（如重复选择器、空样式规则）。对于 Vue SFC，SonarQube 支持 `.vue` 文件的扫描，能分别分析 `<template>`、`<script>` 和 `<style>` 块。"

**小白**："最后一个问题——我们前端是 monorepo，12 个 package。应该拆成 12 个 SonarQube 项目还是 1 个？"

**大师**："取决于你的团队组织。如果 12 个包由同一个团队维护、共享同一个发布周期 → 聚合为 1 个项目，用 `sonar.sources` 的逗号分隔覆盖所有包路径。如果 12 个包由不同团队维护 → 拆成 12 个项目，各自有独立的 Quality Gate 和 Issue 列表。折中方案：**1 个 monorepo 项目 + 在项目内通过目录筛选**，团队按包查看自己负责的 Issue。"

---

## 3. 项目实战

### 3.1 环境准备

- Node.js 18+
- npm/pnpm 包管理器
- SonarQube 10.7+ 实例
- 项目 Token

### 3.2 分步实现

**步骤 1：创建 React + TypeScript 示例项目**

```bash
mkdir frontend-sonarqube-demo && cd frontend-sonarqube-demo
npm init -y
npm install react react-dom typescript @types/react @types/react-dom
npm install -D jest ts-jest @types/jest @testing-library/react \
  @testing-library/jest-dom jest-environment-jsdom eslint
```

创建 `tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "outDir": "./dist",
    "rootDir": "./src"
  },
  "include": ["src"]
}
```

**步骤 2：编写包含质量问题的代码**

`src/components/LoginForm.tsx`：

```tsx
import React, { useState } from 'react';

interface LoginFormProps {
  onSubmit: (username: string, password: string) => void;
}

export const LoginForm: React.FC<LoginFormProps> = ({ onSubmit }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = (e: any) => { // any 类型滥用
    e.preventDefault();
    // 潜在 XSS：直接将用户输入拼接进 innerHTML
    const element = document.getElementById('message');
    if (element) {
      element.innerHTML = '<div>Welcome, ' + username + '</div>';
    }
    // 密码明文传输（安全风险）
    console.log('Login attempt:', username, password);
    onSubmit(username, password);
  };

  return (
    <form onSubmit={handleSubmit}>
      <input type="text" value={username}
        onChange={(e) => setUsername(e.target.value)} />
      <input type="password" value={password}
        onChange={(e) => setPassword(e.target.value)} />
      <button type="submit">Login</button>
    </form>
  );
};
```

`src/utils/validator.ts`：

```typescript
export function validateEmail(email: string): boolean {
  // 不安全的正则：ReDoS 攻击面
  const emailRegex = /^([a-zA-Z0-9]+)*@[a-zA-Z0-9]+(\.[a-zA-Z]{2,})+$/;
  return emailRegex.test(email);
}

export function calculateDiscount(price: number, discount: number): number {
  // 除零风险
  const rate = price / discount;
  return price - rate;
}

export function complexLogic(a: number, b: number, c: number,
                              d: number, e: number): number {
  let result: number = 0;
  if (a > 0) {
    if (b > 0) {
      if (c > 0) { result = 1; }
      else { result = 2; }
    } else {
      if (c > 0 && d > 0) { result = 3; }
      else if (d > 0) { result = 4; }
      else { result = 5; }
    }
  } else if (a == 0) {
    result = e > 0 ? 6 : 7;
  } else {
    // 空 catch 块
    try { result = 10 / a; }
    catch (ex) { }
  }
  return result;
}
```

**步骤 3：配置测试和覆盖率**

`src/utils/__tests__/validator.test.ts`：

```typescript
import { validateEmail, calculateDiscount } from '../validator';

describe('validateEmail', () => {
  it('should accept valid email', () => {
    expect(validateEmail('user@example.com')).toBe(true);
  });

  it('should reject invalid email', () => {
    expect(validateEmail('not-an-email')).toBe(false);
  });
});

describe('calculateDiscount', () => {
  it('should calculate 10% discount on 100', () => {
    expect(calculateDiscount(100, 10)).toBe(90);
  });
});
```

`jest.config.js`：

```javascript
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  collectCoverage: true,
  coverageDirectory: 'coverage',
  coverageReporters: ['lcov', 'text'],
  testMatch: ['**/__tests__/**/*.test.ts'],
};
```

**步骤 4：配置 SonarQube 扫描**

`sonar-project.properties`：

```properties
sonar.projectKey=com.example:frontend-demo
sonar.projectName=Frontend Demo (React + TypeScript)
sonar.projectVersion=1.0.0

sonar.host.url=http://localhost:9000
sonar.token=squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

sonar.sources=src
sonar.tests=src
sonar.test.inclusions=src/**/__tests__/**
sonar.sourceEncoding=UTF-8

# TypeScript 配置
sonar.typescript.tsconfigPath=tsconfig.json

# 覆盖率报告
sonar.javascript.lcov.reportPaths=coverage/lcov.info

# 排除配置文件和测试夹具
sonar.exclusions=**/jest.config.js,**/node_modules/**
```

**步骤 5：执行测试和扫描**

```bash
# 运行测试生成覆盖率报告
npx jest --coverage

# 下载并执行 SonarScanner
export SCANNER_VERSION=6.2.1.4610
wget -q https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SCANNER_VERSION}-linux-x64.zip
unzip -q sonar-scanner-cli-${SCANNER_VERSION}-linux-x64.zip
export PATH=$PATH:$(pwd)/sonar-scanner-${SCANNER_VERSION}-linux-x64/bin

# 执行扫描
sonar-scanner
```

**步骤 6：在 Web UI 查看结果**

访问 `http://localhost:9000/dashboard?id=com.example:frontend-demo`。

预期 Issue：

| 类型 | 内容 | 位置 |
|------|------|------|
| Vulnerability | XSS: innerHTML 拼接用户输入 | LoginForm.tsx |
| Vulnerability | 硬编码密码/明文日志 | LoginForm.tsx |
| Code Smell | `any` 类型使用 | LoginForm.tsx |
| Code Smell | 复杂度过高 | validator.ts |
| Code Smell | 空 catch 块 | validator.ts |
| Bug | 除零风险 | validator.ts |

### 3.3 monorepo 扫描配置

如果项目是 monorepo（如 Nx、Turborepo、pnpm workspace），推荐配置：

```properties
sonar.sources=packages/web/src,packages/mobile/src,packages/shared/src
sonar.tests=packages/web/src,packages/mobile/src,packages/shared/src
sonar.javascript.lcov.reportPaths=packages/web/coverage/lcov.info,packages/mobile/coverage/lcov.info,packages/shared/coverage/lcov.info
```

### 3.4 ESLint 结果导入

如果想让 ESLint 和 SonarQube 分工明确，将 ESLint 外部结果导入 SonarQube：

```bash
# 生成 ESLint JSON 报告
npx eslint src --format json > eslint-report.json

# 在 sonar-project.properties 中添加：
# sonar.eslint.reportPaths=eslint-report.json
```

### 3.5 验证

```bash
# 检查项目质量门禁
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/qualitygates/project_status?projectKey=com.example:frontend-demo" \
  | python3 -m json.tool

# 验证覆盖率数据
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.example:frontend-demo&metricKeys=coverage,lines_to_cover" \
  | python3 -m json.tool
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | SonarQube（前端） | ESLint + Prettier |
|------|------------------|-------------------|
| 安全漏洞检测 | ✅ XSS、注入、ReDoS 等 | ❌ 需要额外插件且覆盖不全 |
| 代码异味（逻辑） | ✅ 复杂度、空 catch、除零 | ❌ 侧重语法/格式 |
| 历史趋势 | ✅ 质量趋势图表 | ❌ 无 |
| 实时反馈 | ❌ 仅在扫描时反馈 | ✅ 保存时即反馈 |
| 编码风格 | 🟡 有限（命名、格式） | ✅ 全覆盖 |
| 自定义规则 | 🟡 困难（需插件开发） | ✅ 灵活（自定义 plugin） |

**建议分工**：ESLint/Prettier 管编码风格和格式化，SonarQube 管安全和质量红线。SonarQube 的规则中如果和 ESLint 高度重叠（如 `==` vs `===`），可考虑在 Quality Profile 中关闭。

### 4.2 适用场景

- **中大型前端项目**：代码量 > 5 万行，需要历史趋势和团队协作
- **安全敏感的前端应用**：金融、医疗等有合规要求的场景
- **前后端统一治理**：与 Java 后端共用同一质量平台
- **monorepo 管理**：多包聚合展示质量指标

**不适用场景**：
- 纯静态博客/个人主页（项目太小）
- 实时编码反馈（应使用 SonarLint + ESLint）

### 4.3 注意事项

1. **TypeScript 规则数量有限**：TypeScript/JavaScript 规则约 200+ 条，不如 Java（600+）全面。对于 TS 特有的类型问题，依赖 TypeScript 编译器本身。
2. **路径区分大小写**：`sonar.javascript.lcov.reportPaths` 中的路径在 Linux 上区分大小写，务必和 `jest.config.js` 中配置的 `coverageDirectory` 一致。
3. **node_modules 排除**：务必添加 `sonar.exclusions=**/node_modules/**`，否则扫描时间可能增长 10 倍。
4. **`tsconfig.json` 位置**：如果 `tsconfig.json` 不在项目根目录，使用 `sonar.typescript.tsconfigPath` 指定路径。

### 4.4 常见踩坑经验

**故障 1：覆盖率始终为 0%**

根因：Jest 生成的 `lcov.info` 中源文件路径是绝对路径，而 SonarScanner 期望相对路径。解决：在 `jest.config.js` 中设置 `coverageReporters: [['lcov', { 'projectRoot': '..' }]]`（Jest 29+）或确保执行 Jest 时的 `rootDir` 与 SonarScanner 扫描目录一致。

**故障 2：TypeScript 文件被当作 JavaScript 分析，Issue 很少**

根因：未配置 `sonar.typescript.tsconfigPath`。SonarScanner 通过此配置确认 TS 语法特性，没有 tsconfig 时会降级为 ES5 分析。

**故障 3：扫描结果包含上万个 ESLint Issue（导入外部结果时）**

根因：同时使用了 SonarQube 内置规则和导入的 ESLint 报告，导致每个问题出现两次。解决：选择一种方式为主——要么用 SonarQube 内置规则（关闭 ESLint 导入），要么导入 ESLint 结果（在 SonarQube Profile 中关闭对应规则）。

### 4.5 思考题

1. 前端 monorepo 中，分包独立发布（不同版本号），应该拆分 SonarQube 项目还是合并？如何设计 projectKey 命名规范？
2. 当一个问题同时被 ESLint 和 SonarQube 报告，你如何决定"以谁为准"来修复？团队如何避免"两个工具两套标准"的混乱？

> **答案提示**：第1题考虑独立发布的包通常由不同团队维护，建议拆分子项目。第2题的策略是以"最严格的那个"为准，或将编码风格类规则统一交给 ESLint，SonarQube 专注安全/缺陷类规则。

---

> **推广计划提示**：前端团队的 SonarQube 落地通常比后端团队慢——因为前端开发者对 SonarQube 的认知度更低。建议先在 2-3 个人的前端小组试点 2 周，产出"前端接入 SonarQube 的傻瓜式教程"（含配置模板 + 常见 Issue 修复指南），再全团队推广。
