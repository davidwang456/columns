/*
 * 第 8 章：消费位移与 __consumer_offsets（先消费再查组位移）
 * mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch08.ConsumerOffsetsTopicIntroDemo
 */
package org.example.column.ch08;

import java.time.Duration;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.ExecutionException;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.ListConsumerGroupOffsetsResult;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

public final class ConsumerOffsetsTopicIntroDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC = "column.ch08.offsets";

    public static void main(String[] args) throws Exception {
        ensureTopicAndSeed();

        String groupId = "column-ch08-" + UUID.randomUUID();

        Properties cprops = new Properties();
        cprops.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        cprops.put(ConsumerConfig.GROUP_ID_CONFIG, groupId);
        cprops.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "true");
        cprops.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(cprops)) {
            consumer.subscribe(Collections.singletonList(TOPIC));
            long deadline = System.currentTimeMillis() + 15_000;
            while (System.currentTimeMillis() < deadline) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
                if (!records.isEmpty()) {
                    records.forEach(
                        r -> System.out.printf("读到 partition=%d offset=%d%n", r.partition(), r.offset()));
                    break;
                }
            }
        }

        Properties ap = new Properties();
        ap.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(ap)) {
            ListConsumerGroupOffsetsResult r = admin.listConsumerGroupOffsets(groupId);
            Map<TopicPartition, OffsetAndMetadata> offsets = r.partitionsToOffsetAndMetadata().get();
            System.out.println("\n--- Admin 查询该组的已提交位移 ---");
            offsets.forEach((tp, om) -> System.out.printf("%s offset=%s%n", tp, om.offset()));
        }
    }

    private static void ensureTopicAndSeed() throws ExecutionException, InterruptedException, Exception {
        Properties p = new Properties();
        p.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(p)) {
            if (!admin.listTopics().names().get().contains(TOPIC)) {
                admin.createTopics(List.of(new NewTopic(TOPIC, 2, (short) 1))).all().get();
            }
        }
        Properties pp = new Properties();
        pp.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        pp.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        pp.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(pp)) {
            producer.send(new ProducerRecord<>(TOPIC, "seed", "hello-offsets")).get();
        }
    }

    private ConsumerOffsetsTopicIntroDemo() {
    }
}
