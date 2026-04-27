# 第1章：Nginx 术语全景与 master-worker 架构原理

> 源码关联：src/core/nginx.c、src/core/ngx_cycle.h、src/os/unix/ngx_process_cycle.c

---

## 1. 项目背景

故事从一家快速成长的互联网电商公司——"鲜果园"说起。鲜果园从一家小型水果配送站起家，三年内用户量从几千暴涨到五百万。技术团队最初用一台 Tomcat 服务器扛下了所有流量，但随着秒杀活动的推出，系统在晚上八点准时"宕机"成了常态。用户投诉如雪片般飞来，CTO 老张在凌晨三点的电话里对架构师小李吼道："我要的是能同时扛住十万人抢榴莲的网关，不是一台一到高峰期就装死的机器！"

小李临危受命，需要在两周内找到一个既能扛高并发、又能平滑扩容的解决方案。团队里有人提议上云原生网关，有人建议用商业负载均衡设备，预算却只有一台 8 核 16G 的服务器。就在大家争论不休时，运维工程师老王默默推过来一个链接——Nginx 官方文档首页。

Nginx，这个由俄罗斯程序员 Igor Sysoev 于 2004 年发布的 Web 服务器，凭借事件驱动架构和极低的资源占用，已经成为全球访问量前一万网站中超过 60% 的选择。从静态资源服务到七层负载均衡，从反向代理到 API 网关，Nginx 几乎无处不在。但鲜果园的团队面对 Nginx 时却一脸茫然：master 进程是什么？worker 进程又做什么？upstream、location、server 这些术语像天书一样。团队需要一个统一的语系，一个能从架构层面讲清楚 Nginx 如何工作的开篇。

本章便是为此而生。我们将从业务痛点出发，用一场轻松的对话厘清核心术语，再深入源码剖析 Nginx 的 master-worker 进程模型，最终用实战绘制一张团队人人能懂的架构图。

---

## 2. 项目设计

**场景**：鲜果园技术部的会议室，白板前围坐着三个人——小胖（后端新人，爱吃爱玩）、小白（测试工程师，喜静喜深入）、大师（架构师，资深技术 Leader）。

---

**小胖**：（嘴里嚼着薯片，大大咧咧地）我说各位，这 Nginx 不就是一 Web 服务器吗？我大学那会儿配过 Apache，改改端口、设设目录就能跑，有啥好研究的？

**小白**：（推了推眼镜）Apache 是进程/线程模型，每个连接一个进程或线程。咱们秒杀活动十万并发，Apache 早就把内存吃光了。Nginx 号称能扛百万并发，凭的是什么？

**大师**：（在白板上画了一个圈）小胖，你把 Nginx 想成一家大型餐厅。Apache 的模式是来一个客人就雇一个服务员，客人走了服务员也不能立刻辞退——成本高、效率低。Nginx 呢？它雇了一群精干的跑堂（worker 进程），每个跑堂能同时伺候上千桌客人，而且不需要给每桌配一个服务员。

**小胖**：这不就跟海底捞的号位系统一样吗？一个服务员管十几桌，客人点菜用扫码，不用一直盯着。

**大师**：（点头）技术映射一下，这就是**事件驱动（Event-Driven）**和**非阻塞 IO（Non-blocking IO）**。Nginx 的 worker 进程使用 epoll（Linux）或 kqueue（FreeBSD）来监视成千上万个连接，哪个连接有数据可读可写，才去处理哪个——而不是每个连接都占一个线程傻等。

**小白**：那 master 进程呢？我看配置文件里老提到它。

**大师**：（在白板上画了两个框，大的标 master，小的标 worker）master 是餐厅经理，负责**招聘和解雇 worker**（启动和停止 worker 进程）、**接收外部指令**（信号处理）、**执行热升级**（不关机换版本）。worker 才是真正干活的跑堂，它们之间互不干扰，崩溃一个也不会影响其他 worker。

**小胖**：哦！所以 master 自己不接客？

**大师**：master 从不直接处理客户端请求，它的工作就是保证 worker 们正常工作。这是一个经典的**master-worker 多进程模型**。

**小白**：那 upstream、location、server 这些术语呢？配置文件中到处都是。

