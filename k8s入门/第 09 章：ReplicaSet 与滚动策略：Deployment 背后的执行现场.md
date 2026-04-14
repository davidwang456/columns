# 第 09 章：ReplicaSet 与滚动策略：Deployment 背后的执行现场

> 对应学习计划第 1 周 ReplicaSet 观察与 rollout 操作。

很多开发者用 Deployment 用得很顺手，却始终停留在“会 rollout”这一层。一旦发布异常、版本切换卡住、旧 Pod 清理不掉，就不知道问题到底发生在哪。原因往往很简单：**Deployment 不是直接在管理 Pod，它是通过 ReplicaSet 间接完成这件事的。** 这一章，就是把你从“会用 Deployment”再往里推进一步，让你看懂它背后的执行现场。

## 1）项目背景

在 Kubernetes 的工作负载体系里，Deployment 之所以强大，不是因为它自己手里直接掌握所有细节，而是因为它把副本维持和版本切换拆成了分层协作：

- Deployment 负责“我想要什么版本、什么策略、什么长期状态”。
- ReplicaSet 负责“当前这批 Pod 的副本数要维持多少”。
- Pod 负责真正运行容器。

这种分层看起来多了一层对象，实际上换来了更清晰的职责边界。尤其是在版本更新时，这种设计特别有价值：Deployment 并不是把旧 Pod 直接改成新 Pod，而是创建一个新的 ReplicaSet，让新旧两批 ReplicaSet 在一段时间内并存，再逐步完成替换。

理解这件事非常重要，因为它解释了很多现象：

- 为什么每次升级 Deployment，都会看到新的 ReplicaSet 出现。
- 为什么回滚时，旧 ReplicaSet 还能被重新启用。
- 为什么手工修改 ReplicaSet 往往会被上层控制器“纠正回来”。

如果你把 Deployment 看成“发布指挥官”，那 ReplicaSet 就是它手下负责实际带队的人。Deployment 下达命令，ReplicaSet 负责把这批对应版本的 Pod 数量维持住。

所以，这一章的重点不是学一个新对象，而是把 Deployment 的内部运行机制看透一层。理解了 ReplicaSet，你对滚动更新、历史版本、回滚、控制器纠偏这些能力的认识会立刻扎实很多。

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，我平时升级 Deployment 就用 `kubectl set image`，感觉挺方便的。但我总搞不清楚，为什么一升级就会冒出来一个新的 ReplicaSet。

**大师**：因为 Deployment 不会直接改老的那批 Pod，而是会基于新的 Pod 模板生成一个新的 ReplicaSet，再让新旧两批副本逐步交接。

**小白**：为什么要搞这么麻烦，不能直接改原来的 Pod 吗？

**大师**：不能。Pod 模板一旦变化，本质上就是“这是一批新实例”，不是原地编辑旧实例。Kubernetes 更擅长“创建新对象并替换旧对象”，而不是在原地魔法变身。

**小白**：那 ReplicaSet 的职责其实就是“维持某个版本那一批 Pod 的数量”？

**大师**：非常准确。你可以把一个 ReplicaSet 理解成“某个 Pod 模板版本的副本维护器”。

**小白**：那我能不能直接去改 ReplicaSet 的副本数？

**大师**：短期可以，但如果这个 ReplicaSet 是被 Deployment 管着的，你改完很可能又会被 Deployment 改回去。因为真正的期望状态定义在更上层。

**小白**：明白了。Deployment 管策略，ReplicaSet 管执行，Pod 管运行。

**大师**：对。所以一旦 rollout 出问题，你不能只盯 Deployment，还要看底下有几个 ReplicaSet、它们各自维持了多少 Pod、哪些是新版本、哪些是旧版本。

**小白**：那回滚其实也是重新启用旧 ReplicaSet？

**大师**：本质上可以这么理解。Deployment 会把期望重新指向旧模板，从而让旧 ReplicaSet 再次成为主角。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们接着上一章的 Deployment 来观察 ReplicaSet 如何参与版本演进。

### 第一步：查看当前 Deployment 对应的 ReplicaSet

