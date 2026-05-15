# 第17章：Jenkins 流水线集成与质量门禁阻断

## 1. 项目背景

**业务场景**：某电商公司的订单系统由 Jenkins Pipeline 驱动 CI/CD。Pipeline 包含 4 个阶段：Build → Test → Deploy to Staging → Deploy to Production。但 SonarQube 扫描目前只在 "Test" 阶段并行执行——无论扫描结果如何，Deploy 都会继续。

两个月前，一个 SQL 注入漏洞（SonarQube 已报但无人关注）进入了生产环境，导致攻击者获取了 200 万用户数据。事故复盘时发现：Jenkins Pipeline 中的 SonarQube 扫描步骤返回了 FAILURE，但 Pipeline 脚本没有检查这个结果，代码照常部署到了生产环境。

安全团队震惊了："我们以为 CI 中的质量门禁在保护我们，但它其实只是一张'贴在墙上的检查报告'——写着'不合格'，但没人拦着不让上线。"

**痛点放大**：

- **门禁失效**：Pipeline 中只执行了扫描，没有等待门禁结果，相当于"体检了但不看报告"
- **异步陷阱**：SonarQube 扫描结果在 Compute Engine 处理完成后才返回，但 Pipeline 可能在 CE 完成前就进入了下一阶段
- **超时设计缺失**：如果 CE 处理超时（如数据库慢查询），Pipeline 无限等待，阻塞整个 CI 队列
- **凭据管理混乱**：SonarQube Token 直接写在 Jenkinsfile 中，被提交到了 Git 仓库

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（看着 Jenkins 的控制台输出，满头问号）："大师！我的 Pipeline 里明明跑了 `sonar-scanner`，控制台也显示 EXECUTION SUCCESS，但是 Quality Gate 明明是 Failed，为什么 Jenkins 还是绿勾？代码还是部署了？"

**大师**："因为你只扫了，没查。`sonar-scanner` 的 EXECUTION SUCCESS 只代表'分析报告已上传'，不代表'门禁通过了'。门禁检查是 SonarQube 服务器端的 Compute Engine 做的——你需要额外等待 CE 处理完毕，然后查询门禁结果。"

**小白**："Jenkins 的 `waitForQualityGate()` 就是干这个的？它的原理是什么？"

**大师**："`waitForQualityGate()` 是 SonarQube Jenkins 插件提供的一个 Pipeline Step。它的工作流程：

1. Scanner 上传报告时附带一个唯一的 `ceTaskId`
2. `waitForQualityGate()` 用这个 `ceTaskId` 轮询 SonarQube 服务器的 `/api/ce/task` 接口
3. 等待 CE 任务完成（状态变为 SUCCESS 或 FAILED）
4. 查询 `/api/qualitygates/project_status` 获取门禁状态
5. 如果门禁 Failed 且 `abortPipeline: true`，Pipeline 立即终止

轮询间隔默认 5 秒，超时时间可以由 `timeout` 步骤控制。"

**小胖**："那 `withSonarQubeEnv` 是干什么的？为什么要在外面包一层？"

**大师**："`withSonarQubeEnv` 从 Jenkins 的凭据管理器读取 SonarQube 的连接信息（URL、Token），注入到环境变量中。这样你就不需要在 `sonar-project.properties` 或命令行里暴露 Token——Token 存储在 Jenkins 的安全凭据存储中。

它的三层作用：
1. **注入 SONAR_HOST_URL**：自动设置 `sonar.host.url`
2. **注入 SONAR_AUTH_TOKEN**：自动设置 `sonar.token`
3. **捕获 ceTaskId**：在 Scanner 执行完成后，自动从输出中解析 ceTaskId，供 `waitForQualityGate` 使用"

**小白**："如果 CE 处理时间特别长——比如 5 分钟还没完成——Pipeline 会一直等下去吗？"

**大师**："这就是为什么需要 `timeout` 包装。典型配置是：

```groovy
timeout(time: 10, unit: 'MINUTES') {
    waitForQualityGate abortPipeline: true
}
```

如果 10 分钟内门禁还没返回结果，Pipeline 会触发超时错误。这通常意味着 SonarQube 服务器出现了严重问题（CE 队列积压、数据库慢查询），需要人工介入。"

---

## 3. 项目实战

### 3.1 环境准备

