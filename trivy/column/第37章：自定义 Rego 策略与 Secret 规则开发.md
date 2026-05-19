# 第37章：自定义 Rego 策略与 Secret 规则开发

> 版本：Trivy v0.50+
> 面向人群：安全工程师、平台工程师、合规专员
> 源码参考：pkg/misconf/scanners/、pkg/fanal/secret/、pkg/detector/secret/

---

## 1. 项目背景

### 业务场景

云帆科技在第 22 章掌握了 OPA/Rego 的基础用法，在第 30 章建立了企业级策略即代码体系。但当一个金融客户提出了「金融级 K8s 安全基线」需求时，团队发现之前的策略集不够用——客户要求覆盖 Pod Security Context、Network Policy、Resource Quota、Pod Disruption Budget 四个维度的 10 条硬性规则，每一条规则需要覆盖多种资源的边界条件，每条规则必须有可验证的测试用例。

同时，安全团队需要扩展 Secret 检测能力。Trivy 内置的 Secret 规则覆盖了 AWS Key、GitHub Token 等通用场景，但云帆科技内部有一套自己的 Token 格式（以 `cs-` 开头，32 位 Base62 编码），目前 Trivy 无法识别。如果代码中包含这种 Token，Trivy 的 Secret 扫描会静默放过。

「我们需要两套能力。」老李在需求会上总结，「第一，一套『金融级』的 Rego 策略库，覆盖 K8s 安全的四大维度，有边界测试验证。第二，一套自定义 Secret 规则，识别公司内部的敏感凭证格式。」

### 痛点放大

**第一，Rego 策略的复杂度过高。** 基础篇的 Rego 策略大多是「检查某个字段是否存在」。但金融级策略需要处理多条件组合、资源间的交叉验证、默认值的正确处理。编写这类策略需要系统化的 Rego 技巧。

**第二，缺乏系统化的测试方法论。** 没有单元测试的策略是危险的——你可能在「修复」策略时引入了回归 bug，导致正常资源被误阻断。OPA Test 提供了测试框架，但如何设计测试用例、如何覆盖边界条件——这些是需要系统性训练的。

**第三，Secret 规则的自定义能力被低估。** Trivy 的 Secret 检测不仅仅支持正则——它还支持熵值计算、关键词过滤、allow/deny 列表。理解这些高级特性可以将检测的误报率降低 50% 以上。

**第四，策略的性能需要关注。** 在 K8s 集群中，Trivy Operator 每小时对几万个资源执行 Rego 策略。如果某条策略写得低效（如 O(n*n) 遍历），可能拖慢整个集群的扫描速度。

**本章的核心目标是：开发一套包含 10 条规则的「金融级 K8s 安全基线」Rego 策略库（含 OPA Test 测试），同时编写自定义 Secret 检测规则，实现正则、熵值、关键词三重检测的组合拳。**

---

## 2. 项目设计

**场景**：云帆科技的安全策略开发冲刺，小胖（合规专员）、小白（平台工程师）、大师在讨论如何让规则滴水不漏。

---

**小胖**：「客户提了 4 大维度、10 条规则。每一条都要覆盖 Deployment、StatefulSet、DaemonSet、CronJob。这工程量不小啊。」

**小白**：「不能每条规则单独写——那会有大量重复代码。我们需要『规则框架』：把通用的『遍历所有容器』逻辑抽取出来，每条具体规则只写自己的判断条件。Rego 支持模块化——你可以用 import 导入公共模块。」

**大师**：「技术映射：写 Rego 策略就像写法律条文。法律正文（规则逻辑）要精简、无歧义；实施细则（测试用例）要覆盖所有可能的情况；司法解释（注释）要解释为什么这样规定。三条缺一不可。」

**小胖**：「那 Secret 规则呢？我们公司的 Token 格式是 `cs-[A-Za-z0-9]{32}`，但只用正则匹配的话，误报率太高了——很多代码里的测试数据也可能匹配这个模式。」

**大师**：「单靠正则确实不够。Trivy 的 Secret 检测有三层：

**第一层：正则匹配。** 用模式识别符合条件的字符串。这是最粗粒度的——能匹配候选者，但误报率高。

