# 第5章：Gradle 项目扫描与 Android 场景

## 1. 项目背景

**业务场景**：某移动互联网公司同时维护一个后端 Kotlin 微服务（Gradle 构建）和一个 Android 客户端应用。Android 团队最近因为线上崩溃率上升（从 0.3% 升至 1.2%）被要求做代码质量提升。运维团队从崩溃日志中发现，80% 的崩溃来自空指针异常和资源未释放——这些都是静态分析工具可以事先捕获的问题。

与此同时，后端团队也在使用 Kotlin + Gradle 构建微服务，他们发现 Gradle 的多模块配置比 Maven 更灵活（通过 `build.gradle.kts` 的 Kotlin DSL），但这也导致 SonarQube 接入时的配置分散在各模块的构建脚本中，缺乏统一标准。

两个团队面临共同的挑战：如何在一个非 Maven 的构建体系下，高效接入 SonarQube，并覆盖 Kotlin 语言特性（如协程、data class、可空类型）的规则检查？Android 项目还需要额外处理 Lint 检查结果和不同 build variant（debug/release）下的扫描策略。

**痛点放大**：

```
Gradle 项目接入 SonarQube 的三个主要障碍：

1. 文档障碍：90% 的 SonarQube 教程针对 Maven 项目，Gradle 示例稀疏
2. Kotlin 障碍：Kotlin 特有语法（data class, sealed class, coroutine）
   的规则覆盖不足，需要额外配置
3. Android 障碍：buildTypes 和 productFlavors 导致源码路径复杂，
   覆盖率报告可能生成在 build/intermediates/ 的深层目录
```

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（打开 Android Studio，看着满屏的 build.gradle.kts）："大师，Maven 那套我搞懂了，但我们 Android 项目用的是 Gradle。`org.sonarqube` 这个 Gradle 插件怎么用？我搜了半天，发现不同博客给的配置完全不一样……"

**大师**："Gradle 生态的一个特点是配置方式演进快。从 Groovy DSL 到 Kotlin DSL，从 `apply plugin` 到 `plugins { }` 块，写法一直在变。先记住一个原则：在 SonarQube 的 Gradle 插件中，核心配置永远是这几个属性——`hostUrl`、`token`、`projectKey`、`projectName`，不管 DSL 怎么写，这几个不会变。"

**小白**（翻着 SonarQube 官方文档）："官方文档上写的 Gradle 插件 ID 是 `org.sonarqube`，版本是 5.x。我在 `plugins {}` 块里声明后，它到底干了什么？和 Maven 插件是一回事吗？"

**大师**："本质上一样——都是把 SonarScanner 的能力嵌入到构建工具中。Gradle 插件会注册一系列 task，其中最核心的是 `sonarqube` task。它做的事情：

1. 读取你的 Java/Kotlin 编译配置（源码路径、编译输出、classpath）
2. 读取测试配置和覆盖率报告路径
3. 调用 SonarScanner 引擎执行分析
4. 将结果上传到 SonarQube 服务器

一个关键区别：`gradle sonarqube` 会触发依赖的 task（如 `compileJava`、`compileKotlin`），但**不会自动运行测试**！这和 Maven 一样——你需要先执行 `gradle test`。"

**小胖**："那覆盖率呢？我 Android 项目用的是 `jacoco`，但 Android 的 `buildTypes` 有 debug 和 release，覆盖率报告应该用哪个 variant？"

**大师**："Android 项目的覆盖率通常只对 debug variant 有意义——因为 release 版本开了代码混淆和优化，覆盖率数据会失真。常规做法是：

```kotlin
// 在 app/build.gradle.kts 中
android {
    buildTypes {
        debug {
            isTestCoverageEnabled = true  // 仅 debug 启用覆盖率
        }
    }
}
```

执行测试时使用 debug variant：
```bash
./gradlew testDebugUnitTest       # 单元测试
./gradlew createDebugCoverageReport  # 生成覆盖率报告
```

然后配置 SonarQube 插件指向覆盖率报告路径。"

**小白**："Android 还有 Lint 检查。SonarQube 能读取 Android Lint 的结果吗？会不会和 SonarQube 自己的规则重复检查？"

**大师**："SonarQube 可以通过 `sonar.androidLint.reportPaths` 导入 Android Lint 的 XML 报告。至于重复——Android Lint 和 SonarQube 的规则有重叠（如资源泄露、硬编码字符串），但不完全重复。Android Lint 侧重 Android 特有的问题（如缺少 `contentDescription`、过度绘制），SonarQube 侧重通用代码质量问题（如 NPE、SQL 注入、复杂度）。

