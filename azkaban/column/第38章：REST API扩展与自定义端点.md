# 第38章：REST API扩展与自定义端点

## 1. 项目背景

### 业务场景

监控系统需要每分钟查询Azkaban中所有RUNNING状态的Flow及其耗时。当前Azkaban没有这样一个"批量查询"的API——需要先调用`fetchAllExecutors`获取所有Executor，再逐个调用`getRunning`。如果有5个Executor，就是6次HTTP请求，延迟高且浪费资源。

架构师决定在Azkaban中注册一个自定义Servlet端点`/custom/runningFlowsSummary`，一次请求返回所有Executor运行中的Flow汇总信息。

### 痛点放大

没有自定义端点时：
- 外部系统集成需要多次API调用，延迟高
- 无法提供定制化的聚合数据
- 运维脚本需要复杂的数据拼接逻辑

## 2. 项目设计——剧本式交锋对话

**小胖**：大师，我想给Azkaban加一个自定义API端点，该怎么做？

**大师**：Azkaban Web Server是基于Jetty的，添加自定义端点的本质是注册一个新的Servlet。

**小白**：Servlet是什么？跟REST API有什么关系？

**大师**：在Java Web开发中，Servlet是处理HTTP请求的基本单元。Azkaban的`/executor`、`/manager`、`/schedule`这些路径都对应不同的Servlet。添加新端点只需三步：
1. 写一个继承`HttpServlet`的类
2. 在启动配置中注册URL映射
3. 编译部署

### 技术映射总结

- **Servlet** = 餐厅服务员（每个窗口负责不同的菜单请求）
- **URL映射** = 门牌号（/executor = 1号窗口，/custom = 新开的特需窗口）
- **Jetty** = 餐厅本身（承载所有窗口和服务）

## 3. 项目实战

### 3.1 核心实现

#### 步骤1：自定义Servlet

```java
package com.company.azkaban.servlet;

import azkaban.executor.ExecutorManager;
import azkaban.server.HttpRequestUtils;
import azkaban.server.session.Session;
import azkaban.user.User;
import azkaban.utils.JSONUtils;
import org.apache.log4j.Logger;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.*;

/**
 * 自定义API端点：批量查询运行中的Flow
 * 
 * GET /custom/runningFlowsSummary
 * 返回所有Executor上运行中的Flow汇总
 */
public class RunningFlowsServlet extends HttpServlet {
    
    private static final Logger logger = Logger.getLogger(RunningFlowsServlet.class);
    private final ExecutorManager executorManager;
    
    public RunningFlowsServlet(ExecutorManager executorManager) {
        this.executorManager = executorManager;
    }
    
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) 
            throws ServletException, IOException {
        
        try {
            // 1. 验证用户身份
            Session session = HttpRequestUtils.getSessionFromRequest(req);
            User user = session.getUser();
            
            if (user == null) {
                resp.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                writeJson(resp, createError("Authentication required"));
                return;
            }
            
            // 2. 获取所有活跃Executor
            List<Map<String, Object>> executorSummaries = new ArrayList<>();
            long now = System.currentTimeMillis();
            
            for (var executor : executorManager.getAllActiveExecutors()) {
                Map<String, Object> execSummary = new HashMap<>();
                execSummary.put("executorId", executor.getId());
                execSummary.put("host", executor.getHost());
                execSummary.put("port", executor.getPort());
                
                // 获取该Executor上的运行中Flow
                List<Integer> runningFlows = executorManager.getRunningFlows(executor.getId());
                execSummary.put("runningFlowCount", runningFlows.size());
                
                // 获取每个Flow的详细信息
                List<Map<String, Object>> flowDetails = new ArrayList<>();
                for (int execId : runningFlows) {
                    var flow = executorManager.getExecutableFlow(execId);
                    if (flow != null) {
                        Map<String, Object> detail = new HashMap<>();
                        detail.put("execId", flow.getExecutionId());
                        detail.put("projectName", flow.getProjectName());
                        detail.put("flowId", flow.getFlowId());
                        detail.put("submitUser", flow.getSubmitUser());
                        detail.put("startTime", flow.getStartTime());
                        
                        // 计算已运行时间
                        long duration = now - flow.getStartTime();
                        detail.put("runningDurationMinutes", duration / 60000);
                        
                        // 获取当前运行的Job
                        List<String> runningJobs = new ArrayList<>();
                        for (var node : flow.getExecutableNodes()) {
                            if (node.getStatus() == azkaban.executor.Status.RUNNING) {
                                runningJobs.add(node.getId());
                            }
                        }
                        detail.put("runningJobs", runningJobs);
                        
                        flowDetails.add(detail);
                    }
                }
                execSummary.put("flows", flowDetails);
                executorSummaries.add(execSummary);
            }
            
            // 3. 构建汇总统计
            Map<String, Object> summary = new HashMap<>();
            int totalRunningFlows = executorSummaries.stream()
                .mapToInt(e -> (int) e.get("runningFlowCount"))
                .sum();
            
            summary.put("totalExecutors", executorSummaries.size());
            summary.put("totalRunningFlows", totalRunningFlows);
            summary.put("queryTimestamp", now);
            summary.put("executors", executorSummaries);
            
            // 4. 返回JSON
            writeJson(resp, summary);
            
        } catch (Exception e) {
            logger.error("Failed to get running flows summary", e);
            resp.setStatus(HttpServletResponse.SC_INTERNAL_SERVER_ERROR);
            writeJson(resp, createError("Internal server error: " + e.getMessage()));
        }
    }
    
    @Override
    protected void doPost(HttpServletRequest req, HttpServletResponse resp) 
            throws ServletException, IOException {
        // POST也支持：用于批量Kill运行中的Flow
        String action = req.getParameter("action");
        
        if ("killAll".equals(action)) {
            try {
                int killed = 0;
                for (var executor : executorManager.getAllActiveExecutors()) {
                    for (int execId : executorManager.getRunningFlows(executor.getId())) {
                        executorManager.cancelFlow(execId, "azkaban");
                        killed++;
                    }
                }
                
                Map<String, Object> result = new HashMap<>();
                result.put("status", "success");
                result.put("killedFlows", killed);
                writeJson(resp, result);
                
            } catch (Exception e) {
                resp.setStatus(500);
                writeJson(resp, createError(e.getMessage()));
            }
        } else {
            resp.setStatus(400);
            writeJson(resp, createError("Unknown action: " + action));
        }
    }
    
    private void writeJson(HttpServletResponse resp, Object data) throws IOException {
        resp.setContentType("application/json");
        resp.setCharacterEncoding("UTF-8");
        resp.getWriter().write(JSONUtils.toJSON(data));
    }
    
    private Map<String, Object> createError(String message) {
        Map<String, Object> error = new HashMap<>();
        error.put("error", message);
        return error;
    }
}
```

