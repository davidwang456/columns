# 第18章：GitLab CI 与 Jenkins 流水线集成

> 版本：Trivy v0.50+
> 面向人群：DevOps、运维、CI/CD 工程师

---

## 1. 项目背景

### 业务场景

云帆科技的 GitHub Actions 安全门禁上线后，前端和开源项目团队的工作流得到了显著改善。但公司还有一半以上的项目托管在自建的 GitLab 上，后端核心系统和数据 pipeline 则完全依赖 Jenkins 集群。CTO 在季度 review 中发现了一个尴尬的数据对比：

- GitHub 项目的漏洞发现率：92%（PR 阶段拦截）
- GitLab 项目的漏洞发现率：34%（大部分在发版后才被发现）
- Jenkins 项目的漏洞发现率：12%（基本靠人工抽查）

GitLab 组的负责人小白解释说：「我们在 `.gitlab-ci.yml` 里确实加了一个 Trivy 的 job，但它只是 `echo` 一下扫描结果，不会阻断流水线。而且每次运行都要重新下载数据库，一个 30 秒的扫描变成 8 分钟，开发嫌慢就把那个 stage 跳过了。」

Jenkins 组的情况更糟。老李负责的 20 个 Slave 节点，有的装了 Trivy v0.35，有的装了 v0.48，还有一个节点根本没装。Pipeline 脚本更是百花齐放——有的用 shell 调用 Trivy，有的用 Docker 跑 `aquasec/trivy`，有的直接从 GitHub Release 下载最新版。某次因为一个节点上的 Trivy 版本太旧，扫描规则库不兼容，导致整个流水线报红，开发团队通宵排查才发现是工具版本问题。

更深层的问题是「增量漏洞检测」的缺失。GitHub Actions 的 PR 评论可以明确告诉开发者「这次变更引入了 3 个新漏洞」，但 GitLab 和 Jenkins 的流水线只能输出「当前镜像共有 45 个漏洞」。开发者不知道哪些是自己引入的，哪些是上个月就存在的，修复动力严重不足。

CTO 要求：「一个月内，GitLab 和 Jenkins 的扫描覆盖率必须达到 100%，且与 GitHub 同等质量——即门禁、增量检测、统一报告。」

### 痛点放大

**第一，工具版本碎片化。** 多个 Jenkins Slave 各自管理 Trivy 二进制，版本不一致导致扫描结果不可比、行为不可预测。

**第二，缓存缺失导致重复下载。** GitLab CI 的 Runner 默认使用一次性容器，Trivy 的漏洞数据库每次都要重新拉取，严重拖慢流水线。

**第三，增量检测能力缺失。** 传统 CI 扫描只输出「当前状态的漏洞总数」，不对比基线，开发者无法判断「我是不是引入了新的漏洞」。

**第四，报告展示不友好。** Jenkins 的控制台日志里打印几百行表格，开发懒得看；GitLab 的 Security Dashboard 需要额外配置才能展示 Trivy 结果。

**第五，门禁与审批流程缺失。** 扫描发现漏洞后，流水线不会自动阻断，也没有便捷的「申请例外」机制。结果是：要么全部放行（形同虚设），要么全部阻断（影响效率）。

**本章的核心目标是：在 GitLab CI 和 Jenkins 中建立标准化、可缓存、可增量、可门禁的 Trivy 安全流水线，实现与 GitHub Actions 同等质量的安全左移。**

---

## 2. 项目设计

**场景**：云帆科技的 CI/CD 统一治理会议，小白（GitLab 负责人）、小胖（Jenkins 负责人）和大师正在设计两套系统的集成方案。

---

**小白**：「GitLab CI 的问题是 Runner 太『干净』了。每次 job 都是新容器，Trivy 缓存全部丢失。我试过用 GitLab 的 cache 功能，但缓存命中率很低。」

**小胖**：「Jenkins 的问题正好相反——Slave 节点太『脏』了。Trivy 装得到处都是，版本参差不齐。我曾经在一个节点上发现三个不同版本的 Trivy 二进制，分别在不同目录。」

**大师**：「技术映射：GitLab Runner 像『一次性纸杯』，用完就扔，不保留任何痕迹；Jenkins Slave 像『老茶缸』，里面沉淀了各种历史茶渍。两种极端都需要治理：