**第二层：熵值过滤。** 真正的 Secret 是随机字符串，信息熵高。测试数据或示例值熵值低。通过计算字符串的 Shannon Entropy 可以过滤掉大部分假阳性。一般来说，熵值 > 4.0 才可能是 Secret。

**第三层：关键词上下文。** 如果匹配的字符串旁边出现了 `password`、`secret`、`token`、`key` 等关键词，可信度更高。如果出现在 `example`、`test`、`mock` 等词旁边，可能是测试数据。

三层叠加：正则匹配 → 熵值过滤 → 关键词上下文 → 最终判定。这是 Trivy Secret 引擎的核心算法。」

**小白**：「我们还需要关注性能。Rego 的策略执行次数 = 扫描资源数 × Pod 内容器数 × 规则数。假设 1000 个 Deployment × 3 个容器 × 10 条规则 = 30000 次策略评估。如果某条策略写低效了，就会成为热点。」

**大师**：「优化要点：

1. **提前退出**：把最快能确定『无违规』的条件放在最前面。比如先检查 `input.kind` 是不是目标类型，不是就直接退出。
2. **避免 O(n*n) 嵌套**：不要对同一个数组做两次遍历。用 Rego 的 set 操作取差集。
3. **利用 Rego 的编译缓存**：Trivy 会对 Rego 策略做编译缓存——相同策略不重复编译。
4. **合理分段**：不要把 10 条规则写在一个巨大文件里——按大类分文件，独立编译、独立执行。」

---

## 3. 项目实战

### 环境准备

- **OPA**：v0.60+（`opa` CLI）
- **Trivy**：v0.50+
- **测试 K8s YAML**：用于验证规则

### 步骤一：搭建 Rego 规则框架

**目标**：创建金融级 K8s 安全基线的目录结构和公共模块。

```bash
mkdir -p finsec-policies/{rules,tests,lib}
```

创建公共模块 `finsec-policies/lib/common.rego`：

```rego
package lib.common

# 获取 Pod 模板中的所有容器（支持 Deployment/StatefulSet/DaemonSet/CronJob）
get_containers(resource) := containers {
    containers := object.get(resource, ["spec", "template", "spec", "containers"], [])
} else := containers {
    containers := object.get(resource, ["spec", "jobTemplate", "spec", "template", "spec", "containers"], [])
} else := containers {
    containers := object.get(resource, ["spec", "containers"], [])
}

# 检查资源是否有 Pod 模板
is_pod_resource(resource) {
    resource.kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "CronJob", "Job", "Pod"}
}

# 获取资源标签（兼容 Pod template 和顶层 metadata）
get_labels(resource) := labels {
    labels := object.get(resource, ["metadata", "labels"], {})
}
get_pod_labels(resource) := labels {
    labels := object.get(resource, ["spec", "template", "metadata", "labels"], {})
}

# 检查是否是生产 Namespace
is_production(resource) {
    ns := object.get(resource, ["metadata", "namespace"], "default")
    contains(ns, "prod")
}
```

### 步骤二：编写 10 条金融级 Rego 规则

创建 `finsec-policies/rules/pod-security.rego`（PodSecurityContext 维度）：

```rego
package finsec.pod_security

import data.lib.common

# FS-001: 所有容器必须以非 root 运行
deny[msg] {
    common.is_pod_resource(input)
    containers := common.get_containers(input)
    container := containers[_]
    not container.securityContext.runAsNonRoot == true
    msg := sprintf("%s/%s: container '%s' must set runAsNonRoot: true",
        [input.kind, input.metadata.name, container.name])
}

# FS-002: 所有容器必须丢弃 ALL capabilities
deny[msg] {
    common.is_pod_resource(input)
    containers := common.get_containers(input)
    container := containers[_]
    caps := object.get(container, ["securityContext", "capabilities", "drop"], [])
    not caps[_] == "ALL"
    msg := sprintf("%s/%s: container '%s' must drop ALL capabilities",
        [input.kind, input.metadata.name, container.name])
}

# FS-003: 禁止特权容器
deny[msg] {
    common.is_pod_resource(input)
    containers := common.get_containers(input)
    container := containers[_]
    container.securityContext.privileged == true
    msg := sprintf("%s/%s: container '%s' must not run as privileged",
        [input.kind, input.metadata.name, container.name])
}

# FS-004: 必须使用只读根文件系统
deny[msg] {
    common.is_pod_resource(input)
    containers := common.get_containers(input)
    container := containers[_]
    not container.securityContext.readOnlyRootFilesystem == true
    msg := sprintf("%s/%s: container '%s' must set readOnlyRootFilesystem: true",
        [input.kind, input.metadata.name, container.name])
}

# FS-005: 必须限制 seccomp 和 AppArmor（生产环境）
deny[msg] {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    common.is_production(input)
    annotations := object.get(input, ["spec", "template", "metadata", "annotations"], {})
    not annotations["container.apparmor.security.beta.kubernetes.io"]
    msg := sprintf("%s/%s: production Pods must have AppArmor annotation configured",
        [input.kind, input.metadata.name])
}
```

