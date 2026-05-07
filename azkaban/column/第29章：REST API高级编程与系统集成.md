# 第29章：REST API高级编程与系统集成

## 1. 项目背景

### 业务场景

数据平台需要将Azkaban深度集成到公司的DevOps流水线中。具体需求包括：

1. **CI/CD集成**：当Git仓库中的Flow文件发生变更时，自动上传到Azkaban测试环境并执行冒烟测试
2. **监控集成**：Prometheus需要每分钟查询最近失败Flow的数量
3. **自助服务门户**：业务团队需要一个简化的Web界面来触发他们的ETL流程（不想学Azkaban的完整界面）
4. **自动清理**：每周定期清理3个月前的历史执行记录

这些需求都依赖Azkaban的REST API。但团队发现Azkaban的API文档不完整，部分接口需要从源码中逆向工程。

### 痛点放大

API掌握不足时：

1. **手工作坊**：日常运维操作全部在Web界面手工完成，效率低下
2. **集成困难**：外部系统不知道如何可靠地调用Azkaban
3. **批量操作不支持**：Web界面没有"批量创建50个调度"的功能，只能重复操作

## 2. 项目设计——剧本式交锋对话

**小胖**（对着Postman调试API）：大师，Azkaban的API文档太简陋了！我想查某个Flow的执行历史，试了好几个URL都不对……

**大师**：Azkaban的API确实是"看源码比看文档强"。我给你整理一份核心API清单：

**小白**：有没有Python SDK之类的封装？

**大师**：官方没有Python SDK，但社区有一些封装。我给你写一个完整的Python客户端，覆盖所有常用操作：

```python
class AzkabanClient:
    # 项目管理
    create_project(name, desc)
    delete_project(name)
    upload_zip(project, zip_path)
    
    # Flow管理
    execute_flow(project, flow, params)
    cancel_flow(exec_id)
    get_execution_status(exec_id)
    get_job_logs(exec_id, job_name)
    
    # 调度管理
    create_schedule(project, flow, cron, timezone)
    get_all_schedules()
    remove_schedule(schedule_id)
    
    # 监控查询
    get_running_flows()
    get_recent_failures(minutes=15)
```

**小胖**：那批量操作呢？我想给50个Flow统一创建凌晨2点的调度。

**大师**：写一个循环脚本就行。但要注意速率限制——Azkaban的Web Server处理能力有上限，别一口气发50个请求打崩服务器。

### 技术映射总结

- **REST API** = 普通话（不同系统之间的通用语言）
- **批量操作** = 自动发邮件（一封一封发很累，写个脚本自动群发）
- **速率限制** = 餐厅翻台率（一下子来50桌客人厨房会炸）

## 3. 项目实战

### 3.1 环境准备

Python 3.8+ with `requests` library.

### 3.2 分步实现

#### 步骤1：完整的Python API客户端

**目标**：封装Azkaban所有核心操作。

