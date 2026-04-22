# 第 26 章：IO 工具（ByteSource/CharSource/Files）工程实践

## 1 项目背景

在日志分析系统的文件处理模块中，工程师小郑需要处理大量日志文件。传统 Java IO 代码冗长，资源关闭容易遗漏，大文件处理时内存溢出。需要一种更简洁、安全的 IO 处理方式。

## 2 项目设计

**小白**："Guava IO 工具封装了常见操作，自动处理资源关闭：

```java
// 读取文件为字符串
String content = Files.asCharSource(file, Charset.UTF_8).read();

// 行级处理
Files.asCharSource(file, Charset.UTF_8).readLines(line -> {
    process(line);
});

// 字节处理
ByteSource source = Files.asByteSource(file);
byte[] bytes = source.read();
HashCode hash = source.hash(Hashing.md5());
```

**技术映射**：Guava IO 就像是'带自动关门的文件柜'——用完自动关门，不用担心遗忘。"

## 3 项目实战

```java
public class LogProcessor {
    
    // 安全读取大文件（流式处理）
    public void processLargeFile(File file) throws IOException {
        CharSource source = Files.asCharSource(file, StandardCharsets.UTF_8);
        
        try (Stream<String> lines = source.lines()) {
            lines.filter(line -> line.contains("ERROR"))
                 .limit(1000)  // 只处理前 1000 条错误
                 .forEach(this::processError);
        }
    }
    
    // 文件复制
    public void copyFile(File src, File dst) throws IOException {
        Files.copy(src, dst);
    }
    
    // 计算文件 MD5
    public String fileMd5(File file) throws IOException {
        return Files.asByteSource(file)
                   .hash(Hashing.md5())
                   .toString();
    }
    
    // 写入文件
    public void writeLines(File file, List<String> lines) throws IOException {
        Files.asCharSink(file, StandardCharsets.UTF_8).writeLines(lines);
    }
    
    // 递归遍历目录
    public List<File> listLogFiles(File dir) {
        return new ArrayList<>(Files.fileTreeTraverser().breadthFirstTraversal(dir)
            .filter(f -> f.getName().endsWith(".log"))
            .toList());
    }
}
```

## 4 项目总结

### 核心工具

| 工具 | 用途 |
|------|------|
| `ByteSource` | 字节级读取 |
| `CharSource` | 字符级读取 |
| `ByteSink` | 字节级写入 |
| `CharSink` | 字符级写入 |
| `Files` | 文件操作 |
| `Closer` | 资源关闭管理 |

### 优势

1. 资源自动关闭
2. 方法链流畅
3. 支持大文件流式处理
