# 第18章：MySQL/PostgreSQL数据源实战

## 1. 项目背景

"Prometheus用来做监控没问题，但我们业务数据都在MySQL里——日活用户、订单量、转化率、收入……这些老板关心的业务指标怎么上Grafana？"

数据工程师阿明收到管理层需求："我要在Grafana上看到一个业务Dashboard，包含实时订单量、今日GMV、各品类销售额排行、用户增长曲线。"Prometheus里只有技术指标（QPS、延迟、CPU），业务数据全部在MySQL的数十张表里。阿明的第一反应是写ETL把业务数据同步到Prometheus——但这条路走不通，Prometheus的设计哲学是"不存业务数据"。

其实Grafana原生支持MySQL和PostgreSQL作为数据源！你可以在Grafana上直接写SQL查询，用Grafana的Dashboard展示业务数据。时间序列宏`$__timeFilter()`、`$__timeGroup()`让时序可视化与Prometheus一样流畅；变量支持SQL查询，可以做联动筛选；Table面板更是直接还原SQL结果集。

本章将教你把MySQL/PostgreSQL变成Grafana的"业务数据仓库"，建立从数据库到Dashboard的完整通路。

## 2. 项目设计

**小胖**（看着MySQL Workbench里的几百行数据）：大师，老板要我在Grafana上展示"过去7天各品类的订单趋势"。我在Prometheus里翻了半天，根本没有这些数据。同事说可以用MySQL数据源，但SQL的查询结果怎么变成图表？不是只有PromQL才能画时间序列吗？

**大师**（笑了）：这是常见误解。Grafana的强大在于它的数据抽象层——不管你从Prometheus、MySQL还是PostgreSQL读取数据，最终都转成统一的DataFrame结构。MySQL的一条SQL完全可以变成Time series面板上的折线图。

关键就在于几个Grafana特有的SQL宏。

**小白**（认真记录）：具体有哪些宏？

**大师**：三个最核心的：

`$__timeFilter(column)`：自动生成时间范围的WHERE条件。如果你的Dashboard选了"Last 7 days"，这个宏会在SQL中展开为：`created_at BETWEEN '2025-01-08 00:00:00' AND '2025-01-15 23:59:59'`。这让你不用在SQL中写死时间范围。

`$__timeGroup(column, 'interval')`：按时间粒度分组。相当于`DATE_FORMAT(created_at, '%Y-%m-%d %H:00:00')`，但Grafana会根据时间范围自动选择合适的粒度。写`$__timeGroup(created_at, '1h')`表示按小时分组。

`$__unixEpochFilter(column)`：和`$__timeFilter`一样，但转换成Unix时间戳（数字格式）。适用于存时间戳整数的表。

**小胖**：举个例子。我想看每小时的订单量趋势，SQL怎么写？

**大师**：

```sql
SELECT
  $__timeGroup(created_at, '1h') AS time,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
GROUP BY time
ORDER BY time
```

两个关键点：
1. SELECT里必须有一个列命名为`time`——Grafana靠这个列来定位X轴。
2. Query的Format选"Time series"——Grafana就知道要按时间序列渲染。

**小白**（追问）：那如果是MySQL，表里的时间列是`DATETIME`类型，和Grafana的UTC时间之间有时区问题吗？

**大师**：这和你的Grafana Dashboard时区设置有关。如果Dashboard时区选了"Browser Time (Asia/Shanghai)"，`$__timeFilter()`会自动转换为上海时区。但如果MySQL服务器时区是UTC，就需要处理偏移。最佳实践是：数据库统一用UTC存储，Dashboard时区选Browser Time，Grafana自动转换。

还有一个容易忽略的优化点——MySQL查询性能。如果`orders`表有上亿行，`$__timeFilter(created_at)`对应的条件`BETWEEN ...`需要`created_at`列上有索引。没有索引的话全表扫描，Dashboard加载超慢。

**小胖**：变量呢？比如我想动态选择品类？

