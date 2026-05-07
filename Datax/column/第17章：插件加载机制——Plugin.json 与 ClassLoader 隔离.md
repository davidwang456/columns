# 第17章：插件加载机制——Plugin.json 与 ClassLoader 隔离

## 1. 项目背景

数据平台组的小吴开发了一个自定义的 Kafka Reader 插件，在本地 IntelliJ IDEA 中运行一切正常。但打包部署到生产 DataX 目录后，任务执行到一半抛出诡异的 `NoSuchMethodError`：

```
java.lang.NoSuchMethodError: com.google.common.collect.ImmutableMap.of(
    Ljava/lang/Object;Ljava/lang/Object;)Lcom/google/common/collect/ImmutableMap;
```

小吴排查发现——他的 Kafka Reader 依赖了 Guava 30.0，而 DataX 自带的 hdfsreader 依赖了 Guava 20.0。两个 JAR 都被放到了 classpath 上，JVM 先加载了旧版本的 Guava，导致新版本的 API 调用失败。

这让他困惑——DataX 不是有 ClassLoader 隔离吗？为什么还会冲突？经过深入排查，他发现 ClassLoader 隔离是"按插件类型"（reader/writer）隔离的，但"按单个插件"并没有隔离——同一类型下的多个插件（kafkareader + hdfsreader）共享一个 ClassLoader，依赖冲突依然会发生。

本章带你从 LoadUtil 出发，深入理解 DataX 插件体系的 ClassLoader 设计——它能解决什么问题、不能解决什么问题、边界在哪里。

## 2. 项目设计——剧本式交锋对话

**（小吴工位旁，一块白板上画着 ClassLoader 树）**

**小吴**：（沮丧）我以为 ClassLoader 隔离就像 Docker 容器——每个插件一个"容器"，互不干扰。结果发现一堆 reader 插件共享一个 ClassLoader！

**大师**：（在白板上画了三个 ClassLoader）你理解偏了。DataX 的 ClassLoader 隔离粒度不是"每个插件"，而是"每种插件类型"。也就是说：

```
类加载器树:
  AppClassLoader (系统类加载器)
    ├── ReaderClassLoader (所有 Reader 插件共享)
    │     ├── mysqlreader.jar
    │     ├── hdfsreader.jar
    │     ├── kafkareader.jar  ← 和上面两个共享同一个 ClassLoader
    └── WriterClassLoader (所有 Writer 插件共享)
          ├── mysqlwriter.jar
          └── hdfswriter.jar
```

**技术映射**：ClassLoader 隔离 = 大学宿舍。AppClassLoader 是学校大门（公共类），ReaderClassLoader 是 A 宿舍楼（Reader 们住一起），WriterClassLoader 是 B 宿舍楼（Writer 们住一起）。同宿舍楼的室友能互相串门（共享依赖），但 A 楼和 B 楼之间不能直接串门。

**小胖**：（咬着一根冰棍）那为啥不每个插件都独立一个 ClassLoader？那样不就没冲突了？

**大师**：（笑）理论上可以，但代价很高。如果 30 个 Reader 插件各有自己的 ClassLoader，每个都加载一份 logback、fastjson、commons-lang——你有 30 份 logback 的 JAR 在内存里，浪费上百 MB。而且多个 ClassLoader 之间的对象传递需要序列化/反序列化，性能开销不可忽视。

**小白**：（追问）那为什么 Reader 和 Writer 之间要隔离？不都是加载 JAR 吗？

**大师**：因为 Reader 和 Writer 之间**存在数据交互**（通过 Record/Column 对象）。如果它们共享 ClassLoader，一个 Reader 传给 Writer 的 `StringColumn` 对象在两个 ClassLoader 中的 Class 定义必须一致，否则会抛 `ClassCastException`。通过隔离 Reader 和 Writer 的 ClassLoader，DataX 强制所有跨插件传递的对象必须来自公共 ClassLoader（AppClassLoader），而这正是 common 模块放的位置。