```python
#!/usr/bin/env python3
# azkaban_client_v2.py —— Azkaban REST API 完整客户端

import requests
import time
import json
from pathlib import Path

class AzkabanClient:
    """Azkaban REST API 完整客户端"""
    
    def __init__(self, base_url, username=None, password=None, session_id=None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        
        if session_id:
            self.session.cookies.set('azkaban.browser.session.id', session_id)
        elif username and password:
            self._login(username, password)
    
    # ============ 认证 ============
    def _login(self, username, password):
        resp = self._post("/", data={
            "action": "login",
            "username": username,
            "password": password
        })
        if "session.id" not in resp.text:
            raise Exception(f"Login failed: {resp.text[:200]}")
        print("✓ Login successful")
    
    # ============ 项目管理 ============
    def create_project(self, name, description=""):
        return self._post("/manager?action=create", data={
            "name": name,
            "description": description
        })
    
    def delete_project(self, name):
        return self._get("/manager", params={"delete": "true", "project": name})
    
    def list_projects(self):
        return self._get("/index", params={"ajax": "fetchuserprojects"})
    
    def upload_project(self, project, zip_path):
        with open(zip_path, 'rb') as f:
            return self._post(
                f"/manager?project={project}&ajax=upload",
                files={"file": f}
            )
    
    def fetch_project_flows(self, project):
        return self._get("/manager", params={
            "project": project,
            "ajax": "fetchprojectflows"
        })
    
    # ============ Flow执行 ============
    def execute_flow(self, project, flow, flow_params=None, disabled_jobs=None,
                     failure_action="finishPossible", concurrent_option="skip"):
        data = {
            "ajax": "executeFlow",
            "project": project,
            "flow": flow,
            "failureAction": failure_action,
            "concurrentOption": concurrent_option,
        }
        
        if flow_params:
            for key, value in flow_params.items():
                data[f"flowOverride[{key}]"] = value
        
        if disabled_jobs:
            data["disabled"] = ",".join(disabled_jobs)
        
        resp = self._post("/executor", data=data)
        result = resp.json()
        
        if "execid" in result:
            return result["execid"]
        elif "error" in result:
            raise Exception(f"Execute failed: {result['error']}")
        return None
    
    def cancel_flow(self, exec_id):
        return self._get("/executor", params={
            "execid": exec_id,
            "ajax": "cancelFlow"
        })
    
    def get_execution_info(self, exec_id):
        return self._get("/executor", params={
            "execid": exec_id,
            "ajax": "fetchexecflow"
        })
    
    def get_flow_executions(self, project, flow, start=0, length=20):
        return self._get("/manager", params={
            "project": project,
            "ajax": "fetchFlowExecutions",
            "flow": flow,
            "start": start,
            "length": length
        })
    
    def get_job_logs(self, exec_id, job_id, offset=0, length=50000):
        return self._get("/executor", params={
            "execid": exec_id,
            "jobId": job_id,
            "ajax": "fetchExecJobLogs",
            "offset": offset,
            "length": length
        })
    
    def wait_for_completion(self, exec_id, timeout=3600, poll_interval=15):
        start_time = time.time()
        while time.time() - start_time < timeout:
            info = self.get_execution_info(exec_id)
            status = info.get("status", "UNKNOWN")
            if status in ("SUCCEEDED", "FAILED", "KILLED"):
                return status
            time.sleep(poll_interval)
        return "TIMEOUT"
    
    # ============ 调度管理 ============
    def schedule_cron(self, project, flow, cron_expression, 
                      timezone="Asia/Shanghai", options=None):
        data = {
            "ajax": "scheduleCronFlow",
            "projectName": project,
            "flow": flow,
            "cronExpression": cron_expression,
            "scheduleTimezone": timezone
        }
        if options:
            data.update(options)
        return self._post("/schedule", data=data)
    
    def fetch_all_schedules(self):
        return self._get("/schedule", params={"ajax": "fetchAllScheduledFlows"})
    
    def remove_schedule(self, schedule_id):
        return self._post("/schedule", data={
            "action": "removeSched",
            "scheduleId": schedule_id
        })
    
    def pause_schedule(self, schedule_id):
        return self._post("/schedule", data={
            "action": "pauseSched",
            "scheduleId": schedule_id
        })
    
    def resume_schedule(self, schedule_id):
        return self._post("/schedule", data={
            "action": "resumeSched",
            "scheduleId": schedule_id
        })
    
    # ============ Executor管理 ============
    def fetch_all_executors(self):
        return self._get("/executor", params={"ajax": "fetchallexecutors"})
    
    def activate_executor(self, executor_id):
        return self._post("/executor?ajax=activate", 
                         data={"executorId": executor_id})
    
    # ============ 监控查询 ============
    def get_running_flows(self):
        return self._get("/executor", params={"ajax": "getRunning"})
    
    def get_recent_failures(self, project=None, hours=24):
        """获取最近N小时的失败Flow"""
        resp = self._get("/executor", params={
            "ajax": "fetchexecflowhistory",
            "start": 0,
            "length": 100
        })
        data = resp.json()
        cutoff = (time.time() - hours * 3600) * 1000
        
        failures = [
            e for e in data.get("executions", [])
            if e.get("status") == "FAILED"
            and e.get("startTime", 0) > cutoff
            and (project is None or e.get("projectName") == project)
        ]
        return failures
    
    # ============ 内部方法 ============
    def _get(self, path, params=None):
        resp = self.session.get(f"{self.base_url}{path}", params=params)
        resp.raise_for_status()
        try:
            return resp.json()
        except:
            return {"raw": resp.text}
    
    def _post(self, path, data=None, files=None):
        resp = self.session.post(f"{self.base_url}{path}", data=data, files=files)
        resp.raise_for_status()
        try:
            return resp.json()
        except:
            return resp
```

#### 步骤2：批量操作工具

**目标**：实现批量创建调度、批量执行等。

