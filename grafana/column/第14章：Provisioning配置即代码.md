# 第14章：Provisioning配置即代码

## 1. 项目背景

"新环境部署，运维用Ansible装好了Grafana，但Dashboard和数据源还得手动一个个创建。更头疼的是，开发改了Dashboard，我怎么知道改了什么？谁改的？什么时候改的？"

这是一个典型的多环境管理困境。自动化部署方案小刘负责维护开发/测试/预发布/生产四套环境的Grafana。每次新版本上线，他需要确保四套环境的Grafana配置一致——Dashboard、DataSource、告警规则、通知渠道一个都不能少。但现实是：UI手工操作导致环境间配置逐步产生差异，某个关键Dashboard的修改只应用到生产环境、忘记同步到测试环境，最终测试环境看到的监控数据和生产完全不一样。

Grafana的Provisioning体系正是解决这个问题的核武器。它允许你用YAML/JSON文件声明式地定义Datasource、Dashboard、Alert Rule、Notifier等所有配置，配合Git版本控制，实现真正的"配置即代码"。开发改了Dashboard只改变量文件 → 提交到Git → CI/CD自动部署到各环境——全程可追溯、可回滚。

本章将通过"Git仓库驱动的Grafana配置管理"实战，教你用Provisioning彻底告别手工UI操作。

## 2. 项目设计

**小胖**（盯着屏幕上一堆Grafana标签页）：大师，我有四个环境——dev、staging、pre-prod、prod。每个环境一套Grafana。我现在的做法是：在dev环境的Grafana上改好Dashboard，然后导出JSON，再导入到其他三个环境。今天导入的时候手滑覆盖了prod环境的生产Dashboard，运维差点拿刀砍我。

**大师**（放下键盘）：你这个流程有三个致命缺陷。第一，手工导出导入容易出错。第二，没有任何历史记录，谁在什么时候改了什么完全不知道。第三，没有检查机制——你怎么知道四个环境的Dashboard真的完全一致？

**小白**（若有所思）：这听起来像是基础设施即代码能解决的问题。

**大师**：正是。Grafana的Provisioning就是"Grafana的配置即代码"。它的核心思想是——用一个目录里的文件来声明Grafana应该长什么样。Grafana启动时读取这些文件，自动创建/更新对应的资源。

**小胖**：那具体支持哪些资源的Provisioning？

**大师**：Grafana 10.x支持五类资源的Provisioning：

**DataSource Provisioning**：YAML文件定义数据源连接信息。Grafana启动时自动创建或更新数据源。

**Dashboard Provisioning**：JSON文件是Dashboard的定义。Grafana监控指定目录，发现.json文件就自动导入。

**Alerting Provisioning**（API层面）：通过API导出导入告警规则和Contact Point。原生文件Provisioning支持还在完善中。

**Notifier Provisioning**：通知渠道的YAML配置。

**Plugin Provisioning**：声明需要哪些插件，Grafana自动安装。

**小白**：那Provisioning的工作原理是什么？Grafana怎么知道去哪个目录找文件？

**大师**：通过grafana.ini中的`[provisioning]`段配置。默认路径是`/etc/grafana/provisioning/`。这个目录下的每个子目录对应一种资源类型——datasources/、dashboards/、notifiers/、plugins/。Grafana启动时扫描这些目录，发现变化就更新。

关键参数是`updateIntervalSeconds`。Dashboard目录配了这个参数后，Grafana会定期扫描目录，发现新文件自动导入，发现文件变化自动更新——不需要重启Grafana。

**小胖**（兴奋）：那我是不是可以把Dashboard JSON文件放Git仓库，然后用CI/CD同步到Grafana服务器的provisioning目录？

**大师**：对！这就是GitOps标准的实践方式。我给你画个流程图：