创建 `finsec-policies/rules/network.rego`（NetworkPolicy 维度）：

```rego
package finsec.network

import data.lib.common

# FS-006: Production Namespace 必须有默认拒绝的 NetworkPolicy
deny[msg] {
    input.kind == "Namespace"
    common.is_production(input)
    # 简化检查：需要在外层验证是否存在 NetworkPolicy
    msg := sprintf("Namespace %s should have a default-deny NetworkPolicy", [input.metadata.name])
}

# FS-007: 所有 Pod 必须至少有一个 NetworkPolicy 选择它（生产环境）
deny[msg] {
    input.kind in {"Deployment", "StatefulSet", "DaemonSet"}
    common.is_production(input)
    labels := common.get_pod_labels(input)
    count(labels) == 0
    msg := sprintf("%s/%s: production Pods must have labels for NetworkPolicy matching",
        [input.kind, input.metadata.name])
}
```

创建 `finsec-policies/rules/resources.rego`（ResourceQuota 维度）：

```rego
package finsec.resources

import data.lib.common

# FS-008: 所有容器必须有 CPU 和 Memory 的 requests 和 limits
deny[msg] {
    common.is_pod_resource(input)
    containers := common.get_containers(input)
    container := containers[_]
    resources := object.get(container, "resources", {})
    requests := object.get(resources, "requests", {})
    limits := object.get(resources, "limits", {})
    not (requests.cpu and requests.memory and limits.cpu and limits.memory)
    msg := sprintf("%s/%s: container '%s' must define CPU and Memory requests and limits",
        [input.kind, input.metadata.name, container.name])
}

# FS-009: 生产环境 Memory limits 不能超过 4Gi（防止 OOM）
deny[msg] {
    common.is_pod_resource(input)
    common.is_production(input)
    containers := common.get_containers(input)
    container := containers[_]
    mem_limit := object.get(container, ["resources", "limits", "memory"], "0")
    parse_memory(mem_limit) > 4 * 1024 * 1024 * 1024
    msg := sprintf("%s/%s: container '%s' memory limit exceeds 4Gi (currently %s)",
        [input.kind, input.metadata.name, container.name, mem_limit])
}
```

创建 `finsec-policies/rules/disruption.rego`（PodDisruptionBudget 维度）：

```rego
package finsec.disruption

# FS-010: 生产环境 Deployment（replicas>1）必须有对应的 PDB
deny[msg] {
    input.kind in {"Deployment", "StatefulSet"}
    replicas := object.get(input, ["spec", "replicas"], 0)
    replicas > 1
    ns := object.get(input, ["metadata", "namespace"], "")
    contains(ns, "prod")
    # 验证逻辑：需要在实际扫描时提供集群中的 PDB 列表
    msg := sprintf("%s/%s: production workload with %d replicas should have a PodDisruptionBudget",
        [input.kind, input.metadata.name, replicas])
}
```

### 步骤三：编写 OPA Test 测试用例

创建 `finsec-policies/tests/test_pod_security.rego`：

