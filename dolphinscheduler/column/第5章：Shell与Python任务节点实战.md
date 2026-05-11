# 第5章：Shell与Python任务节点实战

## 1. 项目背景

某电商公司的数据团队每天早晨都需要处理前一日的销售数据，生成日报供管理层审阅。该公司的日均订单量在十万级别，销售数据分散在十几个业务系统中，最终由上游ETL合并为统一的CSV文件存放在SFTP服务器上。整个数据流水线包含四个核心环节：(1) Shell脚本从SFTP服务器拉取原始CSV销售文件，文件体积通常在2GB左右，包含订单号、商品名称、成交金额、下单时间等核心字段；(2) Shell脚本对拉取的原始数据进行清洗——由于上游系统可能存在重复推送的情况，需要按订单号去重，同时统一日期格式、剔除金额为负数的异常记录；(3) Python脚本读取清洗后的数据，利用pandas按产品维度进行销售聚合分析，计算各品类的销售额、销量、客单价及同比变化率；(4) 将汇总指标和关键结论输出，通过自定义参数传递给下游的日报生成与邮件推送工作流，最终自动发送到管理层邮箱。

然而，这套流程目前全部依赖人工操作。数据分析师每天到公司后，先SSH登录到服务器，手动执行sftp命令拉取文件，再逐条运行清洗和分析脚本。痛点显而易见：如果上游系统延迟，销售数据迟迟未到，分析师就只能反复刷新等待，有时甚至要等到半夜；任何一个环节出错——比如清洗脚本忘了更新字段名、CSV分隔符从逗号变成了制表符、Python脚本缺少pandas依赖——都可能导致整个链路中断。更麻烦的是，出错的节点修好后，还需要记住哪些下游步骤尚未执行，逐个手动重跑，费时费力不说，还容易漏跑步骤，导致报表数据不完整。

公司新接入了Apache DolphinScheduler，计划将这套流程迁移到平台上自动调度执行。Shell任务和Python任务是DolphinScheduler中最常用的两类任务节点，它们各有擅长的领域，掌握二者的选择标准、参数传递机制和异常处理方式是构建健壮数据流水线的第一步。本章将以销售日报的完整流程为案例，带你从零搭建一条包含Shell与Python混合任务的DAG工作流，亲身体验从手动运维到自动化调度的蜕变。

## 2. 项目设计——剧本式交锋对话

会议室里，小胖、小白和大师三人围坐在白板前，开始讨论销售日报工作流的技术方案。

**小胖**（撸起袖子，信心满满）：“这有啥好纠结的，数据拉取和清洗肯定用Shell啊！sed/awk一把梭，文本处理快得很。分析报表用Python，pandas几行代码就能分组汇总，再画个柱状图，完美！再说了，咱们现有的脚本都是现成的，直接粘进去不就完了？”

**小白**（皱着眉头）：“你先别急，我有几个问题。第一，Shell任务清洗完的数据，文件路径怎么传给下游的Python任务？总不能靠猜路径或者写死文件名吧？第二，Shell脚本万一执行失败了——比如SFTP连接超时或者文件不存在——退出码不是0，DolphinScheduler能感知到失败吗？如果感知不到，下游的清洗和Python分析就会拿到空文件或者脏数据继续跑，这比不跑还可怕。第三，Python脚本依赖pandas、matplotlib这些第三方库，如果Worker机器上压根没装，任务是不是直接挂了？咱们十几台Worker，总不能一台一台登上去装吧？”

**小胖**（被问住了，挠头）：“呃……参数传递可以用环境变量？失败的话，Shell脚本自己做个判断exit 1？至于依赖嘛，手动每台机器pip install一遍，或者写个ansible批量装？”

**大师**（笑着在笔记本电脑上敲了几个关键词）：“小胖的方案大方向没问题，Shell做数据拉取和清洗，Python做分析，分工合理——'专业的事交给专业的工具'这条原则你把握得很好。小白的几个问题问到了关键点上，说明你在认真思考生产环境的可靠性。我来逐一解释。” 

大师走到白板前，画了一张简图：

```
[Shell: sftp_pull] → [Shell: data_clean] → [Python: sales_analysis]
      ↓ OUT                 ↓ OUT                ↓ OUT
   DATA_FILE            CLEAN_FILE          TOTAL_REVENUE
```

