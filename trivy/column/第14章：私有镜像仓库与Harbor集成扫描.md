# 第14章：私有镜像仓库与 Harbor 集成扫描

> 版本：Trivy v0.50+
> 面向人群：运维、DevOps、安全工程师

---

## 1. 项目背景

### 业务场景

云帆科技的镜像管理一直是个「灰色地带」。开发小王本地构建完镜像后，直接 `docker push` 到一台自建的 Docker Registry。这台 Registry 没有认证、没有扫描、没有保留策略，只是简单地用 nginx 反代了一下。三个月下来，Registry 的磁盘占满了 2TB，里面堆积着从 `v0.0.1-alpha` 到 `v0.0.1-alpha-fix-20240315` 的无数个测试标签。

直到有一天，一个老旧的 `payment-gateway:v1.0.0` 镜像在生产环境回滚时被重新拉取，而这个镜像包含了著名的 Log4Shell 漏洞。回滚变成了「回退到漏洞版本」，安全团队花了整整一个通宵才把事情平息。

CTO 事后质询：「为什么一个带漏洞的镜像能在 Registry 里躺了一年？为什么推送前没有人扫描？为什么回滚时没有人检查？」

老李回答不出来。因为团队确实没有任何镜像准入机制——任何人都可以 push，任何镜像都可以被拉取，Registry 只是一个「公共储物柜」。

与此同时，公司正在引入 Harbor 作为企业级镜像仓库。Harbor 内置了 Trivy 扫描器（通过 Trivy Adapter），但老李发现配置并不简单：扫描策略怎么设？阻止策略怎么配？Webhook 通知怎么接？不同项目的安全基线不一样，怎么做到「支付类项目 Critical 阻断，内部工具类项目仅告警」？

### 痛点放大

**第一，「最后一公里」的安全盲区。** 很多团队的 CI/CD 已经集成了 Trivy 扫描，但那是在「构建阶段」。镜像构建完成后 push 到 Registry，之后在 Registry 中被其他团队复用、被生产环境回滚、被历史版本追溯——这些场景下的镜像完全没有经过二次检查。构建时的安全不等于存储时的安全。

**第二，镜像标签管理混乱。** 测试镜像、开发镜像、生产镜像混存在一个 Registry 中，没有项目隔离和保留策略。一个带漏洞的镜像可能被某个脚本误拉取，或者在一次紧急回滚中被「救急」使用。

**第三，多项目的差异化策略难以落地。** 支付网关和内部文档工具显然需要不同的安全基线。但 Registry 层面的统一策略往往是「一刀切」，要么太松（支付网关的漏洞漏过去），要么太紧（内部工具的正常构建被阻断）。

**第四，扫描结果的通知和闭环缺失。** 镜像扫描发现了漏洞，但通知发到哪里？谁负责修复？什么时候重新扫描验证？没有闭环，扫描就是「自嗨」。

**本章的核心目标是：搭建以 Harbor 为核心的私有镜像仓库安全治理体系，实现「推送即扫描、高危即阻断、修复即验证」的自动化闭环。**

---

## 2. 项目设计

**场景**：云帆科技的 Harbor 迁移专项会，老李、小胖（开发代表）和小白（DevOps）正在设计 Registry 的安全策略。

---

**小胖**：「老李，咱们以前那个裸 Registry 就是个大垃圾桶，谁都能往里扔镜像。Harbor 真的能解决这个问题？」

**老李**：「Harbor 至少能管权限——不同项目隔离，不同角色只能访问自己的镜像。但更重要的是它和 Trivy 的集成。」

**小白**：「Harbor 从 2.0 开始就内置了 Trivy Adapter。它的架构是：Harbor Core 负责项目管理，Job Service 负责调度扫描任务，Trivy Adapter 是一个轻量级的 HTTP 服务，把 Harbor 的扫描请求翻译成 Trivy 命令，然后把结果返回给 Harbor。」

**大师**：「技术映射：你可以把 Harbor 的扫描体系想象成机场安检。Harbor Core 是『航站楼』，管理航班（镜像）的进出；Job Service 是『调度中心』，决定什么时候安检；Trivy Adapter 是『安检仪』，检查每个行李箱（镜像层）里有没有违禁品（漏洞）。」