- Jenkins 2.400+
- 已安装插件：SonarQube Scanner、Pipeline、Credentials Binding
- SonarQube 实例已部署

### 3.2 分步实现

**步骤 1：安装和配置 Jenkins SonarQube 插件**

1. Jenkins → Manage Jenkins → Plugins → Available plugins
2. 搜索 "SonarQube Scanner"，安装
3. 重启 Jenkins

配置 SonarQube Server：

1. Jenkins → Manage Jenkins → Configure System → SonarQube servers
2. 点击 "Add SonarQube"
3. 填写：
   - Name: `SonarQube`
   - Server URL: `http://localhost:9000`
   - Server authentication token: 点击 Add → Jenkins Credentials Provider → 类型 "Secret text" → 填入 Token → ID: `sonar-token`
4. 点击 Save

**配置 SonarQube Scanner 工具**：

1. Manage Jenkins → Global Tool Configuration → SonarQube Scanner
2. 点击 "Add SonarQube Scanner"
3. Name: `SonarScanner`
4. 选择 "Install automatically"，选择 Scanner 版本（如 6.2.1.4610）

**步骤 2：编写 Jenkinsfile（Maven 项目）**

```groovy
pipeline {
    agent any

    tools {
        maven 'Maven-3.9'
    }

    environment {
        SONAR_HOST_URL = 'http://localhost:9000'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build & Test') {
            steps {
                sh 'mvn clean verify -DskipITs'
            }
        }

        stage('SonarQube Analysis') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh 'mvn sonar:sonar'
                }
            }
        }

        stage('Quality Gate Check') {
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    script {
                        def qg = waitForQualityGate()
                        if (qg.status != 'OK') {
                            error "Pipeline aborted: Quality Gate status is ${qg.status}"
                        }
                    }
                }
            }
        }

        stage('Deploy to Staging') {
            when {
                expression { currentBuild.result == 'SUCCESS' }
            }
            steps {
                sh 'kubectl apply -f staging/'
            }
        }
    }

    post {
        failure {
            script {
                // 发送钉钉/企业微信通知
                emailext(
                    subject: "Pipeline Failed: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                    body: "Quality Gate: ${env.SONAR_QG_STATUS}\n${env.BUILD_URL}",
                    to: 'dev-team@company.com'
                )
            }
        }
        always {
            cleanWs()
        }
    }
}
```

**步骤 3：编写 Jenkinsfile（Gradle 项目）**

```groovy
pipeline {
    agent any

    stages {
        stage('Build, Test & Analyze') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh './gradlew clean test jacocoTestReport sonarqube'
                }
            }
        }

        stage('Quality Gate') {
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }
    }
}
```

**步骤 4：编写 Jenkinsfile（前端项目）**

```groovy
pipeline {
    agent any

    environment {
        SONAR_HOST_URL = 'http://localhost:9000'
    }

    stages {
        stage('Install & Test') {
            steps {
                sh 'npm ci'
                sh 'npx jest --coverage'
            }
        }

        stage('SonarQube Analysis') {
            steps {
                withSonarQubeEnv('SonarQube') {
                    sh '''
                        ${SONAR_SCANNER_HOME}/bin/sonar-scanner \
                          -Dsonar.projectKey=com.company:frontend \
                          -Dsonar.sources=src \
                          -Dsonar.javascript.lcov.reportPaths=coverage/lcov.info
                    '''
                }
            }
        }

        stage('Quality Gate') {
            steps {
                timeout(time: 5, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }
    }
}
```

**步骤 5：验证门禁阻断效果**

(a) 故意提交一个包含除零风险的代码：

```java
public double riskyDivide(double a, double b) {
    return a / b;
}
```

(b) 推送到代码仓库，触发 Jenkins Pipeline。

(c) 检查 Pipeline 输出：

```
[SonarQube Analysis] Quality Gate check failed - expected OK but got ERROR
Error: Pipeline aborted: Quality Gate status is ERROR
Finished: FAILURE
```

(d) 确认部署阶段被跳过（没有 "Deploy to Staging" 的输出）。

### 3.3 进阶配置

**多模块项目的条件门禁**：