“DolphinScheduler的Shell任务和Python任务底层原理是一样的——Worker节点通过fork系统调用创建子进程来执行脚本。Shell任务会调用`/bin/bash -c`来启动，Python任务则根据`dolphinscheduler_env.sh`中配置的`PYTHON_HOME`，找到该目录下的`bin/python`来执行。Worker会通过管道实时采集子进程的标准输出和标准错误，推送到平台的日志界面，你们在UI上就能看到实时日志。”

“参数传递方面，自定义参数支持IN/OUT模式。上游任务通过`${setVar=变量名=变量值}`语法将结果写入日志，DolphinScheduler会自动解析并存储为OUT参数。下游任务设置一个同名参数的IN类型，平台会自动把上游的值注入进去——不是环境变量，而是通过命令行参数或写入临时脚本的方式传递。”

“退出码处理更简单：子进程退出码为0表示成功，非0表示失败。DolphinScheduler会自动检查退出码，非0就会将任务标记为失败，并且默认不会触发依赖该任务的下游节点。你可以通过配置'失败继续'策略来改变这个行为，但生产环境建议保持默认——上游失败，下游就该停下来，避免用脏数据跑出错误的报表。”

“至于Python依赖，建议在Worker节点上统一管理Python虚拟环境，确保`PYTHON_HOME`指向正确的解释器路径。如果是跨机器调度，可以提前在Worker上执行`pip install -r requirements.txt`，或者把依赖打包上传到资源中心，让Python脚本在运行时通过`sys.path`动态加载。”

**小胖**（猛拍大腿）：“原来如此！而且Shell脚本还能用`resource://`前缀引用资源中心的文件吧？”

**大师**点点头：“没错。比如你有一个通用的日志函数脚本`logger.sh`上传到资源中心，在Shell任务里直接`source resource://logger.sh`就能引用，不用每台机器上拷贝一份。”

**小白**合上笔记本：“明白了，那咱们开始干活吧！”

## 3. 项目实战

### 步骤1：环境准备

首先确认Worker节点的运行环境。生产环境的Worker通常有多台，建议在任意一台Worker上先做验证，确认无误后再批量同步配置。

```bash
# SSH登录到任意一台Worker节点
# 检查Python3是否可用
python3 --version

# 检查pandas是否安装
python3 -c "import pandas; print(pandas.__version__)"

# 如果未安装，执行以下命令安装
pip3 install pandas -i https://pypi.tuna.tsinghua.edu.cn/simple

# 建议一并安装常用的数据处理库
pip3 install numpy matplotlib openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple
```

接着检查`dolphinscheduler_env.sh`中`PYTHON_HOME`的配置。该文件位于DolphinScheduler安装目录的`conf/env/`之下，Worker启动时会source这个文件加载环境变量：

```bash
# 查看文件: ${DOLPHINSCHEDULER_HOME}/conf/env/dolphinscheduler_env.sh
cat ${DOLPHINSCHEDULER_HOME}/conf/env/dolphinscheduler_env.sh | grep PYTHON_HOME

# 典型配置示例
export PYTHON_HOME=/usr/local/python3
```

确保`PYTHON_HOME`路径下存在`bin/python`可执行文件。如果使用conda环境，则`PYTHON_HOME`应指向conda环境的根目录（如`/opt/conda/envs/ds_env`）。修改`dolphinscheduler_env.sh`后需要重启Worker服务使其生效。

如果有通用的工具脚本（如日志记录函数、数据库连接检测脚本等），可以先上传到资源中心。进入DolphinScheduler Web UI → "资源中心" → "上传文件"，将脚本上传后，在Shell任务中通过`resource://`前缀即可引用。

### 步骤2：创建Shell任务"SftpPull"

在DolphinScheduler中新建工作流"销售日报工作流"，拖入一个Shell任务节点，命名为"SftpPull"。

任务脚本内容如下：

