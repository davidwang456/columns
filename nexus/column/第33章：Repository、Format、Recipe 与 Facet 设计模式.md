# 第33章：Repository、Format、Recipe 与 Facet 设计模式

## 1. 项目背景

云鲸科技的架构组在第 32 章通读了 Nexus 源码结构后，下一个目标是为公司内部的 Go 模块格式（Golang modules）写一个 Nexus 格式插件。老周的小组花了两周试图从 Maven 格式的代码中"复制粘贴"出一个 Go 格式——结果发现 Maven 格式处理器里嵌入了大量 Maven 特有的逻辑（POM 解析、metadata XML 生成、坐标路由），直接复制的结果是代码量爆炸且逻辑混乱。

"为什么 Nexus 不做成每个格式完全独立的模块？"小李抱怨道，"Maven、npm、Docker 它们的存储逻辑差不多，但代码里一大堆重复的——读取 Blob、写入 Blob、权限检查、事件发布——每个格式都写了一遍。"

大师在白板上画了一张图——这正是 Nexus 的核心设计模式：**Repository 是舞台，Format/Recipe 定义了这场戏的规则，Facet 是角色和道具**。Maven 的 POM 解析是 Maven 格式特有的 Facet，而 Blob 读写是所有格式共享的 Facet。理解这种"面向组合"的设计，是读懂 Nexus 源码和开发格式插件的核心。

本章将以一个"解剖学"的视角，从 `Repository` 抽象向下拆解到 `Format`、`Recipe`、`Facet` 三层设计——每一种制品格式（Maven、npm、Docker、Raw）是如何通过这些组件组合出完整功能的，以及如何利用这种设计模式以最少的代码新增一种格式。

## 2. 项目设计

大师打开 IntelliJ IDEA，搜索到 `Repository` 接口，小李和小胖紧盯着屏幕。

**小李**："大师，Repository 是一个非常薄的接口——`getName()`、`getFormat()`、`getType()`、`getConfiguration()`。它看起来只是一个数据容器。真正的业务逻辑在哪？"

**大师**："Repository 是一个**聚合根**（Aggregate Root），它本身不执行任何业务逻辑——它是一个'舞台'，业务逻辑由附着在它上面的 **Facet** 来执行。打个比方——Repository 是一个房间，Format 决定了这个房间的类型（会议室/报告厅/食堂），Recipe 是房间的装修方案（桌椅怎么摆、投影仪装在哪），Facet 是房间里的职能岗位（前台、保洁、保安）。有人来房间——前台 Facet 负责接待（HTTP 路由），保安 Facet 负责检查来访者（权限校验），保洁 Facet 负责整理文件（清理策略）。"

> **技术映射**：Repository = 聚合根（持有配置和状态），Format = 格式标识（Maven2/npm/Docker），Recipe = 格式的"配方"（type → Facet 的组合清单），Facet = 仓库的一个维度上的能力接口（storage/security/browse/cleanup）。

**小胖**："那一个 Maven hosted 仓库有哪些 Facet？"

**大师**："大约 8-12 个。**StorageFacet**（存储能力——读写 Blob）、**MavenHostedFacet**（Maven 特有的上传校验——GET 上传后执行 POM 解析）、**BrowseFacet**（浏览目录树）、**PurgeUnusedFacet**（清理未使用的 SNAPSHOT）、**SecurityFacet**（权限检查）、**AttributesFacet**（元数据管理）、**ComponentMaintenanceFacet**（组件删除）、**SearchFacet**（索引更新）。每个 Facet 都是仓库的一个"侧面"（facet 的本意就是宝石的切面），它们组合在一起才是一个完整功能的仓库。"

**小李**："那 Recipe 是什么？它跟 Facet 的关系是？"

**大师**："Recipe 是 Format + Type（hosted/proxy/group）的'配料表'。以 `Maven2HostedRecipe` 为例，它标注其对应的 repository type 是 `hosted`，在创建 Maven hosted 仓库时，它的 `apply()` 方法被调用——向 Repository 实例 attach 一组 Facet。举例来说：`recipe.apply(repository)` 内部会执行 `repository.attach(storageFacet)`、`repository.attach(mavenHostedFacet)`、`repository.attach(browseFacet)`……这样就组装出了一个完整的 Maven hosted 仓库。"