**大师**：Grafana的变量支持SQL查询。创建一个变量`$category`，Type选`Query`，Data Source选MySQL，Query填：
```sql
SELECT DISTINCT category AS __text, category AS __value
FROM products
ORDER BY category
```

`AS __text`是显示给用户的文本，`AS __value`是实际替换到查询中的值。

然后在面板的SQL中使用：
```sql
SELECT
  $__timeGroup(created_at, '1h') AS time,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
  AND category = '$category'
GROUP BY time
```

注意`'$category'`要加引号——因为它是字符串。如果变量是多选的，用`IN ($category)`，Grafana自动展开为`IN ('A','B','C')`。

**小白**（推眼镜）：我有个问题——多数据源混合场景。比如订单量从MySQL查，但订单接口的QPS从Prometheus查。怎么在一张Table里同时展示？

**大师**：这正是Transform的用武之地。两个Panel Query，Query A连MySQL查订单量，Query B连Prometheus查QPS。然后用Join by field按时间/品类关联——在第9章我们学过。只是两个查询来自不同数据源而已，Transform完全不关心数据源类型。

但注意：如果MySQL和Prometheus的查询结果时间戳粒度不一致（比如一个是每小时一个点，一个是每15秒一个点），Join前需要各自Reduce到统一粒度。

**技术映射**：`$__timeFilter()` = 自动确定的时间范围书签（翻到哪一页自动标记），`$__timeGroup()` = 时间粒度选择器（天/小时/分钟），Format Time series = 告诉Grafana"这是时序数据"的标签。

## 3. 项目实战

**环境准备**

基于之前的Docker Compose环境，添加MySQL测试数据：

```sql
-- 补充更多测试数据
CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_name VARCHAR(100),
    category VARCHAR(50),
    amount DECIMAL(10,2),
    status VARCHAR(20),
    user_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 插入100条模拟数据
INSERT INTO orders (product_name, category, amount, status, user_id, created_at)
SELECT
  CONCAT('Product-', FLOOR(RAND()*10)),
  CASE FLOOR(RAND()*4)
    WHEN 0 THEN '电子产品' WHEN 1 THEN '服装' WHEN 2 THEN '食品' ELSE '家居'
  END,
  ROUND(RAND()*500 + 50, 2),
  CASE FLOOR(RAND()*5)
    WHEN 0 THEN 'completed' WHEN 1 THEN 'pending' WHEN 2 THEN 'cancelled' ELSE 'completed'
  END,
  FLOOR(RAND()*100 + 1),
  DATE_SUB(NOW(), INTERVAL FLOOR(RAND()*168) HOUR)
FROM (SELECT 1 UNION SELECT 2 UNION ... UNION SELECT 100) t;  -- 简化
```

更实用的方式是用存储过程批量生成：
```sql
DELIMITER //
CREATE PROCEDURE generate_orders(IN num_rows INT)
BEGIN
  DECLARE i INT DEFAULT 1;
  WHILE i <= num_rows DO
    INSERT INTO orders (product_name, category, amount, status, created_at)
    VALUES (
      CONCAT('Product-', FLOOR(RAND()*10)),
      ELT(FLOOR(RAND()*4)+1, '电子产品', '服装', '食品', '家居'),
      ROUND(RAND()*500 + 50, 2),
      ELT(FLOOR(RAND()*3)+1, 'completed', 'pending', 'cancelled'),
      DATE_SUB(NOW(), INTERVAL FLOOR(RAND()*168) HOUR)
    );
    SET i = i + 1;
  END WHILE;
END //
DELIMITER ;

CALL generate_orders(500);
```

**步骤一：基础业务监控Dashboard**

创建Dashboard，添加数据源为MySQL的Panel。

**面板1：今日订单总量（Stat）**
```sql
SELECT COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
```
Format: `Table`，面板类型: `Stat`。设置Value options → Calculate: `Last`。

**面板2：今日GMV（Stat）**
```sql
SELECT COALESCE(SUM(amount), 0) AS value
FROM orders
WHERE status = 'completed' AND $__timeFilter(created_at)
```
Unit: `Currency (CNY)`。

