# Kafka 专栏（仓库内材料）

本目录为 **Kafka 专栏** 的正文、模板与示例代码，与 Apache Kafka 源码仓库同仓存放，便于对照阅读源码。

## 阅读路径

1. [CHAPTER_INDEX.md](CHAPTER_INDEX.md)：**38 章**完整索引（35 章正文 + 3 章扩展）。
2. [WRITING_SPEC.md](WRITING_SPEC.md)：写作规范与**冻结技术栈**（JDK 17、Kafka 3.7.0、KRaft 单节点）。
3. [templates/chapter-template.md](templates/chapter-template.md)：单章 Markdown 骨架。
4. [chapters/](chapters/)：各章独立文件（**第 1～38 章**均已落地正文；**第 7～38 章**由 [scripts/fill_remaining_chapters.py](scripts/fill_remaining_chapters.py) 生成骨架后可再人工润色案例与命令输出）。
5. [examples/](examples/)：本地 Broker 与 Java 客户端示例。
6. [rollout/](rollout/)：发布节奏、测试用例模板、运维告警映射。

## 本地环境

```bash
cd kafka-column/examples
docker compose up -d
```

验证 Broker：

```bash
docker exec -it kafka-column-broker /opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092
```

Java 示例（在 `examples` 目录；默认主类为第 3 章，其余章用 `-Dexec.mainClass=` 指定）：

```bash
mvn -q compile exec:java
mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch04.ReplicationBasicsDemo
mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch05.ProducerBasicsDemo
mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch06.ConsumerPollCommitDemo
mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch07.ConsumerGroupIntroDemo
mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch08.ConsumerOffsetsTopicIntroDemo
```

重新生成第 7～38 章正文（覆盖 `chapters/` 下各章 **第 7～38 章** 的 Markdown 文件）：

```bash
cd kafka-column/scripts
python fill_remaining_chapters.py
```

## 规范版本

- **1.0**：与专栏目录首次落地同步。
