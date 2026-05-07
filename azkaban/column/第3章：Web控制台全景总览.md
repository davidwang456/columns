# 第3章：Web控制台全景总览

## 1. 项目背景

### 业务场景

数据开发小王接手了前任同事留下的Azkaban调度系统，打开Web界面一看，满屏的Projects、Flows穿梭，几十个定时调度跑着，红色失败标记时不时冒出来。他现在面临一个紧急任务：找出昨晚"日报生成"Flow为什么失败，并在上午10点之前手动补跑。

打开Web界面，他一时间不知道从哪里下手——界面上的按钮、标签、菜单太多了。他想查看历史执行记录，却点进了调度管理页面；想下载日志，却找到了配置修改入口。时间一分一秒过去，焦虑感拉满。

### 痛点放大

不熟悉Azkaban Web控制台时，常见问题包括：

1. **找不到入口**：Web界面上有导航栏、项目列表、菜单栏、顶部按钮，新手需要花15-30分钟才能摸清每个区域的功能。
2. **误操作风险**：在"调度管理"界面误点了"删除调度"，导致重要任务停止定时触发。
3. **日志定位低效**：不熟悉日志查看入口和层级关系，找到失败Job的日志需要点击5-6次页面跳转。
4. **状态误判**：看到"Running"状态以为一切正常，其实Job卡住了，需要更精细的状态识别。

## 2. 项目设计——剧本式交锋对话

**小胖**（焦急地滑动鼠标滚轮）：大师，这个Azkaban Web界面怎么跟个迷宫似的？我想找一个昨晚失败的Job日志，点了半天都找不到。左边菜单、顶上导航，中间还有卡片……到底先看哪？

**大师**（走过去，接过鼠标）：别急，我教你一个口诀——"左导航、中执行、右参数"。来，我们先理解界面的整体结构。

Azkaban的Web界面本质上是围绕"三个核心实体"设计的——Project（项目）、Flow（工作流）、Execution（执行实例）。就像你点外卖：Project是"常点的餐馆"集合，Flow是"今天下的一单"，Execution是"这次订单的配送状态"。

**小白**（若有所思）：那界面上的各区域怎么对应这三个实体？

**大师**（在白板上画了一个简图）：你看——

```
┌──────────────────────────────────────────────┐
│  顶部导航栏：Projects | Scheduling | Executing │   ← 主要功能入口
├────────────┬─────────────────────────────────┤
│  左侧菜单   │  主内容区                        │
│            │  ┌───────────────────────────┐  │
│  • All     │  │  Project 列表（卡片形式）    │  │
│    Projects│  │  每个卡片：名称/描述/时间    │  │
│  • Flows   │  └───────────────────────────┘  │
│  •         │  选中一个Project后：              │
│    Schedule│  ┌───────────────────────────┐  │
│  • History │  │  Flow列表 | Executions历史  │  │
│  • Logs    │  │  详细信息面板              │  │
│            │  └───────────────────────────┘  │
└────────────┴─────────────────────────────────┘
```

**小胖**：哦！所以三个顶级导航分别对应——Projects是管理所有项目，Scheduling是看有哪些定时调度，Executing是看当前在跑的任务？

**大师**：没错。但要注意，"Scheduling"标签并不等于"执行记录"，它展示的是"调度计划"——什么时候会触发；想看已经执行过的记录，要在Project详情页的"Executions"列表里查。

**小白**：我有一个问题：当我在Project列表看到一个项目，点击进去之后，怎么快速判断哪些Flow运行成功了、哪些失败了呢？

**大师**：好问题。进入Project后，你会看到一个Flow列表。每个Flow旁边有一个状态标记，颜色就能告诉你一切：

- **绿色** ✅ → 最后一次运行成功
- **红色** ❌ → 最后一次运行失败
- **蓝色** 🔵 → 正在运行中
- **黄色** ⚠️ → 被手动杀死

这就像交通信号灯，一目了然。想看详细日志，就点红色标记旁边的"Log"链接。

**小胖**：那我在Flow详情界面，怎么知道是哪个具体的Job失败了？

**大师**：在Flow执行详情页，你会看到一个DAG图。失败的Job节点会变成**红色**，成功的变成**绿色**，运行的变成**蓝色**。点击红色节点，就能看到该Job的具体日志、开始时间、结束时间、耗时。这就是我们常说的"故障定位的黄金链路"。

**小白**：那个顶部的搜索框是干什么用的？我试过搜Flow名字，好像搜不到。

