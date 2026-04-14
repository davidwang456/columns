/*
 * 第 5 章示例：ProducerRecord、Key、Headers 与基础 Producer 配置
 * 运行：mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch05.ProducerBasicsDemo
 */
package org.example.column.ch05;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.ExecutionException;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.internals.RecordHeader;
import org.apache.kafka.common.serialization.StringSerializer;

public final class ProducerBasicsDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC = "column.ch05.events";

    public static void main(String[] args) throws Exception {
        createTopicIfNeeded();

        Properties props = new Properties();
        props.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        props.put(ProducerConfig.ACKS_CONFIG, "all");
        // 入门显式写出，便于与第 13 章调参对照
        props.put(ProducerConfig.LINGER_MS_CONFIG, 0);
        props.put(ProducerConfig.BATCH_SIZE_CONFIG, 16_384);

        try (KafkaProducer<String, String> producer = new KafkaProducer<>(props)) {
            sendWithKeyAndHeaders(producer);
            sendKeyNullSticky(producer);
        }
    }

    private static void createTopicIfNeeded() throws ExecutionException, InterruptedException {
        Properties adminProps = new Properties();
        adminProps.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(adminProps)) {
            if (!admin.listTopics().names().get().contains(TOPIC)) {
                admin.createTopics(List.of(new NewTopic(TOPIC, 3, (short) 1))).all().get();
                System.out.println("已创建 topic: " + TOPIC + " partitions=3 replicationFactor=1\n");
            }
        }
    }

    private static void sendWithKeyAndHeaders(KafkaProducer<String, String> producer) throws Exception {
        System.out.println("=== 1) 带 Key + Headers（trace-id、content-type）===");
        String key = "order-" + UUID.randomUUID();
        ProducerRecord<String, String> rec = new ProducerRecord<>(TOPIC, null, key, "{\"amt\":100}", List.of(
            new RecordHeader("trace-id", "tr-abc-001".getBytes(StandardCharsets.UTF_8)),
            new RecordHeader("content-type", "application/json".getBytes(StandardCharsets.UTF_8))));
        var md = producer.send(rec).get();
        System.out.printf(
            "partition=%d offset=%d key=%s headers=%s%n",
            md.partition(),
            md.offset(),
            key,
            "trace-id, content-type");
    }

    private static void sendKeyNullSticky(KafkaProducer<String, String> producer) throws Exception {
        System.out.println("\n=== 2) Key=null 的多条消息（默认分区器：轮询/粘性分区，与第 3 章衔接）===");
        for (int i = 0; i < 4; i++) {
            ProducerRecord<String, String> rec = new ProducerRecord<>(TOPIC, null, "heartbeat-" + i);
            var md = producer.send(rec).get();
            System.out.printf("partition=%d offset=%d value=%s%n", md.partition(), md.offset(), rec.value());
        }
    }

    private ProducerBasicsDemo() {
    }
}
