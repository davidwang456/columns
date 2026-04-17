# ROS 学习专栏 · 总目录

> 章节格式遵循 [template.md](template.md)。统一环境见 [ENV.md](ENV.md)。**思考题答案**见 [APPENDIX-answers.md](APPENDIX-answers.md)（与下一章正文呼应）。

**文件名与全书顺序**：`chapters/` 下为 **`第NN章：主题.md`**（两位数字）。**第01–12章** = **A01–A12**，**第13–25章** = **B01–B13**，**第26–38章** = **M01–M13**（与下表「编号」列一致）。正文链接已指向新文件名。

## 基础篇（13 章）— 新人开发、测试

| 编号 | 标题 | 文件 |
|------|------|------|
| B01 | ROS 2 是什么：节点图与「没有中间件会怎样」 | [第13章：ROS 2 是什么-节点图与「没有中间件会怎样」.md](chapters/第13章：ROS 2 是什么-节点图与「没有中间件会怎样」.md) |
| B02 | 工作空间、包与 colcon：可复现构建 | [第14章：工作空间、包与 colcon-可复现构建.md](chapters/第14章：工作空间、包与 colcon-可复现构建.md) |
| B03 | 节点与执行器：回调与单线程/多线程 | [第15章：节点与执行器-回调与单线程-多线程.md](chapters/第15章：节点与执行器-回调与单线程-多线程.md) |
| B04 | 话题与消息：发布订阅第一印象 | [第16章：话题与消息-发布订阅第一印象.md](chapters/第16章：话题与消息-发布订阅第一印象.md) |
| B05 | QoS 入门：可靠与尽力而为 | [第17章：QoS 入门-可靠与尽力而为.md](chapters/第17章：QoS 入门-可靠与尽力而为.md) |
| B06 | 服务：同步请求响应 | [第18章：服务-同步请求响应.md](chapters/第18章：服务-同步请求响应.md) |
| B07 | 参数与 YAML：可配置行为 | [第19章：参数与 YAML-可配置行为.md](chapters/第19章：参数与 YAML-可配置行为.md) |
| B08 | 自定义 msg/srv | [第20章：自定义 msg-srv.md](chapters/第20章：自定义 msg-srv.md) |
| B09 | TF2：坐标系与静态变换 | [第21章：TF2-坐标系与静态变换.md](chapters/第21章：TF2-坐标系与静态变换.md) |
| B10 | Launch：XML/Python 与参数替换 | [第22章：Launch-XML-Python 与参数替换.md](chapters/第22章：Launch-XML-Python 与参数替换.md) |
| B11 | 生命周期节点（Lifecycle） | [第23章：生命周期节点（Lifecycle）.md](chapters/第23章：生命周期节点（Lifecycle）.md) |
| B12 | Action：长时间任务与可取消 | [第24章：Action-长时间任务与可取消.md](chapters/第24章：Action-长时间任务与可取消.md) |
| B13 | 日志、rosbag2 入门与最小集成测试 | [第25章：日志、rosbag2 入门与最小集成测试.md](chapters/第25章：日志、rosbag2 入门与最小集成测试.md) |

## 中级篇（13 章）— 核心开发、运维

| 编号 | 标题 | 文件 |
|------|------|------|
| M01 | DDS 发现、域（Domain）与跨机通信 | [第26章：DDS 发现、域（Domain）与跨机通信.md](chapters/第26章：DDS 发现、域（Domain）与跨机通信.md) |
| M02 | QoS 深度：history、deadline、durability | [第27章：QoS 深度-history、deadline、durability.md](chapters/第27章：QoS 深度-history、deadline、durability.md) |
| M03 | 命名空间、重映射与多实例部署 | [第28章：命名空间、重映射与多实例部署.md](chapters/第28章：命名空间、重映射与多实例部署.md) |
| M04 | Nav2 栈概览与行为树入门 | [第29章：Nav2 栈概览与行为树入门.md](chapters/第29章：Nav2 栈概览与行为树入门.md) |
| M05 | SLAM / 定位概念与工具链选 | [第30章：SLAM-定位概念与工具链选.md](chapters/第30章：SLAM-定位概念与工具链选.md) |
| M06 | 传感器驱动与标定流程 | [第31章：传感器驱动与标定流程.md](chapters/第31章：传感器驱动与标定流程.md) |
| M07 | pluginlib：算法可替换 | [第32章：pluginlib-算法可替换.md](chapters/第32章：pluginlib-算法可替换.md) |
| M08 | rosbag2 进阶：录制策略与回放测试 | [第33章：rosbag2 进阶-录制策略与回放测试.md](chapters/第33章：rosbag2 进阶-录制策略与回放测试.md) |
| M09 | 性能与带宽：topic hz/bw、系统剖析入门 | [第34章：性能与带宽-topic hz-bw、系统剖析入门.md](chapters/第34章：性能与带宽-topic hz-bw、系统剖析入门.md) |
| M10 | 可观测性：tracing、诊断话题与仪表盘 | [第35章：可观测性-tracing、诊断话题与仪表盘.md](chapters/第35章：可观测性-tracing、诊断话题与仪表盘.md) |
| M11 | 容器化（Docker）与最小 CI | [第36章：容器化（Docker）与最小 CI.md](chapters/第36章：容器化（Docker）与最小 CI.md) |
| M12 | 多机器人协同与通信隔离 | [第37章：多机器人协同与通信隔离.md](chapters/第37章：多机器人协同与通信隔离.md) |
| M13 | 安全与 SROS 2 / 权限边界（实践向） | [第38章：安全与 SROS 2-权限边界（实践向）.md](chapters/第38章：安全与 SROS 2-权限边界（实践向）.md) |

