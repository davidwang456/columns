# 第22章：IaC 高级合规扫描与自定义 Rego 策略

> 版本：Trivy v0.50+
> 面向人群：DevOps、平台工程师、安全架构师

---

## 1. 项目背景

### 业务场景

云帆科技的 K8s 配置错误扫描已经覆盖了基础场景——`privileged: true`、缺少 `resource limits`、公开 S3 Bucket 等高危配置都能被 Trivy 内置规则捕获。但平台工程师小白最近遇到了一个头疼的需求。

公司的安全合规团队制定了一份《云原生安全基线 v3.0》，里面包含了 80 多条检查项。其中 60 条 Trivy 内置规则已经覆盖，但剩下的 20 条是公司的自定义要求：

- 所有 Pod 必须带有 `cost-center` 和 `owner` 标签，否则无法入账资源费用。
- 所有 Deployment 的 `replicas` 不能超过 50，防止某个服务独占集群资源。
- 所有容器镜像必须来自公司内部的 Harbor（`harbor.cloud-sail.internal`），禁止直接使用 Docker Hub。
- 所有 Secret 的 name 必须以项目代号开头（如 `payment-*`、`user-*`），防止跨项目误用。
- 所有 Ingress 必须启用 HTTPS，且 TLS 证书的 secret 必须存在于同一个 Namespace。

这些规则既不是通用的安全漏洞，也不是 CIS Benchmark 的标准项，而是「云帆科技特有的治理要求」。Trivy 的内置 Checks 完全不支持这类自定义逻辑。

小白一开始尝试用 shell 脚本做这些检查——`grep`、`awk`、`yq` 轮番上阵。脚本写了 500 多行，维护起来如同噩梦。更糟糕的是， shell 脚本无法处理变量引用、条件逻辑、模块复用，当 Terraform 的 `for_each` 展开后，脚本完全无法追踪实际创建的资源。

与此同时，公司的 Terraform 代码量已经增长到 10 万行，管理着 2000+ 云资源。每次 `terraform plan` 的输出可能有上百个资源的变更，人工审查不仅慢，而且容易遗漏。安全团队需要一个自动化的方案，在 `terraform apply` 之前就拦截不符合基线的变更。

### 痛点放大

**第一，内置规则无法覆盖企业自定义策略。** Trivy 的内置 Checks 基于业界通用最佳实践，但每个企业都有独特的治理要求——标签规范、命名约定、资源配额、网络隔离策略。没有自定义能力，安全基线就无法落地。

**第二，Shell/Python 脚本维护困难。** 用脚本解析 Terraform/K8s YAML 看似简单，但当遇到复杂语法（如 Terraform 的 `dynamic` 块、K8s 的 `helm template` 输出）时，脚本的脆弱性暴露无遗。

**第三，策略与代码分离。** 安全基线写在 Confluence 文档里，实际检查写在脚本里，两者很容易脱节。基线更新了，脚本没更新；或者脚本加了新检查，文档没同步。没有「策略即代码」（Policy as Code）的机制，治理就停留在纸面上。

**第四，缺少测试框架。** 写了一个检查规则，怎么验证它不会误报？怎么确保它覆盖了所有边界条件？传统脚本缺乏系统化的测试方法，导致规则上线后频繁误伤正常业务。

**本章的核心目标是：用 Open Policy Agent（OPA）的 Rego 语言编写自定义策略，通过 Trivy 的 `--config-policy` 机制对 Terraform、K8s YAML、CloudFormation 等企业 IaC 资产进行深度合规检查。**

---

## 2. 项目设计

**场景**：云帆科技的基线策略开发会，小白（平台工程师）、小胖（安全合规专员）和大师正在讨论如何落地自定义安全规则。

---

**小胖**：「安全团队发了 80 条基线，其中 20 条 Trivy 扫不出来。我写了几个 shell 脚本检查标签和命名规范，但 Terraform 的 `count` 和 `for_each` 把我搞崩溃了——脚本根本看不懂展开后的资源。」

**小白**：「Shell 脚本解析 Terraform 就是『用螺丝刀拧螺母』，工具选错了。Terraform 的 `plan` 输出是结构化的 JSON，K8s YAML 也是结构化的，你需要的是『结构化查询语言』，而不是文本处理工具。」

