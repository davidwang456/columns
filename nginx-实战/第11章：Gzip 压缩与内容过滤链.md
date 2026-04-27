# 第11章：Gzip 压缩与内容过滤链

> 源码关联：`src/http/modules/ngx_http_gzip_filter_module.c`

---

## 1. 项目背景

"鲜果园"的移动端Web页面在经过几次大促活动后，前端团队收到了大量用户反馈："首页加载太慢了，半天刷不出水果图片"。技术团队经过分析发现，问题的瓶颈并不在后端API响应速度上——接口平均耗时只有80毫秒——真正的罪魁祸首是网络传输层。鲜果园的详情页HTML体积达到了420KB，JavaScript bundle有680KB，CSS也有180KB。在4G网络环境下，这么多未压缩的数据传输到用户手机，首屏时间（FCP）被拖到了3.5秒以上。

前端负责人提出方案：把图片全部换成WebP格式，对静态资源做CDN分发。但运维负责人指出，即使图片优化了，HTML、CSS、JS这些文本资源的体积依然不可忽视。而且这些文本内容高度可压缩——HTML中充斥着重复的`<div>`、`<span>`标签，JS中有大量的空格和注释，CSS中重复的颜色值和属性声明比比皆是。经过测算，这些文本资源经过Gzip压缩后，体积可以减少60%到80%。

小胖接到任务，在Nginx上开启Gzip压缩。他上网搜了一下，在配置文件里加了三行：

```nginx
gzip on;
gzip_types text/html;
```

然后得意地告诉大师搞定了。大师看了一眼配置，问："CSS和JS压缩了吗？压缩级别用的是默认的6吗？静态资源有没有预压缩？大文件压缩会不会阻塞worker进程？"小胖一脸茫然。

小白则关注另一个层面的问题。她在阅读Nginx源码时注意到，`ngx_http_gzip_filter_module.c`中并没有直接发送响应体，而是实现了`body_filter`函数，把压缩后的数据传给下一个filter。这引发了她更深层次的疑问：Nginx的响应内容在到达客户端之前，到底经历了多少层"加工"？这些filter是如何串联起来的？如果多个filter同时作用，它们的执行顺序由什么决定？

大师决定在本章中带领团队深入理解Nginx的Filter Chain模型，从Gzip压缩的原理出发，彻底掌握内容过滤链的工作机制，并在实战中完成一套兼顾性能与效果的压缩方案。

---

## 2. 项目设计

周二的午后，技术区的落地窗外阳光正好。小胖捧着一盒刚切好的西瓜走进会议室，小白已经在位子上安静地看书，旁边摊着打印出来的`ngx_http_gzip_filter_module.c`源码。大师随后进来，手里是一杯冰美式。

**小胖**（放下西瓜，叉起一块）："大师，Gzip压缩我昨天已经配好了，就加了两行配置。测试了一下，HTML确实变小了，从400KB压到了90KB，是不是可以收工了？"

**大师**（看了一眼小胖的笔记本屏幕）："你只压缩了HTML，那CSS呢？JavaScript呢？JSON接口响应呢？而且这些资源的压缩级别你调了吗？"

**小胖**（嚼着西瓜）："压缩级别？那不是越高越好吗？直接调到9呗。"

**小白**（从源码中抬头）："不对。Gzip的压缩级别1到9，级别越高压缩率越大，但CPU消耗也成倍增加。对于Web场景，通常级别4到6是性价比最高的平衡点。而且我注意到，Nginx的gzip模块是在worker进程里实时压缩的，如果所有worker都在高负载下做level 9压缩，CPU很快就会被吃光。"

**大师**（点点头）："小白说得对。Gzip压缩本质上是Deflate算法加LZ77和Huffman编码的组合。级别1到3是快速模式，适合高并发低延迟场景；级别4到6是均衡模式，适合大多数Web应用；级别7到9是极限压缩模式，适合对带宽极度敏感且CPU充裕的场景。"

**小胖**（挠挠头）："那我怎么知道该选哪个级别？"

**大师**："这需要根据你的业务特征做权衡。鲜果园的场景是：文本资源多、用户网络环境参差不齐（4G/5G/WiFi混合）、服务器CPU有富余但不算豪华。我建议主站资源用level 5，API JSON响应用level 4。"

