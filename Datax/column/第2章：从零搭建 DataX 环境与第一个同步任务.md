# 第2章：从零搭建 DataX 环境与第一个同步任务

## 1. 项目背景

新人小李今天入职数据平台组，TL 给他的第一个任务很简单：把测试环境 MySQL 中的 100 万条用户数据同步到另一台 MySQL 实例。小李之前用过 sqoop，但 sqoop 需要 Hadoop 环境，测试环境没装。TL 说："用 DataX 吧，五分钟就能跑起来。"

小李下载了 DataX 的 tar 包，解压后面对一堆 JAR 文件和 Python 脚本，一时间不知道从何下手。他尝试直接运行 `java -jar datax.jar`，报错；尝试 `python datax.py`，又提示找不到 JAVA_HOME。同事告诉他：DataX 不提供二进制发行版了，需要自己从源码编译。小李打开 pom.xml，看到 50+ 个子模块，差点退坑。

这个场景在很多团队中出现过——DataX 的安装虽然简单（JDK + Maven + 源码），但对于不熟悉 Java 生态的同学来说，仍然有门槛。本章从零开始，分 Windows、Mac、Linux 三种平台，手把手带你完成 DataX 的源码编译、环境配置，并用 StreamReader/StreamWriter 运行第一个任务，让你对 DataX 的工作流建立直观体感。

## 2. 项目设计——剧本式交锋对话

**（小李工位，小胖路过，看到小李皱着眉头看屏幕）**

**小胖**：（凑过来）哎新人，卡住了？是不是在搭环境？

**小李**：是啊，我看官方文档说解压就能用，但我下载源码后完全不知道下一步该干嘛。

**小胖**：（嘿嘿一笑）那是老版本，新版都要自己编译。不过其实很简单，核心就三步：装 JDK → 装 Maven → 跑一条命令。就跟煮方便面一样——烧水、放面、放调料，三步搞定。

**小李**：可是我 Windows 上装 JDK 好麻烦，还要配一堆环境变量。

**小白**：（头也不抬，盯着屏幕）你可以用 scoop 或 choco 装，一行命令。Linux 更简单，apt install 就行。关键是 JDK 版本——必须用 JDK 8，别问我怎么知道的，我用 JDK 11 编译报了一下午的错。

**大师**：（端着咖啡走过来）环境搭建确实会劝退不少人。我建议先搞清楚 DataX 的启动链路——你运行的 `python datax.py` 本质上做了什么？它其实是一个 Shell 包装器，做了三件事：
1. 找到 JAVA_HOME，拼出 java 可执行文件路径
2. 构建 classpath，把 datax.jar 和 plugins/ 目录加进去
3. 组装 JVM 参数，然后 `exec java -Xms1g -Xmx1g -cp ... com.alibaba.datax.core.Engine -job xxx.json`

如果你理解了这些，就不会被"找不到 Java"这类问题困住了。

**技术映射**：datax.py = 智能遥控器。它帮你找到电视机（JVM），调到正确的频道（classpath），然后按下开机键（Engine.main）。

**小胖**：我有个疑惑——为啥非要用 JDK 8？JDK 17 都出来好几年了。

**大师**：（叹气）历史债务。DataX 最早是 2015 年阿里开源的，当时 Java 8 是主流。虽然 JVM 向后兼容，但部分插件依赖的第三方库（比如旧版 Groovy、部分 Hadoop 组件）在高版本 JDK 上有兼容问题。我们生产环境试过 JDK 11，hdfsreader 插件的 Kerberos 认证就挂了。

**小白**：那编译命令 `mvn -U clean package assembly:assembly -Dmaven.test.skip=true` 里的参数分别是什么意思？

**大师**：好问题。
- `-U`：强制更新 SNAPSHOT 依赖
- `clean`：清空上次编译产物
- `package`：打包每个子模块为 JAR
- `assembly:assembly`：调用 maven-assembly-plugin，把分散的 JAR 按 plugin.json 规则拷贝到 target/datax/ 目录，生成可分发的二进制包
- `-Dmaven.test.skip=true`：跳过测试，因为很多插件的单元测试依赖真实数据库，会失败

没有 `assembly:assembly`，你只会得到零散的 JAR，无法直接运行。