**小胖**：「那扫描是什么时候触发？每次 push 都扫？」

**小白**：「可以配置两种触发方式：

1. **推送时扫描（On Push）**：镜像推送到 Registry 后立即触发扫描。这是第一时间的安全检查。
2. **定时扫描（On Schedule）**：每天/每周扫描所有已有镜像。这是防止「新爆发的漏洞影响老镜像」的二次检查。

我建议两者都开启：On Push 做门禁，On Schedule 做兜底。」

**老李**：「那如果扫描出 Critical 漏洞，能不能直接阻止这个镜像被拉取？」

**大师**：「Harbor 支持『阻止策略』（Prevent vulnerable images from running）。你可以在项目级别配置：如果镜像包含 Severity >= HIGH 的漏洞，就阻止它被 pull。但要注意，这个策略是项目级的，不是所有项目都用同一套标准。比如：

- `project/payment-gateway`：阻止 HIGH 及以上，且不能跳过。
- `project/internal-docs`：阻止 CRITICAL  only，允许管理员手动放行。
- `project/sandbox`：仅扫描，不阻止，用于开发实验。」

**小胖**：「那如果扫描误报怎么办？比如一个已知安全的 OS Vendor Backport 被 Trivy 报成了 HIGH。」

**小白**：「Harbor 的漏洞白名单功能可以解决这个问题。你可以在项目级别设置『CVE 允许列表』，把已评估接受的漏洞写进去。但更优雅的方式是结合上一章的 VEX—— Harbor 目前对 VEX 的直接支持还在演进中，短期内可以用 CVE 白名单过渡。」

**老李**：「扫描结果的通知也很重要。我们不能让漏洞躺在 Harbor 的 UI 里等人去看。」

**大师**：「Harbor 支持 Webhook 通知。你可以配置扫描完成后，把结果推送到 Slack、钉钉、企业微信或自定义 HTTP endpoint。Webhook 的 payload 包含镜像名、扫描状态、漏洞统计，你的接收端可以解析后做进一步处理——比如 P0 漏洞直接打电话告警，P1 漏洞发邮件，P2 漏洞只在 Dashboard 上显示。」

**小胖**：「还有一个问题。我们的 CI 已经集成了 Trivy，构建时扫了一遍；Harbor 推送后又扫了一遍。这不是重复工作吗？」

**大师**：「技术映射：这确实是重复，但重复是有价值的。CI 中的扫描是『出厂检验』，确保不合格产品不出厂；Harbor 中的扫描是『入库复检』，防止有人绕过 CI 直接 push，或者 CI 和 Registry 之间的网络传输中镜像被篡改。而且 Harbor 的扫描是集中式的、可审计的，所有项目的扫描结果在一个界面里能看到，便于安全团队全局把控。」

**老李**：「那我们定个方案吧：

1. 所有生产镜像必须通过 Harbor 分发，禁用裸 Registry。
2. Harbor 按项目划分安全等级，配置差异化的扫描策略和阻止策略。
3. 推送时自动触发 Trivy 扫描，Critical 漏洞阻断 pull。
4. 每天凌晨定时全量扫描，发现新增漏洞时通过 Webhook 通知安全团队。
5. 扫描结果与 JIRA 集成，自动创建漏洞修复工单。」

---

## 3. 项目实战

### 环境准备

- **Harbor**：v2.8+，已安装并运行
- **Trivy**：v0.50+（Harbor 内置的 Trivy Adapter 通常自带兼容版本）
- **Docker**：客户端已配置 Harbor 的认证
- **通知渠道**：Slack/钉钉 Webhook URL（可选）

### 步骤一：Harbor 安装与 Trivy Adapter 配置

**目标**：确保 Harbor 的漏洞扫描功能就绪。