```rego
package finsec.pod_security

test_run_as_non_root_missing {
    violations := deny with input as {
        "kind": "Deployment",
        "metadata": {"name": "bad-deploy", "namespace": "prod"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "app",
                        "image": "app:latest",
                        "securityContext": {"privileged": false}
                        # 缺少 runAsNonRoot
                    }]
                }
            }
        }
    }
    count(violations) == 1
    contains(violations[_], "runAsNonRoot")
}

test_run_as_non_root_compliant {
    violations := deny with input as {
        "kind": "Deployment",
        "metadata": {"name": "good-deploy"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "app",
                        "image": "app:latest",
                        "securityContext": {
                            "runAsNonRoot": true,
                            "privileged": false
                        }
                    }]
                }
            }
        }
    }
    count(violations) == 0
}

test_privileged_container_detected {
    violations := deny with input as {
        "kind": "Pod",
        "metadata": {"name": "bad-pod"},
        "spec": {
            "containers": [{
                "name": "app",
                "image": "app:latest",
                "securityContext": {
                    "privileged": true,
                    "runAsNonRoot": true
                }
            }]
        }
    }
    count(violations) == 1
    contains(violations[_], "privileged")
}

test_multiple_containers_checked {
    violations := deny with input as {
        "kind": "Deployment",
        "metadata": {"name": "multi-cont"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "good", "image": "good", "securityContext": {
                            "runAsNonRoot": true,
                            "capabilities": {"drop": ["ALL"]}
                        }},
                        {"name": "bad", "image": "bad", "securityContext": {
                            "runAsNonRoot": false,
                            "privileged": true
                        }}
                    ]
                }
            }
        }
    }
    count(violations) >= 2
}

test_cronjob_checked {
    violations := deny with input as {
        "kind": "CronJob",
        "metadata": {"name": "bad-cron"},
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{
                                "name": "job",
                                "image": "job:latest",
                                "securityContext": {"privileged": true}
                            }]
                        }
                    }
                }
            }
        }
    }
    count(violations) >= 1
}
```

运行测试：

```bash
opa test finsec-policies/rules/ finsec-policies/tests/ -v

# 输出：
# PASS: 5/5
# test_run_as_non_root_missing: PASS
# test_run_as_non_root_compliant: PASS
# test_privileged_container_detected: PASS
# test_multiple_containers_checked: PASS
# test_cronjob_checked: PASS
```

### 步骤四：编写自定义 Secret 检测规则

**目标**：创建检测云帆内部 Token 格式的 Secret 规则。

创建 `custom-secret/cloudsail-secret.yaml`：

```yaml
# Trivy 自定义 Secret 规则配置
allow-rules:
  # 排除明显是示例/测试的值
  - id: cloudsail-token-example
    path: .*
    description: Exclude example tokens
    allow:
      - "cs-EXAMPLE0000000000000000000000000"
      - "cs-TEST0000000000000000000000000000"
      - "cs-DUMMY000000000000000000000000000"

rules:
  # 自定义规则：云帆内部 Token 格式
  - id: cloudsail-internal-token
    title: Cloud Sail Internal API Token
    severity: CRITICAL
    description: |
      Detects Cloud Sail internal API tokens (cs- + 32 hex characters).
      These tokens grant access to internal services.
    path: .*
    # 正则：cs- 前缀 + 32位 hex
    regex: cs-[a-f0-9]{32}
    # 关键词：在这些关键词附近时提高可信度
    keywords:
      - "CLOUDSAIL_TOKEN"
      - "CS_API_KEY"
      - "cs_token"
      - "cloudsail_secret"
    # 需要允许的小写关键词（排除）
    ignore:
      - "example"
      - "test"
      - "mock"
      - "sample"
      - "placeholder"

  # 自定义规则：内部数据库连接串
  - id: cloudsail-db-connection-string
    title: Cloud Sail Internal Database Connection
    severity: HIGH
    description: |
      Detects connection strings to internal databases containing credentials.
    path: .*
    regex: '(postgres|mysql|mongodb)://[^:]+:[^@]+@[^/]+/cloudsail'
    keywords:
      - "DATABASE_URL"
      - "DB_CONNECTION"
      - "JDBC_URL"
    allow:
      - "postgres://localhost:5432/cloudsail"
      - "mysql://root:@localhost:3306/cloudsail"
```

### 步骤五：在 Secret 规则中使用熵值过滤

**目标**：编写辅助脚本，验证候选 Token 的熵值，降低误报。

创建 `secret-entropy-checker.py`：