```bash
kubectl get deployment web
kubectl get rs
```

通常你会看到一个名称后面带哈希值的 ReplicaSet，例如：

```bash
web-6c7f8d4d8b
```

这个哈希值本质上反映了 Pod 模板版本。只要模板有变化，通常就会产生新的 ReplicaSet。

### 第二步：升级镜像，观察新旧 ReplicaSet 交替

```bash
kubectl set image deployment/web nginx=nginx:1.28
kubectl rollout status deployment/web
kubectl get rs -w
```

这一步里你最应该看的是：

- 新 ReplicaSet 的副本数逐步增加。
- 旧 ReplicaSet 的副本数逐步减少。
- 整个切换过程不是瞬间替换，而是按策略滚动进行。

你也可以同时观察 Pod：

```bash
kubectl get pods -l app=web -w
```

这样就能把“Deployment 层面的发布动作”和“ReplicaSet 层面的副本变化”对应起来看。

### 第三步：查看发布历史

```bash
kubectl rollout history deployment/web
```

你会看到 Deployment 的版本历史记录。虽然这些记录展示在 Deployment 这一层，但背后真正承载历史状态的，往往正是不同的 ReplicaSet。

### 第四步：执行回滚

```bash
kubectl rollout undo deployment/web
kubectl get rs
kubectl get pods -l app=web -w
```

回滚后你会发现，旧版本对应的 ReplicaSet 又开始恢复副本，而新版本对应的 ReplicaSet 则逐渐退场。这会让你直观理解：**回滚不是“时光倒流修改 Pod”，而是“重新切换控制目标，让旧模板重新接管”。**

### 第五步：不要把 ReplicaSet 当成日常主操作对象

虽然你现在已经认识了 ReplicaSet，但要记住一个实践原则：

- 日常发布和维护，优先操作 Deployment。
- ReplicaSet 更适合作为“理解内部机制”和“排查发布问题”的观察对象。

如果你直接去改 Deployment 管理下的 ReplicaSet，大概率会被上层控制器改回去。因为最终事实来源，仍然是 Deployment 的期望状态。

### 这一节应该带走什么

- Deployment 不是直接控制 Pod，而是通过 ReplicaSet 分层管理。
- 每个 ReplicaSet 通常代表一个 Pod 模板版本。
- 滚动更新和回滚，本质上都是新旧 ReplicaSet 的交接过程。
- ReplicaSet 适合观察和理解，不应取代 Deployment 成为日常主操作入口。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它把 Deployment 的内部执行逻辑拆得更清楚，便于理解版本演进。
- 它让回滚和历史版本保留成为可能。
- 它有助于在发布异常时快速定位问题发生在哪一层。

### 缺点

- 对初学者来说，多一层对象会增加理解门槛。
- 如果不了解控制关系，容易误把 ReplicaSet 当成可以长期手工管理的主对象。
- 观察层级变多之后，排障需要更强的结构化思维。

### 使用场景

- 理解 Deployment 的更新与回滚机制。
- 排查滚动更新异常、旧副本残留或版本切换问题。
- 观察特定版本模板对应的 Pod 生命周期。

### 注意事项

- 被 Deployment 管理的 ReplicaSet 不应作为长期人工修改入口。
- 看 rollout 问题时，要同时看 Deployment、ReplicaSet 和 Pod 三层状态。
- 历史 ReplicaSet 的存在是正常现象，它们承载了版本切换轨迹。

### 常见踩坑经验

- 最常见的坑，是手工修改 Deployment 生成出来的 ReplicaSet，结果一会儿生效一会儿被改回，自己越改越乱。
- 第二个坑，是 rollout 出问题时只盯 Deployment，不看 ReplicaSet 的副本变化。
- 第三个坑，是把回滚理解成“把现有 Pod 改回旧版本”，而不是“重新启用旧模板和旧 ReplicaSet”。

这一章真正带来的提升，是让你从“会用 Deployment”进入“看懂 Deployment 是怎么工作的”这一层。对后面理解滚动更新策略、发布风险和平台控制循环，这一步非常关键。