```bash
# 使用 Harbor 官方安装器（已包含 Trivy）
curl -LO https://github.com/goharbor/harbor/releases/download/v2.9.0/harbor-offline-installer-v2.9.0.tgz
tar xzvf harbor-offline-installer-v2.9.0.tgz
cd harbor

# 编辑 harbor.yml，启用 Trivy
cat >> harbor.yml << 'EOF'
trivy:
  offline_scan: false
  insecure: false
  gitlab_token: ""
  skip_update: false
EOF

# 安装（带 Trivy）
sudo ./install.sh --with-trivy

# 验证 Trivy Adapter 状态
curl -s http://localhost:8080/api/v2.0/systeminfo | jq '.with_trivy'
# 预期输出：true
```

**检查扫描器注册状态**：

登录 Harbor UI → 「系统管理」→ 「扫描器」，确认 `Trivy` 状态为「健康」。

### 步骤二：配置项目级扫描策略

**目标**：为不同项目设置差异化的安全基线。

**在 Harbor UI 中操作**：

1. 创建项目 `payment-gateway`（高安全级别）。
2. 进入项目 → 「配置管理」→ 「漏洞扫描」。
3. 设置：
   - **自动扫描镜像**：开启「推送时扫描」。
   - **阻止漏洞镜像**：开启。
   - **Severity**：选择 `HIGH`（阻止 HIGH 及以上漏洞的镜像被拉取）。
   - **CVE 允许列表**：暂时留空，后续填入已评估接受的漏洞。

4. 创建项目 `internal-tools`（低安全级别）。
5. 设置：
   - **自动扫描镜像**：开启。
   - **阻止漏洞镜像**：开启。
   - **Severity**：选择 `CRITICAL`（只阻断 CRITICAL）。

**通过 API 配置（自动化场景）**：

```bash
# 设置 payment-gateway 项目的阻止策略
curl -X PUT "https://harbor.cloud-sail.internal/api/v2.0/projects/payment-gateway" \
  -H "Content-Type: application/json" \
  -u "admin:password" \
  -d '{
    "metadata": {
      "prevent_vul": "true",
      "severity": "high",
      "auto_scan": "true"
    }
  }'
```

### 步骤三：推送镜像并验证扫描

**目标**：验证推送触发扫描和阻断策略。

```bash
# 登录 Harbor
docker login harbor.cloud-sail.internal

# 构建一个故意含漏洞的镜像
docker build -t harbor.cloud-sail.internal/payment-gateway/app:v1.0 \
  -f Dockerfile.vulnerable .

# 推送（应触发自动扫描）
docker push harbor.cloud-sail.internal/payment-gateway/app:v1.0

# 在 Harbor UI 中查看扫描结果
# 预期：如果包含 HIGH 漏洞，镜像旁会显示红色标记，且 pull 被阻止
```

**验证阻断效果**：

```bash
# 尝试拉取被阻止的镜像
docker pull harbor.cloud-sail.internal/payment-gateway/app:v1.0
# 预期报错：Error response from daemon: unknown: current image with "HIGH" vulnerability...
```

> **可能遇到的坑**：Harbor 的阻止策略只对「通过 Harbor 拉取」生效。如果用户直接在 Docker Daemon 上运行本地构建的镜像，Harbor 无法阻止。因此必须配合 Kubernetes 的 ImagePullPolicy 和准入控制，确保生产环境只从 Harbor 拉取镜像。

### 步骤四：配置 Webhook 通知

**目标**：扫描完成后自动通知相关团队。

**在 Harbor UI 中操作**：

1. 进入项目 → 「Webhook」→ 「新建」。
2. 配置：
   - **名称**：`security-alerts`
   - **事件类型**：勾选「扫描镜像完成」。
   - **目标 URL**：`https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN`
   - **请求头**：`Content-Type: application/json`

3. 或者使用自定义接收端：

创建 `webhook-receiver.py`：

