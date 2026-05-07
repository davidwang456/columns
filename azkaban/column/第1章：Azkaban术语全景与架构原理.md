# 第1章：Azkaban术语全景与架构原理

## 1. 项目背景

### 业务场景

某电商公司的数据团队共有20余人，日常需要维护超过200个ETL任务。这些任务之间存在复杂的依赖关系——比如"用户行为日志采集"完成后，才能触发"行为数据清洗"，随后并行启动"用户画像构建"和"实时推荐计算"，最后汇总到"经营日报生成"。

团队在早期采用crontab + shell脚本的方式调度任务，运维同学手工维护每一个定时任务的启动时间。起初只有10来个任务时，这种方式勉强可以接受。但随着业务增长，任务数从10个涨到200个，crontab配置变成了一个巨大的文本文件，没人敢轻易修改——你永远不知道改了一个任务的启动时间会不会打乱后续所有任务的依赖关系。

### 痛点放大

没有调度系统的情况下，团队面临的问题包括：

1. **依赖黑洞**：任务B必须在任务A完成后才能运行，但crontab只能写固定的时间偏移。如果某天任务A跑了2小时（而不是平时30分钟），任务B就会在错误的时间启动，读到不完整的数据。

2. **失败不可见**：凌晨3点任务挂了没人知道，直到早上9点运营同学发现报表是空的。

3. **修复成本高**：出问题后，需要手动从中间某个任务重新跑，一路跑到底，这个过程可能持续数小时。

4. **协作混乱**：多人同时修改crontab文件，经常出现覆盖、遗漏等现象。

此时引入一个调度系统就成了刚需。Azkaban作为LinkedIn开源的轻量级Hadoop作业调度器，正好解决了这些问题。

## 2. 项目设计——剧本式交锋对话

**小胖**（手里拿着一杯奶茶，望着白板上的crontab配置发呆）：大师，咱们这crontab文件越来越像一坨意大利面了，新人根本不敢碰。我听说有个叫Azkaban的东西能帮咱们管理这些任务？

**大师**：对，Azkaban本质上就是一个"任务管家"。你可以把它想象成一个自动化生产线——原材料进来（源数据），经过多个加工站（Job），最终变成成品（报表、画像）。Azkaban负责调度每个加工站的启动顺序，并监控它们是否正常运转。

**小白**（若有所思）：那它和crontab有什么本质区别？都是定时触发，能有多大差别？

**大师**（在白板上画了一张DAG图）：关键区别在于——crontab只关心"什么时候启动"，而Azkaban关心"任务之间的因果关系"。你看，这是一个典型的数据流水线：

```
用户行为日志采集 (Job A)
    ↓
行为数据清洗 (Job B)
    ↓      ↓
画像构建  (Job C)   推荐计算 (Job D)
    ↓      ↓
经营日报生成 (Job E)
```

**小胖**：哦！这就是大学里学过的DAG图，有向无环图对吧？

**大师**：没错。Azkaban把这种依赖关系叫作Flow。一个Flow里包含多个Job，Job之间通过dependsOn定义依赖。只有父Job成功完成，子Job才会启动。而且C和D是并行执行的——这在crontab里你根本做不到。

**小白**：那如果C失败了怎么办？D会继续跑吗？

**大师**：好问题。这就是Azkaban的另一个核心能力——失败策略。你可以配置：①立即停止整个Flow ②仅跳过失败Job继续后续 ③等待手动处理后重跑失败的Job。默认情况下，C和D独立并行，C失败不影响D，但E（日报）因为同时依赖C和D，所以E会被阻塞，直到C被修复并重跑成功。

**小胖**：那Azkaban和别家的调度系统好像？比如DolphinScheduler、Airflow？

**大师**：这是选型时必须面对的问题。我用生活例子来对比——

- **Azkaban**像一家精品咖啡馆，专注于做好咖啡（Hadoop作业调度），配置简单，上手快，适合中小团队快速落地。
- **Airflow**像一家自助餐厅，啥都有，灵活但复杂，需要投入更多成本维护。
- **DolphinScheduler**像一家连锁餐厅，分布式能力更强，适合超大规模集群。

**小白**：那咱们现在才200个任务，20个人的团队，用Azkaban就够了对吧？

**大师**（点头）：对。Azkaban的核心理念是"简单即美"。它的设计哲学就是：用最少的组件、最直观的配置，解决最核心的调度问题。下面我们把它的架构图理清楚。

### 技术映射总结

- **Flow** = 生产流水线的工序图（有向无环的任务依赖关系）
- **Job** = 流水线上的一个加工站（最小执行单元）
- **Schedule** = 流水线的定时启动开关（基于Cron的定时触发）
- **Executor** = 实际干活的工人（执行引擎，负责运行Job）
- **Web Server** = 工单系统（用户界面，负责任务提交和状态追踪）

## 3. 项目实战

### 3.1 环境准备

| 依赖 | 版本 | 说明 |
|------|------|------|
| JDK | 1.8+ | Java运行环境 |
| MySQL | 5.7+ | 元数据存储（多Executor模式需要） |
| Gradle | 4.x+ | 构建工具 |
| Git | 2.x+ | 源码管理 |

本章使用Azkaban Solo Server模式，内置H2数据库，无需额外安装MySQL。

### 3.2 分步实现

#### 步骤1：下载并编译Azkaban

**目标**：获取Azkaban源码并完成编译。

```bash
# 克隆Azkaban源码（以3.90.0版本为例）
git clone https://github.com/azkaban/azkaban.git
cd azkaban

# 切换到稳定版本标签
git checkout 3.90.0

# 使用Gradle编译，跳过测试以加速
./gradlew build installDist -x test
```