**小白**（推了推眼镜）："大师，我读了源码，发现gzip模块并没有直接发送响应给客户端，而是通过`ngx_http_next_body_filter`把压缩后的数据传给下一个模块。这让我想到，Nginx的响应是不是要经过一条'加工流水线'？"

**大师**（露出赞许的神色）："你的直觉非常准确。Nginx处理响应的过程确实是一条**Filter Chain（过滤链）**。当一个请求到达CONTENT阶段，内容生成模块（如`proxy_pass`、`root`静态文件）产生原始的响应头和响应体。但这些原始数据不会直接发给客户端，而是依次经过多个filter模块的处理。"

大师在白板上画了一条链：

```
内容生成模块 (proxy_pass / static)
    ↓
ngx_http_not_modified_filter_module
    ↓
ngx_http_headers_filter_module
    ↓
ngx_http_gzip_filter_module  ← 我们关注的层
    ↓
ngx_http_range_filter_module
    ↓
ngx_http_chunked_filter_module
    ↓
ngx_http_write_filter_module  ← 最终发送给客户端
```

**大师**（指着链条）："这是Nginx默认加载的filter模块顺序。每个filter模块都实现了两个核心函数：`header_filter`和`body_filter`。`header_filter`负责修改响应头，`body_filter`负责修改响应体。一个filter处理完后，调用`ngx_http_next_header_filter`和`ngx_http_next_body_filter`，把接力棒传给下一个filter。"

**小胖**（放下叉子）："这好像食堂的打饭流水线啊——先打饭（内容生成），再加卤（headers_filter），然后压缩打包（gzip_filter），最后切分小份（range_filter）发给同学。"

**大师**（微笑）："比喻很贴切。但注意，filter链的顺序是固定的，由模块的编译顺序决定，配置文件里无法调整。`ngx_http_gzip_filter_module`排在`headers_filter`之后、`range_filter`之前，这意味着：gzip压缩发生在全国（Range）分块之前，所以Range请求会先被gzip压缩，再按Range切分——这个顺序非常重要，如果反过来，切分后再压缩，Range的偏移量就会全部错乱。"

**小白**（追问）："那`gzip_static`指令是什么原理？我看到有些项目里除了`gzip on`，还会写`gzip_static on`。"

**大师**："这是两个完全不同的机制。`gzip on`是**动态压缩**——Nginx在接收到请求后，实时读取原始文件，在内存中压缩，然后发送压缩后的数据。每一次请求都要消耗CPU。"

"而`gzip_static on`是**预压缩**——它要求你在磁盘上预先准备好`.gz`后缀的压缩文件。比如`index.html`旁边放一个`index.html.gz`。当客户端支持gzip时，Nginx直接发送预压缩文件，跳过实时压缩，CPU消耗几乎为零。这对于不经常变化的静态资源是最佳实践。"

**小胖**（恍然大悟）："那鲜果园的JS、CSS这种构建后几乎不变的资源，是不是应该用`gzip_static`？"

**大师**："完全正确。而且构建工具（如Webpack、Vite）通常支持在打包时生成`.gz`文件，运维只需要在Nginx上开启`gzip_static`即可。"

**小白**（又提出一个问题）："大师，如果客户端不支持gzip，Nginx会不会发了压缩内容导致客户端无法解析？"

**大师**："不会的。gzip模块会检查请求头中的`Accept-Encoding`字段，只有当客户端明确表示支持gzip（或deflate）时，才会启用压缩。而且Nginx还会在响应头中添加`Content-Encoding: gzip`，告知客户端响应体是压缩过的。如果客户端不支持，Nginx直接发送原始内容。"

**小胖**（看着白板上的链条）："那`gunzip`模块又是做什么的？"

**大师**："`gunzip`是gzip的逆操作。某些场景下，上游服务器已经返回了gzip压缩的内容，但Nginx下游的某些模块（比如SSI服务端包含模块）需要处理原始未压缩的内容。`ngx_http_gunzip_filter_module`可以在接收端解压，让后续filter能处理明文。这种'压缩传输、解压处理'的模型在云原生环境中很常见。"

**小白**（快速记录）："也就是说，Nginx的内容过滤链既可以压缩（gzip）也可以解压（gunzip），而且支持静态预压缩（gzip_static）和动态实时压缩两种方式。"