```python
#!/usr/bin/env python3
"""
Secret 熵值过滤器
用于验证自定义 Secret 规则的准确性
"""

import math
import re
from collections import Counter


def shannon_entropy(data: str) -> float:
    """计算字符串的 Shannon 熵值"""
    if not data:
        return 0.0

    counter = Counter(data)
    length = len(data)

    entropy = 0.0
    for count in counter.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def is_likely_secret(candidate: str, min_entropy: float = 3.5) -> bool:
    """判断候选字符串是否可能是真正的 Secret"""
    entropy = shannon_entropy(candidate)

    # 纯数字串（熵值低）
    if candidate.isdigit():
        return False

    # 全是同一个字符（熵值极低）
    if len(set(candidate)) <= 2:
        return False

    # 熵值不够高
    if entropy < min_entropy:
        return False

    return True


def test_pattern(pattern: str, test_strings: list[str]):
    """测试正则，输出每个匹配的熵值"""
    regex = re.compile(pattern)

    print(f"Testing pattern: {pattern}")
    print("-" * 40)

    for text in test_strings:
        matches = regex.findall(text)
        for match in matches:
            entropy = shannon_entropy(match)
            likely = is_likely_secret(match)
            print(f"  Match: {match[:30]:30s} | Entropy: {entropy:.2f} | Likely: {likely}")


if __name__ == "__main__":
    # 测试云帆 Token 正则
    pattern = r"cs-[a-f0-9]{32}"

    test_strings = [
        "CLOUDSAIL_TOKEN=cs-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",   # 真 Token（高熵值）
        "example_token = cs-00000000000000000000000000000000",   # 示例值（熵值极低）
        "const token = 'cs-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",  # 测试数据（熵值低）
        "cs-3f7a2b1c8d5e4f6a9b0c1d2e3f4a5b6c",                 # 真 Token
        "echo cs-0123456789abcdef0123456789abcdef",             # 可能出现在日志中
    ]

    test_pattern(pattern, test_strings)
```

**输出示例**：

```
Testing pattern: cs-[a-f0-9]{32}
----------------------------------------
  Match: cs-a1b2c3d4e5f6a7b8c9d0e1f2a3b4  | Entropy: 3.98 | Likely: True
  Match: cs-0000000000000000000000000000  | Entropy: 0.00 | Likely: False
  Match: cs-aaaaaaaaaaaaaaaaaaaaaaaaaaaa  | Entropy: 0.00 | Likely: False
  Match: cs-3f7a2b1c8d5e4f6a9b0c1d2e3f4a5  | Entropy: 4.00 | Likely: True
  Match: cs-0123456789abcdef0123456789abc  | Entropy: 4.00 | Likely: True
```

### 步骤六：集成测试与验证

**目标**：用 Trivy 扫描包含 Secret 和违规 K8s YAML 的项目。

```bash
# 1. 测试 Rego 策略
# 创建一个违规 Deployment
cat > test/bad-deploy.yaml <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payment-gateway
  namespace: prod
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: app
        image: payment:latest
        # 缺少 securityContext
EOF

# 用 Trivy 加载自定义策略扫描
trivy config \
  --config-policy ./finsec-policies/rules/ \
  --severity HIGH,CRITICAL \
  test/bad-deploy.yaml

# 2. 测试 Secret 规则
# 在代码中植入模拟 Token
echo 'CLOUDSAIL_TOKEN=cs-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6' > test/secret.txt

trivy fs \
  --scanners secret \
  --secret-config ./custom-secret/cloudsail-secret.yaml \
  test/secret.txt

# 3. 运行 OPA 测试
opa test finsec-policies/rules/ finsec-policies/tests/ -v

# 4. 覆盖率检查
opa test finsec-policies/ --coverage
```

### 测试验证

1. 运行 `opa test` 确认 10 条规则至少 20 个测试用例全部通过。
2. 对包含违规 Deployment 的 YAML 执行 Trivy config，验证 5 条 PodSecurity 规则全部命中。
3. 植入一个真正的 cs-token 和两个假 token，运行 Trivy fs -scanners secret，验证只有真 token 被标记为 CRITICAL。
4. 验证边界条件：StatefulSet 和 CronJob 的 Pod template 检查是否正确工作。
5. 用 `--coverage` 确认测试覆盖率 > 90%。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| Rego 模块化 | 公共库复用；规则精简 | 跨包引用时调试困难 |
| OPA Test | 原生测试框架；断言简洁 | 测试数据构造繁琐 |
| 正则 + 熵值 + 关键词 | 三重过滤大幅降低误报 | 熵值阈值需要每个模式独立调优 |
| 规则分离 | 各类规则独立维护；独立执行 | 多条规则检查同一资源时有重复遍历 |
| YAML 配置 Secret 规则 | 热加载；无需重新编译 | YAML 语法不支持复杂逻辑（如动态计算） |