**面板3：订单趋势（Time series）**
```sql
SELECT
  $__timeGroup(created_at, '1h') AS time,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
GROUP BY time
ORDER BY time
```
Format: `Time series`。

**面板4：各品类订单量（Bar chart）**
```sql
SELECT
  category,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
GROUP BY category
ORDER BY value DESC
```
Format: `Table`，面板类型: `Bar chart`。

**面板5：订单状态分布（Pie chart）**
```sql
SELECT
  status,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
GROUP BY status
```
Format: `Table`，面板类型: `Pie chart`。

**步骤二：变量驱动的动态Dashboard**

**变量1：品类（支持多选）**
| 属性 | 值 |
|------|-----|
| Name | `category` |
| Type | `Query` |
| Data source | MySQL |
| Query | `SELECT DISTINCT category AS __text, category AS __value FROM orders ORDER BY category` |
| Multi-value | ✅ |
| Include All option | ✅ |

**变量2：时间粒度**
| 属性 | 值 |
|------|-----|
| Name | `time_grain` |
| Type | `Custom` |
| Values | `1m,5m,15m,30m,1h,6h,12h,1d` |

在面板SQL中引用变量：
```sql
SELECT
  $__timeGroup(created_at, '$time_grain') AS time,
  COUNT(*) AS value
FROM orders
WHERE $__timeFilter(created_at)
  AND category IN ($category)
GROUP BY time
ORDER BY time
```

**步骤三：复合查询——多指标同一面板**

需求：同一张Time series图上显示"订单量"和"GMV"两根线。

```sql
-- Query A：订单量
SELECT $__timeGroup(created_at, '1h') AS time, COUNT(*) AS value, '订单量' AS metric
FROM orders WHERE $__timeFilter(created_at)
GROUP BY time

-- Query B：GMV（使用同一面板的第二个Query）
SELECT $__timeGroup(created_at, '1h') AS time, SUM(amount) AS value, 'GMV' AS metric
FROM orders WHERE status='completed' AND $__timeFilter(created_at)
GROUP BY time
```

注意：两个Query都有`metric`列区分。在Transform中可以做进一步处理。

**步骤四：高级SQL——开窗函数与同比**

MySQL 8.0+支持窗口函数，可以做更复杂的数据分析：

```sql
-- 每日订单量的7日移动平均
SELECT
  DATE(created_at) AS time,
  COUNT(*) AS value,
  AVG(COUNT(*)) OVER (ORDER BY DATE(created_at) ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma7
FROM orders
WHERE $__timeFilter(created_at)
GROUP BY DATE(created_at)
ORDER BY time
```

**步骤五：告警——基于SQL的告警规则**

在Grafana Alerting中基于MySQL查询创建告警：

Query：
```sql
SELECT COUNT(*) AS value FROM orders WHERE created_at > NOW() - INTERVAL 30 MINUTE
```

Expression：`IS BELOW 10`（如果最近30分钟订单量低于10，触发告警）

适用场景：订单量骤降告警、退款率超限告警等业务告警。

**步骤六：性能优化实战**

**问题场景**：订单表1000万行，Dashboard查询"Last 30 days"超时。

**优化手法**：

1. **确保时间列有索引**：
```sql
CREATE INDEX idx_orders_created_at ON orders(created_at);
```

2. **使用覆盖索引**（避免回表）：
```sql
CREATE INDEX idx_orders_time_category_amount 
ON orders(created_at, category, amount);
```

3. **在SQL中限制返回数据量**：
```sql
-- 不要直接查全部，加LIMIT
SELECT $__timeGroup(created_at, '1h') AS time, COUNT(*) AS value
FROM orders USE INDEX (idx_orders_time_category_amount)
WHERE $__timeFilter(created_at) AND category = '$category'
GROUP BY time ORDER BY time
```

4. **设置Grafana的查询超时和连接池**：
在数据源配置中设置`Max open conn = 10`、`Query timeout = 30`。