- **GitLab**：要让『纸杯』能接入『公共饮水机』——用共享缓存或预置数据库镜像，避免每次都重新下载。
- **Jenkins**：要给『茶缸』制定『清洗标准』——统一用 Docker 方式运行 Trivy，或者统一版本管理脚本，消灭多版本并存。」

**小白**：「那增量检测呢？GitHub Actions 的 PR 评论可以展示『新增漏洞』，GitLab 怎么做？」

**大师**：「GitLab 14.0+ 引入了『安全报告对比』（Security Report Comparison）功能。如果你在 Merge Request 中上传了 Trivy 的 JSON 报告，GitLab 会自动对比目标分支和源分支的报告，高亮显示『新增的漏洞』。开发者可以在 MR 的 Security Tab 里直接看到增量结果。」

**小胖**：「Jenkins 没有这种原生对比功能吧？」

**小白**：「Jenkins 确实没有，但可以用脚本实现。思路是：

1. 每次扫描主分支时，把 JSON 报告保存为 artifact（如 `master-scan.json`）。
2. 扫描 feature 分支时，加载主分支的报告作为基线。
3. 用 Python 脚本对比两份报告，提取只在 feature 分支中出现的漏洞。
4. 如果新增漏洞中包含 HIGH/CRITICAL，阻断流水线。」

**小胖**：「门禁机制呢？我们有些 HIGH 漏洞确实要 3 天才能修，总不能一直卡着流水线吧？」

**大师**：「渐进式门禁设计：

- **P0（CRITICAL + CISA KEV）**：无条件阻断，不允许例外。
- **P1（CRITICAL 无 KEV / HIGH 有 PoC）**：默认阻断，但开发者可以在 MR 描述中标注 `#security-override: reason`，由安全 bot 解析后允许通过（需记录审计日志）。
- **P2（HIGH 无 PoC / MEDIUM）**：仅告警，不阻断，但要求在当前 Sprint 内修复。

这样既保证了底线安全，又给了业务灵活性。」

**小白**：「报告展示方面，GitLab 的 Security Dashboard 可以直接渲染 Trivy 报告吗？」

**大师**：「GitLab 支持多种安全报告格式，但原生不直接支持 Trivy 的 JSON。有两种方案：

1. **转换格式**：用脚本把 Trivy JSON 转换成 GitLab 支持的 `report.json` 格式（需符合 GitLab 的 JSON schema）。
2. **使用 HTML 报告**：Trivy 支持生成 HTML 报告，作为 artifact 上传，开发可以在 GitLab UI 中直接下载查看。

Jenkins 方面更灵活——可以用 HTML Publisher Plugin 把 Trivy 的 HTML 报告展示在 Jenkins UI 中，也可以用自定义的 Pipeline 步骤把关键指标推送到 Slack。」

---

## 3. 项目实战

### 环境准备

- **GitLab**：v15+，Runner 已注册
- **Jenkins**：v2.4+，Pipeline 插件已安装
- **Trivy**：v0.50+（或通过 Docker 运行）
- **共享存储**：用于缓存（可选 NFS/S3）

### 步骤一：GitLab CI 基础扫描与缓存

**目标**：建立带缓存的 GitLab CI Trivy 扫描。

创建 `.gitlab-ci.yml`：