**大师**（放下白板笔）："总结得很好。现在我们可以把这些知识落地到鲜果园的实战中了。"

---

## 3. 项目实战

**环境准备**
- Nginx 1.24.0（源码编译时需启用`--with-http_gzip_static_module`）
- Node.js 18 + Webpack 5（构建前端资源并生成预压缩文件）
- CentOS 8 / Ubuntu 22.04

### 步骤一：准备静态资源并生成预压缩文件

创建一个简单的Web项目结构：

```bash
mkdir -p /data/xian果园/static/{js,css,html}
```

编写`index.html`：

```html
<!DOCTYPE html>
<html>
<head>
    <title>鲜果园 - 新鲜直达</title>
    <link rel="stylesheet" href="/css/main.css">
</head>
<body>
    <h1>鲜果园 - 今日推荐</h1>
    <div id="app"></div>
    <script src="/js/app.js"></script>
</body>
</html>
```

编写`main.css`（故意写得冗长以体现压缩效果）：

```css
/* 鲜果园主样式 */
body { margin: 0; padding: 0; font-family: 'Helvetica Neue', Arial, sans-serif; background-color: #fafafa; color: #333; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
.header { background-color: #ff6b6b; color: white; padding: 20px; text-align: center; }
.product-card { background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin: 10px; padding: 15px; }
.product-title { font-size: 18px; font-weight: bold; color: #222; margin-bottom: 8px; }
.product-price { font-size: 16px; color: #ff6b6b; font-weight: 600; }
.product-desc { font-size: 14px; color: #666; line-height: 1.5; }
.footer { text-align: center; padding: 40px; color: #999; font-size: 12px; }
```

编写`app.js`（模拟一个较大的JS bundle）：

```javascript
// 鲜果园前端应用
(function() {
    'use strict';
    
    const products = [
        { id: 1, name: '烟台红富士苹果', price: 29.9, desc: '脆甜多汁，产地直发' },
        { id: 2, name: '海南金煌芒果', price: 39.9, desc: '肉厚核薄，香甜如蜜' },
        { id: 3, name: '智利进口车厘子', price: 89.9, desc: 'JJJ级大果，新鲜空运' },
        { id: 4, name: '云南蓝莓', price: 19.9, desc: '花青素爆表，护眼首选' }
    ];

    function renderProducts() {
        const app = document.getElementById('app');
        const container = document.createElement('div');
        container.className = 'container';
        
        products.forEach(product => {
            const card = document.createElement('div');
            card.className = 'product-card';
            card.innerHTML = `
                <div class="product-title">${product.name}</div>
                <div class="product-price">¥${product.price}</div>
                <div class="product-desc">${product.desc}</div>
            `;
            container.appendChild(card);
        });
        
        app.appendChild(container);
    }

    function init() {
        console.log('鲜果园应用初始化');
        renderProducts();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
```

使用`gzip`命令生成预压缩文件（生产环境通常由构建工具自动生成）：

```bash
cd /data/xian果园/static

# 为每个静态文件生成.gz预压缩版本
gzip -k -9 html/index.html
gzip -k -9 css/main.css
gzip -k -9 js/app.js

# 查看文件大小对比
ls -lh html/ css/ js/
```

预期输出（大小因系统而异，但压缩比应明显）：

```
html/index.html     420B
html/index.html.gz  280B
css/main.css        680B
css/main.css.gz     320B
js/app.js          1.2K
js/app.js.gz       450B
```

### 步骤二：编写Nginx配置文件

```nginx
user  nginx;
worker_processes  auto;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    # === Gzip 动态压缩配置 ===
    gzip                    on;
    gzip_vary               on;           # 添加Vary: Accept-Encoding头
    gzip_proxied            any;          # 对代理请求也启用压缩
    gzip_comp_level         5;            # 压缩级别：5（均衡模式）
    gzip_buffers            16 8k;        # 压缩缓冲区
    gzip_min_length         256;          # 小于256字节的文件不压缩
    gzip_http_version       1.1;          # HTTP/1.1及以上才压缩
    
    # 压缩的MIME类型
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml+rss
        application/atom+xml
        image/svg+xml;

    # === Gzip 静态预压缩 ===
    gzip_static             on;           # 优先使用预压缩的.gz文件

    # === Gunzip（如需解压上游压缩内容）===
    gunzip                  on;           # 允许解压上游gzip内容

    server {
        listen       80;
        server_name  localhost;
        root         /data/xian果园/static;
        index        index.html;

        # HTML文件：开启压缩
        location ~* \.html$ {
            add_header Cache-Control "no-cache";
            # gzip和gzip_static在此自动生效
        }

        # CSS/JS文件：长期缓存 + 预压缩
        location ~* \.(css|js)$ {
            expires 30d;
            add_header Cache-Control "public, immutable";
            add_header Vary "Accept-Encoding";
        }

        # API接口：JSON动态压缩
        location /api/ {
            proxy_pass http://127.0.0.1:8080;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            
            # API接口单独降低压缩级别，减少CPU消耗
            gzip_comp_level 4;
        }

        # 健康检查
        location /health {
            access_log off;
            return 200 "Nginx is running\n";
            add_header Content-Type text/plain;
        }
    }
}
```