```bash
#!/bin/bash
echo "===== Starting data pull from SFTP server ====="

# 生产环境中，使用expect或sshpass工具配合sftp命令拉取数据
# sftp -o StrictHostKeyChecking=no user@sftp-server:/data/sales/$(date +%Y%m%d).csv /tmp/sales_data/
# 本章使用模拟数据演示流程

mkdir -p /tmp/sales_data

cat > /tmp/sales_data/raw_${system.biz.date}.csv << EOF
order_id,product,amount,order_time
001,手机,2999,2023-08-25 10:00:00
002,电脑,5999,2023-08-25 11:30:00
003,手机,2999,2023-08-25 10:00:00
004,耳机,599,2023-08-25 14:20:00
005,电脑,5999,2023-08-25 15:00:00
006,手机,2999,2023-08-25 16:45:00
EOF

# 关键：通过setVar语法将文件路径作为OUT参数传递给下游
DATA_FILE="/tmp/sales_data/raw_${system.biz.date}.csv"
echo "DATA_FILE=${DATA_FILE}"
echo "===== Data pull completed ====="
```

> **注意**：这里使用了内置系统参数`${system.biz.date}`，它会被自动替换为前一天的日期（yyyyMMdd格式），例如20230825。系统还提供了`${system.biz.curdate}`（当天日期）和`${system.datetime}`（当前日期时间，yyyMMddHHmmss格式）。

保存后，在任务的"自定义参数"区域添加一个OUT参数：

| 参数名 | 参数值 | 方向 |
|--------|--------|------|
| DATA_FILE |   | OUT   |

参数值留空，DolphinScheduler会在日志中匹配`DATA_FILE=/tmp/sales_data/raw_xxx.csv`这行输出，自动提取等号右侧的内容作为参数值。

### 步骤3：创建Shell任务"DataClean"

拖入第二个Shell任务节点，命名为"DataClean"。在自定义参数中配置：

| 参数名   | 参数值 | 方向 |
|----------|--------|------|
| DATA_FILE |        | IN   |
| CLEAN_FILE |       | OUT  |

DATA_FILE声明为IN类型，DolphinScheduler会自动从上游任务"DataPull"的OUT参数中获取值并注入。任务脚本如下：

```bash
#!/bin/bash
echo "===== Starting data cleaning ====="
echo "Input file from upstream: ${DATA_FILE}"

# 检查上游传入的文件是否存在
if [ ! -f "${DATA_FILE}" ]; then
    echo "ERROR: Input file ${DATA_FILE} not found!"
    exit 1
fi

# 按order_id去重，保留首次出现的记录
CLEAN_FILE="/tmp/sales_data/clean_${system.biz.date}.csv"
awk -F',' 'NR==1 {print; next} !seen[$1]++' "${DATA_FILE}" > "${CLEAN_FILE}"

DUP_COUNT=$(wc -l < "${DATA_FILE}")
CLEAN_COUNT=$(wc -l < "${CLEAN_FILE}")
echo "Original rows: ${DUP_COUNT}, After dedup: ${CLEAN_COUNT}"

# 通过setVar语法将清洗后的文件路径传递给下游Python任务
echo "CLEAN_FILE=${CLEAN_FILE}"
echo "===== Data cleaning completed ====="
exit 0
```

> **关键点**：脚本中使用`exit 1`明确标记失败。DolphinScheduler检测到非0退出码后，会将该节点标记为失败，并默认阻断下游任务的执行。同时，`${CLEAN_FILE}`参数通过echo输出到日志，平台自动捕获为OUT参数。注意参数名必须与配置中的参数名严格一致（CLEAN_FILE），否则无法匹配。

### 步骤4：创建Python任务"SalesAnalysis"

拖入一个Python任务节点，命名为"SalesAnalysis"。自定义参数配置：

| 参数名     | 参数值 | 方向 |
|------------|--------|------|
| CLEAN_FILE |        | IN   |

Python脚本内容：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pandas as pd
import os
import sys

# 从上游Shell任务传递的CLEAN_FILE参数会作为命令行参数注入
clean_file = os.environ.get('CLEAN_FILE', '/tmp/sales_data/clean.csv')

print(f"Reading cleaned data from: {clean_file}")

if not os.path.exists(clean_file):
    print(f"ERROR: File {clean_file} not found!")
    sys.exit(1)

df = pd.read_csv(clean_file)
print(f"Loaded {len(df)} records successfully.")

# 按产品维度进行销售聚合分析
summary = df.groupby('product').agg(
    订单数=('amount', 'count'),
    销售额=('amount', 'sum')
).round(2)

print("=" * 50)
print("          销售分析报表")
print("=" * 50)
print(summary.to_string())
print("=" * 50)