```yaml
variables:
  TRIVY_VERSION: "0.50.0"
  TRIVY_CACHE_DIR: "$CI_PROJECT_DIR/.trivy-cache"

stages:
  - build
  - security
  - deploy

# 缓存数据库，避免每次重新下载
trivy_cache:
  stage: .pre
  script:
    - mkdir -p $TRIVY_CACHE_DIR
  cache:
    key: trivy-db-v1
    paths:
      - .trivy-cache/db/

# 构建镜像
build:
  stage: build
  image: docker:latest
  services:
    - docker:dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA

# 镜像安全扫描
scan-image:
  stage: security
  image: aquasec/trivy:$TRIVY_VERSION
  variables:
    TRIVY_CACHE_DIR: "$CI_PROJECT_DIR/.trivy-cache"
  cache:
    key: trivy-db-v1
    paths:
      - .trivy-cache/db/
    policy: pull
  script:
    - trivy image --cache-dir $TRIVY_CACHE_DIR
      --severity HIGH,CRITICAL
      --format template
      --template "@/contrib/gitlab-codequality.tpl"
      --output trivy-codequality.json
      $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
    - trivy image --cache-dir $TRIVY_CACHE_DIR
      --severity HIGH,CRITICAL
      --format json
      --output trivy-report.json
      $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  artifacts:
    when: always
    reports:
      # GitLab 原生支持 Code Quality 报告展示
      codequality: trivy-codequality.json
    paths:
      - trivy-report.json
    expire_in: 1 week

# 代码仓库扫描
scan-repo:
  stage: security
  image: aquasec/trivy:$TRIVY_VERSION
  variables:
    TRIVY_CACHE_DIR: "$CI_PROJECT_DIR/.trivy-cache"
  cache:
    key: trivy-db-v1
    paths:
      - .trivy-cache/db/
    policy: pull
  script:
    - trivy fs --cache-dir $TRIVY_CACHE_DIR
      --scanners vuln,secret,misconfig
      --severity HIGH,CRITICAL
      --exit-code 1
      .
```

**关键配置**：
- `cache:key: trivy-db-v1`：所有 Job 共享同一个缓存键。
- `cache:policy: pull`：scan Job 只读缓存，不写入（避免并发写入冲突）。
- `TRIVY_CACHE_DIR`：指向项目目录内的缓存路径，便于 GitLab Runner 归档。

> **可能遇到的坑**：如果 Runner 是 Kubernetes Executor，缓存需要通过 PVC 或 S3 实现，本地 `paths` 缓存可能不生效。

### 步骤二：GitLab MR 增量漏洞检测

**目标**：在 Merge Request 中展示「新增漏洞」。

```yaml
# 在 .gitlab-ci.yml 中增加对比 job
compare-scan:
  stage: security
  image: python:3.11-slim
  only:
    - merge_requests
  script:
    - pip install jq
    # 下载目标分支的基线报告（需事先存储在 artifact 或外部存储）
    - curl -o baseline.json "$CI_API_V4_URL/projects/$CI_PROJECT_ID/jobs/artifacts/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME/raw/trivy-report.json?job=scan-image"
      --header "PRIVATE-TOKEN: $CI_JOB_TOKEN" || echo "No baseline found"
    # 对比脚本
    - |
      python3 << 'PY'
      import json, sys
      def load_cves(path):
          try:
              with open(path) as f:
                  data = json.load(f)
              return {v["VulnerabilityID"] for r in data.get("Results", [])
                      for v in r.get("Vulnerabilities", [])}
          except:
              return set()
      baseline = load_cves("baseline.json")
      current = load_cves("trivy-report.json")
      new_vulns = current - baseline
      if new_vulns:
          print(f"NEW VULNERABILITIES: {len(new_vulns)}")
          for v in sorted(new_vulns):
              print(f"  - {v}")
          sys.exit(1)
      else:
          print("No new vulnerabilities introduced.")
      PY
  dependencies:
    - scan-image
```

### 步骤三：Jenkins 标准化 Pipeline

**目标**：统一 Jenkins 多节点的 Trivy 版本和扫描行为。

**方案：Docker 化运行（推荐）**