**小胖**：所以编译完后 target/datax/ 就等同于官方二进制包了？

**大师**：完全正确。你把 target/datax/ 拷贝到任何有 JDK 8 的机器上，都能跑。

## 3. 项目实战

### 3.1 环境准备

| 依赖 | 版本要求 | Windows 安装方式 | Linux 安装方式 |
|------|---------|----------------|---------------|
| JDK | 1.8.0_xxx | 下载安装包，配置 JAVA_HOME | `apt install openjdk-8-jdk` |
| Maven | 3.6+ | 下载解压，配置 MAVEN_HOME | `apt install maven` |
| Python | 2.7 或 3.x | 安装 Anaconda 或从官网下载 | 系统自带 |
| Git | 2.x | `winget install Git.Git` | `apt install git` |

### 3.2 步骤一：验证环境

```bash
# Windows PowerShell 或 Linux/Mac Terminal
$ java -version
openjdk version "1.8.0_382"
OpenJDK Runtime Environment (build 1.8.0_382-b05)
OpenJDK 64-Bit Server VM (build 25.382-b05, mixed mode)

$ mvn -version
Apache Maven 3.9.4

$ python --version
Python 3.11.5
```

如果提示 `java: command not found`，检查 JAVA_HOME 环境变量：

```bash
# Windows PowerShell
$ echo $env:JAVA_HOME
C:\Program Files\Java\jdk1.8.0_382

# Linux
$ echo $JAVA_HOME
/usr/lib/jvm/java-8-openjdk-amd64
```

### 3.3 步骤二：克隆并编译源码

```bash
# 克隆 DataX 源码
git clone https://github.com/alibaba/DataX.git
cd DataX

# 编译打包（首次编译约需 5-10 分钟，下载大量依赖）
mvn -U clean package assembly:assembly -Dmaven.test.skip=true
```

编译成功的标志——控制台输出：

```
[INFO] ------------------------------------------------------------------------
[INFO] BUILD SUCCESS
[INFO] ------------------------------------------------------------------------
[INFO] Total time:  05:23 min
[INFO] Finished at: 2026-05-06T14:30:00+08:00
[INFO] ------------------------------------------------------------------------
```

编译产物位于 `target/datax/datax/`，目录结构如下：

```
target/datax/datax/
├── bin/
│   ├── datax.py          # Python启动脚本
│   ├── dx2dx.py
│   └── perftrace.py
├── lib/
│   └── datax-core.jar    # 核心框架JAR
├── conf/
│   ├── core.json         # 框架核心配置
│   └── logback.xml       # 日志配置
├── plugin/
│   ├── reader/
│   │   ├── streamreader/
│   │   ├── mysqlreader/
│   │   └── ...
│   └── writer/
│       ├── streamwriter/
│       ├── mysqlwriter/
│       └── ...
└── job/                   # 示例Job配置
    └── stream2stream.json
```

### 3.4 步骤三：创建第一个 JSON 配置文件

创建 `D:/tmp/datax_jobs/hello_world.json`：

```json
{
    "job": {
        "content": [
            {
                "reader": {
                    "name": "streamreader",
                    "parameter": {
                        "column": [
                            {"type": "long", "value": 1},
                            {"type": "string", "value": "hello datax"}
                        ],
                        "sliceRecordCount": 10
                    }
                },
                "writer": {
                    "name": "streamwriter",
                    "parameter": {
                        "print": true,
                        "encoding": "UTF-8"
                    }
                }
            }
        ],
        "setting": {
            "speed": {
                "channel": 1
            }
        }
    }
}
```

配置解读：

| 字段 | 含义 |
|------|------|
| `reader.name: "streamreader"` | 使用内置的流式数据生成器 |
| `reader.parameter.column` | 定义两列：一列 LONG 类型值 1，一列 STRING 类型值 "hello datax" |
| `reader.parameter.sliceRecordCount: 10` | 生成 10 条记录 |
| `writer.name: "streamwriter"` | 使用内置的控制台输出 |
| `writer.parameter.print: true` | 将每条记录打印到控制台 |
| `setting.speed.channel: 1` | 使用 1 个并发通道 |

### 3.5 步骤四：运行第一个任务

