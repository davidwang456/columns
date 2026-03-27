# 背景

   Elasticsearch 是一个分布式的开源搜索和分析引擎，适用于所有类型的数据，包括文本、数字、地理空间、结构化和非结构化数据。Elasticsearch 在 Apache Lucene 的基础上开发而成，由 Elasticsearch N.V.（即现在的 Elastic）于 2010 年首次发布。Elasticsearch 以其简单的 REST 风格 API、分布式特性、速度和可扩展性而闻名，是 Elastic Stack 的核心组件；Elastic Stack 是适用于数据采集、充实、存储、分析和可视化的一组开源工具。人们通常将 Elastic Stack 称为 ELK Stack（代指 Elasticsearch、Logstash 和 Kibana），目前 Elastic Stack 包括一系列丰富的轻量型数据采集代理，这些代理统称为 Beats，可用来向 Elasticsearch 发送数据。

# 安装elasticsearch集群

1.下载rpm文件，如elasticsearch-7.7.0-x86_64.rpm,放置到想要的安装目录下：

```
mv elasticsearch-7.7.0-x86_64.rpm  /usr/share
```

2.增加elasticsearch用户组和用户 

```
groupadd elasticsearch
useradd elasticsearch -g elasticsearch -p elasticsearch
passwd elasticsearch
```

3.执行rmp安装

```
rpm -ivh elasticsearch-7.7.0-x86_64.rpm
```

此时，elasticsearch被安装到当前目录下

4.给elasticsearch授权

```
 cd elasticsearch
 ll elasticsearch
 chown -R elasticsearch:elasticsearch elasticsearch
 ll elasticsearch
```

5.修改配置文件

```
#vi /etc/elasticsearch/elasticsearch.yml
# ---------------------------------- Cluster -----------------------------------
#
# Use a descriptive name for your cluster:
#
cluster.name: my-es
#
# ------------------------------------ Node ------------------------------------
#
# Use a descriptive name for the node:
#
node.name: node-2
#
# Add custom attributes to the node:
#
#node.attr.rack: r1
#
# ----------------------------------- Paths ------------------------------------
#
# Path to directory where to store the data (separate multiple locations by comma):
#
path.data: /var/lib/elasticsearch
#
# Path to log files:
#
path.logs: /var/log/elasticsearch
#
# ----------------------------------- Memory -----------------------------------
#
# Lock the memory on startup:
#
#bootstrap.memory_lock: true
#
# Make sure that the heap size is set to about half the memory available
# on the system and that the owner of the process is allowed to use this
# limit.
#
# Elasticsearch performs poorly when the system is swapping the memory.
#
# ---------------------------------- Network -----------------------------------
#
# Set the bind address to a specific IP (IPv4 or IPv6):
#
network.host: 0.0.0.0
#
# Set a custom port for HTTP:
#
http.port: 9200
#
# For more information, consult the network module documentation.
#
# --------------------------------- Discovery ----------------------------------
#
# Pass an initial list of hosts to perform discovery when this node is started:
# The default list of hosts is ["127.0.0.1", "[::1]"]
#
discovery.seed_hosts: ["192.168.1.100","192.168.1.101", "192.168.1.102"]
#
# Bootstrap the cluster using an initial set of master-eligible nodes:
#
cluster.initial_master_nodes: ["node-1", "node-2","node-3"]
```

6.配置hosts

```
vi /etc/hosts #新增hosts配置
192.168.1.100  node-1
192.168.1.101 node-2
192.168.1.102 node-3
```

7.重复动作在其它服务器

8.启动elasticsearch服务

```
#启动命令
systemctl start elasticsearch.service
#验证命令
ps -ef | grep elasticsearch
#查看服务端口
net status -ntulp | grep elasticsearch
```

或者启动浏览器访问

http://192.168.1.100:9200/

# elasticsearch-exporter安装

     在使用 ElasticSearch 过程中需要对 ElasticSearch 运行状态进行监控，例如集群及索引状态等， Prometheus 监控服务提供了基于 Exporter 的方式来监控 ElasticSearch 运行状态，并提供了开箱即用的 Grafana 监控大盘。

elasticsearch_exporter与ES集群是分开独立，不需要对原有的ES集群(可能有很多个)做任何修改，不需要重启，只要能访问es集群即可。

1.下载elasticsearch_exporter-1.2.1.linux-amd64.tar.gz到本地并解压

```
# 下载
wget https://github.com/justwatchcom/elasticsearch_exporter/releases/download/v1.2.1/elasticsearch_exporter-1.2.1.linux-amd64.tar.gz
# 解压
tar -xvf elasticsearch_exporter-1.2.1.linux-amd64.tar.gz
cd elasticsearch_exporter-1.2.1.linux-amd64/
```

2.运行elasticsearch_export

```
./elasticsearch_exporter --es.all --es.indices --es.cluster_settings --es.indices_settings --es.shards --es.snapshots --es.timeout=10s --web.listen-address=:9114 --web.telemetry-path=/metrics --es.uri http://localhost:9200
```

# 下载安装prometheus

```
#下载Prometheus
https://prometheus.io/download/
tar -vxf prometheus-VERSION.linux-amd64.tar.gz
cd prometheus-VERSION.linux-amd64
vi prometheus.yml
```

修改prometheus监控配置，监控elasticsearch的9114端口metrics

```
# my global config
global:
  scrape_interval:     15s # Set the scrape interval to every 15 seconds. Default is every 1 minute.
  evaluation_interval: 15s # Evaluate rules every 15 seconds. The default is every 1 minute.
  # scrape_timeout is set to the global default (10s).

# Alertmanager configuration
alerting:
  alertmanagers:
  - static_configs:
    - targets:
      # - alertmanager:9093

# Load rules once and periodically evaluate them according to the global 'evaluation_interval'.
rule_files:
  # - "first_rules.yml"
  # - "second_rules.yml"

# A scrape configuration containing exactly one endpoint to scrape:
# Here it's Prometheus itself.
scrape_configs:
  # The job name is added as a label `job=<job_name>` to any timeseries scraped from this config.
  - job_name: 'elasticsearch'
    metrics_path: '/metrics'
    scrape_interval: 15s
    static_configs:
    - targets: ['192.168.1.100:9114']
```

启动prometheus

```
./prometheus --config.file=prometheus.yml --web.listen-address=:9092 &
```

# Grafana安装搭建

```
#下载Grafana
https://grafana.com/grafana/download
#解压Grafana
tar -vxf grafana-VERSION.linux-amd64.tar.gz
#启动Grafana
nohup ./bin/grafana-server >> grafana.log 2>&1 &
```

**验证grafana**

浏览器访问http://${host}:3000/ 默认用户名密码 admin/admin

grafana配置  
导入2322模板  
下载地址：https://grafana.com/grafana/dashboards/2322

![](http://p9.toutiaoimg.com/large/tos-cn-i-jcdsk5yqko/174f1ff98439410d897a7b3164b162f5)