```groovy
// Jenkinsfile
pipeline {
    agent any
    
    environment {
        TRIVY_VERSION = '0.50.0'
        TRIVY_CACHE_DIR = '/var/cache/trivy'
        IMAGE_NAME = "myapp:${BUILD_NUMBER}"
    }
    
    stages {
        stage('Build') {
            steps {
                script {
                    docker.build(env.IMAGE_NAME)
                }
            }
        }
        
        stage('Security Scan') {
            steps {
                script {
                    // 使用固定版本的 Trivy Docker 镜像，消灭版本碎片化
                    def trivyArgs = """
                        --rm
                        -v /var/run/docker.sock:/var/run/docker.sock
                        -v ${env.TRIVY_CACHE_DIR}:/root/.cache/trivy
                        -v ${env.WORKSPACE}/reports:/reports
                        aquasec/trivy:${env.TRIVY_VERSION}
                    """
                    
                    // 镜像扫描
                    sh """
                        docker run ${trivyArgs} image \
                            --severity HIGH,CRITICAL \
                            --format json \
                            --output /reports/trivy-image.json \
                            --exit-code 0 \
                            ${env.IMAGE_NAME}
                    """
                    
                    // 代码扫描
                    sh """
                        docker run ${trivyArgs} fs \
                            --scanners vuln,secret,misconfig \
                            --severity HIGH,CRITICAL \
                            --format json \
                            --output /reports/trivy-fs.json \
                            --exit-code 0 \
                            /reports/../
                    """
                }
            }
        }
        
        stage('Gate') {
            steps {
                script {
                    def imageReport = readJSON file: 'reports/trivy-image.json'
                    def fsReport = readJSON file: 'reports/trivy-fs.json'
                    
                    def criticalCount = 0
                    def highCount = 0
                    
                    [imageReport, fsReport].each { report ->
                        report.Results?.each { result ->
                            result.Vulnerabilities?.each { vuln ->
                                if (vuln.Severity == 'CRITICAL') criticalCount++
                                if (vuln.Severity == 'HIGH') highCount++
                            }
                        }
                    }
                    
                    echo "CRITICAL: ${criticalCount}, HIGH: ${highCount}"
                    
                    if (criticalCount > 0) {
                        error("Build blocked: ${criticalCount} CRITICAL vulnerabilities found")
                    }
                    
                    if (highCount > 5) {
                        unstable("WARNING: ${highCount} HIGH vulnerabilities found")
                    }
                }
            }
        }
    }
    
    post {
        always {
            // 发布 HTML 报告
            publishHTML(target: [
                allowMissing: false,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: 'reports',
                reportFiles: '*.html',
                reportName: 'Trivy Security Report'
            ])
            
            // 归档 JSON 报告
            archiveArtifacts artifacts: 'reports/*.json', allowEmptyArchive: true
        }
    }
}
```

**关键设计**：
- 统一使用 `aquasec/trivy:${TRIVY_VERSION}` Docker 镜像，消灭节点版本差异。
- 挂载宿主机的 `trivy-cache` 目录，实现跨构建的数据库缓存。
- `exit-code: 0` 让扫描不直接失败，由 Pipeline 脚本解析后决定阻断或告警。

### 步骤四：Jenkins 增量检测

**目标**：实现 feature 分支与主分支的漏洞对比。

```groovy
// 在 Gate stage 前增加对比 stage
stage('Delta Scan') {
    steps {
        script {
            // 下载主分支基线报告（从上次成功的构建 artifact）
            copyArtifacts(
                projectName: env.JOB_NAME,
                selector: lastSuccessful(),
                filter: 'reports/trivy-image.json',
                target: 'baseline/',
                optional: true
            )
            
            sh '''
                python3 << 'PY'
import json
import sys

def extract_cves(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return {(v["VulnerabilityID"], v["PkgName"]) 
                for r in data.get("Results", [])
                for v in r.get("Vulnerabilities", [])}
    except:
        return set()

baseline = extract_cves("baseline/reports/trivy-image.json")
current = extract_cves("reports/trivy-image.json")

new_vulns = current - baseline
removed_vulns = baseline - current

print(f"New vulnerabilities: {len(new_vulns)}")
print(f"Fixed vulnerabilities: {len(removed_vulns)}")

for v in sorted(new_vulns):
    print(f"  [NEW] {v[0]} in {v[1]}")

# 保存增量报告
with open("reports/delta.json", "w") as f:
    json.dump({
        "new": [{"cve": v[0], "pkg": v[1]} for v in new_vulns],
        "fixed": [{"cve": v[0], "pkg": v[1]} for v in removed_vulns]
    }, f, indent=2)

if new_vulns:
    # 检查是否有 CRITICAL 新增
    with open("reports/trivy-image.json") as f:
        data = json.load(f)
    critical_new = any(
        v["Severity"] == "CRITICAL" and (v["VulnerabilityID"], v["PkgName"]) in new_vulns
        for r in data.get("Results", [])
        for v in r.get("Vulnerabilities", [])
    )
    if critical_new:
        print("FATAL: New CRITICAL vulnerabilities introduced!")
        sys.exit(1)
PY
            '''
        }
    }
}
```

### 步骤五：统一报告模板与通知

**目标**：无论 GitLab 还是 Jenkins，输出格式统一的报告。

创建 `trivy-report-template.tpl`：