```bash
# 进入编译产物目录
cd target/datax/datax

# 运行任务
python bin/datax.py D:/tmp/datax_jobs/hello_world.json
```

运行输出解读：

```
2026-05-06 14:35:00.123 [main] INFO  Engine - the job params is : 
{...}                              ← 打印解析后的 JSON 配置

2026-05-06 14:35:00.456 [main] INFO  JobContainer - jobContainer starts job.
2026-05-06 14:35:00.789 [main] INFO  JobContainer - reader name: [streamreader]
2026-05-06 14:35:00.789 [main] INFO  JobContainer - writer name: [streamwriter]

2026-05-06 14:35:01.012 [main] INFO  JobContainer - job [preCheck] phase starts.
2026-05-06 14:35:01.234 [main] INFO  JobContainer - job [preCheck] phase ends.

2026-05-06 14:35:01.345 [main] INFO  JobContainer - job [init] phase starts.
                                 ← 加载 streamreader 和 streamwriter 插件
2026-05-06 14:35:02.567 [main] INFO  JobContainer - job [init] phase ends.

2026-05-06 14:35:02.678 [main] INFO  JobContainer - job [prepare] phase starts.
2026-05-06 14:35:02.789 [main] INFO  JobContainer - job [prepare] phase ends.

2026-05-06 14:35:02.890 [main] INFO  JobContainer - job [split] phase starts.
                                 ← 切分 Task：sliceRecordCount=10，channel=1，生成 1 个 Task
2026-05-06 14:35:03.001 [main] INFO  JobContainer - job [split] phase ends.

2026-05-06 14:35:03.112 [main] INFO  JobContainer - job [schedule] phase starts.
                                 ← 启动 TaskGroupContainer，执行 Task
1   hello datax                 ← streamwriter 输出第 1 条记录
2   hello datax                 ← 第 2 条
...
10  hello datax                 ← 第 10 条

2026-05-06 14:35:03.456 [main] INFO  JobContainer - 
任务启动时刻     : 2026-05-06 14:35:02
任务结束时刻     : 2026-05-06 14:35:03
任务总计耗时     : 1s
任务平均流量     : 10B/s
记录写入速度     : 10rec/s
读出记录总数     : 10
读写失败总数     : 0

2026-05-06 14:35:03.567 [main] INFO  JobContainer - job [schedule] phase ends.
2026-05-06 14:35:03.678 [main] INFO  JobContainer - job [post] phase starts.
2026-05-06 14:35:03.789 [main] INFO  JobContainer - job [post] phase ends.
2026-05-06 14:35:03.890 [main] INFO  JobContainer - job [destroy] phase starts.
2026-05-06 14:35:03.901 [main] INFO  JobContainer - job [destroy] phase ends.
```

### 3.6 步骤五：使用自动生成配置模板

DataX 提供了一条便捷命令，可以自动生成 Reader 和 Writer 的配置模板：

```bash
# 自动生成 streamreader → streamwriter 的配置模板
python bin/datax.py -r streamreader -w streamwriter
```

输出：

```json
{
    "job": {
        "content": [
            {
                "reader": {
                    "name": "streamreader",
                    "parameter": {
                        "column": [],
                        "sliceRecordCount": ""
                    }
                },
                "writer": {
                    "name": "streamwriter",
                    "parameter": {
                        "encoding": "",
                        "print": true
                    }
                }
            }
        ],
        "setting": {
            "speed": {
                "channel": ""
            }
        }
    }
}
```

你可以把这个模板保存为一个 `.json` 文件，填上具体参数即可运行。换成 MySQL 插件试试：

```bash
python bin/datax.py -r mysqlreader -w mysqlwriter
```

模板中会显示 MySQL Reader 和 Writer 需要的全部参数及其含义。

### 3.7 可能遇到的坑及解决方法

**坑1：`mvn` 命令不存在**

解决：确认 Maven 已安装且加入 PATH。Windows 用户注意：安装后需重启终端。

**坑2：编译时报`Could not transfer artifact`**

解决：Maven 中央仓库下载失败。在 `~/.m2/settings.xml` 中配置阿里云镜像：

