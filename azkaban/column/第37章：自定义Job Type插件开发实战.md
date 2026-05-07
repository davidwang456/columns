# 第37章：自定义Job Type插件开发实战

## 1. 项目背景

### 业务场景

数据团队有一个高频需求：在ETL任务的每个关键步骤前后自动执行"数据快照"——快照包括记录当前Hive表的数据行数、分区数、数据大小等元信息。这个需求如果用command类型实现，需要在每个Job的脚本前后都加上快照逻辑，代码冗余且容易遗漏。

架构师建议：开发一个自定义Job Type——`snapshot`类型。任何Job只需声明`type=snapshot`并配置快照目标，就能自动完成快照，无需在业务脚本中嵌入快照逻辑。

### 痛点放大

没有自定义Job Type时：
- 公共逻辑重复嵌入在多个脚本中
- 每次调整快照格式需要修改所有脚本
- 新同学可能忘记加快照逻辑，导致数据缺失

## 2. 项目设计——剧本式交锋对话

**小胖**：大师，我想把"数据快照"功能做成一个Azkaban插件，怎么写？

**大师**：自定义Job Type本质上就是继承`AbstractProcessJob`并实现`run()`方法。我给一个完整模板：

```java
public class MyCustomJob extends AbstractProcessJob {
    public MyCustomJob(String jobId, Props sysProps, Props jobProps, Logger log) {
        super(jobId, sysProps, jobProps, log);
    }

    @Override
    public void run() throws Exception {
        // 你的自定义逻辑
    }
}
```

然后需要两个配置文件来注册：
1. `private.properties`：声明插件的类名
2. `common.properties`：声明该JobType接受哪些参数

**小白**：部署方式是怎样的？

**大师**：把编译好的jar包放到Azkaban的`plugins/jobtypes/`目录下，配置好properties文件，重启Executor即可。Azkaban会通过SPI机制自动发现和加载你的插件。

### 技术映射总结

- **AbstractProcessJob** = 标准积木底座（所有自定义Job的基类）
- **SPI** = 即插即用接口（符合标准就能被自动识别）
- **private.properties** = 插件身份证（声明"我是谁"）

## 3. 项目实战

### 3.1 完整插件实现

#### 步骤1：SnapshotJob实现