**小吴**：（突然明白）所以我的 Kafka Reader 和 hdfsreader 共享 ReaderClassLoader，但它们的 Guava 版本不一样——这就是问题根源！

**大师**：对。解决方案有三：
1. 升级 hdfsreader 的 Guava 到 30.0（影响面大，需要全量回归测试）
2. 降级 Kafka Reader 的 Guava 到 20.0（可能功能缺失）
3. 把 Kafka Reader 编译为"shade JAR"（用 maven-shade-plugin 把 Guava 重命名包路径打进 JAR），这样即使共享 ClassLoader，也不会冲突

生产环境通常用方案 3——最小的改动范围、最低的风险。

## 3. 项目实战

### 3.1 步骤一：追踪 LoadUtil 源码

**目标**：理解插件加载的完整流程。

打开 `core/src/main/java/com/alibaba/datax/core/util/container/LoadUtil.java`：

```java
public class LoadUtil {
    private static final String PLUGIN_BASE_PATH = "plugin/";
    private static final String PLUGIN_JSON = "plugin.json";
    
    // 加载指定类型的插件
    public static Class<?> loadPluginClass(PluginType pluginType, String pluginName) {
        // 1. 构建插件目录路径
        String pluginPath = PLUGIN_BASE_PATH + pluginType.toString() + "/" + pluginName + "/";
        // 如: plugin/reader/mysqlreader/
        
        // 2. 读取 plugin.json
        File pluginJsonFile = new File(pluginPath + PLUGIN_JSON);
        Configuration pluginConf = Configuration.from(new FileInputStream(pluginJsonFile));
        String className = pluginConf.getString("class");
        // 如: com.alibaba.datax.plugin.reader.mysqlreader.MysqlReader
        
        // 3. 获取该插件类型的 ClassLoader（所有同类型插件共享）
        URLClassLoader classLoader = getPluginClassLoader(pluginType);
        
        // 4. 反射加载类
        return classLoader.loadClass(className);
    }
    
    private static URLClassLoader getPluginClassLoader(PluginType pluginType) {
        // 单例模式：每种插件类型只有一个 ClassLoader
        if (readerClassLoader == null) {
            readerClassLoader = createClassLoader("plugin/reader/");
        }
        return readerClassLoader;
    }
    
    private static URLClassLoader createClassLoader(String basePath) {
        // 遍历 basePath 下所有子目录的 /libs/ 和 *.jar
        List<URL> urls = new ArrayList<>();
        File pluginDir = new File(basePath);
        for (File subDir : pluginDir.listFiles()) {
            if (subDir.isDirectory()) {
                // 加载子目录下的 JAR
                File libDir = new File(subDir, "libs");
                if (libDir.exists()) {
                    for (File jar : libDir.listFiles()) {
                        urls.add(jar.toURI().toURL());
                    }
                }
                // 加载子目录下的主 JAR
                for (File jar : subDir.listFiles()) {
                    if (jar.getName().endsWith(".jar")) {
                        urls.add(jar.toURI().toURL());
                    }
                }
            }
        }
        
        return new URLClassLoader(
            urls.toArray(new URL[0]),
            Thread.currentThread().getContextClassLoader() // 父加载器 = AppClassLoader
        );
    }
}
```

### 3.2 步骤二：验证 ClassLoader 隔离

**目标**：通过修改源码打印 ClassLoader 树，验证 Reader 和 Writer 确实使用不同 ClassLoader。

在 `LoadUtil.getPluginClassLoader()` 中加入日志：

```java
URLClassLoader loader = createClassLoader(basePath);
LOG.info("PluginType[{}] ClassLoader: {} -> parent: {}", 
    pluginType, loader, loader.getParent());
```

**类加载器链**：

```
PluginType[READER] ClassLoader: URLClassLoader@1234 -> parent: sun.misc.Launcher$AppClassLoader@5678
PluginType[WRITER] ClassLoader: URLClassLoader@9012 -> parent: sun.misc.Launcher$AppClassLoader@5678
```