**大师**：那个搜索框只搜索**Project名称**，不搜Flow和Job。这是很多人踩过的坑。要搜索某个Flow的执行记录，你需要先进入对应的Project，然后在Project内部的"Executions"列表中用浏览器的Ctrl+F来搜。

### 技术映射总结

- **Projects标签** = 公司项目总览看板（全部项目的入口）
- **Scheduling标签** = 排期日历（所有定时调度计划一览）
- **Executing标签** = 车间看板（当前正在运行的任务实时状态）
- **DAG图颜色** = 交通信号灯（绿=正常，红=故障，蓝=执行中）
- **Log链接** = 监控探头（直接跳转到具体任务的详细日志）

## 3. 项目实战

### 3.1 环境准备

确保第2章部署的Azkaban Solo Server正常运行中，用默认账号`azkaban/azkaban`登录。

### 3.2 分步实现

#### 步骤1：认识登录后的首页布局

**目标**：掌握Azkaban首页的四大功能区域。

```
首页四大区域：
┌────────────────────────────────────────────────┐
│ ① 顶部导航栏                                     │
│ < Projects | < Scheduling | < Executing | ...   │
│ ② 用户信息区（右上角）                             │
│   admin ▼  (下拉：用户设置/退出)                   │
│ ③ 左侧动作菜单                                    │
│   Create Project 按钮                             │
│   Upload Project 按钮                             │
│ ④ 主内容区——Project列表                           │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│   │ Project1 │ │ Project2 │ │ Project3 │ ...   │
│   │ 描述...   │ │ 描述...   │ │ 描述...   │       │
│   └──────────┘ └──────────┘ └──────────┘       │
└────────────────────────────────────────────────┘
```

登录后直接在浏览器中操作，熟悉每个区域的鼠标悬停提示（Tooltip）。

#### 步骤2：创建和管理Project

**目标**：完成Project的创建、查看、修改、删除全流程。

```bash
# 通过REST API创建项目，方便批量操作
# 登录
curl -c cookies.txt \
  -X POST "http://localhost:8081" \
  --data "action=login&username=azkaban&password=azkaban"

# 创建项目
curl -b cookies.txt \
  -X POST "http://localhost:8081/manager?action=create" \
  --data "name=demo_etl&description=ETL流水线演示项目"

# 查看项目列表
curl -b cookies.txt \
  "http://localhost:8081/index?ajax=fetchuserprojects"
```

**Web界面操作**：
1. 点击 `Create Project` 按钮
2. 填写 Name：`demo_etl`，Description：`ETL流水线演示项目`
3. 提交后刷新首页，即可看到新建的Project卡片

#### 步骤3：理解Project内部各Tab页功能

**目标**：掌握Project详情页中5个Tab的作用。

进入Project后，顶部有5个Tab：

| Tab | 中文含义 | 核心功能 | 使用频率 |
|-----|---------|---------|---------|
| Flows | 工作流列表 | 查看/上传/删除Flow | ★★★★★ |
| Permissions | 权限管理 | 添加/移除用户权限 | ★★★☆☆ |
| Project Logs | 项目日志 | 查看项目级操作日志 | ★★☆☆☆ |
| Schedules | 调度管理 | 创建/修改/暂停/删除定时调度 | ★★★★☆ |
| Upload | 上传文件 | 上传新的.zip包替代Flow | ★★★☆☆ |

```bash
# API方式查看Project的Flows列表
curl -b cookies.txt \
  "http://localhost:8081/manager?ajax=fetchprojectflows&project=demo_etl"
```

#### 步骤4：上传Flow并查看执行记录

**目标**：上传一个Flow并观察执行页面。

创建 `demo_basic.job`：

```bash
# demo_basic.job
type=command
command=echo "Step1: Data extraction starting..."
command.1=sleep 2
command.2=echo "Step2: Data processing..."
command.3=sleep 3
command.4=echo "Step3: Report generation..."
command.5=echo "All steps completed!"
```

```bash
# 打包上传
zip demo_flow.zip demo_basic.job

curl -b cookies.txt \
  -X POST "http://localhost:8081/manager?project=demo_etl&ajax=upload" \
  -F "file=@demo_flow.zip"
```

**Web界面验证**：
1. 进入 `demo_etl` 项目
2. 点击 `Flows` Tab，可以看到 `demo_basic` Flow
3. 点击 `Execute Flow`，进入执行确认页面
4. 点击 `Execute` 开始运行