**大师**：（在白板上画了一个倒树状图）你们看，Nginx 的配置是一个层级结构。最顶层是 **main 上下文**（全局配置），下面分出一个 **events 上下文**（连接数、事件模型），再下面是 **http 上下文**。http 里面可以有多个 **server 块**——每个 server 就是一家分店，监听不同的端口或域名。server 里面再有 **location 块**——这是具体的窗口，决定什么请求由什么服务处理。

**小胖**：我懂了！server 就是"鲜果园总店"和"鲜果园分店"，location 就是"水果柜台"和"蔬菜柜台"！

**大师**：对。而 **upstream** 呢，就是你们的供应商集群。比如鲜果园有五个水果供应商，upstream 定义了这五个供应商的地址和负载均衡策略，location 里的 proxy_pass 把订单分发给它们。

**小白**：还有一个词叫 **connection**，和 **request** 有什么区别？

**大师**：connection 是 TCP 连接，request 是 HTTP 请求。HTTP/1.1 支持长连接（Keep-Alive），一个 connection 上可以发多个 request。Nginx 的 worker 能同时管理几十万个 connection，但每个 connection 上的 request 是串行处理的——不过多个 connection 之间是并行的。

**小胖**：那 **filter chain** 和 **phase handler** 是啥？听起来像流水线。

**大师**：正是流水线。当一个请求进入 Nginx，它会经过 11 个**处理阶段（phase）**，比如读取请求头、检查访问权限、匹配 location、生成内容、记录日志。每个阶段都可以挂载处理函数（handler）。而 **filter chain** 是响应的加工流水线：先压缩（gzip）、再分片（slice）、最后写入网络。请求从一头进去，经过层层处理，从另一头出来。

**小白**：这个设计真是精巧。模块化、分阶段、流水线……难怪 Nginx 这么高效。

**大师**：（在白板上画完最后一条线）所以，Nginx 的核心架构可以总结为：**一个 master 管理多个 worker，每个 worker 基于事件驱动处理海量连接；配置按 main -> events -> http -> server -> location 层级组织；请求按 phase handler 分阶段处理，响应按 filter chain 流水线加工。**

**小胖**：（拍手）这下我懂了！说白了，Nginx 就是一个会同时伺候十万个客人的超级跑堂，而且还有个聪明的经理在后台盯着！

**大师**：（笑）你这比喻虽然不严谨，但方向是对的。接下来，咱们就看看这个"超级跑堂"的源码是怎么实现的。

---

## 3. 项目实战

### 环境准备

- **操作系统**：Ubuntu 22.04 LTS（或 WSL2）
- **Nginx 版本**：1.31.0（与专栏源码一致）
- **依赖工具**：build-essential、libpcre3-dev、zlib1g-dev、libssl-dev、git

```bash
# 安装编译依赖
sudo apt update
sudo apt install -y build-essential libpcre3-dev zlib1g-dev libssl-dev git

# 克隆源码（如果尚未克隆）
cd /opt
git clone https://github.com/nginx/nginx.git
cd nginx
git checkout release-1.31.0
```

### 步骤一：编译带 debug 的 Nginx

```bash
# 配置编译参数，开启 debug 模式
./auto/configure \
    --prefix=/usr/local/nginx \
    --with-debug \
    --with-http_ssl_module \
    --with-http_v2_module \
    --with-http_realip_module \
    --with-http_stub_status_module \
    --with-cc-opt="-O0 -g"

# 编译并安装
make -j$(nproc)
sudo make install
```

编译完成后，验证版本：

```bash
/usr/local/nginx/sbin/nginx -v
# 输出：nginx version: nginx/1.31.0
```

### 步骤二：启动 Nginx 并观察进程模型

```bash
# 启动 Nginx
sudo /usr/local/nginx/sbin/nginx

# 查看进程树
ps auxf | grep nginx
```

预期输出类似：

```
root      12345  0.0  0.1  12345  2345 ?        Ss   10:00   0:00 nginx: master process /usr/local/nginx/sbin/nginx
www-data  12346  0.0  0.2  13456  3456 ?        S    10:00   0:00 nginx: worker process
www-data  12347  0.0  0.2  13456  3456 ?        S    10:00   0:00 nginx: worker process
```

可以看到：
- **1 个 master 进程**，以 root 身份运行，负责管理 worker
- **2 个 worker 进程**（默认等于 CPU 核数），以 www-data（或 nobody）身份运行，处理实际请求

### 步骤三：用 strace 追踪 master 的信号处理