```
# 云帆科技 - 安全扫描报告

**构建**: {{ .ArtifactName }}  
**时间**: {{ now }}  
**扫描工具**: Trivy v0.50.0  

## 摘要

| 指标 | 数值 |
|------|------|
| 总漏洞数 | {{ len (where .Results "Vulnerabilities") }} |
| CRITICAL | {{ len (where .Results "Vulnerabilities" "severity" "CRITICAL") }} |
| HIGH | {{ len (where .Results "Vulnerabilities" "severity" "HIGH") }} |
| 新增漏洞 | {{ env "DELTA_NEW" "N/A" }} |
| 修复漏洞 | {{ env "DELTA_FIXED" "N/A" }} |

## 状态

{{ if gt (len (where .Results "Vulnerabilities" "severity" "CRITICAL")) 0 }}
🔴 **失败** - 存在 CRITICAL 漏洞，构建被阻断。
{{ else if gt (len (where .Results "Vulnerabilities" "severity" "HIGH")) 5 }}
🟡 **警告** - HIGH 漏洞超过 5 个，建议尽快修复。
{{ else }}
🟢 **通过** - 无 CRITICAL，HIGH 漏洞在可控范围内。
{{ end }}
```

**Slack 通知脚本**（Pipeline 通用）：

```python
#!/usr/bin/env python3
import json, os, urllib.request

def notify_slack(webhook, report_path):
    with open(report_path) as f:
        report = json.load(f)
    
    critical = sum(1 for r in report.get("Results", [])
                   for v in r.get("Vulnerabilities", [])
                   if v.get("Severity") == "CRITICAL")
    
    emoji = "🔴" if critical > 0 else "🟢"
    msg = f"{emoji} Security Scan: {os.getenv('JOB_NAME', 'unknown')}\nCritical: {critical}"
    
    data = json.dumps({"text": msg}).encode()
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)

if __name__ == "__main__":
    import sys
    notify_slack(sys.argv[1], sys.argv[2])
```

### 步骤六：渐进式门禁配置

**目标**：实现 P0 阻断、P1 审批、P2 告警的三级门禁。

**GitLab CI 实现**：

```yaml
gate:
  stage: security
  image: python:3.11-slim
  script:
    - |
      python3 << 'PY'
import json, sys, os

with open("trivy-report.json") as f:
    report = json.load(f)

critical_kev = []
critical_other = []
high_poc = []
high_other = []

for r in report.get("Results", []):
    for v in r.get("Vulnerabilities", []):
        cve = v["VulnerabilityID"]
        sev = v["Severity"]
        # 简化判断：实际应查询 CISA KEV 和 EPSS
        if sev == "CRITICAL":
            if "44228" in cve or "45046" in cve:  # 模拟 KEV
                critical_kev.append(cve)
            else:
                critical_other.append(cve)
        elif sev == "HIGH":
            high_other.append(cve)

# P0: KEV Critical -> 阻断
if critical_kev:
    print(f"P0 BLOCKED: KEV Critical vulnerabilities: {critical_kev}")
    sys.exit(1)

# P1: Other Critical -> 检查 MR 描述中的 override
mr_desc = os.getenv("CI_MERGE_REQUEST_DESCRIPTION", "")
if critical_other and "#security-override" not in mr_desc:
    print(f"P1 BLOCKED: Critical vulnerabilities without override: {critical_other}")
    sys.exit(1)

# P2: HIGH -> 告警但不阻断
if high_other:
    print(f"P2 WARNING: HIGH vulnerabilities: {len(high_other)}")

print("Gate passed.")
PY
```

### 测试验证

1. 提交 `.gitlab-ci.yml` 到 GitLab，验证 MR 触发扫描且缓存生效。
2. 检查 GitLab Security Dashboard，确认 Code Quality 报告正确展示。
3. 在 Jenkins 上创建新 Pipeline，验证 Docker 化 Trivy 运行正常。
4. 故意引入漏洞提交，验证 GitLab/Jenkins 均正确阻断或告警。
5. 对比增量检测报告，确认「新增/修复」漏洞统计准确。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| GitLab 集成 | 原生 Code Quality 报告，MR 增量对比直观 | Kubernetes Runner 缓存配置较复杂 |
| Jenkins 集成 | Pipeline as Code 灵活，生态丰富 | 多节点版本管理困难，需 Docker 化规范 |
| 统一报告 | HTML/JSON 双输出，适配不同受众 | 模板维护需要 Go Template 知识 |
| 增量检测 | 聚焦「新增风险」，减少噪音 | 基线报告存储和检索需要额外设计 |
| 渐进门禁 | P0/P1/P2 分层，兼顾安全与效率 | override 机制可能被滥用，需审计 |