total_revenue = df['amount'].sum()
print(f"总收入: {total_revenue}")

# 将汇总指标作为OUT参数传递给下一个工作流
if len(sys.argv) > 1:
    print(f"TOTAL_REVENUE={total_revenue}")

sys.exit(0)
```

> **依赖说明**：Python任务依赖pandas库。Worker节点上必须在`PYTHON_HOME`对应的Python环境中安装pandas，否则任务会抛出`ModuleNotFoundError`。建议在Worker上线前统一执行`pip install pandas`，或使用requirements.txt批量安装依赖。

### 步骤5：连接DAG依赖

在工作流编辑器中，用连线依次连接三个任务：

```
[SftpPull] ──→ [DataClean] ──→ [SalesAnalysis]
```

右键连线可以设置依赖条件。默认是"成功"（上游成功后下游才执行），保持默认即可。

### 步骤6：配置工作流参数并运行

点击工作流的"保存"按钮，然后点击"上线"。发布后，在顶部工具栏设置工作流级别的参数（可选）：

| 参数名        | 参数值                               | 说明         |
|---------------|--------------------------------------|--------------|
| bizDate       | ${system.biz.date}                   | 业务日期     |

最后点击"运行"，选择"串行执行"策略，确认启动。

### 步骤7：查看执行日志

工作流开始执行后，可以在"工作流实例"页面实时查看各个任务的执行状态：

- **蓝色**：任务排队中
- **黄色**：任务执行中
- **绿色**：执行成功
- **红色**：执行失败

点击某个任务节点，再切换到"日志"标签页，即可看到实时滚动输出的日志内容。Worker会将子进程的标准输出和标准错误全部采集回来，方便调试。

### 步骤8：常见报错与排查

| 异常现象                 | 可能原因                                | 解决方法                                                       |
|--------------------------|-----------------------------------------|----------------------------------------------------------------|
| Python任务报`ModuleNotFoundError: No module named 'pandas'` | Worker节点的Python环境中未安装pandas    | SSH到Worker节点执行`pip3 install pandas`                       |
| 任务一直排队不执行       | PYTHON_HOME配置错误，Worker找不到Python | 检查`dolphinscheduler_env.sh`中PYTHON_HOME路径是否正确         |
| Shell任务报`permission denied` | 脚本尝试写入的目录没有写权限             | 确保Worker运行用户对`/tmp/sales_data/`有读写权限               |
| 下游任务参数值为空       | 自定义参数名大小写或拼写与上游OUT不一致  | 严格检查参数名的大小写和拼写，例如`CLEAN_FILE`与`clean_file`会被视为不同参数 |

## 4. 项目总结

### Shell任务 vs Python任务 对比

| 维度         | Shell任务                                                    | Python任务                                                   |
|--------------|--------------------------------------------------------------|--------------------------------------------------------------|
| 执行方式     | Worker通过`/bin/bash -c`创建子进程                           | Worker通过`PYTHON_HOME/bin/python`创建子进程                   |
| 适用场景     | 文件搬运、系统命令调用、文本处理（sed/awk/grep）、数据拉取   | 数据聚合分析、复杂统计计算、API调用、机器学习推理、可视化输出  |
| 核心优势     | 启动快、零依赖、与Linux系统命令和管道无缝衔接                 | 生态丰富（pandas/numpy/scikit-learn/matplotlib等），可读性高   |
| 退出码判定   | 脚本`exit 0`为成功，非0为失败，未显式调用则继承最后一条命令  | `sys.exit(0)`为成功，非0或未捕获异常均为失败                   |
| 环境依赖     | 依赖Shell解释器（Linux/macOS默认支持）                        | 依赖Python解释器及第三方库，需管理员在每个Worker节点上预装     |
| 参数传递     | 支持自定义参数IN/OUT，通过echo输出`变量名=值`即可             | 同样支持自定义参数，取值可通过`os.environ`或命令行参数获取     |
| 调试难度     | 脚本较长时逻辑不直观，容易写出可维护性差的"面条代码"         | 结构化编程，异常堆栈清晰，IDE调试方便                         |
| 资源引用     | 支持`resource://`前缀引用资源中心文件，可直接source           | 暂不支持`resource://`语法，需将资源文件下载到本地后导入        |

### 适用场景总结