打开一个终端，用 strace 监控 master 进程：

```bash
sudo strace -e trace=signal -p $(cat /usr/local/nginx/logs/nginx.pid)
```

在另一个终端发送信号：

```bash
# 热重载配置（SIGHUP）
sudo kill -HUP $(cat /usr/local/nginx/logs/nginx.pid)

# 重新打开日志文件（SIGUSR1）
sudo kill -USR1 $(cat /usr/local/nginx/logs/nginx.pid)
```

观察 strace 输出，可以看到 master 收到了 SIGHUP 和 SIGUSR1，并相应执行了 worker 进程的重启和日志重开操作。

### 步骤四：绘制架构图

使用 Draw.io 或 Excalidraw 绘制以下架构图，保存为 PNG 并上传到团队 Wiki：

```
+--------------------------------------------------+
|                   Nginx Architecture               |
+--------------------------------------------------+
|                                                    |
|  +----------------+                                |
|  |  Master Process |  <-- 信号处理 (SIGHUP/USR1/USR2)|
|  |   (root)        |  <-- 启动/管理 worker           |
|  |   pid: 12345    |  <-- 热升级 (binary swap)       |
|  +--------+-------+                                |
|           |                                        |
|  +--------+--------+  +--------+--------+         |
|  | Worker Process 1 |  | Worker Process 2 |         |
|  | (www-data)       |  | (www-data)       |         |
|  | Event Loop (epoll)|  | Event Loop (epoll)|        |
|  | Handle Connections |  | Handle Connections |       |
|  +--------+--------+  +--------+--------+         |
|           |                        |               |
+-----------+------------------------+---------------+
            |                        |
            v                        v
+-----------+------------------------+---------------+
|              HTTP Request Processing               |
|  Phase Handlers -> Content -> Filters -> Output   |
+--------------------------------------------------+
```

### 步骤五：源码速览

打开 src/core/nginx.c，找到 main 函数的骨架：

```c
// src/core/nginx.c
int main(int argc, char *const *argv)
{
    ngx_int_t         i;
    ngx_log_t        *log;
    ngx_cycle_t      *cycle, init_cycle;
    ngx_core_conf_t  *ccf;

    // ... 省略初始化代码 ...

    // 进入主循环或进程管理
    if (ngx_process == NGX_PROCESS_SINGLE) {
        ngx_single_process_cycle(cycle);  // 单进程模式（调试）
    } else {
        ngx_master_process_cycle(cycle);   // 多进程模式（生产）
    }
}
```

打开 src/os/unix/ngx_process_cycle.c，查看 master 进程的主循环：

```c
// src/os/unix/ngx_process_cycle.c
void ngx_master_process_cycle(ngx_cycle_t *cycle)
{
    ngx_uint_t  i;
    sigset_t    set;

    // 设置信号屏蔽字
    sigemptyset(&set);
    sigaddset(&set, SIGCHLD);
    sigaddset(&set, SIGALRM);
    // ... 其他信号 ...

    // 启动 worker 进程
    ngx_start_worker_processes(cycle, ccf->worker_processes, NGX_PROCESS_RESPAWN);

    // master 主循环：等待信号
    for ( ;; ) {
        sigsuspend(&set);  // 阻塞等待信号

        // 根据信号类型处理：
        // SIGHUP  -> 重新加载配置
        // SIGUSR1 -> 重新打开日志
        // SIGUSR2 -> 启动热升级
        // SIGWINCH -> 优雅关闭 worker
        // ...
    }
}
```

**代码注释**：
- ngx_master_process_cycle 是 master 进程的主循环，它通过 sigsuspend 阻塞等待信号
- 收到 SIGHUP 时，master 会重新读取配置并启动新的 worker 进程，然后优雅关闭旧的 worker
- ngx_start_worker_processes 通过 fork() 创建指定数量的 worker 子进程

### 测试验证

```bash
# 测试 Nginx 是否正常工作
curl -I http://localhost/

# 预期输出：
# HTTP/1.1 200 OK
# Server: nginx/1.31.0
# ...

# 压测验证并发处理能力
sudo apt install -y apache2-utils
ab -n 100000 -c 1000 http://localhost/

# 观察 worker 进程的 CPU 占用
top -p $(pgrep -d',' nginx)
```