**验证**：
1. Reader 和 Writer 的 ClassLoader 是两个不同的 URLClassLoader 实例 → 隔离成功
2. 两者的父 ClassLoader 都是 AppClassLoader → 都可见 common 模块的类
3. mysqlreader 和 hdfsreader 的类来自同一个 URLClassLoader → 不隔离

### 3.3 步骤三：验证依赖冲突场景

**目标**：模拟两个 Reader 插件的 Guava 版本冲突。

**场景构造**：
1. hdfsreader 的 libs 下有 `guava-20.0.jar`
2. 新建 kafkareader 的 libs 下有 `guava-30.0.jar`
3. kafkareader 代码中调用了 `ImmutableMap.of(k, v)`（两个参数版本——这是 Guava 30.0 新增的）

**运行**：

```
java.lang.NoSuchMethodError: com.google.common.collect.ImmutableMap.of(...)
    at com.alibaba.datax.plugin.reader.kafkareader.KafkaReader$Task.startRead(KafkaReader.java:89)
```

**原因分析**：ReaderClassLoader 加载 JAR 时按目录遍历顺序。如果 hdfsreader 的 guava-20.0.jar 先被扫描加入 classpath，JVM 会先从它加载 `ImmutableMap` 类。kafkareader 调用 `ImmutableMap.of(k, v)` 时，找到的是旧版本类——旧版本没有两个参数的重载方法 → NoSuchMethodError。

### 3.4 步骤四：用 maven-shade-plugin 解决冲突

**目标**：将 Kafka Reader 的依赖全部打包进 JAR（shade 方式），重命名包路径防止冲突。

**在 Kafka Reader 的 pom.xml 中**：

```xml
<build>
    <plugins>
        <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-shade-plugin</artifactId>
            <version>3.4.1</version>
            <executions>
                <execution>
                    <phase>package</phase>
                    <goals>
                        <goal>shade</goal>
                    </goals>
                    <configuration>
                        <!-- 将 Guava 重命名到 kafkareader 的独立命名空间 -->
                        <relocations>
                            <relocation>
                                <pattern>com.google.common</pattern>
                                <shadedPattern>com.alibaba.datax.plugin.reader.kafkareader.shaded.com.google.common</shadedPattern>
                            </relocation>
                            <relocation>
                                <pattern>com.google.gson</pattern>
                                <shadedPattern>com.alibaba.datax.plugin.reader.kafkareader.shaded.com.google.gson</shadedPattern>
                            </relocation>
                        </relocations>
                        <!-- 排除 DataX 公共模块（由父 ClassLoader 提供） -->
                        <artifactSet>
                            <excludes>
                                <exclude>com.alibaba.datax:datax-common</exclude>
                            </excludes>
                        </artifactSet>
                    </configuration>
                </execution>
            </executions>
        </plugin>
    </plugins>
</build>
```

**效果**：

```
# 编译前（guava-30.0 的类）
com.google.common.collect.ImmutableMap

# 编译后（类名不变，包名改了）
com.alibaba.datax.plugin.reader.kafkareader.shaded.com.google.common.collect.ImmutableMap
```

JVM 不会认为重命名后的 `ImmutableMap` 和 hdfsreader 的 `com.google.common.collect.ImmutableMap` 是同一个类——冲突消除。

### 3.5 步骤五：plugin.json 契约详解

**目标**：掌握 plugin.json 的完整字段规范。

```json
{
    "name": "mysqlreader",
    "class": "com.alibaba.datax.plugin.reader.mysqlreader.MysqlReader",
    "description": "从MySQL数据库读取数据",
    "developer": "alibaba"
}
```

| 字段 | 必填 | 说明 | 注意事项 |
|------|------|------|---------|
| `name` | 是 | 插件名称，与 JSON 中 `reader.name` 一致 | 全小写，不含空格 |
| `class` | 是 | Reader/Writer 实现类的全限定名 | 必须能从该插件的 JAR 中找到 |
| `description` | 否 | 插件描述 | 仅用于文档 |
| `developer` | 否 | 开发者 | 仅用于文档 |