```python
# 使用AzkabanClient进行批量操作

client = AzkabanClient("http://localhost:8081", "admin", "admin")

# 1. 批量创建调度
flows_with_schedule = {
    "daily_report": "0 0 2 * * ?",
    "user_profile": "0 0 3 * * ?",
    "data_sync": "0 0/30 * * * ?",
    "cleanup": "0 0 5 * * ?",
}

for flow, cron in flows_with_schedule.items():
    try:
        result = client.schedule_cron("core_pipeline", flow, cron)
        print(f"✓ {flow}: schedule created")
    except Exception as e:
        print(f"✗ {flow}: {e}")

# 2. 批量补数据
import datetime

start_date = datetime.date(2025, 1, 10)
end_date = datetime.date(2025, 1, 15)
current = start_date

while current <= end_date:
    date_str = current.strftime("%Y-%m-%d")
    try:
        exec_id = client.execute_flow(
            "core_pipeline", "daily_report",
            flow_params={"process_date": date_str}
        )
        status = client.wait_for_completion(exec_id, timeout=7200)
        print(f"✓ {date_str}: {status}")
    except Exception as e:
        print(f"✗ {date_str}: {e}")
    current += datetime.timedelta(days=1)
```

#### 步骤3：CI/CD集成

**目标**：Git提交时自动部署到Azkaban。

```yaml
# .github/workflows/azkaban-deploy.yml
name: Deploy to Azkaban

on:
  push:
    paths:
      - 'azkaban-flows/**'
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Build Deployment Package
        run: |
          cd azkaban-flows/${{ matrix.project }}
          zip -r /tmp/deploy.zip ./*
      
      - name: Deploy to Azkaban
        env:
          AZKABAN_URL: ${{ secrets.AZKABAN_URL }}
          AZKABAN_USER: ${{ secrets.AZKABAN_USER }}
          AZKABAN_PASS: ${{ secrets.AZKABAN_PASS }}
        run: |
          python3 deploy.py --project=${{ matrix.project }} --env=test
```

```python
# deploy.py —— CI/CD部署脚本
import os
import sys
from azkaban_client_v2 import AzkabanClient

def deploy(project, env="test"):
    client = AzkabanClient(
        os.environ["AZKABAN_URL"],
        os.environ["AZKABAN_USER"],
        os.environ["AZKABAN_PASS"]
    )
    
    # 上传新版本
    zip_path = "/tmp/deploy.zip"
    result = client.upload_project(project, zip_path)
    
    if "error" in str(result):
        print(f"✗ Upload failed: {result}")
        sys.exit(1)
    
    print(f"✓ Deployed to {env}")
    
    # 执行冒烟测试
    smoke_test_flow = f"smoke_test_{project}"
    exec_id = client.execute_flow(project, smoke_test_flow)
    status = client.wait_for_completion(exec_id, timeout=600)
    
    if status != "SUCCEEDED":
        print(f"✗ Smoke test FAILED: {status}")
        sys.exit(1)
    
    print(f"✓ Smoke test PASSED")

if __name__ == '__main__':
    deploy(sys.argv[2], sys.argv[4])
```

#### 步骤4：自助服务门户

**目标**：为业务团队提供简化的触发界面。

```python
#!/usr/bin/env python3
# self_service_portal.py —— 基于Flask的自助服务门户

from flask import Flask, render_template, request, jsonify
from azkaban_client_v2 import AzkabanClient

app = Flask(__name__)
client = AzkabanClient("http://localhost:8081", "portal_user", "portal_pass")

# 简化的工作流定义（隐藏Azkaban的复杂性）
WORKFLOWS = {
    "daily_report": {
        "name": "日报生成",
        "description": "生成昨天的业务日报",
        "params": ["date"],
        "estimated_time": "10分钟"
    },
    "data_export": {
        "name": "数据导出",
        "description": "导出指定日期的数据到CSV",
        "params": ["date", "table_name"],
        "estimated_time": "5分钟"
    }
}

@app.route('/')
def index():
    return render_template('portal.html', workflows=WORKFLOWS)

@app.route('/trigger', methods=['POST'])
def trigger_workflow():
    data = request.json
    workflow_id = data.get('workflow_id')
    params = data.get('params', {})
    
    if workflow_id not in WORKFLOWS:
        return jsonify({"error": f"Unknown workflow: {workflow_id}"}), 400
    
    try:
        exec_id = client.execute_flow(
            "self_service", workflow_id,
            flow_params=params
        )
        return jsonify({
            "status": "submitted",
            "execution_id": exec_id,
            "message": f"工作流已提交，预计{WORKFLOWS[workflow_id]['estimated_time']}完成"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/status/<exec_id>')
def check_status(exec_id):
    info = client.get_execution_info(exec_id)
    return jsonify({
        "status": info.get("status"),
        "start_time": info.get("startTime"),
        "end_time": info.get("endTime")
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

#### 步骤5：自动清理脚本

**目标**：定期清理过期数据。

```python
#!/usr/bin/env python3
# auto_cleanup.py —— 自动清理过期数据