**大师**：「技术映射：Rego 就是 IaC 的 SQL。SQL 查询数据库里的结构化数据，Rego 查询 IaC 里的结构化配置。你可以问 SQL『找出所有年龄大于 30 岁的用户』，也可以问 Rego『找出所有没有 cost-center 标签的 Pod』。语法不同，但思维方式完全一样——声明式查询。」

**小胖**：「Rego 听起来像编程语言，难学吗？」

**小白**：「Rego 是声明式的，没有循环、没有赋值、没有副作用。你写『什么条件是违规的』，Rego 引擎帮你遍历所有数据找匹配。比如检查 Pod 标签：

```rego
violation[msg] {
  input.kind == "Pod"
  not input.metadata.labels["cost-center"]
  msg := sprintf("Pod %s missing cost-center label", [input.metadata.name])
}
```

这段代码的意思是：『如果资源的 kind 是 Pod，且 metadata.labels 里没有 cost-center，那么就产生一条违规信息』。非常直观。」

**小胖**：「那这些规则怎么和 Trivy 结合？Trivy 不是有自己的 Checks Bundle 吗？」

**大师**：「Trivy 从 v0.40+ 开始支持通过 `--config-policy` 加载自定义 Rego 策略。你可以这样运行：

```bash
trivy config --config-policy ./policies/ .
```

Trivy 会扫描目录下的所有 IaC 文件，同时用内置 Checks 和你的自定义 Rego 策略一起检查。违规结果会统一输出到报告中，不会分开展示。」

**小胖**：「那 Terraform Plan 怎么扫？Terraform 的 HCL 语法和 K8s YAML 不一样啊。」

**小白**：「Trivy 支持两种模式扫描 Terraform：

1. **扫描 `.tf` 源文件**：Trivy 解析 HCL 语法，转换为内部数据结构，然后运行 Rego 规则。
2. **扫描 `terraform plan` 的 JSON 输出**：`terraform show -json tfplan` 生成展开后的资源清单，Trivy 扫描这个 JSON，可以捕获变量展开后的实际值。

第二种更推荐，因为它能看到 `for_each`、`count`、`module` 展开后的真实资源，避免源文件中的『伪安全』——比如你在 `.tf` 里写了 `replicas = var.replica_count`，源文件扫描无法知道 `replica_count` 是不是超过了 50。」

**大师**：「还有测试的问题。Rego 有一个内置的测试框架，你可以写 `_test.rego` 文件，用 `opa test` 命令验证规则的正确性。比如：

```rego
test_pod_missing_label {
  violation["Pod bad-pod missing cost-center label"] with input as {
    "kind": "Pod",
    "metadata": {"name": "bad-pod", "labels": {"app": "test"}}
  }
}
```

这个测试确保：当输入是一个没有 cost-center 标签的 Pod 时，规则一定能产生对应的违规信息。你可以在 CI 中运行这些测试，确保策略变更是安全的。」

**小胖**：「那策略怎么分发？我们有 30 个 Terraform 仓库、50 个 K8s 仓库，总不能每个仓库都复制一份规则吧？」

**小白**：「把 Rego 策略放在独立的 Git 仓库（如 `cloud-sail/policies`），然后在各个仓库的 CI 中通过 `git submodule` 或 `curl` 拉取。更高级的方案是用 OPA Bundle 机制，把策略打包成 tar.gz，通过 HTTP 分发。Trivy 支持 `--config-policy` 指向本地目录或远程 URL。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+，已安装
- **OPA**：可选（用于本地测试 Rego 策略）
- **Terraform**：v1.5+（用于 Plan 扫描测试）
- **测试代码**：Terraform 模块和 K8s YAML

### 步骤一：编写自定义 Rego 策略

**目标**：实现云帆科技的 5 条自定义基线。

创建策略目录结构：

```
policies/
├── k8s/
│   ├── labels.rego
│   ├── replicas.rego
│   ├── image_source.rego
│   └── secret_naming.rego
├── terraform/
│   ├── s3_encryption.rego
│   └── ingress_tls.rego
└── tests/
    ├── labels_test.rego
    └── replicas_test.rego
```

**策略 1：强制 Pod 标签**