## 高级篇（12 章）— 架构师、资深开发

| 编号 | 标题 | 文件 |
|------|------|------|
| A01 | rcl/rclcpp 执行模型与源码导读 | [第01章：rcl-rclcpp 执行模型与源码导读.md](chapters/第01章：rcl-rclcpp 执行模型与源码导读.md) |
| A02 | rmw 与 DDS 实现切换（Fast-DDS / Cyclone 等） | [第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md](chapters/第02章：rmw 与 DDS 实现切换（Fast-DDS-Cyclone 等）.md) |
| A03 | 零拷贝与 loaned message（能力与边界） | [第03章：零拷贝与 loaned message（能力与边界）.md](chapters/第03章：零拷贝与 loaned message（能力与边界）.md) |
| A04 | ros2_control 与硬件接口分层 | [第04章：ros2_control 与硬件接口分层.md](chapters/第04章：ros2_control 与硬件接口分层.md) |
| A05 | 极端网络：延迟、抖动、丢包下的 QoS 组合 | [第05章：极端网络-延迟、抖动、丢包下的 QoS 组合.md](chapters/第05章：极端网络-延迟、抖动、丢包下的 QoS 组合.md) |
| A06 | 自定义 Action / 复杂状态机与容错 | [第06章：自定义 Action-复杂状态机与容错.md](chapters/第06章：自定义 Action-复杂状态机与容错.md) |
| A07 | 交叉编译与嵌入式部署（Yocto/板级） | [第07章：交叉编译与嵌入式部署（Yocto-板级）.md](chapters/第07章：交叉编译与嵌入式部署（Yocto-板级）.md) |
| A08 | 构建系统深入：ament、依赖与私有仓库 | [第08章：构建系统深入-ament、依赖与私有仓库.md](chapters/第08章：构建系统深入-ament、依赖与私有仓库.md) |
| A09 | 性能极限：内存、锁、实时补丁（PREEMPT_RT 等概念） | [第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md](chapters/第09章：性能极限-内存、锁、实时补丁（PREEMPT_RT 等概念）.md) |
| A10 | SRE：故障演练、SLA、告警与回滚 | [第10章：SRE-故障演练、SLA、告警与回滚.md](chapters/第10章：SRE-故障演练、SLA、告警与回滚.md) |
| A11 | 生产案例复盘（综合）：导航栈调优一页纸方法论 | [第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md](chapters/第11章：生产案例复盘（综合）-导航栈调优一页纸方法论.md) |
| A12 | 专栏总结：路线图、认证与社区资源 | [第12章：专栏总结-路线图、认证与社区资源.md](chapters/第12章：专栏总结-路线图、认证与社区资源.md) |

## 阅读顺序建议

- **开发**：**B01 → B13**（第13章→第25章），再 **M01 → M13**（第26章→第38章），最后 **A01 → A12**（第01章→第12章）。
- **测试**：优先 B12–B13、M08、M10、A10；配合 [APPENDIX-answers.md](APPENDIX-answers.md) 做验收清单。
- **运维**：优先 M01–M03、M09–M11、M13、A07、A10。

全书共 **38** 章，单章目标 **3000–5000** 字（含对话与代码说明）。