```mermaid
graph LR
    A[开发者修改Dashboard] --> B[导出JSON到Git仓库]
    B --> C[提交Pull Request]
    C --> D[Code Review]
    D --> E[合并到主分支]
    E --> F[CI/CD Pipeline]
    F --> G[同步JSON到Grafana provisioning目录]
    G --> H[Grafana自动加载]
```

**小胖**：等等，有个问题。如果Dashboard在Grafana UI中被人改了，provisioning又把它改回去了，这不就变成"两个人打架"吗？

**大师**：这正是Provisioning的一个重要保护机制——`allowUiUpdates`参数。

如果`allowUiUpdates: true`，UI中修改完Provisioning管理的Dashboard后，修改可以被保存。但下次Grafana重启（或provisioning目录扫描时），文件内容会覆盖UI中的修改。

如果`allowUiUpdates: false`，UI中的Save按钮直接变灰，你根本无法通过UI修改这个Dashboard。这就是"强制配置即代码"模式。

一般建议：生产环境设`allowUiUpdates: false`，开发环境设`true`方便快速调试。

**小白**：那Alerting的Provisioning呢？告警规则怎么纳入Git管理？

**大师**：Grafana的Alerting Provisioning还在进化中。目前最稳定的方式是用API进行告警规则的导入导出：

```bash
# 导出所有告警规则
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/provisioning/alert-rules/export \
  -o alert-rules.json

# 导入（先删后建）
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @alert-rules.json \
  http://localhost:3000/api/v1/provisioning/alert-rules
```

**技术映射**：Provisioning = 建房图纸（蓝图定义一切，施工队按图建房），allowUiUpdates = 修改权限（false = 只能按图纸建，true = 允许现场微调但下次按图纸重建）。

## 3. 项目实战

**环境准备**

基于之前的Docker Compose环境，准备Provisioning目录结构。

```bash
mkdir -p provisioning/datasources
mkdir -p provisioning/dashboards
mkdir -p provisioning/notifiers
mkdir -p provisioning/plugins
```

**步骤一：DataSource Provisioning**

创建 `provisioning/datasources/prometheus.yaml`：

```yaml
apiVersion: 1

# 数据源列表
datasources:
  - name: Prometheus-Prod
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    version: 1
    editable: true
    jsonData:
      timeInterval: "15s"
      queryTimeout: "60s"
      httpMethod: "POST"
      manageAlerts: true
      prometheusType: "Prometheus"
      prometheusVersion: "2.50.0"

  - name: Prometheus-Staging
    type: prometheus
    access: proxy
    url: http://prometheus-staging:9090
    isDefault: false
    version: 1
    editable: true
    jsonData:
      timeInterval: "15s"

# 删除不在列表中的数据源（可选，谨慎使用）
deleteDatasources: []
```

重启Grafana或触发Provisioning重载后，这两个数据源自动出现。

**步骤二：Dashboard Provisioning**

创建 `provisioning/dashboards/dashboard-provider.yaml`：

```yaml
apiVersion: 1

providers:
  - name: 'GitOps Dashboards'
    orgId: 1
    folder: 'GitOps Managed'
    folderUid: ''
    type: file
    disableDeletion: false     # false = 文件删除后Dashboard也删除
    updateIntervalSeconds: 30  # 每30秒扫描一次目录变化
    allowUiUpdates: false      # 禁止UI手工修改
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

现在准备Dashboard JSON文件。先从Grafana导出一个现成的Dashboard JSON，放入目录：

```bash
# 把导出的Dashboard JSON文件放入
cp my-dashboard.json provisioning/dashboards/
```

确保JSON文件的顶层结构正确：

```json
{
  "dashboard": {
    "uid": "host-monitor-v2",
    "title": "主机监控 (Provisioning管理)",
    "tags": ["provisioned", "infrastructure"],
    "timezone": "browser",
    "schemaVersion": 38,
    "version": 0,
    "refresh": "30s",
    "panels": [...]
  },
  "folderUid": "",
  "overwrite": true
}
```

关键字段说明：
- `uid`：如果不指定，Grafana自动生成。跨环境保持一致的话手动指定。
- `overwrite`：true = 文件更新时覆盖同名Dashboard。
- `version`：provisioning Dashboard很少需要递增version，Grafana会自动处理。

**步骤三：多环境Dashboard管理**

使用环境变量或不同的目录实现多环境差异化配置。

```yaml
# providers配置中使用环境变量
providers:
  - name: '${ENV_NAME} Dashboards'
    folder: '${ENV_NAME}'
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards/${ENV_NAME}
```

目录结构：
```
provisioning/dashboards/
├── dashboard-provider.yaml
├── common/          # 所有环境共用
│   ├── host-monitor.json
│   └── app-overview.json
├── dev/             # 开发环境专用
│   └── debug-panels.json
├── staging/
└── prod/
```

**步骤四：批量Dashboard导入实战**

编写Python脚本从Grafana实例导出所有Dashboard到provisioning目录：

```python
import requests
import json
import os
import re