5. **使用物化视图（高查询频次场景）**：
```sql
CREATE MATERIALIZED VIEW order_hourly_summary AS
SELECT DATE_FORMAT(created_at, '%Y-%m-%d %H:00:00') AS hour,
       COUNT(*) AS order_count,
       SUM(amount) AS total_amount
FROM orders
GROUP BY hour;
```

**常见坑点**
1. **SQL注入风险**：Grafana不会对变量做SQL注入防护。如果变量来自用户输入（Text box变量），攻击者可以注入恶意SQL。规避：尽量使用Query/Interval等受限变量类型。
2. **时间类型不匹配**：MySQL的`TIMESTAMP`和`DATETIME`与Grafana的时间处理方式不同。`TIMESTAMP`会自动转UTC，`DATETIME`存什么就是什么。
3. **NULL值处理**：SQL查询返回`NULL`时，Grafana的Stat面板显示为空或0。用`COALESCE()`包裹。
4. **变量查询SQL报错**：变量查询中如果SQL返回空结果或语法错误，整页Dashboard都受影响。

## 4. 项目总结

**时间宏速查表**

| 宏 | 用途 | 示例输出 |
|----|------|---------|
| `$__timeFilter(column)` | 时间范围过滤 | `created_at BETWEEN '2025-01-08' AND '2025-01-15'` |
| `$__timeGroup(column, '1h')` | 按时间粒度分组 | `floor(unix_timestamp(created_at)/3600)*3600` |
| `$__timeFrom()` | 起始时间戳 | `1736294400` |
| `$__timeTo()` | 结束时间戳 | `1736899200` |
| `$__unixEpochFilter(column)` | Unix时间戳过滤 | `column >= 1736294400 AND column <= 1736899200` |
| `$__interval_ms` | 计算间隔（毫秒）| `3600000` |

**优点**
| 特性 | 说明 |
|------|------|
| 零ETL | 直接查MySQL/PostgreSQL，无需数据传输 |
| SQL灵活 | 窗口函数、子查询、JOIN全支持 |
| 变量联动 | 变量的SQL查询支持动态选项 |
| 业务告警 | 基于SQL的业务异常检测 |

**缺点**
| 特性 | 说明 |
|------|------|
| 查询压力 | 每次Dashboard刷新都执行SQL，高频刷新压力大 |
| SQL注入风险 | 变量值直接拼到SQL中 |
| 时序功能有限 | 不如PromQL那样为时序优化 |
| 性能依赖索引 | 大数据量查询必须有索引支持 |

**适用场景**
1. 业务Dashboard：订单量/GMV/转化率/DAU等业务核心指标
2. 数据库监控：连接数/慢查询/表大小/锁等待
3. 混合大盘：业务数据（MySQL）+ 技术指标（Prometheus）
4. 运营报表：定期频率(1h+)的统计查询
5. 数据质量监控：记录数异常、空值检测

**注意事项**
1. 生产环境务必使用只读数据库账号
2. SQL查询时间上限受`dataproxy.timeout`控制，大数据量查询需要优化
3. Format选"Time series"时，第一个列必须是time类型
4. MySQL 5.7和8.0的查询行为有差异（如窗口函数仅8.0+支持）

**常见踩坑经验**
1. **Format选错Data**：选了"Time series"但SQL的time列不是时间类型→面板空白。选"Table"→显示正常。
2. **时区导致数据偏差**：MySQL是UTC时区但Dashboard选Asia/Shanghai，数据偏移8小时。解决：Dashboard时区设为UTC。
3. **Macro在子查询中不工作**：`$__timeFilter()`不能放在子查询中（Grafana不支持嵌套宏）。解决：先写外层过滤。

**思考题**
1. 如果业务数据同时在MySQL和PostgreSQL两个数据库中，如何在Grafana的一张Table面板上同时展示两个数据源的关联数据？
2. 当MySQL的`$__timeFilter()`生成的BETWEEN条件没走索引时（常见于分区表），有什么替代方案？