**配置要点说明**：

1. **`gzip_comp_level 5`**：鲜果园场景下的均衡选择。实测表明，对于文本资源，level 5相比level 1能多压缩10%到15%的体积，而CPU消耗增加不到30%。

2. **`gzip_min_length 256`**：过小的文件压缩后反而可能变大（因为gzip头部有固定开销），设置阈值避免负优化。

3. **`gzip_static on`**：Nginx会优先查找同名的`.gz`文件。如果`index.html.gz`存在且客户端支持gzip，直接发送预压缩文件，零CPU实时消耗。

4. **`gzip_vary on`**：添加`Vary: Accept-Encoding`响应头，告知CDN和浏览器：此资源的缓存需要区分客户端是否支持压缩，避免给不支持gzip的客户端发送压缩内容。

5. **`gunzip on`**：如果上游服务器已经返回了`Content-Encoding: gzip`的内容，但Nginx需要处理原始内容（如SSI包含），gunzip模块会自动解压。

### 步骤三：语法检查与重载

```bash
/usr/local/nginx/sbin/nginx -t
/usr/local/nginx/sbin/nginx -s reload
```

### 步骤四：验证测试

**测试场景1：验证HTML动态压缩**

```bash
curl -H "Accept-Encoding: gzip" -I http://localhost/index.html
```

预期响应头：

```
HTTP/1.1 200 OK
Content-Type: text/html
Content-Encoding: gzip
Vary: Accept-Encoding
```

**测试场景2：验证静态预压缩文件被使用**

```bash
# 请求JS文件，带gzip支持
curl -H "Accept-Encoding: gzip" -I http://localhost/js/app.js
```

Nginx会直接发送`app.js.gz`，响应头中`Content-Encoding: gzip`且`Content-Length`与`.gz`文件大小一致。

**测试场景3：验证无压缩支持时发送原始内容**

```bash
# 不带Accept-Encoding请求
curl -I http://localhost/js/app.js
```

预期响应头中没有`Content-Encoding: gzip`，`Content-Length`为原始`app.js`的大小。

**测试场景4：验证压缩率**

```bash
# 对比原始大小和压缩后大小
echo "原始大小:"
wc -c /data/xian果园/static/css/main.css

echo "压缩后大小:"
curl -s -H "Accept-Encoding: gzip" http://localhost/css/main.css | wc -c
```

预期压缩率达到50%到70%。

**测试场景5：查看Filter Chain执行顺序（调试用）**

```bash
# 开启debug日志（编译时需加上--with-debug）
# 在nginx.conf中添加：error_log logs/error.log debug;

# 搜索gzip filter相关日志
grep "gzip" /usr/local/nginx/logs/error.log | head -20
```

### 步骤五：源码级理解

打开`src/http/modules/ngx_http_gzip_filter_module.c`，查看filter链的接入方式：