#### 步骤5：学习执行详情页的信息解读

**目标**：掌握执行详情页中每个区块的信息含义。

执行详情页分为5个区块：

```
┌─────────────────────────────────────────────────┐
│ 执行摘要栏                                        │
│ Execution Id: 12 | Status: RUNNING | Submit: xxx  │
│ Start: xxx | End: n/a | Duration: 00:03:15       │
├──────────────────────┬──────────────────────────┤
│ ② DAG可视化图         │ ③ 右侧Job列表             │
│    ┌───────┐         │ Job1 • SUCCEEDED         │
│    │ Job-1 │(绿色)    │ Job2 • RUNNING           │
│    └───┬───┘         │ Job3 • READY             │
│        │             │                          │
│    ┌───┴───┐         │                          │
│    │ Job-2 │(蓝色)    │                          │
│    └───────┘         │                          │
├──────────────────────┴──────────────────────────┤
│ ④ 底部状态栏                                     │
│ Job List | Flow Parameters | Execution Options    │
├─────────────────────────────────────────────────┤
│ ⑤ 页脚——操作按钮                                  │
│ Cancel | Pause | Resume | Download Logs          │
└─────────────────────────────────────────────────┘
```

**关键操作**：
- 点击DAG图中的Job节点可查看该Job的详细日志
- 右侧Job列表的彩色圆点表示状态
- 底部"Job List"显示所有Job的表格形式列表

#### 步骤6：日志查看与下载

**目标**：掌握日志的在线查看和下载方式。

```bash
# API方式获取某个Job的日志
# execution_id从执行页面URL中获取
curl -b cookies.txt \
  "http://localhost:8081/executor?execid=<execution_id>&jobId=<job_name>&ajax=fetchExecJobLogs&offset=0&length=1000"
```

**Web界面**：
1. 进入执行详情页
2. 点击某个Job节点（绿色/红色/蓝色圆圈）
3. 弹出窗口显示该Job的stdout/stderr日志
4. 窗口底部有"Download Log"链接，可下载完整日志文件

### 3.3 测试验证

创建一个模拟成功/失败的测试用例来熟悉界面：

```bash
# success_job.job
type=command
command=echo "This job will succeed"
command.1=sleep 1
```

```bash
# fail_job.job
type=command
command=echo "This job will fail"
command.1=exit 1
```

上传这两个Job分别执行，观察：
- 成功Job在DAG图中显示为**绿色**
- 失败Job在DAG图中显示为**红色**
- 失败Job的详情页会自动显示错误日志

## 4. 项目总结

### Azkaban Web界面能力矩阵

| 功能模块 | 核心能力 | 适用角色 |
|---------|---------|---------|
| Projects | 项目CRUD、Flow管理 | 数据开发 |
| Scheduling | 定时调度配置、暂停/恢复 | 数据开发/运维 |
| Executing | 实时运行监控、DAG可视化 | 运维/开发 |
| Permissions | 权限分配、多租户隔离 | 管理员 |
| History | 历史执行记录查询、日志下载 | 开发/运维 |

### 适用场景

- **适用**：日常任务监控、故障快速定位、执行历史回溯、权限配置、团队协作
- **不适用**：批量操作100+个Project（需用API）、自动化运维（需API脚本）、复杂的数据分析报表

### 注意事项

- 顶部搜索框只搜Project名称，不搜Flow/Job
- Execution Id从1开始自增，生产环境会很大，不要手动关联
- 日志长时间不清理会撑爆磁盘，配置`azkaban.log.retention.days`控制保留天数
- 多人共用admin账号时，无法追溯谁做了什么操作

### 常见踩坑经验

1. **界面空白/加载不出**：原因通常是浏览器缓存了旧版JS/CSS文件。按`Ctrl+Shift+R`强制刷新即可解决。
2. **DAG图不显示**：可能是因为浏览器禁用了Canvas或WebGL。尝试换Chrome/Edge浏览器，或检查浏览器扩展（如广告拦截器）是否误屏蔽。
3. **执行History加载超时**：当Execution数量超过10万条时，默认的分页查询会很慢。需要在MySQL侧对executions表加复合索引。

### 思考题

1. 如果某个Project下有500条Execution记录，Web界面默认每页显示20条，如何快速定位到Execution Id=888的记录？请给出至少两种方法。
2. 在Flow执行详情页，当某个Job失败后，你需要通知下一班的值班同事处理这个故障。如何高效地"传递信息"？除了截图之外，还有什么更好的方式？
