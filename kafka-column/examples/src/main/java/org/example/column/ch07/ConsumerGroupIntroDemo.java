/*
 * 第 7 章：同一 group.id 下分区分配（单进程演示；多进程扩容见正文）
 * mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch07.ConsumerGroupIntroDemo
 */
package org.example.column.ch07;

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
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

public final class ConsumerGroupIntroDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC = "column.ch07.group";
    public static void main(String[] args) throws Exception {
        ensureTopic();
        seedMessages();

        Properties cprops = new Properties();
        cprops.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        cprops.put(ConsumerConfig.GROUP_ID_CONFIG, "column-ch07-shared-group-" + UUID.randomUUID());
        cprops.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        cprops.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(cprops)) {
            consumer.subscribe(Collections.singletonList(TOPIC));
            int n = 0;
            long deadline = System.currentTimeMillis() + 15_000;
            while (n < 6 && System.currentTimeMillis() < deadline) {
                ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
                records.forEach(
                    r -> System.out.printf(
                        "partition=%d offset=%d value=%s%n", r.partition(), r.offset(), r.value()));
                n += records.count();
            }
            System.out.println("assignment=" + consumer.assignment());
        }
    }

    private static void ensureTopic() throws ExecutionException, InterruptedException {
        Properties p = new Properties();
        p.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        try (AdminClient admin = AdminClient.create(p)) {
            if (!admin.listTopics().names().get().contains(TOPIC)) {
                admin.createTopics(List.of(new NewTopic(TOPIC, 4, (short) 1))).all().get();
            }
        }
    }

    private static void seedMessages() throws Exception {
        Properties p = new Properties();
        p.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);
        p.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        p.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        try (KafkaProducer<String, String> producer = new KafkaProducer<>(p)) {
            for (int i = 0; i < 6; i++) {
                producer.send(new ProducerRecord<>(TOPIC, "x" + i, "v-" + i)).get();
            }
        }
    }

    private ConsumerGroupIntroDemo() {
    }
}