```c
static ngx_int_t
ngx_http_gzip_header_filter(ngx_http_request_t *r)
{
    ngx_http_gzip_ctx_t   *ctx;
    ngx_http_gzip_conf_t  *conf;

    conf = ngx_http_get_module_loc_conf(r, ngx_http_gzip_filter_module);

    // 检查配置是否开启、客户端是否支持gzip、内容类型是否匹配
    if (!conf->enable
        || r->headers_out.status != NGX_HTTP_OK
        || (r->headers_out.content_encoding
            && r->headers_out.content_encoding->value.len)
        || (r->headers_out.content_length_n != -1
            && r->headers_out.content_length_n < conf->min_length)
        || !ngx_http_gzip_accept_encoding(r))
    {
        return ngx_http_next_header_filter(r);  // 不满足条件，直接传给下一个filter
    }

    // 创建gzip上下文，初始化zlib压缩流
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_gzip_ctx_t));
    if (ctx == NULL) {
        return NGX_ERROR;
    }

    ngx_http_set_ctx(r, ctx, ngx_http_gzip_filter_module);

    // 设置响应头 Content-Encoding: gzip
    r->headers_out.content_encoding = &ngx_http_gzip;
    
    // 清除Content-Length（因为压缩后长度不确定）
    ngx_http_clear_content_length(r);
    
    // 如果使用了chunked传输，清除Accept-Ranges
    ngx_http_clear_accept_ranges(r);
    ngx_http_weak_etag(r);

    return ngx_http_next_header_filter(r);  // 继续传给下一个filter
}
```

再看`body_filter`的核心逻辑：

```c
static ngx_int_t
ngx_http_gzip_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_http_gzip_ctx_t  *ctx;
    ngx_chain_t          *cl;

    ctx = ngx_http_get_module_ctx(r, ngx_http_gzip_filter_module);
    if (ctx == NULL || ctx->done) {
        return ngx_http_next_body_filter(r, in);  // 没有gzip上下文，直接透传
    }

    // 逐chain压缩数据
    for (cl = in; cl; cl = cl->next) {
        // 使用zlib的deflate()进行压缩
        // 压缩后的数据放入out_chain
    }

    return ngx_http_next_body_filter(r, ctx->out);  // 传给下一个filter
}
```

这段代码展示了Filter Chain的核心设计：每个filter模块只关注自己的职责（gzip模块只负责压缩），处理完成后通过`ngx_http_next_header_filter`和`ngx_http_next_body_filter`将接力棒传给下一个模块。这种链式结构使得模块之间解耦，新增filter只需插入到链的合适位置即可。

---

### 测试验证

```bash
# 基础连通性验证（示例）
curl -i http://127.0.0.1:8080/health
```

验证要点：
- 关键接口返回码符合预期（2xx/4xx/5xx与设计一致）；
- 关键日志字段完整（如 request_id、upstream 耗时、状态码）；
- 在小流量压测下无异常错误峰值。

## 4. 项目总结

经过本章的实战，鲜果园的静态资源传输体积平均减少了65%，首屏加载时间从3.5秒降低到了1.2秒。小胖和小白不仅掌握了Gzip的配置技巧，更深入理解了Nginx Filter Chain的运作原理。

### 优劣对比：动态压缩 vs 静态预压缩

| 维度 | 动态压缩（gzip on） | 静态预压缩（gzip_static） |
|------|--------------------|-------------------------|
| **CPU消耗** | 每次请求都消耗CPU进行压缩 | 零实时CPU消耗，直接发送预压缩文件 |
| **响应延迟** | 有压缩延迟，首字节时间增加 | 无压缩延迟，首字节时间最优 |
| **磁盘占用** | 只存储原始文件 | 需同时存储原始文件和.gz文件 |
| **灵活性** | 自动适应任何内容变化 | 内容变更后需重新生成.gz文件 |
| **适用资源** | 动态内容、频繁变化的API响应 | 静态资源（JS、CSS、HTML） |
| **压缩级别** | 可动态调整 | 预压缩时固定，通常用最高级别9 |

### 适用场景

1. **静态资源分发**：JS、CSS、HTML等不常变化的文件，使用`gzip_static`预压缩，配合CDN分发。
2. **动态API响应**：JSON/XML格式的API返回，使用`gzip on`动态压缩，级别设为4到5。
3. **微服务网关**：Nginx作为网关聚合多个上游服务的响应，对聚合后的JSON进行压缩输出。
4. **日志/数据导出**：大规模文本数据导出接口，压缩后可减少80%以上传输时间。
5. **移动端Web优化**：在弱网环境下，压缩对用户体验的提升尤为明显。

### 不适用场景

