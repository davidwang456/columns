/*
 * 第 3 章示例：Topic / Partition / 有序性边界
 * 运行（在 examples 目录）：mvn -q compile exec:java
 */
package org.example.column.ch03;

import java.time.Duration;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.ExecutionException;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

public final class PartitionOrderingDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC = "column.ch03.orders";
    private static final int PARTITIONS = 3;

    public static void main(String[] args) throws ExecutionException, InterruptedException {
        createTopicIfNeeded();

        Map<String, Integer> keyToPartition = new HashMap<>();

        Properties producerProps = new Properties();
        producerProps.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        producerProps.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        producerProps.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        producerProps.put(ProducerConfig.ACKS_CONFIG, "all");

        try (KafkaProducer<String, String> producer = new KafkaProducer<>(producerProps)) {
            String fixedKey = "user-42";
            for (int i = 0; i < 5; i++) {
                ProducerRecord<String, String> rec =
                    new ProducerRecord<>(TOPIC, fixedKey, "order-" + i);
                var md = producer.send(rec).get();
                keyToPartition.put("fixed:" + i, md.partition());
            }
            for (int i = 0; i < 5; i++) {
                String randomKey = "sess-" + UUID.randomUUID();
                ProducerRecord<String, String> rec =
                    new ProducerRecord<>(TOPIC, randomKey, "evt-" + i);
                var md = producer.send(rec).get();
                keyToPartition.put("random:" + i, md.partition());
            }
        }

        System.out.println("--- 发送结果：分区号（同一固定 key 应落在同一分区） ---");
        keyToPartition.forEach((k, p) -> System.out.println(k + " -> partition " + p));

        Properties consumerProps = new Properties();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG, "column-ch03-demo-" + UUID.randomUUID());
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(consumerProps)) {
            consumer.subscribe(Collections.singletonList(TOPIC));
            int total = 0;
            long deadline = System.currentTimeMillis() + 15_000;
            while (total < 10 && System.currentTimeMillis() < deadline) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
                for (ConsumerRecord<String, String> r : records) {
                    System.out.printf(
                        "消费: partition=%d offset=%d key=%s value=%s%n",
                        r.partition(), r.offset(), r.key(), r.value());
                    total++;
                }
            }
        }
    }

    private static void createTopicIfNeeded() throws ExecutionException, InterruptedException {
        Properties adminProps = new Properties();
        adminProps.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(adminProps)) {
            if (!admin.listTopics().names().get().contains(TOPIC)) {
                admin.createTopics(List.of(new NewTopic(TOPIC, PARTITIONS, (short) 1))).all().get();
                System.out.println("已创建 topic: " + TOPIC + " partitions=" + PARTITIONS);
            }
        }
    }

    private PartitionOrderingDemo() {
    }
}