### 适用场景

1. **行业定制安全基线**：PCI-DSS、HIPAA、SOC2 等合规要求的策略化。
2. **企业内部凭证管理**：检测私有 API Token、内部数据库连接串。
3. **金融/政务等高安全行业**：严格的 K8s 安全基线要求。
4. **多租户 K8s 集群**：确保租户间的安全隔离。
5. **镜像仓库的安全策略**：配合 Harbor 的扫描策略，在 Registry 层拦截。

**不适用场景**：
1. 团队 < 10 人且使用标准 K8s 配置——默认规则足够。
2. Secret 格式极其简单（如固定前缀 + 6 位数字）——正则即可，熵值过滤无效。

### 注意事项

- **Rego 规则不是越多越好**。每条规则都有评估成本。定期审查规则的必要性，移除已不再需要的规则。
- **熵值阈值的调优**。每个 Secret 格式需要独立调优准确的熵值阈值。阈值太低 → 误报多；太高 → 漏报多。
- **Secret 规则中的路径筛选**。path 字段要尽量精确——用 .* 检查所有文件会大幅增加扫描时间。
- **规则发布需要灰度**。新规则先以 warn 模式运行（不阻断 CI），观察 1-2 周确认无大量误报后升级为 block 模式。

### 常见踩坑经验

**踩坑案例 1：Rego 策略在生产环境表现与测试不一致**
- **现象**：OPA test 全部 pass，但在 CI 中规则未触发。
- **根因**：测试用的 input 结构与实际扫描时的结构有差异（如 metadata 嵌套层级）。
- **解法**：用 Trivy debug 模式输出现场实际 JSON，复制到测试用例中。

**踩坑案例 2：Secret 规则误报率太高**
- **现象**：自定义 Secret 规则触发了 500+ 条告警，其中 480 条是误报。
- **根因**：只用了正则匹配，没配置 keywords 和 ignore 列表。
- **解法**：先运行 `trivy fs --scanners secret` 收集所有匹配项，分析后配置 keywords（提高真报的可信度）+ ignore（排除假报）。

**踩坑案例 3：Rego import 跨文件时找不到模块**
- **现象**：`opa test` 报错 import data.lib.common not found。
- **根因**：OPA test 需要指定所有依赖的目录。`opa test policy1 policy2 tests/` 的顺序和路径影响 import 解析。
- **解法**：使用统一的 `-b` 参数指定 bundle 根目录：`opa test -b . policies/rules/ policies/tests/`。

### 思考题

1. FS-009 规则中检查了 memory limits 不超过 4Gi，但 K8s 的 memory 格式多样（`4Gi`, `4096Mi`, `4294967296`）。请编写一个 Rego 函数 `parse_memory(mem_str)`，将各种格式统一转换为字节数，以便比较。
2. 如果你的 Secret 检测覆盖了 8 种 Token 格式，在每次扫描时都要对这 8 种格式都做正则匹配。如何设计一个「正则编译缓存」来优化性能，避免每次扫描都重新编译正则？

> **答案提示**：第 40 章「从零构建企业级安全扫描平台」的策略引擎部分使用了类似的规则管理思路。

---

> **推广计划**：本章建议由安全合规团队主导，平台工程团队协助测试落地。第一步（1 周），完成 10 条核心 Rego 规则的编写和测试。第二步（1 周），在 2-3 个非关键 Namespace 中以 warn 模式运行。第三步（2 周），根据反馈调整规则，删减误报规则，补充漏报规则。第四步（1 周），升级为 block 模式并推广到所有生产 Namespace。Secret 规则建议每季度 Review 一次，根据新出现的凭证格式新增规则。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）和 OPA 项目编写，所有源码引用均遵循原许可证条款。