GRAFANA_URL = "http://localhost:3000"
TOKEN = "glsa_xxx"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
OUTPUT_DIR = "./provisioning/dashboards/exported"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 获取所有Dashboard
resp = requests.get(f"{GRAFANA_URL}/api/search", 
                    params={"type": "dash-db", "limit": 5000},
                    headers=HEADERS)
dashboards = resp.json()

for db in dashboards:
    uid = db["uid"]
    title = db["title"]
    
    # 获取Dashboard详细JSON
    detail = requests.get(f"{GRAFANA_URL}/api/dashboards/uid/{uid}", 
                          headers=HEADERS).json()
    
    # 清理不必要字段，标准化版本
    dash = detail["dashboard"]
    dash["id"] = None  # provisioning不需要id
    if "version" in dash:
        dash["version"] = 0
    
    # 构造provisioning格式
    provisioning_doc = {
        "dashboard": dash,
        "folderUid": db.get("folderUid", ""),
        "overwrite": True
    }
    
    # 生成安全文件名
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
    filename = f"{OUTPUT_DIR}/{uid}_{safe_title}.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(provisioning_doc, f, indent=2, ensure_ascii=False)
    
    print(f"导出: {filename}")

print(f"共导出 {len(dashboards)} 个Dashboard到 {OUTPUT_DIR}")
```

**步骤五：Notifier Provisioning（通知渠道）**

创建 `provisioning/notifiers/notifiers.yaml`：

```yaml
apiVersion: 1

notifiers:
  - name: ops-email-notifier
    type: email
    uid: ops-email-uid
    is_default: false
    settings:
      addresses: ops@example.com
      singleEmail: false
  
  - name: ops-slack-notifier
    type: slack
    uid: ops-slack-uid
    is_default: true
    settings:
      url: https://hooks.slack.com/services/XXX
      recipient: "#ops-alerts"
      username: "Grafana Bot"
      icon_emoji: ":alert:"
      uploadImage: false
```

**步骤六：GitOps完整流程**

```bash
# 1. 初始化Git仓库
git init
git add provisioning/
git commit -m "init: Grafana provisioning v1.0"

# 2. 开发修改Dashboard后导出
# ...（导出JSON到provisioning/dashboards/）...

# 3. 提交变更
git add provisioning/dashboards/
git commit -m "update: 主机监控面板添加网络流量图"