### 适用场景

1. **GitLab 私有化部署**：自托管 GitLab 的企业，需要原生集成的安全扫描。
2. **Jenkins 多节点集群**：统一 Docker 化运行，消除版本碎片化。
3. **混合 CI 环境**：GitLab + Jenkins 并存的企业，统一报告格式和门禁标准。
4. **增量安全治理**：团队已积累大量历史漏洞，需要聚焦「不要新增」而非「全部清零」。
5. **合规审计**：GitLab/Jenkins 的构建日志和 artifact 可作为审计证据。

**不适用场景**：
1. 完全使用 GitHub Actions 的团队——本章方案过于复杂，直接用第 17 章即可。
2. 无自托管 CI 的轻量级团队——建议使用 SaaS 化的安全扫描服务。

### 注意事项

- **缓存并发写入**：GitLab CI 的多个 Job 同时写缓存可能导致损坏。建议用 `policy: pull-push` 配合锁机制，或改用外部缓存（如 S3）。
- **Jenkins Agent 标签**：确保有 Docker 环境的 Agent 才执行 Trivy Job，避免在无 Docker 的节点上报错。
- **Base 报告时效性**：增量对比的基线报告如果太旧（如主分支一周没构建），对比结果可能失真。建议保持主分支每日构建。
- **Override 审计**：所有 `#security-override` 的记录应保存到外部系统（如数据库或邮件归档），便于事后追溯。

### 常见踩坑经验

**踩坑案例 1：GitLab Runner 缓存未命中**
- **现象**：每次 Job 都重新下载数据库。
- **根因**：Runner 配置了 `cache`，但执行环境是 Docker，每次容器重启后缓存路径不同。
- **解法**：将 `TRIVY_CACHE_DIR` 指向项目目录（`$CI_PROJECT_DIR/.trivy-cache`），而不是系统临时目录。

**踩坑案例 2：Jenkins Pipeline 中 Docker 权限不足**
- **现象**：`docker run trivy` 报 `permission denied`。
- **根因**：Jenkins 用户不在 docker 组，或未挂载 docker.sock。
- **解法**：确保 Jenkins Agent 已加入 docker 组；Pipeline 中正确挂载 `/var/run/docker.sock`。

**踩坑案例 3：增量对比基线报告不存在**
- **现象**：首次运行 Delta Scan 时报 `baseline.json not found`。
- **根因**：主分支从未成功执行过扫描并归档 artifact。
- **解法**：在主分支的 Pipeline 中强制保留 artifact；Delta Scan 逻辑中增加基线缺失的优雅降级（视为「全新扫描」而非报错）。

### 思考题

1. 假设你的团队同时使用了 GitLab、Jenkins 和 GitHub Actions 三种 CI 系统。请设计一个「中央安全仪表板」架构，使得三种系统的扫描结果都能汇总到同一界面，并统一展示「新增漏洞趋势」、「修复 SLA 达成率」等指标。
2. Jenkins 的 `copyArtifacts` 在跨项目时可能失败（权限问题）。请设计一个更健壮的基线报告存储方案，使得任何分支都能可靠地获取到主分支的最新基线报告。

> **答案提示**：第 31 章「构建 DevSecOps 自动化安全网关」将介绍跨 CI 系统的统一安全编排方案。

---

> **推广计划**：本章是 DevOps 和 CI/CD 负责人的核心必读内容。GitLab 团队直接复制 `.gitlab-ci.yml` 模板并根据项目调整 `paths` 触发规则。Jenkins 团队将 `Jenkinsfile` 纳入共享库（Shared Library），所有新项目继承标准 Pipeline。安全团队负责维护 Trivy 版本号、报告模板和渐进式门禁的阈值配置。开发团队只需关注 MR/PR 中的安全评论和阻断通知，无需理解底层 CI 配置。