- **优先选择Shell任务**：当你的操作主要是文件搬运（sftp/scp）、格式转换（CSV→JSON）、去重清洗（awk/sort/uniq）、调用系统命令（hdfs、aws cli、kubectl）时，Shell天然契合这些场景。它的启动开销极小，无需任何第三方依赖，一条管道就能串联多个命令。但要注意，当Shell脚本超过100行时应考虑拆分为多个任务节点，避免单个节点过于臃肿。
- **优先选择Python任务**：当你的操作需要复杂的数据聚合（多维度分组、透视表）、统计建模（回归分析、时间序列预测）、可视化输出（matplotlib图表）、调用第三方API或SDK（如钉钉、企业微信机器人）时，Python的生态优势无可替代。其代码可读性和可维护性也远优于同等复杂度的Shell脚本。
- **两者混合使用**：推荐将Shell用于数据接入和预处理（IO密集型），Python用于核心计算和分析（计算密集型），通过自定义参数的IN/OUT机制串联——这正是本章案例展示的理想模式。这种组合能在性能和可维护性之间取得最佳平衡。如果未来需要引入Spark等重型计算框架，可以平滑地将Python节点替换为对应的Spark任务节点。

### 常见陷阱与最佳实践

1. **退出码约定**：始终在脚本末尾显式调用`exit 0`或`sys.exit(0)`。如果脚本逻辑中有错误分支，务必`exit 1`，不要让它"自然结束"——Shell脚本中，如果最后一条执行的命令是`echo`，退出码就是0，DolphinScheduler会判定任务成功，即便核心逻辑已经悄然失败。一个常见陷阱是：脚本中用`grep`查找某关键字，如果没找到（返回码1），但又没有使用`set -e`，脚本会继续执行到最后并返回0，造成"假成功"。
2. **Python环境隔离**：生产环境强烈建议为不同项目创建独立的Python虚拟环境（venv或conda），在`dolphinscheduler_env.sh`中为不同的Worker组指定各自的`PYTHON_HOME`，避免不同工作流的依赖版本冲突（例如项目A需要pandas 1.x，项目B需要pandas 2.x）。
3. **资源文件版本管理**：通过`resource://`引用资源中心的脚本时，建议在文件名中加入版本号（如`logger_v2.sh`），避免更新脚本后所有引用的旧工作流行为意外改变。DolphinScheduler资源中心不保留历史版本，修改即覆盖，因此版本号是唯一可靠的追溯手段。
4. **自定义参数命名**：建议采用全大写+下划线命名法（如`INPUT_FILE_PATH`、`OUTPUT_REPORT_DIR`），并在团队内部形成书面约定。特别注意——参数名是严格大小写敏感的，`CLEAN_FILE`和`clean_file`是两个完全不同的参数，混用会导致传递失败且不报错，下游拿到的将是空值。
5. **超时设置**：Shell任务和Python任务都有默认超时时间，建议根据实际脚本的运行时长合理设置。如果从SFTP拉取一个2GB的文件，30秒的超时显然不够，应设置为300秒或更长。超时后DolphinScheduler会强制kill子进程，任务标记为失败。
6. **Worker资源控制**：Python任务中的pandas在读取大文件时内存开销显著，建议在Worker节点的配置中限制每个任务的CPU和内存使用上限，防止单个任务的OOM影响同机器的其他任务。

### 思考题

1. 如果上游Shell任务通过`echo "RESULT_PATH=/data/output"`设置了OUT参数，但下游Python任务在自定义参数中将IN参数名写成了`result_path`（小写），下游任务运行时这个参数的值是什么？为什么？这个设计有什么优缺点？
2. 假设DataClean任务执行到一半，Worker机器突然重启了。DolphinScheduler会如何处理下游的SalesAnalysis任务？如果DataClean在重启后被重跑成功了，SalesAnalysis会自动触发吗？如果DataClean已经成功但SalesAnalysis执行到一半Worker也挂了，恢复后又会怎样？请结合DolphinScheduler的任务容错和失败重试机制展开思考。
3. 在实际生产环境中，如果SFTP服务器每天生成的文件名并非固定格式（例如有时是`sales_20230825.csv`，有时是`sales_report_2023-08-25.csv`），本章的Shell脚本应该如何修改才能稳定拉取到正确的文件？请写出修改思路和关键代码片段。