```rego
# policies/k8s/labels.rego
package cloud_sail.k8s.labels

deny[msg] {
  input.kind == "Pod"
  not input.metadata.labels["cost-center"]
  msg := sprintf("Pod %s missing required label: cost-center", [input.metadata.name])
}

deny[msg] {
  input.kind == "Pod"
  not input.metadata.labels["owner"]
  msg := sprintf("Pod %s missing required label: owner", [input.metadata.name])
}
```

**策略 2：限制 Deployment 副本数**

```rego
# policies/k8s/replicas.rego
package cloud_sail.k8s.replicas

deny[msg] {
  input.kind == "Deployment"
  input.spec.replicas > 50
  msg := sprintf("Deployment %s has %d replicas, max allowed is 50", 
    [input.metadata.name, input.spec.replicas])
}
```

**策略 3：强制内部镜像仓库**

```rego
# policies/k8s/image_source.rego
package cloud_sail.k8s.image_source

deny[msg] {
  input.kind == "Pod"
  container := input.spec.containers[_]
  not startswith(container.image, "harbor.cloud-sail.internal/")
  msg := sprintf("Container %s in Pod %s uses external image: %s", 
    [container.name, input.metadata.name, container.image])
}
```

**策略 4：Secret 命名规范**

```rego
# policies/k8s/secret_naming.rego
package cloud_sail.k8s.secret_naming

allowed_prefixes := {"payment-", "user-", "infra-", "shared-"}

deny[msg] {
  input.kind == "Secret"
  name := input.metadata.name
  not starts_with_allowed(name)
  msg := sprintf("Secret %s does not start with allowed prefix", [name])
}

starts_with_allowed(name) {
  prefix := allowed_prefixes[_]
  startswith(name, prefix)
}
```

**策略 5：Terraform S3 加密**

```rego
# policies/terraform/s3_encryption.rego
package cloud_sail.tf.s3

deny[msg] {
  input.type == "aws_s3_bucket"
  not input.values.server_side_encryption_configuration
  msg := sprintf("S3 bucket %s must have server-side encryption enabled", [input.values.bucket])
}
```

### 步骤二：编写 Rego 测试

**目标**：验证策略规则的正确性。

```rego
# policies/tests/labels_test.rego
package cloud_sail.k8s.labels

test_pod_with_all_labels {
  not deny with input as {
    "kind": "Pod",
    "metadata": {"name": "good-pod", "labels": {"cost-center": "cc123", "owner": "team-a"}}
  }
}

test_pod_missing_cost_center {
  deny["Pod bad-pod missing required label: cost-center"] with input as {
    "kind": "Pod",
    "metadata": {"name": "bad-pod", "labels": {"owner": "team-a"}}
  }
}

test_pod_missing_owner {
  deny["Pod bad-pod missing required label: owner"] with input as {
    "kind": "Pod",
    "metadata": {"name": "bad-pod", "labels": {"cost-center": "cc123"}}
  }
}
```

运行测试：

```bash
# 安装 OPA（可选，用于本地测试）
curl -L -o opa https://openpolicyagent.org/downloads/v0.60.0/opa_linux_amd64
chmod +x opa

# 运行测试
opa test policies/k8s policies/tests
```

**预期输出**：
```
policies/tests/labels_test.rego:
test_pod_with_all_labels: PASS (1.2ms)
test_pod_missing_cost_center: PASS (0.8ms)
test_pod_missing_owner: PASS (0.9ms)
PASS: 3/3
```

### 步骤三：用 Trivy 加载自定义策略扫描 K8s YAML

**目标**：验证自定义策略在 Trivy 中的实际运行效果。

创建测试 YAML：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: bad-pod
  labels:
    app: test
spec:
  containers:
  - name: app
    image: nginx:latest
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: huge-deployment
spec:
  replicas: 100
  selector:
    matchLabels:
      app: huge
  template:
    metadata:
      labels:
        app: huge
        cost-center: cc999
        owner: team-b
    spec:
      containers:
      - name: app
        image: harbor.cloud-sail.internal/app:v1
```

扫描：

```bash
trivy config --severity HIGH,CRITICAL \
  --config-policy ./policies/k8s \
  --namespaces cloud_sail.k8s \
  -o report.json \
  ./test-manifests/