**运行结果**：编译成功后，`azkaban-solo-server/build/install/azkaban-solo-server` 目录将包含可运行的完整包。

#### 步骤2：启动Solo Server

**目标**：启动单机版Azkaban服务。

```bash
cd azkaban-solo-server/build/install/azkaban-solo-server

# 启动服务（默认端口8081）
bin/azkaban-solo-start.sh

# 验证服务是否启动成功
curl -s http://localhost:8081 | head -5
```

**运行结果**：终端输出 `Azkaban Solo Server started.`，浏览器访问 `http://localhost:8081` 可看到登录页面。

**默认账号**：`azkaban / azkaban`

#### 步骤3：创建第一个Project

**目标**：通过Web界面创建项目。

1. 登录后点击右上角 `Create Project`
2. Project Name 填写 `hello_azkaban`
3. Description 填写 `My first Azkaban project`

**可能遇到的坑**：
- 如果8081端口被占用，修改 `conf/azkaban.properties` 中的 `jetty.port`
- Solo Server模式下的H2数据库文件在 `temp/` 目录下，不要手动删除

#### 步骤4：编写第一个Job文件

**目标**：创建一个简单的Shell Job。

创建文件 `hello.job`，内容如下：

```bash
# hello.job
type=command
command=echo "Hello Azkaban! Current time: $(date)"
command.1=echo "This is my first Azkaban job"
command.2=sleep 3
command.3=echo "Job completed successfully!"
```

**关键参数说明**：
- `type=command`：指定Job类型为命令执行
- `command`：多行命令用 `command.n` 编号，n从1开始递增

#### 步骤5：打包并上传

**目标**：将Job文件打包成zip上传到Azkaban。

```bash
# 打包为zip
zip hello_flow.zip hello.job

# 通过REST API上传并执行
curl -X POST "http://localhost:8081/manager" \
  -F "action=login" \
  -F "username=azkaban" \
  -F "password=azkaban" \
  -c cookies.txt

# 创建项目（如已通过Web创建则跳过）
curl -X POST "http://localhost:8081/manager?action=create" \
  -F "name=hello_azkaban" \
  -F "description=My first project" \
  -b cookies.txt

# 上传Flow文件
curl -X POST "http://localhost:8081/manager?project=hello_azkaban" \
  -F "ajax=upload" \
  -F "file=@hello_flow.zip" \
  -b cookies.txt
```

#### 步骤6：执行Flow并查看结果

在Web界面：
1. 进入 `hello_azkaban` 项目
2. 点击 `Execute Flow` 按钮
3. 确认参数后点击 `Execute`
4. 在 `Executions` 页面查看运行日志

**运行结果输出**：
```
[INFO] Starting job hello
[INFO] Hello Azkaban! Current time: 2025-01-15 10:30:00
[INFO] This is my first Azkaban job
[INFO] Job completed successfully!
[INFO] Job hello completed successfully
```

### 3.3 架构图

```
┌─────────────────────────────────────────────────────────┐
│                    Azkaban Solo/Web Server               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Web UI     │  │  Scheduler   │  │  Executor      │  │
│  │ (Jetty      │  │ (Quartz      │  │ (JobRunner     │  │
│  │  8081)      │  │  Cron解析)    │  │  线程池)       │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                │                   │           │
│         └────────────────┼───────────────────┘           │
│                          │                               │
│                   ┌──────┴──────┐                        │
│                   │  Database   │                        │
│                   │ (H2/MySQL)  │                        │
│                   └─────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

## 4. 项目总结

### 优点 & 缺点对比

| 维度 | Azkaban | Airflow | DolphinScheduler |
|------|---------|---------|------------------|
| 上手难度 | ★☆☆ 极低 | ★★★ 较高 | ★★☆ 中等 |
| 配置文件 | .job文本文件 | Python DAG | 可视化编排 |
| 依赖表达 | directly简单 | operator灵活 | 可视化连线 |
| 社区活跃度 | ★★☆ 中等 | ★★★ 活跃 | ★★☆ 中等 |
| 部署复杂度 | ★☆☆ 简单 | ★★★ 复杂 | ★★☆ 中等 |
| 分布式能力 | ★★☆ 支持 | ★★★ 完善 | ★★★ 优秀 |

### 适用场景

- **适用**：Hadoop/Spark批处理调度、ETL流水线、日报/月报生成、中小规模数据团队（100-2000个任务）
- **不适用**：实时流处理调度、需要复杂条件分支的工作流、超大规模集群（5000+任务）

### 注意事项

- Solo Server只适合开发/测试，生产环境必须使用多Executor模式
- 默认账号密码务必修改
- .job文件编码必须为UTF-8，否则中文命令会乱码
- 大Flow（100+ Job）建议拆分为多个子Flow，提高可维护性

### 常见踩坑经验

1. **端口冲突**：默认8081端口被占用，修改 `jetty.port` 后还需同步修改 `azkaban.properties` 中的 `azkaban.server.url`
2. **Java版本不匹配**：JDK 11+运行时可能出现类加载异常，建议使用JDK 8
3. **H2数据库文件损坏**：异常关机后H2数据库可能损坏，删除 `temp/h2/` 目录重启即可（数据会丢失）

### 思考题

1. Azkaban的Flow依赖关系和DAG图中的"拓扑排序"有什么关系？请推导Flow中Job的执行顺序算法。
2. 如果需要在Azkaban中实现"任务A完成后，根据A的返回值决定执行B还是C"，应该如何设计Flow结构？
