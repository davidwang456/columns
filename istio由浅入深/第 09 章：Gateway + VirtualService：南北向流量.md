# 第 9 章：Gateway + VirtualService：南北向流量

**受众提示**：运维与平台工程主导入口与证书；开发对接 Host 与路由前缀；测试验证公网路径与鉴权衔接。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

集群外部用户访问服务，需要**入口**：Istio 使用 `Gateway` 描述**监听器**（端口、协议、证书），再用绑定到该 Gateway 的 `VirtualService` 描述**HTTP/TCP 路由**。这与仅集群内的 `VirtualService` 不同：**网关**通常部署在 `istio-system` 或独立入口命名空间，是南北向安全与容量的焦点。

本章完成 Bookinfo 的**浏览器访问**路径：`Gateway` → `VirtualService` → `productpage` Service。推广时强调：**TLS 在网关终止**与 **mTLS 到后端** 是两层（第 29 章深入）。

---

## 2. 项目设计：大师与小白的对话

**小白**：Gateway 和 Kubernetes Ingress 资源是不是重复？

**大师**：概念相似，但 Istio `Gateway` 与 **VirtualService** 组合更贴近**服务网格**模型；Ingress 实现因控制器而异。很多团队**外层仍用云 LB**，内层用 Istio Gateway。

**小白**：一个集群几个 Gateway？

**大师**：可按**业务域**或**环境**拆分；过多会增加**证书与 WAF** 管理成本。

**小白**：VirtualService 的 `gateways` 字段填什么？

**大师**：填 **Gateway 资源的 namespace/name**，例如 `istio-system/bookinfo-gateway`；纯 mesh 内路由用 `mesh`。

**小白**：改了 Gateway 为啥断了一会儿？

**大师**：监听器重载、证书更新、或 **Envoy drain**。生产要**金丝雀网关**或维护窗口。

**要点清单**

1. **Gateway** = 监听与证书；**VirtualService** = 路由表。
2. 外部 DNS 与 **SNI/Host** 对齐。
3. 与防火墙、云安全组**联动**设计。

---

## 3. 项目实战

### 3.1 Gateway（HTTP 示例）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: bookinfo-gateway
spec:
  selector:
    istio: ingressgateway
  servers:
  - port:
      number: 80
      name: http
      protocol: HTTP
    hosts:
    - "*"
```

### 3.2 VirtualService 绑定 Gateway

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: bookinfo
spec:
  hosts:
  - "*"
  gateways:
  - bookinfo-gateway
  http:
  - match:
    - uri:
        exact: /productpage
    route:
    - destination:
        host: productpage
        port:
          number: 9080
  - match:
    - uri:
        prefix: /static
    route:
    - destination:
        host: productpage
        port:
          number: 9080
  - match:
    - uri:
        exact: /login
    route:
    - destination:
        host: productpage
        port:
          number: 9080
  - match:
    - uri:
        prefix: /api/v1/products
    route:
    - destination:
        host: productpage
        port:
          number: 9080
```

> 完整示例以 Istio `samples/bookinfo/networking/bookinfo-gateway.yaml` 为准；安装后可直接 apply。

```bash
kubectl apply -f samples/bookinfo/networking/bookinfo-gateway.yaml
export INGRESS_HOST=$(kubectl -n istio-system get svc istio-ingressgateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
# 若无 LB，使用 node IP + NodePort 或 port-forward
```

### 3.3 访问验证

浏览器打开 `http://$INGRESS_HOST/productpage`（路径依示例而定）。

---

## 4. 项目总结

**优点**

- 南北向与东西向**同一套** CRD 与观测模型。
- 便于**按域**拆分路由与证书。

**缺点**

- 网关成为**热点**，需 HA 与容量规划。
- 配置错误影响面大，**变更要评审**。

**适用场景**

- 对外 API、Web 入口、**多租户 Host 路由**。

**注意事项**

- `hosts: "*"` 仅适合实验；生产应**收紧**。
- 与 **CDN/WAF** 的客户端 IP 传递（X-Forwarded-For）要验证。

**常见踩坑**

1. **现象**：404。**原因**：`VirtualService` 未绑定 Gateway 或 `match` 不含路径。**处理**：对照 `proxy-config route`。
2. **现象**：502。**原因**：后端 Service 无 Endpoints 或端口错。**处理**：`kubectl get endpoints`。
3. **现象**：HTTPS 证书错。**原因**：Secret 未挂载或 `credentialName` 错误。**处理**：第 29 章。

---

### 4.1 再谈一个场景：Host 与 TLS SNI

生产环境常在网关使用多个域名与证书。若 `VirtualService` 的 `hosts` 与证书 SAN 不一致，会出现「浏览器报证书错但 curl 指定 IP 正常」的现象。推广材料应强调：**Host/SNI/证书三位一体**联调。

### 4.2 与安全团队协作

WAF、DDoS 清洗与网格入口的关系要写清：哪些 Header 可被信任、哪些必须在 WAF 层剥离伪造。

---

## 附：自测与练习

### 自测题

1. `Gateway` 与绑定它的 `VirtualService` 各自负责什么？
2. 为什么生产不建议 `hosts: "*"`？
3. 外部 DNS 与 `Gateway` 的 listeners 如何对齐？

### 动手作业

用 `istioctl proxy-config listener` 查看 ingressgateway 的监听端口与路由，对照 `Gateway` YAML。

**延伸阅读**：Istio *Ingress Gateways*；官方 Bookinfo 网络示例。