合理的策略是：两个都跑，但不要为重复规则浪费时间修两遍。如果发现明显重复，可以在 SonarQube 中停用对应的规则。"

**小胖**："那 Kotlin 呢？我们后端微服务是 Kotlin + Spring Boot + Gradle。Kotlin 的 data class、sealed class、coroutine 这些特性，SonarQube 能检查吗？"

**大师**："SonarQube 的 Kotlin 分析器（通过 SonarKotlin 插件）支持 Kotlin 的基础规则，包括空安全违规、data class 滥用、协程使用不当等。但覆盖率不如 Java 丰富——目前大约有 50+ 条 Kotlin 专用规则，而 Java 有 600+ 条。好消息是，Kotlin 编译器本身已经做了很多检查（空安全、智能类型转换），所以 SonarQube 主要聚焦在 Kotlin 特有的代码异味上。

另外提醒一点：Kotlin 项目必须配置 `sonar.java.binaries` 指向编译输出目录——即使项目没有 Java 代码。这是因为 Kotlin 编译后的字节码也需要被分析。"

**小胖**："Gradle 多模块项目的配置怎么组织？我有 domain、data、presentation 三个模块，每个都要配一遍 SonarQube 吗？"

**大师**："在根项目的 `build.gradle.kts` 中统一配置：

```kotlin
// 根项目 build.gradle.kts
plugins {
    id("org.sonarqube") version "5.1.0.4882" apply false
}

subprojects {
    apply(plugin = "org.sonarqube")
    sonarqube {
        properties {
            // 所有子模块共享的配置
        }
    }
}
```

需要个别覆盖的模块，在自己的 `build.gradle.kts` 中自定义。"

---

## 3. 项目实战

### 3.1 环境准备

- JDK 17+
- Gradle 8.x
- Android SDK（如果包含 Android 模块）
- SonarQube 10.7+ 实例
- 项目 Token

### 3.2 分步实现

**步骤 1：创建多层 Gradle 项目**

```bash
mkdir gradle-sonarqube-demo && cd gradle-sonarqube-demo
```

创建 `settings.gradle.kts`：

```kotlin
rootProject.name = "gradle-demo"
include(":app")
include(":library")
```

创建根项目 `build.gradle.kts`：

```kotlin
plugins {
    id("org.sonarqube") version "5.1.0.4882" apply false
    kotlin("jvm") version "1.9.23" apply false
}

allprojects {
    group = "com.example"
    version = "1.0.0"
}
```

**步骤 2：配置 library 子模块（纯 Kotlin）**

`library/build.gradle.kts`：

```kotlin
plugins {
    kotlin("jvm")
    id("org.sonarqube")
    jacoco
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    testImplementation(kotlin("test"))
}

sonarqube {
    properties {
        property("sonar.projectKey", "com.example:gradle-demo")
        property("sonar.projectName", "Gradle Demo Project")
        property("sonar.sources", "src/main/kotlin")
        property("sonar.tests", "src/test/kotlin")
        property("sonar.sourceEncoding", "UTF-8")
        property("sonar.host.url", "http://localhost:9000")
        property("sonar.token", System.getenv("SONAR_TOKEN"))
        // Kotlin 编译输出（必须）
        property("sonar.java.binaries",
            "${buildDir}/classes/kotlin/main")
        // JaCoCo 覆盖率报告
        property("sonar.coverage.jacoco.xmlReportPaths",
            "${buildDir}/reports/jacoco/test/jacocoTestReport.xml")
    }
}

tasks.test {
    useJUnitPlatform()
    finalizedBy(tasks.jacocoTestReport)
}

tasks.jacocoTestReport {
    dependsOn(tasks.test)
    reports {
        xml.required.set(true)
        csv.required.set(false)
        html.required.set(true)
    }
}
```

`library/src/main/kotlin/com/example/library/Calculator.kt`：

```kotlin
package com.example.library

import kotlin.math.sqrt

data class CalcResult(val input: Double, val output: Double)

class Calculator {
    // 潜在的分母为零
    fun divide(a: Double, b: Double): Double = a / b

    // 潜在的 MathDomainError（负数开方）
    fun sqrt(value: Double): Double {
        return sqrt(value) // 负数会返回 NaN，而非异常
    }

    // 代码异味：可为空的类型未做空检查
    fun process(input: CalcResult?): String {
        return input!!.output.toString() // 强制非空断言
    }

    // 复杂度过高
    fun complexScore(a: Int, b: Int, c: Int, d: Int): Int {
        return when {
            a > 0 && b > 0 -> 1
            a > 0 && b == 0 && c > 0 -> 2
            a > 0 && b == 0 && c == 0 -> 3
            a > 0 && b < 0 && d > 0 -> 4
            a == 0 -> 5
            else -> 0
        }
    }
}
```