# 4. CI/CD处理（示例为GitHub Actions）
cat > .github/workflows/deploy-grafana.yml <<'EOF'
name: Deploy Grafana Provisioning
on:
  push:
    paths:
      - 'provisioning/**'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate Dashboard JSON
        run: |
          for f in provisioning/dashboards/*.json; do
            python -m json.tool "$f" > /dev/null || exit 1
          done
      - name: Deploy to Grafana server
        run: |
          rsync -avz provisioning/ grafana@server:/etc/grafana/provisioning/
          ssh grafana@server "sudo systemctl reload grafana-server"
EOF
```

**步骤七：Provisioning状态验证**

```bash
# 检查Provisioning是否生效
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/datasources | jq '.[] | {name, type}'

# 查看provisioning-dashboard列表
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/search?tag=provisioned | jq '.[].title'

# 检查Grafana日志
docker logs grafana | grep "provisioning"
```

**常见坑点**
1. **Dashboard JSON缺少uid导致重复创建**：Provisioning通过uid识别Dashboard的唯一性。如果JSON文件没有uid，每次扫描都会创建一个新的。
2. **datasource删除未生效**：YAML中设置`deleteDatasources`为非空列表可实现"不在列表中的数据源自动删除"，这是危险操作，谨慎使用。
3. **allowUiUpdates=false导致无法调试**：调试Dashboard时设为true，稳定后Commit时改为false。
4. **不同Grafana版本schemaVersion不同**：导出Dashboard时schemaVersion取决于导出时的Grafana版本。跨版本部署需要兼容性测试。

## 4. 项目总结

**Provisioning五大支柱**

| 类型 | 文件格式 | 自动导入 | allowUiUpdates支持 | 成熟度 |
|------|---------|---------|--------------------|--------|
| DataSource | YAML | ✅ 启动时 | ✅ | 成熟 |
| Dashboard | JSON | ✅ 定时扫描 | ✅ | 成熟 |
| Notifier | YAML | ✅ 启动时 | ❌ | 稳定 |
| Plugin | YAML | ✅ 启动时 | N/A | 稳定 |
| Alerting | JSON (API) | ❌ 需API | ❌ | 开发中 |

**优点**
| 特性 | 说明 |
|------|------|
| 声明式管理 | 配置文件描述期望状态，Grafana自动达到 |
| 版本控制 | Git管理所有配置，变更可追溯可回滚 |
| 环境一致性 | 同一份配置可部署到dev/staging/prod |
| 无需手工操作 | 消除UI点击的重复劳动和人为错误 |
| 自动发现 | 启动时或定时扫描自动加载变化 |

**缺点**
| 特性 | 说明 |
|------|------|
| Alerting支持不完善 | 告警规则和通知策略的Provisioning尚未文件化 |
| 无动态变量 | 无法在Dashboard JSON中使用环境变量动态替换 |
| 组织级隔离弱 | 跨Org的Provisioning同一套配置不适用 |
| 删除保护弱 | `disableDeletion=false`时删除文件Dashboard也自动删除 |

**适用场景**
1. 多环境部署：同一套Dashboard定义部署到dev/staging/prod
2. GitOps工作流：Dashboard变更经过Code Review再发布
3. 团队协作：多人维护Dashboard通过PR协作
4. 灾难恢复：从Git仓库一键恢复所有Grafana配置

**注意事项**
1. Provisioning Dashboard会自动添加`provisioned`标签，方便识别
2. Grafana启动时DataSource Provisioning先执行，Dashboard后执行
3. Dashboard JSON中引用的DataSource UID必须存在（否则面板会显示"Datasource not found"）
4. 不要手动编辑Provisioning自动创建的Dashboard（allowUiUpdates为false时根本编辑不了）

**常见踩坑经验**
1. **Dashboard文件修改后不生效**：确认`updateIntervalSeconds`设置不为0，且文件名有变化（.json）。如果文件名不变但内容变了，Grafana通过`uid`识别，不检查文件mtime。
2. **数据源引用失效**：因为DataSource的uid在Dashboard JSON中是硬编码的，如果DataSource provisioning改了uid，所有Dashboard都要批量替换。
3. **Provisioning与Import冲突**：如果同时使用Provisioning和手动Import管理同一个Dashboard，两者会冲突。解决：要么全用Provisioning，要么全用Import。

**思考题**
1. 如何在不重启Grafana的情况下，实现"Dashboard变更后自动通知相关团队成员进行Code Review"？
2. 如果同一个DataSource被多个Provisioning文件定义，Grafana会如何处理？哪个文件的配置生效？