**可能遇到的坑**：
1. **端口冲突**：如果系统已有 Nginx 在运行，启动会报 bind() to 0.0.0.0:80 failed。解决：修改 /usr/local/nginx/conf/nginx.conf 的 listen 端口为 8080，或停止系统 Nginx。
2. **权限不足**：80 端口需要 root 权限。解决：使用 sudo 启动，或配置 cap_net_bind_service 能力。
3. **worker 进程数不对**：默认 worker 数等于 CPU 核数。如果虚拟机只有 1 核，只会看到 1 个 worker。解决：在 nginx.conf 中显式设置 worker_processes 4;。

---

## 4. 项目总结

### 优点与缺点

| 维度 | Nginx master-worker 模型 | 传统 Apache prefork/worker 模型 |
|------|------------------------|-------------------------------|
| 内存占用 | 低（worker 共享代码段，独立栈） | 高（每个进程/线程独立内存） |
| 并发能力 | 极高（C10K ~ C100K） | 中等（数千级别） |
| 故障隔离 | 好（单个 worker 崩溃不影响其他） | 差（进程崩溃可能影响整体） |
| 热升级 | 原生支持（二进制替换） | 需借助外部工具 |
| 配置复杂度 | 中等 | 较低 |
| 动态模块 | 支持（--add-module） | 支持（DSO） |

### 适用场景

1. **静态资源服务器**：图片、CSS、JS、视频等，配合 sendfile 零拷贝性能极佳
2. **反向代理与负载均衡**：七层 HTTP 代理，支持多种负载均衡算法
3. **API 网关**：统一入口、认证鉴权、限流熔断
4. **高并发 Web 服务**：事件驱动模型天然适合长连接和海量并发
5. **SSL 终止集中处理**：减轻后端服务器的加密解密负担

**不适用场景**：
1. **需要大量 CPU 计算的后端业务**：Nginx 是 IO 密集型工具，复杂业务逻辑应放在后端服务
2. **需要共享会话状态的应用**：Nginx worker 之间内存隔离，需借助 Redis 等外部存储

### 注意事项

1. **worker_processes 不要无脑设很大**：通常设置为 CPU 核数或 auto，超过核数会导致上下文切换开销增大
2. **master 进程必须以 root 启动**：才能绑定 80/443 端口，worker 启动后会降级为普通用户
3. **信号操作要准确**：kill -9 master 会导致 worker 成为孤儿进程，应使用 nginx -s stop

### 常见踩坑经验

**案例一：worker 进程 CPU 100%**
- **现象**：某台 Nginx 服务器的某个 worker 进程 CPU 占用率长期 100%
- **根因**：一个第三方模块在处理特定请求时陷入死循环，且该模块未做超时保护
- **解决**：升级模块版本，并在 nginx.conf 中加入 worker_rlimit_nofile 和 worker_shutdown_timeout

**案例二：热升级后旧 worker 不退出**
- **现象**：执行 kill -USR2 后，新旧 worker 共存，内存占用翻倍
- **根因**：旧 worker 上还有长连接（WebSocket）未断开，导致 graceful shutdown 卡住
- **解决**：设置 worker_shutdown_timeout 30s;，强制 30 秒后关闭旧 worker

**案例三：配置 reload 后连接数暴涨**
- **现象**：每次 nginx -s reload 后，活跃连接数瞬间翻倍
- **根因**：reload 时 master 先启动新 worker 再关闭旧 worker，中间存在重叠期
- **解决**：这是正常现象，但如果上游连接池配置不当，会导致后端连接耗尽。应开启 upstream 的 keepalive

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. **进阶题**：为什么在 Nginx 的 master-worker 模型中，worker 进程的数量通常建议设置为 CPU 核数？如果设置为核数的 2 倍或 10 倍，会发生什么？

2. **进阶题**：Nginx 的热升级（kill -USR2）过程中，新旧 master 进程是如何通过共享 listen socket 实现无缝切换的？请结合 ngx_add_inherited_sockets 函数的源码逻辑分析。

> 答案提示：第 1 题答案与 CPU 缓存局部性和上下文切换开销有关；第 2 题答案涉及环境变量 NGINX 传递的 socket fd 列表。

---

> **下一章预告**：我们将打开 Nginx 源码目录，像解剖一只青蛙一样，逐个认识 src/core/、src/event/、src/http/ 等目录的职责，并亲手编译一个属于自己的 Nginx。