`library/src/test/kotlin/com/example/library/CalculatorTest.kt`：

```kotlin
package com.example.library

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CalculatorTest {
    private val calc = Calculator()

    @Test
    fun `divide positive numbers`() {
        assertEquals(2.0, calc.divide(10.0, 5.0))
    }

    @Test
    fun `sqrt of perfect square`() {
        assertEquals(3.0, calc.sqrt(9.0))
    }
}
```

**步骤 3：配置 app 子模块（模拟 Android 风格，纯 Kotlin）**

`app/build.gradle.kts`：

```kotlin
plugins {
    kotlin("jvm")
    id("org.sonarqube")
    jacoco
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":library"))
    testImplementation(kotlin("test"))
}

sonarqube {
    properties {
        property("sonar.sources", "src/main/kotlin")
        property("sonar.tests", "src/test/kotlin")
        property("sonar.java.binaries",
            "${buildDir}/classes/kotlin/main")
        property("sonar.coverage.jacoco.xmlReportPaths",
            "${buildDir}/reports/jacoco/test/jacocoTestReport.xml")
    }
}

tasks.test {
    useJUnitPlatform()
    finalizedBy(tasks.jacocoTestReport)
}

tasks.jacocoTestReport {
    dependsOn(tasks.test)
    reports {
        xml.required.set(true)
    }
}
```

`app/src/main/kotlin/com/example/app/Main.kt`：

```kotlin
package com.example.app

import com.example.library.Calculator
import java.io.File

class AppService {
    // 硬编码的路径（安全问题）
    private val configPath = "/etc/app/config.properties"

    fun loadConfig(): String {
        // 路径遍历漏洞
        val file = File(configPath + "/../secrets.properties")
        return file.readText()
    }

    fun doCalculation(input: Double): String {
        val calc = Calculator()
        val result = calc.divide(100.0, input) // 除零风险
        val sqrtResult = calc.sqrt(input - 100) // 负数开方风险
        return "Result: $result, Sqrt: $sqrtResult"
    }
}
```

**步骤 4：执行扫描**

设置环境变量并执行：

```bash
export SONAR_TOKEN=squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 完整构建 + 测试 + 扫描
./gradlew clean test jacocoTestReport sonarqube
```

**执行日志关键输出：**

```
> Task :app:test
BUILD SUCCESSFUL in 5s
> Task :library:sonarqube
> Task :app:sonarqube

> SONARQUBE ANALYSIS COMPLETE
  You can browse the results at http://localhost:9000/dashboard?id=com.example:gradle-demo
```

**步骤 5：查看多模块结果**

访问 Web UI，`com.example:gradle-demo` 项目下会显示 library 和 app 两个模块的分析结果。

| 模块 | Bugs | Vulnerabilities | Code Smells |
|------|------|----------------|-------------|
| library | 🟡 1（除零） | 0 | 🟡 2（强制非空、过高复杂度） |
| app | 🟡 1（除零） | 🟡 1（路径遍历） | 🟡 1（硬编码路径） |

### 3.3 Android 项目专用配置

如果实际接入 Android 项目，需要额外配置：

```kotlin
// 在 app/build.gradle.kts 中
sonarqube {
    properties {
        // 仅扫描 debug variant
        property("sonar.androidLint.reportPaths",
           "${buildDir}/reports/lint-results-debug.xml")
        // Android 测试覆盖率
        property("sonar.coverage.jacoco.xmlReportPaths",
           "${buildDir}/reports/jacoco/jacocoTestDebugUnitTestReport/jacocoTestDebugUnitTestReport.xml")
        // 排除生成的代码
        property("sonar.exclusions",
           "**/BuildConfig.java,**/R.java,**/databinding/**")
    }
}
```

### 3.4 验证

```bash
# 查看全局覆盖面
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/measures/component?component=com.example:gradle-demo&metricKeys=coverage,ncloc,bugs,vulnerabilities,code_smells" \
  | python3 -m json.tool

# 按模块过滤 Issue
curl -s -u admin:Sonar@2024Admin \
  "http://localhost:9000/api/issues/search?componentKeys=com.example:gradle-demo:library&ps=3" \
  | python3 -m json.tool | grep -E '"message"|"severity"'
```

### 3.5 完整代码清单

