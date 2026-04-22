# 第 37 章：反射工具（TypeToken/Invokable）与泛型陷阱

## 1 项目背景

在通用框架开发中，工程师小冯需要处理复杂的泛型反射场景。JSON 序列化时需要获取泛型参数的实际类型，传统反射代码冗长且容易出错。

## 2 项目设计

**大师**：`TypeToken` 捕获泛型信息：

```java
// 解决类型擦除
TypeToken<List<String>> token = new TypeToken<List<String>>() {};
Type type = token.getType();  // java.util.List<java.lang.String>

// 获取泛型参数
TypeToken<Map<String, Integer>> mapToken = new TypeToken<Map<String, Integer>>() {};
ImmutableList<TypeToken<?>> params = mapToken.getTypeArguments();
// [String, Integer]
```

**技术映射**：`TypeToken` 就像是'类型透视镜'——让运行时被擦除的泛型信息重现。"

## 3 项目实战

```java
public class GenericJsonParser {
    
    public <T> T parse(String json, TypeToken<T> typeToken) {
        // 根据 typeToken 获取实际类型信息
        Type type = typeToken.getType();
        return gson.fromJson(json, type);
    }
    
    // 使用示例
    public void demo() {
        String json = "[{\"name\":\"test\"}]";
        
        // 正确解析 List<User>
        List<User> users = parse(json, new TypeToken<List<User>>() {});
    }
}

// Invokable：方法/构造器抽象
public class MethodInvoker {
    public Object invoke(Method method, Object target, Object... args) {
        Invokable<?, Object> invokable = Invokable.from(method);
        
        // 检查可见性
        if (!invokable.isAccessible()) {
            invokable.setAccessible(true);
        }
        
        // 获取参数类型
        ImmutableList<Parameter> params = invokable.getParameters();
        
        try {
            return invokable.invoke(target, args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }
}
```

## 4 项目总结

### 泛型反射工具

| 工具 | 用途 |
|------|------|
| `TypeToken` | 捕获/传递泛型类型 |
| `Invokable` | 方法/构造器封装 |
| `Parameter` | 参数信息获取 |
| `TypeResolver` | 类型变量解析 |

### 常见陷阱

1. 匿名类创建 TypeToken（必须带 `{}`）
2. 嵌套泛型类型解析错误
3. 通配符类型处理