> **技术映射**：Recipe.apply(repository) = 向仓库实例"装配"一组 Facet。不同的 Recipe 装配不同的 Facet 组合——hosted Recipe 装上传相关的 Facet，proxy Recipe 装远程代理相关的 Facet，group Recipe 装聚合路由相关的 Facet。

**小李**："那如果我要新增 Go 模块格式——我需要写哪些类？"

**大师**："最少 5 个。**GoFormat**（标注格式名称为 `go`）→ **GoHostedRecipe**（定义 hosted 仓库的 Facet 装配清单）→ **GoHostedFacet**（Go 特有逻辑，如上传 go.mod 解析和校验）→ **GoProxyRecipe + GoProxyFacet**（代理 goproxy 的远程逻辑）。其他通用 Facet（StorageFacet、SecurityFacet、BrowseFacet）直接复用。这就是 Format/Recipe/Facet 模式的价值——**你只实现差异部分，复用所有共性部分**。"

## 3. 项目实战

### 3.1 环境准备

- Nexus 源码已导入 IDE（参考第 32 章）
- 已完成首次编译（`mvn clean install -DskipTests`）
- 推荐先看 Maven 和 Raw 格式的实现作为参考

### 3.2 分步实战

#### 步骤一：解剖 Maven 格式——从 Format 到 Facet

**目标**：通过跟踪 Maven hosted 仓库的创建过程，理解 Format → Recipe → Facet 的装配流程。

```bash
# 在源码中定位关键类文件

# 1. Maven Format 定义
find . -name "Maven2Format.java" -path "*repository-maven*"
# 预期路径：plugins/nexus-repository-maven/src/main/java/.../Maven2Format.java

# 2. Maven hosted Recipe
find . -name "*HostedRecipe*" -path "*repository-maven*"
# 预期路径：plugins/nexus-repository-maven/src/main/java/.../maven/internal/MavenHostedRecipe.java

# 3. Maven hosted Facet
find . -name "*HostedFacet*" -path "*repository-maven*" | head -5
```

**Maven2Format.java 核心内容**：

```java
// 简化示意（非实际源码）
@Named(Maven2Format.NAME)    // ← Sisu 组件扫描发现
@Singleton
public class Maven2Format extends Format {
    public static final String NAME = "maven2";  // ← 格式名称

    public Maven2Format() {
        super(NAME);
    }
}
```

**MavenHostedRecipe.java 中的 Facet 装配**：

```java
// 简化示意（非实际源码，体现设计模式）
@Named // ← Sisu 组件扫描
@Singleton
public class Maven2HostedRecipe extends RecipeSupport {
    
    @Inject
    public Maven2HostedRecipe(
        @Named("hosted") Type type,       // ← recipe 对应 type: hosted
        Maven2Format format) {            // ← recipe 对应 format: maven2
        super(type, format);
    }

    @Override
    public void apply(Repository repository) throws Exception {
        // 装配通用 Facet
        repository.attach(storageFacet);           // 存储
        repository.attach(securityFacet);          // 权限
        repository.attach(browseFacet);            // 浏览
        repository.attach(attributesFacet);        // 属性/元数据
        repository.attach(componentMaintenanceFacet); // 组件删除
        
        // 装配 Maven 特有 Facet
        repository.attach(mavenHostedFacet);       // Maven 上传校验
        repository.attach(mavenMetadataFacet);     // maven-metadata.xml 管理
        repository.attach(purgeUnusedFacet);       // SNAPSHOT 清理
        repository.attach(searchFacet);            // Maven 坐标索引
    }
}
```

#### 步骤二：对比 Maven、npm、Docker、Raw 的 Recipe 差异

**目标**：理解不同格式共用哪些 Facet、差异在哪里。