1. **已压缩的文件**：图片（JPEG、PNG、WebP）、视频、PDF等本身已采用压缩编码的文件，gzip几乎无效，浪费CPU。
2. **极小文件**：小于256字节的内容，gzip头部开销可能使结果比原始文件更大。
3. **高CPU紧张环境**：如果服务器CPU已经吃紧，动态压缩会雪上加霜，此时应优先使用静态预压缩或升级硬件。
4. **内网高速传输**：内网千兆/万兆环境下，压缩节省的传输时间可能抵不上压缩本身消耗的CPU时间。

### 注意事项与警告

- **MIME类型清单**：`gzip_types`默认只包含`text/html`，必须显式列出所有需要压缩的MIME类型，否则CSS/JS/JSON不会被压缩。
- **ETag与压缩**：gzip压缩会改变响应体内容，Nginx会自动将强ETag转为弱ETag（加`W/`前缀），避免缓存不一致。
- **Vary头**：务必开启`gzip_vary`，否则CDN可能把压缩版本缓存后发给不支持gzip的客户端。
- **反向代理场景**：如果Nginx位于反向代理位置，上游已经压缩了内容，要注意避免双重压缩。检查上游响应的`Content-Encoding`头。
- **内存占用**：高压缩级别和大文件会消耗大量内存缓冲区，需根据`gzip_buffers`合理配置。

### 生产环境三大踩坑实录

**案例一：CDN缓存了压缩内容发给不支持gzip的客户端**

某公司将Nginx接入CDN后，部分老旧Android用户反馈页面乱码。排查发现Nginx未配置`gzip_vary on`，CDN缓存了`Content-Encoding: gzip`的版本。当不支持gzip的客户端请求时，CDN直接返回了压缩后的二进制数据。解决方案：开启`gzip_vary`，让CDN按`Accept-Encoding`区分缓存。

**案例二：图片资源被错误压缩导致CPU飙升**

某运维人员在`gzip_types`中使用了通配符`*`，导致所有MIME类型都被压缩，包括JPEG和PNG图片。这些已压缩格式再gzip几乎不减少体积，反而让每个图片请求都消耗大量CPU。监控显示CPU使用率从20%飙升到90%。解决方案：精确配置`gzip_types`，只包含文本类型。

**案例三：上游已压缩导致双重压缩**

某微服务架构中，上游Go服务已经对所有JSON响应做了gzip压缩。Nginx网关又开启了`gzip on`，导致JSON被二次压缩。客户端解压后得到的是第一次压缩的二进制流，而非原始JSON，解析直接报错。解决方案：在上游或Nginx中选择一层做压缩，或在上游响应头中保留`Content-Encoding: gzip`让Nginx识别跳过。

### 进阶思考题

**思考题1**：在Filter Chain中，`ngx_http_gzip_filter_module`的`body_filter`接收到的`ngx_chain_t *in`中的buffer可能是`ngx_buf_t`的链表结构。如果上游返回的是大文件（如10MB的日志导出），gzip模块如何处理流式压缩以避免一次性加载全部内容到内存？请结合源码中`ngx_http_gzip_filter_module.c`对`ngx_chain_t`的遍历和`ngx_buf_t->last_buf`、`ngx_buf_t->last_in_chain`标志的处理逻辑进行分析。

*提示*：关注filter如何处理`in_memory`缓冲区、`temp_file`缓冲区，以及zlib的`Z_SYNC_FLUSH`和`Z_FINISH`模式在流式输出中的应用。

**思考题2**：鲜果园的架构团队正在考虑将Nginx替换为基于Rust的高性能网关（如Pingora）。在评估过程中，他们发现这些新网关的filter模型与Nginx的Filter Chain有很大不同。请分析Nginx Filter Chain模型（单向链表、每个模块两个filter函数）的优势和局限性，并思考：在设计一个新的Web服务器内容处理框架时，你会采用什么样的架构来兼顾扩展性、性能和可维护性？

*提示*：对比Apache的Bucket Brigade模型、Node.js的Transform Stream模型，以及现代网关常见的中间件（Middleware）管道模型。

---

> **下一章预告**：第12章将深入SSL/TLS配置与HTTPS实战。我们将从SSL握手流程出发，结合`src/event/ngx_event_openssl.c`和`src/http/modules/ngx_http_ssl_module.c`源码，掌握证书配置、Session缓存、OCSP Stapling、HSTS、HTTP/2等高级特性，并挑战Qualys SSL Labs A+评级。敬请期待《第12章：SSL/TLS 配置与 HTTPS 实战》！

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。