```java
package com.company.azkaban.jobtype;

import azkaban.jobExecutor.AbstractProcessJob;
import azkaban.utils.Props;
import org.apache.log4j.Logger;
import java.sql.*;

/**
 * 数据快照Job —— 在执行前后自动记录Hive表的元信息
 */
public class SnapshotJob extends AbstractProcessJob {
    
    public SnapshotJob(String jobId, Props sysProps, Props jobProps, Logger log) {
        super(jobId, sysProps, jobProps, log);
    }
    
    @Override
    public void run() throws Exception {
        String snapshotTable = jobProps.getString("snapshot.table");
        String snapshotDb = jobProps.get("snapshot.db", "default");
        String processDate = jobProps.getString("process_date");
        
        // 1. 记录执行前快照
        info("Taking pre-execution snapshot...");
        SnapshotEntry preSnapshot = captureSnapshot(snapshotDb, snapshotTable);
        preSnapshot.setSnapshotType("PRE");
        preSnapshot.setProcessDate(processDate);
        
        // 2. 执行实际的业务Job
        info("Executing wrapped command...");
        executeWrappedCommand();
        
        // 3. 记录执行后快照
        info("Taking post-execution snapshot...");
        SnapshotEntry postSnapshot = captureSnapshot(snapshotDb, snapshotTable);
        postSnapshot.setSnapshotType("POST");
        postSnapshot.setProcessDate(processDate);
        
        // 4. 保存快照记录到MySQL
        saveSnapshot(preSnapshot);
        saveSnapshot(postSnapshot);
        
        // 5. 对比快照，输出差异报告
        generateDiffReport(preSnapshot, postSnapshot);
    }
    
    private SnapshotEntry captureSnapshot(String db, String table) throws Exception {
        SnapshotEntry entry = new SnapshotEntry();
        entry.setDatabaseName(db);
        entry.setTableName(table);
        entry.setTimestamp(System.currentTimeMillis());
        
        // 通过Hive JDBC获取表元信息
        String hiveUrl = jobProps.get("hive.jdbc.url", 
                                      "jdbc:hive2://localhost:10000/default");
        
        try (Connection conn = DriverManager.getConnection(hiveUrl);
             Statement stmt = conn.createStatement()) {
            
            // 行数
            ResultSet rs = stmt.executeQuery(
                String.format("SELECT COUNT(*) FROM %s.%s", db, table)
            );
            if (rs.next()) {
                entry.setRowCount(rs.getLong(1));
            }
            
            // 分区数
            rs = stmt.executeQuery(
                String.format("SHOW PARTITIONS %s.%s", db, table)
            );
            int partitionCount = 0;
            while (rs.next()) partitionCount++;
            entry.setPartitionCount(partitionCount);
        }
        
        return entry;
    }
    
    private void executeWrappedCommand() throws Exception {
        String command = jobProps.getString("snapshot.command");
        if (command == null) {
            info("No wrapped command to execute");
            return;
        }
        
        Process process = Runtime.getRuntime().exec(
            new String[]{"/bin/bash", "-c", command}
        );
        
        // 异步读取输出
        BufferedReader reader = new BufferedReader(
            new InputStreamReader(process.getInputStream())
        );
        String line;
        while ((line = reader.readLine()) != null) {
            info(line);
        }
        
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new Exception("Wrapped command failed with exit code " + exitCode);
        }
    }
    
    private void saveSnapshot(SnapshotEntry entry) {
        String mysqlUrl = jobProps.get("snapshot.mysql.url",
                                        "jdbc:mysql://localhost:3306/data_monitor");
        String mysqlUser = jobProps.get("snapshot.mysql.user", "monitor");
        String mysqlPass = jobProps.get("snapshot.mysql.pass", "");
        
        try (Connection conn = DriverManager.getConnection(mysqlUrl, mysqlUser, mysqlPass);
             PreparedStatement ps = conn.prepareStatement(
                 "INSERT INTO table_snapshots (db_name, table_name, row_count, " +
                 "partition_count, process_date, snapshot_type, created_at) " +
                 "VALUES (?, ?, ?, ?, ?, ?, NOW())"
             )) {
            
            ps.setString(1, entry.getDatabaseName());
            ps.setString(2, entry.getTableName());
            ps.setLong(3, entry.getRowCount());
            ps.setInt(4, entry.getPartitionCount());
            ps.setString(5, entry.getProcessDate());
            ps.setString(6, entry.getSnapshotType());
            ps.executeUpdate();
            
        } catch (SQLException e) {
            warn("Failed to save snapshot: " + e.getMessage());
        }
    }
    
    private void generateDiffReport(SnapshotEntry pre, SnapshotEntry post) {
        long rowDiff = post.getRowCount() - pre.getRowCount();
        
        info("========== Snapshot Diff Report ==========");
        info(String.format("Table: %s.%s", pre.getDatabaseName(), pre.getTableName()));
        info(String.format("Rows before: %,d", pre.getRowCount()));
        info(String.format("Rows after:  %,d", post.getRowCount()));
        info(String.format("Row delta:   %+,d", rowDiff));
        info(String.format("Partitions before: %d", pre.getPartitionCount()));
        info(String.format("Partitions after:  %d", post.getPartitionCount()));
        info("============================================");
        
        // 异常检测
        if (pre.getRowCount() > 0 && rowDiff == 0) {
            warn("WARNING: No data change detected! Possible ETL failure.");
        }
        if (rowDiff < 0) {
            warn("WARNING: Data loss detected! Row count decreased.");
        }
    }
}

// SnapshotEntry POJO
class SnapshotEntry {
    private String databaseName;
    private String tableName;
    private long rowCount;
    private int partitionCount;
    private String snapshotType;  // PRE / POST
    private String processDate;
    private long timestamp;
    
    // Getters and setters...
    public void setDatabaseName(String n) { this.databaseName = n; }
    public String getDatabaseName() { return databaseName; }
    public void setTableName(String n) { this.tableName = n; }
    public String getTableName() { return tableName; }
    public void setRowCount(long c) { this.rowCount = c; }
    public long getRowCount() { return rowCount; }
    public void setPartitionCount(int c) { this.partitionCount = c; }
    public int getPartitionCount() { return partitionCount; }
    public void setSnapshotType(String t) { this.snapshotType = t; }
    public String getSnapshotType() { return snapshotType; }
    public void setProcessDate(String d) { this.processDate = d; }
    public String getProcessDate() { return processDate; }
    public void setTimestamp(long t) { this.timestamp = t; }
    public long getTimestamp() { return timestamp; }
}
```