```
gradle-sonarqube-demo/
├── build.gradle.kts              # 根项目（SonarQube 插件声明）
├── settings.gradle.kts           # 模块声明
├── gradle.properties             # Gradle 全局属性
├── app/
│   ├── build.gradle.kts          # app 子模块配置
│   └── src/
│       ├── main/kotlin/.../Main.kt
│       └── test/kotlin/...      # 测试代码
└── library/
    ├── build.gradle.kts          # library 子模块配置
    └── src/
        ├── main/kotlin/.../Calculator.kt
        └── test/kotlin/.../CalculatorTest.kt
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | Gradle 插件 | Maven 插件 |
|------|-----------|-----------|
| DSL 灵活性 | ✅ Kotlin DSL 类型安全，IDE 提示好 | ❌ XML 冗长 |
| Android 支持 | ✅ 原生集成 Android buildTypes | ❌ 不适用 |
| Kotlin 集成 | ✅ 和 kotlin-gradle-plugin 无缝配合 | ❌ Kotlin Maven 插件集成度低 |
| 多模块配置 | ✅ 可与 subprojects/allprojects 联动 | ✅ 父 POM 统一继承 |
| 文档丰富度 | ❌ 示例较少，踩坑经验少 | ✅ 文档和社区资源丰富 |
| 社区成熟度 | 🟡 插件功能足够但更新慢 | ✅ 几乎和 SonarQube 主版本同步 |

### 4.2 适用场景

- **Kotlin/JVM 项目**：后端微服务、库项目
- **Android 应用**：与 Android Lint 配合，覆盖移动端质量
- **混合 Java/Kotlin 项目**：Gradle 是多语言 JVM 项目首选

**不适用场景**：
- 非 JVM 语言项目（Python、Go、Node.js 等应该用 SonarScanner CLI）
- 纯粹的 Groovy 脚本项目（Groovy 的 SonarQube 支持非常有限）

### 4.3 注意事项

1. **`sonar.java.binaries` 对 Kotlin 项目也是必需的**：即使是纯 Kotlin 项目，SonarQube 也需要字节码来执行某些分析（如依赖检查、未使用代码检测）。
2. **Gradle 插件的版本选择**：`org.sonarqube` 插件版本号和 SonarQube 服务器版本不是一一对应的。一般来说，插件最新版兼容最近几个 SonarQube 主版本。如果遇到兼容性问题，查看插件的 [官方兼容性矩阵](https://docs.sonarsource.com/sonarqube/latest/analyzing-source-code/scanners/sonarqube-extension-for-gradle/)。
3. **Android 的 build variant**：只扫描 debug variant，因为 release variant 的代码混淆和优化会破坏分析准确性。
4. **排除生成的代码**：Android 项目中 `R.java`、`BuildConfig.java`、DataBinding 生成的代码必须通过 `sonar.exclusions` 排除，否则会产生大量误报。

### 4.4 常见踩坑经验

**故障 1：`gradle sonarqube` 报错 "Could not find method sonarqube()"**

根因：`org.sonarqube` 插件未正确应用。检查根项目 `build.gradle.kts` 中是否声明了插件且版本正确。多模块项目中必须在子模块中 `apply` 或 `id("org.sonarqube")`。

**故障 2：扫描成功但 Kotlin 代码的 Issue 很少甚至为 0**

根因：`sonar.java.binaries` 路径错误，指向了一个空目录或不存在的目录。Kotlin 编译输出默认在 `build/classes/kotlin/main/`，确认该目录存在 `.class` 文件。

**故障 3：覆盖率始终为 0%，但 `build/reports/jacoco/` 下存在报告**

根因：`jacocoTestReport` task 的 XML 输出路径与 SonarQube 配置的 `sonar.coverage.jacoco.xmlReportPaths` 不一致。用 `gradle jacocoTestReport --info` 确认报告实际生成位置。

### 4.5 思考题

1. Android 项目有 `debug`、`release`、`staging` 三个 buildType，每个都有不同的依赖和混淆规则。你应该扫描哪个 buildType？为什么？
2. Gradle 插件 `id("org.sonarqube")` 和 SonarScanner CLI 在分析 Kotlin 代码时，对 Kotlin 协程（suspend function）的分析能力有差异吗？为什么？

> **答案提示**：第1题核心是 debug variant 分析最准确（无混淆、有完整调试信息）。第2题两者使用同一个分析引擎，分析能力无差异，但 Gradle 插件能自动获取编译 classpath，可能发现更多跨模块问题。

---

> **推广计划提示**：Gradle 用户通常比 Maven 用户更需要参考示例代码——因为 DSL 变体多、社区文档少。建议将本章的 `build.gradle.kts` 模板抽象为团队的"标准接入模板"，放在团队内部 Gradle Plugin 中，让新项目"零配置接入"。Android 团队的接入需要测试和 QA 协作，确保 Lint 报告和覆盖率报告的路径在各 CI 环境中正确。