```xml
<mirrors>
    <mirror>
        <id>aliyun</id>
        <mirrorOf>central</mirrorOf>
        <name>aliyun maven</name>
        <url>https://maven.aliyun.com/repository/public</url>
    </mirror>
</mirrors>
```

**坑3：运行时提示`Error: Could not find or load main class`**

解决：编译不完整。检查是否执行了 `assembly:assembly` 目标，确认 `target/datax/datax/lib/` 目录下有 `datax-core.jar`。

**坑4：Python 脚本报`SyntaxError`**

解决：DataX 发布包自带的 Python 脚本兼容 Python 2.7。如果用 Python 3.x，部分语法可能报错。建议用 Python 2.7 或使用 Python 3 兼容的修改版。

**坑5：Windows 路径反斜杠问题**

解决：Windows 下路径使用正斜杠 `/` 或双反斜杠 `\\`：
```bash
python bin/datax.py D:/tmp/datax_jobs/hello_world.json
```

## 4. 项目总结

### 4.1 优点

1. **编译过程标准化**：一条 Maven 命令完成全部子模块编译和插件组装，无需手动拷贝 JAR
2. **目录结构清晰**：编译产物严格区分 bin/lib/conf/plugin/job 五大目录，符合运维规范
3. **配置模板命令**：`-r -w` 自动生成模板，降低新人手写 JSON 的出错率
4. **日志详尽**：日志显示完整的 9 步生命周期和性能统计，一眼能看出任务耗时和速度
5. **跨平台**：同一份源码，Windows/Mac/Linux 编译命令完全一致

### 4.2 缺点

1. **JDK 8 绑定**：强制使用 JDK 8，和现代 Java 项目的 JDK 17/21 存在环境冲突
2. **首次编译慢**：50+ 子模块、数百个依赖，首次编译需要 5-10 分钟
3. **Maven 依赖地狱**：部分 Hadoop 相关插件（hdfsreader）的依赖冲突严重，经常需要手动排除
4. **Python 版本不兼容**：自带的 Python 脚本写于 Python 2.7 时代，Python 3 运行可能报错
5. **无官方 Docker 镜像**：需要自行编写 Dockerfile

### 4.3 StreamReader vs StreamWriter 的测试价值

| 组合 | 用途 | 典型场景 |
|------|------|---------|
| StreamReader → MySQL Writer | 压测 MySQL 写入性能 | 上线前验证 DB 能否扛住写入量 |
| MySQL Reader → StreamWriter | 压测 MySQL 读取性能 | 验证源端读压力对业务的影响 |
| StreamReader → StreamWriter | 验证 DataX 框架本身 | 新人第一个 demo，走通全流程 |
| StreamReader → HDFS Writer | 验证 HDFS 连接和权限 | 部署到新 Hadoop 集群前做连通性测试 |

### 4.4 注意事项

1. **JAVA_HOME 必须配置**：不是 JRE_HOME，是 JDK 的根目录（包含 bin/javac）
2. **Maven settings.xml**：国内用户建议配置阿里云镜像，大幅加速依赖下载
3. **磁盘空间**：编译产物约 1.5GB，确保有足够的磁盘空间
4. **Windows 编码**：CMD 默认 GBK，日志中可能出现中文乱码，建议用 PowerShell 或 Git Bash
5. **不要跳过 assembly**：只执行 `mvn package` 不会生成可运行的 datax 目录

### 4.5 常见踩坑经验

1. **"我在 IDE 里跑通了但命令行报错"**——IDE 自动处理了 classpath，命令行需要完整的 `-cp` 和插件目录，建议始终用 datax.py 启动
2. **"编译成功但 plugin 目录为空"**——未执行 `assembly:assembly`，需要重新执行完整命令
3. **"Linux 上编译报 Permission denied"**——datax.py 需要执行权限，`chmod +x bin/datax.py`

### 4.6 思考题

1. `datax.py` 脚本在启动 Engine 时传入了哪些 JVM 参数？如何修改默认的 `-Xms1g -Xmx1g` 为 `-Xms4g -Xmx4g`？
2. StreamReader 生成的数据流中，如果配置了 `column: [{type: "date", value: "2026-05-06"}]`，DataX 内部会将它映射为哪种 Column 子类型？

（答案见附录）
