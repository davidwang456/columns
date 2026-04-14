# 专栏示例说明

## 依赖

- Docker（建议 ≥ 20.10.4，见上游镜像文档）
- JDK 17 + Maven 3.9+（运行 Java 示例）

## 启动 Kafka

```bash
docker compose up -d
```

Bootstrap：`localhost:9092`（`PLAINTEXT_HOST`）。

## Java 构建

```bash
mvn -q compile
```

主类见各章引用；第 3 章示例：`org.example.column.ch03.PartitionOrderingDemo`。
