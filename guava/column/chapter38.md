# 第 38 章：弃用 API 迁移与大规模代码改造策略

## 1 项目背景

在技术债治理项目中，架构师小郑需要迁移 50 万行代码中废弃的 Guava API。`Futures.get` 废弃、`Objects.firstNonNull` 迁移，需要自动化工具支持。

## 2 项目设计

**大师**："迁移策略：

```
1. 扫描识别：AST 分析找出废弃 API 使用
2. 自动替换：脚本批量替换简单场景
3. 人工审核：复杂场景人工确认
4. 回归测试：全量测试验证
5. 灰度发布：分批次上线
```

**技术映射**：API 迁移就像是'城市改造'——既要换新设施，又不能中断居民生活。"

## 3 项目实战

```java
// 常见迁移映射表
public class MigrationGuide {
    
    // Futures.transform(ListenableFuture, Function) 
    // -> Futures.transform(ListenableFuture, Function, Executor)
    
    // Objects.firstNonNull(a, b) 
    // -> MoreObjects.firstNonNull(a, b)
    
    // Objects.toStringHelper(class)
    // -> MoreObjects.toStringHelper(class)
    
    // Throwables.propagate(e)
    // -> throw new RuntimeException(e) 或特定异常
}

// OpenRewrite 配方示例（自动迁移工具）
public class GuavaMigrationRecipe {
    /*
    rewrite:
      recipe:
        - org.openrewrite.java.guava.GuavaToJavaUtil
        - com.company.custom.Guava33Migration
      
      规则示例：
      - methodPattern: com.google.common.base.Objects firstNonNull(..)
        replacement: com.google.common.base.MoreObjects firstNonNull(..)
    */
}

// 渐进式迁移策略
public class IncrementalMigration {
    
    // 阶段 1：新增代码使用新 API
    // 阶段 2：旧模块改造（按优先级）
    // 阶段 3：全量回归测试
    // 阶段 4：废弃 API 屏蔽（编译错误）
    
    // 兼容性桥接
    public class CompatibilityBridge {
        @Deprecated
        public static <T> T firstNonNull(T a, T b) {
            return MoreObjects.firstNonNull(a, b);
        }
    }
}
```

## 4 项目总结

### 迁移检查清单

- [ ] 扫描废弃 API 使用
- [ ] 制定替换映射表
- [ ] 自动化工具配置
- [ ] 分批次执行计划
- [ ] 回归测试覆盖
- [ ] 上线验证监控

### 工具推荐

1. **OpenRewrite**：自动化代码重构
2. **ErrorProne**：编译期废弃检查
3. **ArchUnit**：架构规则测试