#### 步骤2：注册Servlet

```java
// 在AzkabanWebServer.java的setupAndStartServer()中添加
public class AzkabanWebServer {
    
    private void setupAndStartServer() {
        // ... 现有代码 ...
        
        // 注册自定义Servlet
        ServletContextHandler context = new ServletContextHandler(ServletContextHandler.SESSIONS);
        
        // URL映射: /custom/runningFlowsSummary → RunningFlowsServlet
        context.addServlet(
            new ServletHolder(new RunningFlowsServlet(this.executorManager)),
            "/custom/runningFlowsSummary"
        );
        
        // 添加认证过滤器（只有登录用户可访问）
        context.addFilter(
            LoginFilter.class,
            "/custom/*",
            EnumSet.of(DispatcherType.REQUEST)
        );
        
        logger.info("Custom servlet registered: /custom/runningFlowsSummary");
    }
}
```

#### 步骤3：使用自定义API

```bash
# 查询运行中Flow汇总
curl -b cookies.txt "http://localhost:8081/custom/runningFlowsSummary"

# 返回示例：
# {
#   "totalExecutors": 3,
#   "totalRunningFlows": 5,
#   "queryTimestamp": 1705345200000,
#   "executors": [
#     {
#       "executorId": 1,
#       "host": "exec-01.company.com",
#       "runningFlowCount": 2,
#       "flows": [
#         {
#           "execId": 12345,
#           "projectName": "core_pipeline",
#           "flowId": "daily_report",
#           "submitUser": "wangxiaoming",
#           "runningDurationMinutes": 45,
#           "runningJobs": ["hive_aggregation", "report_build"]
#         }
#       ]
#     }
#   ]
# }

# 批量Kill所有运行中的Flow（慎用！）
curl -b cookies.txt -X POST \
  "http://localhost:8081/custom/runningFlowsSummary?action=killAll"
```

#### 步骤4：健康检查端点

```java
// 添加简单的健康检查端点
public class HealthCheckServlet extends HttpServlet {
    
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) 
            throws IOException {
        
        Map<String, Object> health = new HashMap<>();
        
        // 1. 检查数据库连接
        health.put("database", checkDatabase() ? "UP" : "DOWN");
        
        // 2. 检查Executor连接
        int activeExecutors = executorManager.getAllActiveExecutors().size();
        health.put("executors", String.format("UP (%d active)", activeExecutors));
        health.put("activeExecutorCount", activeExecutors);
        
        // 3. 检查调度器状态
        health.put("scheduler", quartzScheduler.isStarted() ? "UP" : "DOWN");
        
        // 4. 整体状态
        boolean allHealthy = health.values().stream()
            .allMatch(v -> v.toString().startsWith("UP"));
        health.put("status", allHealthy ? "HEALTHY" : "UNHEALTHY");
        
        resp.setStatus(allHealthy ? 200 : 503);
        writeJson(resp, health);
    }
    
    private boolean checkDatabase() {
        try (Connection conn = dataSource.getConnection()) {
            return conn.isValid(5);
        } catch (Exception e) {
            return false;
        }
    }
}

// 注册: context.addServlet(new ServletHolder(new HealthCheckServlet()), "/custom/health");
```

### 3.2 测试验证

```bash
# 测试健康检查端点
curl http://localhost:8081/custom/health
# {"database":"UP","executors":"UP (3 active)","scheduler":"UP","status":"HEALTHY"}

# 测试运行中Flow查询
curl -b cookies.txt http://localhost:8081/custom/runningFlowsSummary | python3 -m json.tool
```

## 4. 项目总结

自定义API端点是Azkaban高级扩展的核心技能。关键步骤：编写Servlet → 注册URL映射 → 添加认证过滤器 → 编译部署。

### 思考题

1. 如何为自定义端点添加"速率限制"（rate limiting），防止外部系统频繁调用？
2. 如果自定义端点需要访问数据库，如何利用Azkaban现有的DataSource连接池？