```python
#!/usr/bin/env python3
from flask import Flask, request
import json

app = Flask(__name__)

@app.route('/harbor-webhook', methods=['POST'])
def handle_scan():
    data = request.json
    
    # Harbor 扫描完成事件
    if data.get('type') == 'SCANNING_COMPLETED':
        resource = data.get('event_data', {}).get('resources', [{}])[0]
        repo = resource.get('repo_full_name', 'unknown')
        tag = resource.get('tag', 'unknown')
        scan_overview = resource.get('scan_overview', {})
        
        # 提取漏洞统计
        summary = scan_overview.get('application/vnd.security.vulnerability.report; version=1.1', {}).get('summary', {})
        critical = summary.get('critical', 0)
        high = summary.get('high', 0)
        
        alert = f"🚨 Harbor 扫描完成: {repo}:{tag}\nCritical: {critical}, High: {high}"
        
        if critical > 0:
            # 发送到钉钉/Slack
            print(f"ALERT: {alert}")
            # TODO: 调用实际的通知 API
        
        return {"status": "ok"}, 200
    
    return {"status": "ignored"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

部署接收端：

```bash
pip install flask
gunicorn -w 2 -b 0.0.0.0:5000 webhook-receiver:app
```

### 步骤五：定时全量扫描与报告

**目标**：防止新爆发的漏洞影响仓库中已存在的镜像。

**在 Harbor UI 中操作**：

1. 「系统管理」→ 「任务」→ 「扫描全部」。
2. 设置定时任务：每天凌晨 2 点执行全量扫描。

**通过 API 触发**：

```bash
# 触发所有项目的全量扫描
curl -X POST "https://harbor.cloud-sail.internal/api/v2.0/system/scanAll" \
  -u "admin:password"
```

**生成全量漏洞报告**：

```bash
#!/bin/bash
# harbor-vuln-report.sh
# 导出所有项目的漏洞摘要

OUTPUT="/var/reports/harbor-vuln-$(date +%Y%m%d).csv"
echo "project,repository,tag,critical,high,medium,low" > "$OUTPUT"

# 获取所有项目
PROJECTS=$(curl -s "https://harbor.cloud-sail.internal/api/v2.0/projects" -u "admin:password" | jq -r '.[].name')

for PROJECT in $PROJECTS; do
    # 获取项目下的所有 artifact
    ARTIFACTS=$(curl -s "https://harbor.cloud-sail.internal/api/v2.0/projects/${PROJECT}/repositories?sort=name" -u "admin:password")
    # 解析漏洞统计并写入 CSV
    # ... 省略具体解析逻辑 ...
done

echo "Report generated: $OUTPUT"
```

### 步骤六：与 Kubernetes 准入控制联动

**目标**：确保生产集群只拉取通过 Harbor 安全扫描的镜像。

**方案 1：Harbor 的阻止策略（基础）**

Harbor 项目级别的阻止策略已经能阻止高危镜像的 pull。但这种方式依赖 Docker Daemon 的配合，且错误信息不够友好。

**方案 2：Kyverno / OPA Gatekeeper（进阶）**

在 K8s 集群中部署 Kyverno，配置策略只允许拉取 Harbor 中扫描通过的镜像：

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-harbor-scan
spec:
  validationFailureAction: Enforce
  rules:
  - name: check-scan-passed
    match:
      any:
      - resources:
          kinds:
          - Pod
    validate:
      message: "Only images scanned by Harbor are allowed"
      pattern:
        spec:
          containers:
          - image: "harbor.cloud-sail.internal/*"
```

> **可能遇到的坑**：Kyverno 需要访问 Harbor API 来获取扫描结果，网络不通或认证失败时会导致 Pod 创建被误拦截。建议配置合理的豁免策略和错误回退。

### 测试验证

1. 确认 Harbor 系统信息中 `with_trivy: true`。
2. 推送含漏洞镜像到 `payment-gateway` 项目，验证扫描触发且 pull 被阻止。
3. 推送同样镜像到 `internal-tools` 项目，验证 CRITICAL only 策略下是否允许 pull。
4. 配置 Webhook 后推送镜像，验证接收端收到扫描完成事件。
5. 执行 `scanAll` API，验证定时扫描任务创建成功。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 集成深度 | Harbor 与 Trivy 原生集成，开箱即用 | Trivy 版本受 Harbor 发布周期限制，可能滞后 |
| 策略粒度 | 项目级别的差异化策略，灵活适配不同业务 | 复杂策略（如按标签、按团队）配置较繁琐 |
| 阻断能力 | 可直接阻止漏洞镜像被拉取 | 无法阻止本地已有镜像的运行 |
| 通知机制 | Webhook 支持多种事件类型 | Payload 格式固定，自定义空间有限 |
| 可视化 | Harbor UI 直观展示漏洞分布和趋势 | 大规模仓库（>10万镜像）时 UI 性能下降 |