#### 步骤2：插件配置文件

`plugins/jobtypes/snapshot/private.properties`：

```properties
# 声明插件类名
jobtype.class=com.company.azkaban.jobtype.SnapshotJob
jobtype.classpath=lib/snapshot-plugin.jar,lib/mysql-connector-java-8.0.28.jar
```

`plugins/jobtypes/common.properties`：

```properties
# 声明此JobType接受的参数
snapshot.table=required
snapshot.db=default
snapshot.command=optional
snapshot.mysql.url=optional
snapshot.mysql.user=optional
snapshot.mysql.pass=optional
hive.jdbc.url=optional
```

#### 步骤3：使用自定义JobType

```bash
# etl_with_snapshot.job
type=snapshot
snapshot.table=ods.orders
snapshot.db=ods
snapshot.command=python3 /opt/etl/process_orders.py
snapshot.mysql.url=jdbc:mysql://monitor-db:3306/data_monitor
snapshot.mysql.user=monitor
snapshot.mysql.pass=monitor_pass
```

#### 步骤4：编译部署

```bash
# 1. 编译插件jar
cd snapshot-plugin/
javac -cp "azkaban-common.jar:lib/*" com/company/azkaban/jobtype/SnapshotJob.java
jar cf snapshot-plugin.jar com/

# 2. 部署到Azkaban
mkdir -p /opt/azkaban-exec/plugins/jobtypes/snapshot/
cp snapshot-plugin.jar /opt/azkaban-exec/plugins/jobtypes/snapshot/lib/
cp private.properties /opt/azkaban-exec/plugins/jobtypes/snapshot/
cp mysql-connector-java-8.0.28.jar /opt/azkaban-exec/plugins/jobtypes/snapshot/lib/

# 3. 重启Executor加载插件
/opt/azkaban-exec/bin/shutdown-exec.sh
/opt/azkaban-exec/bin/start-exec.sh
```

### 3.2 测试验证

```bash
# 创建测试表
mysql -h monitor-db -u monitor -e "
CREATE TABLE IF NOT EXISTS data_monitor.table_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    db_name VARCHAR(100),
    table_name VARCHAR(100),
    row_count BIGINT,
    partition_count INT,
    process_date VARCHAR(20),
    snapshot_type VARCHAR(10),
    created_at DATETIME
);
"

# 在Azkaban中提交SnapshotJob测试
curl -b cookies.txt -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=snapshot_test&flow=etl_with_snapshot"
```

## 4. 项目总结

自定义Job Type是Azkaban扩展性的核心。关键步骤：
1. 继承`AbstractProcessJob`，实现`run()`方法
2. 配置`private.properties`声明插件类名
3. 配置`common.properties`声明接受的参数
4. 将jar部署到`plugins/jobtypes/`目录
5. 重启Executor加载新插件

### 思考题

1. 如何为自定义JobType添加"执行超时"机制——在`run()`方法内而不是依赖外部JobRunner的超时？
2. 如果要编写一个"分布式检查点"JobType（多个Executor之间共享执行状态），需要如何利用Azkaban现有的基础设施？