import pymysql
from datetime import datetime, timedelta

def cleanup_old_records(host, user, password, db, retention_months=6):
    """清理超过保留期限的执行记录"""
    
    conn = pymysql.connect(host=host, user=user, password=password, database=db)
    cutoff = int((datetime.now() - timedelta(days=retention_months*30)).timestamp() * 1000)
    
    cursor = conn.cursor()
    
    # 1. 统计待清理记录
    cursor.execute(
        "SELECT COUNT(*) FROM execution_flows WHERE start_time < %s",
        (cutoff,)
    )
    total = cursor.fetchone()[0]
    print(f"待清理 Flow 记录: {total}")
    
    # 2. 分批删除（避免锁表）
    batch_size = 1000
    deleted = 0
    while True:
        # 先删除关联的execution_logs
        cursor.execute("""
            DELETE FROM execution_logs 
            WHERE exec_id IN (
                SELECT exec_id FROM execution_flows 
                WHERE start_time < %s
                LIMIT %s
            )
        """, (cutoff, batch_size))
        
        # 再删除execution_jobs
        cursor.execute("""
            DELETE FROM execution_jobs
            WHERE exec_id IN (
                SELECT exec_id FROM execution_flows
                WHERE start_time < %s
                LIMIT %s
            )
        """, (cutoff, batch_size))
        
        # 最后删除execution_flows
        cursor.execute(
            "DELETE FROM execution_flows WHERE start_time < %s LIMIT %s",
            (cutoff, batch_size)
        )
        
        affected = cursor.rowcount
        deleted += affected
        conn.commit()
        
        print(f"  已清理 {deleted}/{total} 条记录...")
        
        if affected < batch_size:
            break
    
    print(f"✓ 清理完成，共删除 {deleted} 条记录")
    conn.close()

if __name__ == '__main__':
    cleanup_old_records(
        "prod-db", "azkaban", "prod_pass", "azkaban",
        retention_months=6
    )
```

### 3.3 测试验证

```bash
# 测试Python客户端
python3 -c "
from azkaban_client_v2 import AzkabanClient
c = AzkabanClient('http://localhost:8081', 'admin', 'admin')
print(c.list_projects())
print(c.fetch_all_schedules())
"

# 测试自助门户
curl http://localhost:5000/
curl -X POST http://localhost:5000/trigger \
  -H 'Content-Type: application/json' \
  -d '{"workflow_id":"daily_report","params":{"date":"2025-01-15"}}'
```

## 4. 项目总结

### API能力清单

| 功能域 | 关键API | 可用性 |
|--------|---------|--------|
| 项目管理 | CRUD + Upload | ★★★ |
| Flow执行 | execute/status/cancel/logs | ★★★ |
| 调度管理 | schedule/pause/resume/remove | ★★★ |
| Executor管理 | list/activate | ★★☆ |
| 历史查询 | fetchexecflowhistory | ★★☆ |
| 权限管理 | addPermission | ★☆☆ |

### 适用场景

- **适用**：需要自动化的运维操作、CI/CD集成、外部系统对接、自助服务门户
- **不适用**：简单的单机使用（Web操作足够）

### 注意事项

- session有效期默认24小时，长时间运行需增加re-login机制
- 批量操作需加延迟避免打崩Web Server
- API返回格式并不总是标准JSON——部分返回HTML片段
- 大日志下载建议用`offset`+`length`分页

### 常见踩坑经验

1. **/manager端点返回HTML**：部分API如`delete=true`返回的是HTML重定向而非JSON。需用`allow_redirects=False`。
2. **Flow参数没有覆盖**：`flowOverride[key]`只对在.job文件中已声明`env.key=${key}`的参数生效。
3. **并发请求session冲突**：同一个session并发请求可能相互影响，生产环境使用独立session或连接池。

### 思考题

1. 如何为Azkaban API实现"请求重放保护"——防止同一个API请求因网络重试被重复执行？
2. 如果需要在1000个Flow中搜索"哪个Flow包含特定Job类型的配置"，如何高效实现？全量遍历API接口还是直接查数据库？
