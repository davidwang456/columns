/*
 * 第 6 章：poll、手动提交位移
 * mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch06.ConsumerPollCommitDemo
 */
package org.example.column.ch06;

import java.time.Duration;
import java.util.Collections;
import java.util.List;
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

public final class ConsumerPollCommitDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC = "column.ch06.poll";

    public static void main(String[] args) throws Exception {
        ensureTopic();
        seedMessages(5);

        Properties cprops = new Properties();
        cprops.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        cprops.put(ConsumerConfig.GROUP_ID_CONFIG, "column-ch06-" + UUID.randomUUID());
        cprops.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        cprops.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(cprops)) {
            consumer.subscribe(Collections.singletonList(TOPIC));
            int seen = 0;
            long deadline = System.currentTimeMillis() + 20_000;
            while (seen < 5 && System.currentTimeMillis() < deadline) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
                for (ConsumerRecord<String, String> r : records) {
                    System.out.printf(
                        "处理: partition=%d offset=%d value=%s%n",
                        r.partition(), r.offset(), r.value());
                    // 同步提交当前分区位移（演示用；生产常批量提交）
                    consumer.commitSync();
                    seen++;
                }
            }
            System.out.println("已手动 commitSync，位移已写入 __consumer_offsets（见第 8 章）。");
        }
    }

    private static void ensureTopic() throws ExecutionException, InterruptedException {
        Properties p = new Properties();
        p.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(p)) {
            if (!admin.listTopics().names().get().contains(TOPIC)) {
                admin.createTopics(List.of(new NewTopic(TOPIC, 2, (short) 1))).all().get();
            }
        }
    }

    private static void seedMessages(int n) {
        Properties p = new Properties();
        p.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        p.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        p.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(p)) {
            for (int i = 0; i < n; i++) {
                producer.send(new ProducerRecord<>(TOPIC, "k" + i, "msg-" + i)).get();
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private ConsumerPollCommitDemo() {
    }
}
