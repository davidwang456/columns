/*
 * 第 4 章示例：副本因子、Leader/ISR 与「副本≠并行通道」
 * 运行：mvn -q compile exec:java -Dexec.mainClass=org.example.column.ch04.ReplicationBasicsDemo
 */
package org.example.column.ch04;

import java.util.Collections;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.ExecutionException;

import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.Config;
import org.apache.kafka.clients.admin.ConfigEntry;
import org.apache.kafka.clients.admin.DescribeConfigsResult;
import org.apache.kafka.clients.admin.DescribeTopicsResult;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.admin.TopicDescription;
import org.apache.kafka.common.config.ConfigResource;
import org.apache.kafka.common.errors.InvalidReplicationFactorException;

public final class ReplicationBasicsDemo {

    private static final String BOOTSTRAP = "localhost:9092";
    private static final String TOPIC_TRY_RF2 = "column.ch04.try-rf2";
    private static final String TOPIC_OK = "column.ch04.replication";

    public static void main(String[] args) throws Exception {
        Properties adminProps = new Properties();
        adminProps.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, BOOTSTRAP);

        try (AdminClient admin = AdminClient.create(adminProps)) {
            demonstrateRf2FailsOnSingleBroker(admin);
            ensureTopicRf1(admin);
            describeLeaderAndIsr(admin);
            describeMinInSyncReplicas(admin);
        }
    }

    /** 单 Broker 上请求 RF=2，预期失败，用于建立「副本占用 Broker 槽位」的直觉。 */
    private static void demonstrateRf2FailsOnSingleBroker(AdminClient admin) {
        System.out.println("=== 1) 尝试创建 RF=2 的 Topic（单节点集群预期失败）===");
        try {
            admin.createTopics(List.of(new NewTopic(TOPIC_TRY_RF2, 1, (short) 2))).all().get();
            System.out.println("意外成功：请确认是否多 Broker 环境。");
        } catch (ExecutionException e) {
            Throwable c = e.getCause() == null ? e : e.getCause();
            if (c instanceof InvalidReplicationFactorException) {
                System.out.println("预期异常 InvalidReplicationFactorException: " + c.getMessage());
            } else {
                System.out.println("失败原因（可能仍是副本数与 Broker 数不匹配）: " + c);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static void ensureTopicRf1(AdminClient admin) throws Exception {
        var names = admin.listTopics().names().get();
        if (!names.contains(TOPIC_OK)) {
            admin.createTopics(List.of(new NewTopic(TOPIC_OK, 3, (short) 1))).all().get();
            System.out.println("已创建 topic: " + TOPIC_OK + " partitions=3 replicationFactor=1");
        }
    }

    private static void describeLeaderAndIsr(AdminClient admin) throws Exception {
        System.out.println("\n=== 2) Describe：各分区的 Leader、Replicas、ISR ===");
        DescribeTopicsResult tr = admin.describeTopics(Collections.singleton(TOPIC_OK));
        TopicDescription td = tr.topicNameValues().get(TOPIC_OK).get();
        td.partitions().forEach(tp -> {
            System.out.printf(
                "partition=%d leader=%d replicas=%s isr=%s%n",
                tp.partition(),
                tp.leader().id(),
                tp.replicas().stream().map(n -> String.valueOf(n.id())).toList(),
                tp.isr().stream().map(n -> String.valueOf(n.id())).toList());
        });
        System.out.println("说明：单 Broker 时 replicas/isr 通常只含同一 broker id；多副本时 ISR ⊆ Replicas。");
    }

    private static void describeMinInSyncReplicas(AdminClient admin) throws Exception {
        System.out.println("\n=== 3) Topic 配置：min.insync.replicas（与 acks=all 配合，见第 14 章）===");
        ConfigResource resource = new ConfigResource(ConfigResource.Type.TOPIC, TOPIC_OK);
        DescribeConfigsResult cr = admin.describeConfigs(Collections.singleton(resource));
        Config cfg = cr.values().get(resource).get();
        for (ConfigEntry e : cfg.entries()) {
            if ("min.insync.replicas".equals(e.name()) || "retention.ms".equals(e.name())) {
                System.out.println(e.name() + " = " + e.value() + " (source=" + e.source() + ")");
            }
        }
        System.out.println(
            "生产建议（多副本集群）：通常 RF=3 时设 min.insync.replicas=2，在耐久与可用之间折中；"
                + "单副本 Topic 仅能 min.insync.replicas=1。");
    }

    private ReplicationBasicsDemo() {
    }
}
