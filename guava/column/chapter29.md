# 第 29 章：兼容性与版本升级策略（JDK/Android/Guava）

## 1 项目背景

在技术栈升级项目中，架构师小郑面临复杂的兼容性问题。团队需要从 Guava 20 升级到 33，同时支持 JDK 8 和 11 两个版本，还有 Android 子项目依赖 Guava。升级过程中遇到了 API 废弃、行为变更、依赖冲突等问题。

## 2 项目设计

**小白**："Guava 升级要注意废弃 API 和版本差异：

```java
// Guava 21+ 移除的方法替代方案
// Futures.transform -> Futures.transform (泛型变化)
// Objects.toStringHelper -> MoreObjects.toStringHelper
// Stopwatch.createStarted() -> Stopwatch.createStarted()
```

**大师**："版本选择矩阵：

```
JDK 8+   -> guava-33.0-jre
JDK 7    -> guava-23.6-jre (最后一版支持 JDK7)
Android  -> guava-33.0-android (精简版)
```

**技术映射**：版本管理就像是'交通规则'——不同环境（城市）有不同规则（版本），需要按规则行驶。"

## 3 项目实战

```java
// 升级检查清单
public class MigrationGuide {
    
    // 1. 检查废弃 API
    // 使用 Modernizer 插件扫描
    
    // 2. 处理 Breaking Changes
    // Guava 21: 移除 Futures.immediateFailedFuture 部分用法
    // 替代：Futures.immediateFailedFuture(Throwable)
    
    // 3. 依赖冲突解决
    // Maven 排除传递依赖
    // <exclusion>
    //     <groupId>com.google.guava</groupId>
    //     <artifactId>guava</artifactId>
    // </exclusion>
    
    // 4. 多版本共存（不推荐，但可行）
    // 使用 Shade 插件重命名包
}

// 兼容性封装
public class CompatibleGuava {
    // 封装常用操作，隔离版本差异
    public static <T> T firstNonNull(T first, T second) {
        return MoreObjects.firstNonNull(first, second);
    }
}
```

## 4 项目总结

### 版本升级步骤

1. 检查废弃 API 清单
2. 更新依赖版本
3. 修复编译错误
4. 运行回归测试
5. 灰度发布验证

### 兼容性策略

| 场景 | 策略 |
|------|------|
| 单机应用 | 直接升级 |
| 多模块项目 | 统一版本管理 |
| 库项目 | 保持向后兼容 |
| Android | 使用 android 分支 |