```

**预期输出**（截取自定义规则部分）：
```
test-manifests/bad-manifest.yaml (kubernetes)
=============================================
Tests: 8 (SUCCESSES: 3, FAILURES: 5)

Custom Policy - labels - HIGH
═════════════════════════════
test-manifests/bad-manifest.yaml:2-8
Pod bad-pod missing required label: cost-center
Pod bad-pod missing required label: owner
Container app in Pod bad-pod uses external image: nginx:latest

Custom Policy - replicas - HIGH
═══════════════════════════════
test-manifests/bad-manifest.yaml:10-28
Deployment huge-deployment has 100 replicas, max allowed is 50
```

> **可能遇到的坑**：Trivy 的 `--namespaces` 参数用于指定加载哪些 Rego package。如果不指定，Trivy 会加载 `--config-policy` 目录下的所有 `.rego` 文件。

### 步骤四：扫描 Terraform Plan

**目标**：对 `terraform plan` 的输出进行策略检查。

```bash
# 生成 Plan
cd terraform-project
terraform plan -out=tfplan
terraform show -json tfplan > tfplan.json

# 用 Trivy 扫描 Plan
trivy config --config-policy ./policies/terraform \
  --namespaces cloud_sail.tf \
  tfplan.json
```

**优势**：
- Plan JSON 包含了变量展开后的实际值，可以检测到 `.tf` 源文件扫描无法发现的问题。
- 可以在 `terraform apply` 之前拦截违规变更。

### 步骤五：CI 中集成策略测试与扫描

**目标**：确保策略变更不会误报，且所有 IaC 都经过检查。

**GitLab CI 示例**：

```yaml
stages:
  - policy-test
  - iac-scan

policy-unit-test:
  stage: policy-test
  image: openpolicyagent/opa:0.60
  script:
    - opa test policies/k8s policies/terraform policies/tests
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes:
        - policies/**/*

iac-security-scan:
  stage: iac-scan
  image: aquasec/trivy:0.50.0
  script:
    - trivy config --config-policy ./policies/k8s --namespaces cloud_sail.k8s ./k8s/
    - |
      cd terraform && terraform plan -out=tfplan && terraform show -json tfplan > tfplan.json
    - trivy config --config-policy ./policies/terraform --namespaces cloud_sail.tf ./terraform/tfplan.json
```

### 步骤六：策略版本管理与分发

**目标**：建立企业级策略仓库，实现统一管理和版本控制。

创建策略仓库 `cloud-sail/policies`：

```
.
├── README.md
├── VERSION
├── k8s/
│   ├── labels.rego
│   ├── replicas.rego
│   └── ...
├── terraform/
│   ├── s3_encryption.rego
│   └── ...
├── tests/
│   └── ...
└── bundle.sh          # 打包脚本
```

`bundle.sh`：

```bash
#!/bin/bash
VERSION=$(cat VERSION)
BUNDLE="cloud-sail-policies-${VERSION}.tar.gz"

tar czf "$BUNDLE" k8s/ terraform/
echo "Bundle created: $BUNDLE"
```

消费方仓库的 CI 中拉取策略：

```yaml
before_script:
  - curl -L -o policies.tar.gz "https://artifacts.cloud-sail.internal/policies/latest.tar.gz"
  - tar xzf policies.tar.gz

iac-scan:
  script:
    - trivy config --config-policy ./k8s --namespaces cloud_sail.k8s ./