```groovy
stage('Quality Gate') {
    steps {
        timeout(time: 10, unit: 'MINUTES') {
            script {
                def qg = waitForQualityGate()
                if (qg.status != 'OK') {
                    // 获取详细的失败条件
                    echo "Quality Gate Failed. Details:"
                    qg.conditions.each { condition ->
                        if (condition.status == 'ERROR') {
                            echo "  - ${condition.metricKey}: actual=${condition.actualValue}, threshold=${condition.errorThreshold}"
                        }
                    }
                    error "Quality Gate Failed"
                }
            }
        }
    }
}
```

**手动触发（允许紧急情况绕过门禁）**：

```groovy
stage('Quality Gate') {
    when {
        expression {
            // 如果手动触发且参数 SKIP_QG = true，跳过门禁
            return params.SKIP_QG != 'true'
        }
    }
    steps {
        timeout(time: 10, unit: 'MINUTES') {
            waitForQualityGate abortPipeline: true
        }
    }
}
```

**多分支 Pipeline 的门禁差异**：

```groovy
stage('SonarQube Analysis') {
    steps {
        withSonarQubeEnv('SonarQube') {
            script {
                def sonarArgs = "mvn sonar:sonar"
                if (env.BRANCH_NAME != 'main') {
                    sonarArgs += " -Dsonar.branch.name=${env.BRANCH_NAME}"
                }
                sh sonarArgs
            }
        }
    }
}
```

### 3.4 验证

```bash
# 在 Jenkins 构建日志中确认
# 1. sonar-scanner 执行成功
# 2. waitForQualityGate 返回了状态
# 3. 失败时 Pipeline 状态为 FAILURE
```

---

## 4. 项目总结

### 4.1 Jenkins 集成要点

| 要素 | 说明 |
|------|------|
| `withSonarQubeEnv` | 注入凭据和环境变量，是门禁的前置条件 |
| `waitForQualityGate` | 轮询 CE 结果，等待门禁判定 |
| `abortPipeline: true` | 门禁失败时自动终止 Pipeline |
| `timeout` | 防止无限等待，生产环境建议 10 分钟 |
| `when { expression }` | 根据门禁结果条件执行部署阶段 |

### 4.2 适用场景

- **已有的 Jenkins 基础设施**：在现有 CI 流程中嵌入质量检查
- **多模块 Maven/Gradle 项目**：原生支持，配置量小
- **需要精细控制质量门禁**：支持自定义条件、通知、绕过策略

**不适用场景**：
- Jenkins 版本过老（< 2.300，插件兼容性差）
- 团队转向 GitLab CI/GitHub Actions 的迁移期

### 4.3 注意事项

1. **Token 安全**：永远不要将 Token 硬编码在 Jenkinsfile 中。使用 Jenkins Credentials 存储。
2. **Webhook 回调 vs 轮询**：`waitForQualityGate` 默认使用轮询（每 5 秒一次）。在高频构建场景下，建议配置 Webhook 回调以减少轮询开销。
3. **多分支超时差异化**：Feature 分支可以设 5 分钟超时，main 分支设 15 分钟。

### 4.4 常见踩坑经验

**故障 1：`waitForQualityGate` 一直返回 PENDING，直到超时**

根因：CE 任务处理失败或积压。检查 SonarQube Administration → Projects → Background Tasks，查看具体任务的错误信息。

**故障 2：`withSonarQubeEnv` 认证失败，提示 401**

根因：Jenkins 中存储的 Token 过期或已被吊销。重新生成 Token 并更新 Jenkins Credentials。

### 4.5 思考题

1. 如果多个 Jenkins Job 同时触发同一个项目的扫描，会出现什么情况？如何避免？
2. `waitForQualityGate` 的轮询机制在生产环境中有什么潜在风险？如何升级为 Webhook 回调机制？

> **答案提示**：第1题由 SonarQube 的 CE 任务机制自动去重，最新的扫描会取消排队中的旧任务。第2题：轮询增加了 CI 服务器和 SonarQube 的负载，Webhook 回调更高效，但需要 CI 服务器暴露外网可达的端点。

---

> **推广计划提示**：Jenkins 集成是推广 SonarQube 到 Java/Gradle 团队的"标准配置"。建议为团队提供三个级别的 Jenkinsfile 模板：标准项目模板、多分支项目模板、单体仓库模板。模板应包含所有必需的 `withSonarQubeEnv` + `waitForQualityGate` + `timeout` 组合。