### 适用场景

1. **企业级镜像仓库**：替代裸 Registry，建立统一的安全、权限、审计体系。
2. **镜像准入控制**：推送时自动扫描，高危镜像无法进入生产环境。
3. **历史镜像审计**：定时全量扫描，发现新漏洞对老镜像的影响。
4. **多项目差异化治理**：支付类项目严格阻断，工具类项目宽松告警。
5. **供应链追溯**：通过 Harbor 的复制和签名功能，确保镜像在跨环境传输中的完整性。

**不适用场景**：
1. 需要扫描运行时容器（非镜像）——需配合 Trivy Operator 或运行时安全工具。
2. 需要代码级漏洞扫描（SAST）——Harbor 只扫描镜像内容，不扫描源代码。

### 注意事项

- **Trivy Adapter 的资源限制**：Harbor 默认给 Trivy Adapter 分配的内存可能不足（尤其是扫描大镜像时）。建议修改 `docker-compose.yml` 中的资源限制。
- **数据库更新**：Harbor 内置的 Trivy 有自己的数据库更新机制，确保 Trivy 容器能访问外网或内部 Registry。
- **CVE 白名单的管理**：项目级的 CVE 允许列表容易被滥用，建议与安全团队定期审计。

### 常见踩坑经验

**踩坑案例 1：Harbor 扫描结果为空**
- **现象**：推送镜像后 Harbor UI 显示扫描完成，但漏洞数为 0。
- **根因**：Trivy Adapter 的数据库未下载完成，或镜像基于 scratch/distroless，没有可识别的包管理器数据。
- **解法**：检查 Trivy Adapter 的日志（`docker logs trivy-adapter`）；对 Distroless 镜像确保语言包锁定文件已包含在内。

**踩坑案例 2：阻止策略导致紧急回滚失败**
- **现象**：生产环境出现故障，需要紧急回滚到上一个版本，但 Harbor 阻止了拉取。
- **根因**：旧版本镜像包含当时被允许的漏洞，但新策略收紧后它被阻止了。
- **解法**：为紧急回滚建立「豁免账号」或「豁免标签」（如 `hotfix-*`）；或在 Kyverno 策略中配置紧急豁免机制。

**踩坑案例 3：Webhook 通知泛滥**
- **现象**：开发群每天收到几百条 Harbor 扫描通知，真正重要的信息被淹没。
- **根因**：Webhook 配置了「所有事件」，包括推送、删除、复制等非安全事件。
- **解法**：Webhook 只订阅「扫描镜像完成」事件；接收端增加过滤逻辑，只处理包含 HIGH/CRITICAL 的通知。

### 思考题

1. 假设你的 Harbor 仓库中有 5000 个镜像，其中 300 个包含已知漏洞。请设计一个「漏洞修复 Sprint」的优先级排序方案：如何根据镜像的使用频率（被拉取次数）、业务重要性、漏洞可利用性来确定修复顺序？
2. Harbor 的阻止策略在镜像 pull 时才生效，但 Kubernetes 的节点缓存可能已经保存了旧版本的漏洞镜像。如何设计一个方案，确保节点本地缓存中的漏洞镜像也能被及时发现和清理？

> **答案提示**：第 21 章「Trivy Operator 与 K8s 持续合规」将介绍如何在 Kubernetes 运行态持续监控和清理漏洞镜像。

---

> **推广计划**：本章是运维团队和 DevOps 负责人的必读内容。建议所有生产镜像统一迁移到 Harbor，禁用裸 Registry。安全团队制定《Harbor 项目安全分级规范》，明确不同项目的扫描策略和阻止阈值。CI/CD 负责人确保所有构建流水线 push 的目标地址是 Harbor 而非旧 Registry。开发团队了解 Harbor 的阻断机制，在遇到 pull 失败时先检查 Harbor UI 中的扫描结果。