```

### 测试验证

1. 运行 `opa test`，确认所有策略单元测试通过。
2. 执行 `trivy config --config-policy ./policies/k8s`，确认自定义规则被正确加载并报告违规。
3. 生成 `tfplan.json` 并扫描，确认 Terraform 规则对展开后的资源生效。
4. 提交一个故意违规的 MR，验证 CI 中策略扫描失败并阻断合并。
5. 修改策略规则后再次运行 `opa test`，验证测试框架能捕获策略回归。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 表达能力 | Rego 声明式语法适合策略描述，避免脚本脆弱性 | 学习曲线较陡，团队需要投入时间学习 |
| 测试框架 | `opa test` 支持系统化测试，降低误报风险 | 测试覆盖不足时，规则上线仍可能误伤 |
| Trivy 集成 | 自定义策略与内置 Checks 统一输出 | `--namespaces` 和路径配置较复杂 |
| Plan 扫描 | 能检测变量展开后的实际值 | 需要额外生成 plan JSON，增加 CI 步骤 |
| 版本管理 | 策略可独立版本化、分发、复用 | 多仓库同步策略需要额外的发布流程 |

### 适用场景

1. **企业自定义安全基线**：标签规范、命名约定、资源配额、网络策略等企业特有治理要求。
2. **Terraform 事前拦截**：在 `terraform apply` 之前扫描 plan，防止高危云资源配置上线。
3. **K8s 多租户治理**：Namespace 隔离策略、Quota 限制、NetworkPolicy 强制等。
4. **合规即代码**：将等保/CIS 的条款转化为可执行、可测试、可审计的 Rego 策略。
5. **跨团队策略分发**：安全团队维护策略仓库，开发团队在 CI 中消费，实现治理标准化。

**不适用场景**：
1. 简单到用一条 `grep` 就能完成的检查——引入 Rego 反而增加复杂度。
2. 需要动态运行时决策的场景（如根据实时流量调整策略）——Rego 是静态策略语言。

### 注意事项

- **Rego 的「否定即失败」**：Rego 中 `not` 表示「无法证明为真」，与常规编程语言的「布尔取反」不同。写策略时要特别注意否定语义。
- **输入数据格式**：Trivy 传递给 Rego 的 `input` 结构与原生 YAML/JSON 有差异（如类型标记、空值处理），测试时要使用 Trivy 实际传入的数据结构。
- **性能考虑**：复杂的 Rego 规则（如深度嵌套循环）在大型 IaC 项目上可能运行缓慢。建议用 `opa bench` 测试性能。
- **版本兼容性**：Trivy 不同版本对 Rego 的支持可能有差异，升级 Trivy 后需重新验证自定义策略。

### 常见踩坑经验

**踩坑案例 1：Rego 规则在 OPA 测试中通过，但 Trivy 中不生效**
- **现象**：`opa test` 全部通过，但 `trivy config` 不报告预期的违规。
- **根因**：Trivy 传递给 Rego 的 `input` 结构比预期多了一层包装（如 `input.spec` vs `input.values`）。
- **解法**：用 `--debug` 查看 Trivy 实际传入的 JSON 结构，调整 Rego 中的字段路径。

**踩坑案例 2：Terraform Plan 扫描报 `unsupported block type`**
- **现象**：Trivy 扫描 `tfplan.json` 时解析失败。
- **根因**：Terraform 的 plan JSON 包含 Provider 特定的内部字段，Trivy 的解析器不支持。
- **解法**：升级 Trivy 到最新版；或过滤 plan JSON，只保留 `planned_values` 部分再扫描。

**踩坑案例 3：策略规则误报合法配置**
- **现象**：某个正常使用的 Ingress 被 Rego 规则判定为违规。
- **根因**：规则逻辑没有考虑合法例外（如内部工具允许 HTTP）。
- **解法**：在规则中增加 `exception` 集合，允许白名单中的资源绕过检查；并补充对应的 `_test.rego` 用例。

### 思考题

1. 假设你的公司有 200 条安全基线，其中 50 条是动态变化的（如每季度调整的资源配额上限）。请设计一个「策略参数化」方案：如何在不需要修改 Rego 代码的情况下，通过外部配置（如 YAML/JSON）调整策略的阈值和例外列表？
2. Rego 策略的测试用例需要手动编写，但当策略数量达到 200 条时，测试代码本身就成了维护负担。请设计一个「策略测试生成器」：如何根据策略的 deny 规则自动生成基本的边界测试用例？

> **答案提示**：第 30 章「企业级策略即代码体系」将深入探讨策略参数化、版本管理和跨组织的策略分发。

---

> **推广计划**：本章是平台工程团队和安全架构师的必读内容。建议成立「策略治理小组」，负责维护中央策略仓库（`cloud-sail/policies`），所有自定义基线必须写成 Rego 并附带测试用例。DevOps 团队在 Terraform 和 K8s 的 CI Pipeline 中强制集成 `trivy config --config-policy`。安全团队每季度 review 策略覆盖率，确保新的合规要求及时转化为可执行规则。开发团队在收到自定义策略的阻断时，先阅读策略文档中的「为什么」说明，理解基线背后的安全原理。