```bash
# 查看每种格式的 hosted Recipe 中 attach 了哪些 Facet

echo "=== 格式 Facet 对比 ==="
echo ""
echo "Maven Hosted Recipe 额外 Facet (vs Raw):"
echo "  - MavenHostedFacet: POM 解析 + version policy 校验"
echo "  - MavenMetadataFacet: maven-metadata.xml 生成"
echo "  - PurgeUnusedFacet: 未使用 SNAPSHOT 清理"
echo "  - SearchFacet: 按 GAV 坐标索引"
echo ""
echo "npm Hosted Recipe 额外 Facet (vs Raw):"
echo "  - NpmHostedFacet: package.json 解析 + dist-tag 管理"
echo "  - NpmTokenFacet: npm token 认证"
echo ""
echo "Docker Hosted Recipe 额外 Facet (vs Raw):"
echo "  - DockerHostedFacet: manifest/layer 校验 + v2 API"
echo "  - DockerTokenFacet: Docker Bearer Token 认证"
echo ""
echo "Raw Hosted Recipe: 最少 Facet——没有格式特定的逻辑"
echo "  仅: StorageFacet + SecurityFacet (核心)"
echo ""
echo "共性 Facet (所有格式都用的):"
echo "  - StorageFacet: Blob 读写"
echo "  - SecurityFacet: 权限校验"
echo "  - BrowseFacet: 目录浏览"
echo "  - AttributesFacet: 组件元数据"
```

#### 步骤三：跟踪 Format → Recipe → Facet 的调用链

**目标**：在 IDE 中跟踪一次仓库创建到 Facet 装配的完整调用栈。

```
// 调用栈（仓库创建操作的完整链路）

1. POST /service/rest/v1/repositories/maven/hosted
   → RepositoryResource.createRepository()
       ↓
2. RepositoryManager.create(repoConfiguration)
   → 根据 configuration.getFormat() + configuration.getType() 查找匹配的 Recipe
       ↓
3. Recipe.apply(repository)
   → 遍历 Facet 工厂列表，逐个 repository.attach(facet)
       ↓
4. repository.attach(storageFacet)
   → 每个 Facet 通过 @Inject 注入其依赖（如 StorageFacet 需要 BlobStore）
       ↓
5. Repository 状态变为 "ready" → 可接受 HTTP 请求
```

**验证**（通过 IDE 断点调试）：

```bash
# 在 IDE 中设置断点位置
# 1. RepositoryResource.createRepository() — HTTP 入口
# 2. RepositoryManagerImpl.create() — 查找 Recipe
# 3. Maven2HostedRecipe.apply() — Facet 装配
# 4. RepositoryImpl.attach() — 单个 Facet 绑定

# 启动 Nexus dev 模式：
# cd assemblies/nexus-base-template
# mvn -Pdebug
# 在 IDE 连接远程调试端口（默认 5005）
# 触发创建仓库操作 → 观察断点流程
```

#### 步骤四：设计一个简化版的新格式 Facet 拆解图

**目标**：以 Raw 格式为参考，画出每种格式的"能力拆解图"。