**plugin.json 命名坑**：插件目录下必须有且仅有一个 `plugin.json` 文件。如果有两个 JSON 文件（如 `plugin.json` 和 `plugin-backup.json`），LoadUtil 只会读第一个匹配的文件。

### 3.6 可能遇到的坑及解决方法

**坑1：插件 JAR 没有放在 libs 目录**

有些开发者手动拷贝 JAR 到插件根目录而非 libs 子目录——JAR 能加载，但习惯上第三方依赖必须放 libs/。

**坑2：ClassLoader 泄漏导致 Metaspace OOM**

如果 DataX 进程长时间运行，重复的插件卸载和新插件加载会导致 Metaspace 中的 Class 不释放（ClassLoader 被线程持有无法 GC）。

解决：生产环境不要在同一个 DataX 进程中热加载插件，改为重启进程。

**坑3：shade 后 FastJSON 等序列化库出错**

`maven-shade-plugin` 可能破坏 `META-INF/services` 下的 SPI 描述文件。

解决：使用 `ServicesResourceTransformer` 合并 SPI 配置：

```xml
<transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer"/>
```

## 4. 项目总结

### 4.1 DataX ClassLoader 设计总结

| 特性 | 实现 |
|------|------|
| 隔离粒度 | 按插件类型（READER / WRITER） |
| 实现方式 | 每种类型一个 URLClassLoader |
| 父 ClassLoader | AppClassLoader（可见 common 模块） |
| 跨插件传递 | 通过 AppClassLoader 中的 common 接口（Record/Column） |
| 同类型插件 | 共享 ClassLoader → 依赖可能冲突 |
| 不同类型插件 | ClassLoader 隔离 → 对象传递需要 common 接口 |

### 4.2 优点

1. **类隔离不彻底但有边界**：Reader 和 Writer 之间明确隔离，防止跨类型污染
2. **内存友好**：不是每个插件独立 ClassLoader，避免 30 份 logback 在内存
3. **插件发现自动化**：`plugin.json` 声明式注册，LoadUtil 自动扫描加载
4. **开发体验好**：插件开发者只需关注 `plugin.json` 和 JAR 编译，无需配置复杂的类加载逻辑
5. **common 模块全局可见**：Record/Column 等核心类型所有插件共享，无需序列化

### 4.3 缺点

1. **同类型插件依赖冲突**：Reader 之间或 Writer 之间的 Guava/FastJSON 版本不一致会冲突
2. **无版本管理**：plugin.json 没有版本号字段，无法描述"本插件依赖 Guava 30.0"
3. **shade 方案有副作用**：重命名包路径后，部分反射调用和 SPI 机制会失效
4. **ClassLoader 泄漏**：长时间运行时卸载插件不会 GC 老 ClassLoader
5. **启动无校验**：LoadUtil 在 `init()` 阶段才加载插件类，如果类不存在要等到运行时才报错

### 4.4 插件开发的依赖管理最佳实践

1. **尽量使用 common 模块已有的依赖**（fastjson、commons-lang3、slf4j）
2. **必须引入的第三方依赖，评估版本**——和 DataX 现有插件的依赖版本对齐
3. **版本不对齐时，使用 maven-shade-plugin**——重命名包路径
4. **不要 shade 所有依赖**——排除 common 和 slf4j，减少 JAR 体积
5. **libs 下不要放过多 JAR**——只放第三方依赖，DataX 自有的依赖由父 ClassLoader 提供

### 4.5 思考题

1. 如果要在 DataX 中实现"每个插件独立 ClassLoader"——即 mysqlreader 和 hdfsreader 享受不同 ClassLoader，需要如何修改 LoadUtil？这样改会引入什么新问题？
2. maven-shade-plugin 重命名的包路径会导致什么问题？为什么重命名 FastJSON 后序列化/反序列化可能失败？

（答案见附录）