```
Raw Hosted 仓库:
┌──────────────┐
│  Repository  │ (舞台)
└──────┬───────┘
       │ attach
  ┌────┴────┬──────────┬──────────┐
  │Storage  │ Security │  Browse  │ (核心 Facet)
  │Facet    │ Facet    │  Facet   │
  └─────────┴──────────┴──────────┘

Maven Hosted 仓库 (扩展 Raw):
┌──────────────┐
│  Repository  │
└──────┬───────┘
       │ attach
  ┌────┴────┬──────────┬──────────┬──────────────┬──────────────┬──────────┐
  │Storage  │ Security │  Browse  │ MavenHosted  │MavenMetadata │  Search  │ (Maven 特有 Facet)
  │Facet    │ Facet    │  Facet   │    Facet     │    Facet     │  Facet   │
  └─────────┴──────────┴──────────┴──────────────┴──────────────┴──────────┘
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Recipe 和 Type 不匹配 | 新 Recipe 创建后仓库创建失败 | Recipe 的 `@Named("hosted")` 参数必须与请求中的 `type` 完全一致 |
| Facet 未注册到 Repository | `repository.facet(Facet.class)` 返回 `null` | 确保在 `Recipe.apply()` 中调用了 `repository.attach(facet)` |
| 新格式名未被识别 | 创建仓库时 IDE 报不支持此 format | Format 类需加 `@Named` 注解并被 Sisu 扫描（在 `META-INF/sisu` 中注册） |
| 共享 Facet 的状态冲突 | 不同格式的仓库共享同一个 Facet 实例 | Facet 应标注 `@Singleton` 但无状态——如果 Facet 有状态，必须确保线程安全 |

## 4. 项目总结

### 4.1 Format/Recipe/Facet 角色速查

| 概念 | 类比 | 职责 | 是否格式相关 | 数量 |
|------|------|------|------------|------|
| Format | 语言/标准 | 标识格式名称 | 是 | 每格式一个 |
| Recipe | 配料表 | 声明 type × format 需要哪些 Facet | 是 | 每格式 × 每 type 一个 |
| Facet | 角色/能力 | 实现仓库某个维度的具体逻辑 | 部分通用 | 每棵"能力树"一个 |
| Repository | 舞台 | 持有配置 + 聚合 Facet | 否 | 运行时实例 |
| Type | 种类 | hosted/proxy/group | 否 | 3 种 |

### 4.2 适用场景

1. **新增格式支持**：按"最少 5 类"范式（Format + HostedRecipe + HostedFacet + ProxyRecipe + ProxyFacet）实现
2. **定制现有格式行为**：覆盖或扩展某个 Facet（如扩展 `MavenHostedFacet` 增加自定义校验）
3. **性能分析**：找出哪些 Facet 在某个路径上是瓶颈
4. **架构学习**：Reciple/Facet 是典型的"组合优于继承"设计模式的工程实践
5. **Facet 级别的 A/B 测试**：同一格式的不同 Recipe 装配不同 Facet 组合

**不适用场景**：
1. 只是修改 UI 展示——应在 `nexus-coreui-plugin` 中改，与 Facet 无关
2. 修改全局配置参数——走 `nexus.properties` 而非 Facet

### 4.3 注意事项

- **Facet 之间可以有依赖**：`MavenHostedFacet` 依赖 `StorageFacet`——通过 `repository.facet(StorageFacet.class)` 获取，而不是直接 `@Inject`
- **Facet 方法的线程安全性**：同一个仓库可能被多个并发请求访问，Facet 实现需保证线程安全
- **不要滥用 Facet**：如果一个逻辑只在一个 HTTP 端点使用，应该放在 Resource 类中，只有当逻辑被多个上层组件复用时才抽取为 Facet
- **OSS 版可以在已有格式基础上扩展 Facet**：但不能修改 framework 级的核心类

### 4.4 思考题

1. 假设需要新增一种"只读 Maven 仓库"——该仓库的行为和普通 Maven hosted 完全一样，但禁止所有上传和删除操作。利用 Facet 模式，如何用最少的新增代码实现？能否在不创建新的 Recipe 和 Facet 的情况下做到？
2. 如果 Raw 格式在某个版本中突然需要支持"上传时自动解压 tar.gz 并将内容物展开为独立的 asset"——例如上传 `models/v1.tar.gz` 后 Nexus 自动创建 `models/v1/model.bin` 和 `models/v1/config.json` 两个 asset。这个逻辑应该放在哪个 Facet 中？是扩展现有 Facet 还是新建？

（第32章思考题答案：1. Sisu 扫描机制：Sisu 的 `SpaceModule` 在类路径中扫描 `META-INF/sisu/javax.inject.Named` 文件——这是一个纯文本文件，每行一个包含 `@Named` 注解的类的全限定名。Maven 构建时由 `sisu-maven-plugin` 自动生成该文件。运行时，Sisu 读取该文件，通过反射加载每个类，检查其注解（`@Named`、`@Singleton`、`@Inject`），构建组件注册表。当一个类（如 `UploadResource`）通过 `@Inject` 声明需要 `UploadManager` 时，Guice 从注册表中找到实现了该接口且标注了 `@Named` 的组件并实例化。2. 热部署依赖 OSGi 的 Bundle Lifecycle（INSTALLED→RESOLVED→STARTING→ACTIVE→STOPPING→UNINSTALLED）。核心机制是每个 bundle 有独立的 ClassLoader——加载时不需要重启其他 bundle。限制包括：如果一个 bundle 升级后改变了 `Export-Package` 的接口签名，依赖它的所有 bundle 必须同时升级；`@Singleton` 组件的状态在热部署时会丢失（需要重新初始化）；Karaf 的 `features:install` 只安装新的 bundle，但不自动卸载旧版本的同名 bundle，需要手动 `uninstall`。）

### 4.5 推广计划提示

- **核心开发/插件开发者**：本章是开发格式插件的必读材料。建议从头实现一个简化版 Raw 克隆格式来验证理解
- **架构师**：将 Format/Recipe/Facet 模式作为技术评审的参考——任何需要"多格式扩展"的系统都可以借鉴
- **代码审查者**：当有人提交新格式 PR 时，用本章的"最少 5 类"标准评估其实现是否正确利用了框架
