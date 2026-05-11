# 第13章：前端 WebApp 定制与嵌入

## 1. 项目背景

"你们的 AI 助手回答质量不错，但这个聊天界面太丑了——全白底、默认 Logo、没有我们公司的品牌色。"这是客户验收时最常听到的反馈之一。Dify 提供了"WebApp 发布"功能——一键生成一个对外可访问的聊天页面，用户可以在这个页面上和你的 AI 助手对话。但默认的 WebApp 页面是一个"通用壳"——白色背景、Dify Logo、标准字体，如果你要把它嵌入到公司的官网或 App 里，显然需要改造。

Dify 对 WebApp 的定制支持分为三个层次：
1. **零代码定制**：在 Dify 控制台中直接修改 Logo、配色、欢迎语、建议问题（5 分钟搞定）
2. **iframe 嵌入**：把 WebApp 的聊天组件嵌入到你自己的网页中，通过 PostMessage 通信
3. **Fork 前端源码**：深度定制——修改 React 组件、交互逻辑、页面布局（需要前端开发能力）

本章从最简的零代码定制开始，逐步深入到 iframe 通信协议和前端源码结构，帮你把 Dify 的聊天能力"无缝"地融入自己的产品中。无论你是运营还是前端开发，都能找到适合自己水平的定制方式。

## 2. 项目设计——剧本式交锋对话

**小胖**：（指着屏幕上 Dify 的 WebApp 页面）"大师，我做的客服助手在 Dify 控制台里测试挺好的，但是发布出去后，那个界面也太丑了——白底黑字，左上角还顶着 Dify 的 Logo。老板说'这个不能直接给客户看'，怎么办？"

**大师**："先别急着改代码。Dify 提供了界面定制功能——在 App 发布页面，你可以上传自己的 Logo、改标题、换配色方案、设置欢迎语。这些不需要写一行代码，5 分钟就能搞定。"

**小白**：（在发布页操作）"我看到了——可以上传 Logo、改主题色、设置默认语言。但这只是'换皮'，如果需要更大的改动呢？比如把聊天窗口嵌到我们官网的右下角，像 Intercom 那种效果？"

**大师**："这就是 iframe 嵌入的用场。Dify 支持三种嵌入方式：
1. **全屏嵌入**：直接 `iframe` 指向 WebApp URL，占满一整块区域。
2. **弹窗模式**：默认隐藏，用户点击浮动按钮后才弹出聊天窗口。
3. **自定义嵌入**：使用 JavaScript SDK（`dify-chatbot-bubble`）控制的浮动气泡。"

**技术映射**：iframe 嵌入 = Web Components，通过 PostMessage API 实现跨域通信（父页面 ↔ iframe）。

**小胖**："iframe 怎么和父页面通信？比如用户在聊天窗口里点了某个按钮，我想让父页面跳转。"

**大师**："Dify 的 WebApp iframe 支持 PostMessage 协议。当聊天窗口内发生特定事件时，iframe 会向父页面发送 `window.parent.postMessage()` 消息。父页面可以监听这些事件来做响应——比如用户说'我要下单'，父页面收到后自动跳转到订单页。"

**小白**："那如果我想改得更彻底——比如把聊天界面的输入框改成语音输入、消息列表改成卡片式——这能做到吗？"

**大师**："那就需要 Fork 前端源码了。Dify 的前端是开源的 Next.js 项目，WebApp 相关的代码在 `web/app/(shareLayout)/` 目录下。你可以：
1. Fork `web/` 目录
2. 修改聊天组件（`web/app/components/share/`）
3. 用自己的 Docker 镜像替换 `dify-web` 容器"

**技术映射**：Fork 前端源码 = 完全掌控 UI，代价是后续要和 Dify 主版本保持同步（merge 上游更新）。

## 3. 项目实战

### 分步实现

#### 步骤1：零代码定制——改 Logo、配色、欢迎语（目标：5 分钟品牌化）

1. 进入 Chat App → **发布** → **WebApp 设置**
2. 配置以下内容：

```yaml
WebApp 名称：Acme 智能助手
WebApp 描述：7×24 小时为您服务
应用图标：上传公司 Logo（建议 256×256 PNG）

主题色：#1890FF（主品牌色）
背景色：#F5F5F5（浅灰底色）

欢迎语：👋 您好！我是 Acme 公司的智能助手小 A 
       有什么可以帮您的？

建议问题：
  - 如何申请退换货？
  - 我的订单到哪里了？
  - 公司上班时间是？
  
文件上传：开启（允许用户上传截图描述问题）
语音输入：开启（允许语音转文字）
引用来源：开启（显示知识库引用）
```

3. 点击**保存并发布**，复制公开链接
4. 在浏览器新标签页打开，检查品牌化效果

**关键操作**：Dify 支持实时预览——在发布页的右侧就能看到修改后的效果。每个配置项的变更都能即时反映，不需要"保存→刷新→查看"的循环。

#### 步骤2：iframe 嵌入——把聊天窗口塞进你的网页（目标：无缝集成）

**基础嵌入代码**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Acme 官网 - 智能客服</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: #f0f2f5;
        }
        
        .main-content {
            padding: 40px;
            text-align: center;
        }
        
        /* 浮动聊天按钮 */
        .chat-toggle-btn {
            position: fixed;
            bottom: 30px;
            right: 30px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: #1890FF;
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 9999;
            transition: transform 0.2s;
        }
        
        .chat-toggle-btn:hover {
            transform: scale(1.1);
        }
        
        /* 聊天窗口容器 */
        .chat-container {
            position: fixed;
            bottom: 100px;
            right: 30px;
            width: 400px;
            height: 600px;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
            overflow: hidden;
            display: none;
            z-index: 9998;
            background: white;
        }
        
        .chat-container.active {
            display: block;
        }
        
        .chat-container iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        
        .chat-header {
            padding: 12px 16px;
            background: #1890FF;
            color: white;
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-close-btn {
            background: none;
            border: none;
            color: white;
            font-size: 20px;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="main-content">
        <h1>欢迎来到 Acme 科技</h1>
        <p>这是我们的官网主页内容</p>
    </div>
    
    <!-- 浮动按钮 -->
    <button class="chat-toggle-btn" id="chatToggle" title="联系客服">💬</button>
    
    <!-- 聊天窗口 -->
    <div class="chat-container" id="chatContainer">
        <div class="chat-header">
            <span>💬 Acme 智能助手</span>
            <button class="chat-close-btn" id="chatClose">✕</button>
        </div>
        <iframe 
            id="difyIframe"
            src="http://localhost/chat/WEBAPP_TOKEN"
            allow="microphone">
        </iframe>
    </div>
    
    <script>
        const toggleBtn = document.getElementById('chatToggle');
        const container = document.getElementById('chatContainer');
        const closeBtn = document.getElementById('chatClose');
        
        toggleBtn.addEventListener('click', () => {
            container.classList.add('active');
            toggleBtn.style.display = 'none';
        });
        
        closeBtn.addEventListener('click', () => {
            container.classList.remove('active');
            toggleBtn.style.display = 'block';
        });
    </script>
</body>
</html>
```

#### 步骤3：PostMessage 通信——让父页面"听懂"聊天窗口（目标：双向通信）

```html
<script>
// 父页面监听 iframe 发来的消息
window.addEventListener('message', (event) => {
    // 安全检查：验证消息来源
    if (event.origin !== 'http://localhost') return;
    
    const data = event.data;
    
    switch (data.type) {
        case 'chat-ready':
            console.log('聊天窗口已就绪');
            break;
        
        case 'conversation-started':
            console.log('新会话开始:', data.conversation_id);
            // 可以在父页面上报统计
            trackEvent('chat_started', data.conversation_id);
            break;
        
        case 'message-received':
            // 用户发的消息
            if (data.role === 'user') {
                console.log('用户:', data.text);
            }
            // AI 的回复
            if (data.role === 'assistant') {
                console.log('AI:', data.text);
                // 检查是否包含特定关键词（如"我想下单"）
                if (data.text.includes('下单')) {
                    window.location.href = '/order/create';
                }
            }
            break;
        
        case 'app-rate':
            // 用户评分
            console.log('用户评分:', data.rating);
            break;
    }
});

// 父页面向 iframe 发送消息
function sendToDify(data) {
    document.getElementById('difyIframe').contentWindow.postMessage(data, '*');
}

// 示例：自动填入用户信息
document.getElementById('difyIframe').addEventListener('load', () => {
    sendToDify({
        type: 'set-inputs',
        inputs: {
            user_name: '张三',
            user_level: 'VIP'
        }
    });
});
</script>
```

**PostMessage 协议说明**：

| 事件方向 | type | 含义 |
|---------|------|------|
| iframe → 父 | `chat-ready` | WebApp 加载完成 |
| iframe → 父 | `conversation-started` | 新会话创建，携带 conversation_id |
| iframe → 父 | `message-received` | 收到消息（role: user/assistant） |
| iframe → 父 | `app-rate` | 用户提交评分 |
| 父 → iframe | `set-inputs` | 设置 App 的输入变量 |

#### 步骤4：Fork 前端源码——深度定制聊天界面（目标：完全掌控 UI）

如果你需要修改聊天组件的交互逻辑，深入定制：

```bash
# 1. 克隆 Dify 前端仓库（如果只改了 web/）
cd /your-project
git clone https://github.com/langgenius/dify.git
cd dify/web

# 2. 安装依赖
pnpm install

# 3. WebApp 核心源码位置
# web/app/(shareLayout)/
#   ├── layout.tsx                    # WebApp 页面布局
#   ├── [token]/
#   │   └── page.tsx                  # WebApp 入口页面
#   └── components/
#       └── chatbot/                  # 聊天机器人组件
#
# web/app/components/share/
#   ├── chat/                         # 聊天界面组件
#   ├── form/                         # 表单填写组件
#   └── text-generation/              # 文本生成组件

# 4. 启动本地开发服务器
pnpm dev

# 5. 修改后构建自定义镜像（替换官方的 dify-web）
# Dockerfile.web (自定义)
FROM node:20-alpine AS builder
WORKDIR /app
COPY web/ ./
RUN pnpm install && pnpm build

FROM node:20-alpine
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
```

**WebApp 组件树（简化）**：

```
Chatbot.tsx (主容器)
├── Header.tsx (顶部：Logo + 标题 + 设置)
├── MessageList.tsx (消息列表)
│   ├── UserMessage.tsx (用户消息气泡)
│   ├── AssistantMessage.tsx (AI 消息气泡)
│   │   ├── MarkdownRenderer.tsx (Markdown 渲染)
│   │   └── SourceList.tsx (知识库引用)
│   └── LoadingMessage.tsx (加载动画)
├── InputArea.tsx (输入框)
│   ├── TextInput.tsx (文本输入)
│   ├── FileUpload.tsx (文件上传)
│   └── VoiceInput.tsx (语音输入)
└── SuggestionList.tsx (建议问题列表)
```

### 测试验证

```bash
# 测试 1：验证 WebApp 发布
# 在浏览器打开公开发布链接，检查：
# - Logo 是否正确
# - 欢迎语是否正确  
# - 建议问题是否可点击
# - 发送消息是否正常

# 测试 2：验证 iframe 嵌入
# 创建 test-embed.html（复制步骤 2 的代码）
# 用浏览器打开，点击浮动按钮，检查聊天窗口是否弹出

# 测试 3：验证 PostMessage 通信
# 在浏览器 DevTools Console 中运行：
document.getElementById('difyIframe').contentWindow.postMessage(
    {type: 'set-inputs', inputs: {user_name: '测试用户'}}, '*'
)
# 然后在聊天窗口发消息，观察 AI 是否使用了传入的变量
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **零代码定制** | Logo/配色/欢迎语修改立竿见影，运营人员即可操作 | 可配项有限，不能改布局和交互 |
| **iframe 嵌入** | 一行 HTML 代码即可集成，兼容所有前端框架 | 受跨域限制，UI 统一性不如原生组件 |
| **PostMessage** | 标准 Web API，支持双向通信 | 消息格式文档不够详细，需要自行探索 |
| **Fork 源码** | 完全掌控 UI，可实现任何定制 | 维护成本高，每次 Dify 升级需要手动合并 |

### 适用场景

| 场景 | 推荐方案 |
|------|---------|
| **快速演示/Demo** | 公开链接直接分享 |
| **公司官网集成** | iframe + PostMessage，改 Logo 和配色 |
| **SaaS 产品内嵌** | iframe + 动态变量注入（如用户信息、上下文数据） |
| **品牌 App** | Fork 前端源码，完全自定义 UI |
| **微信小程序/移动端** | 使用 WebView + Dify WebApp URL |

### 注意事项

1. **HTTPS 要求**：如果你的网站是 HTTPS，Dify WebApp 也必须是 HTTPS，否则 iframe 会被浏览器阻止
2. **CORS 配置**：如果使用 PostMessage，确保 Dify 的 `service_api_url` 和你的网站域名都在允许列表中
3. **移动端适配**：默认 WebApp 对移动端有响应式适配，但宽度小于 360px 时体验可能不佳

### 常见踩坑经验

1. **坑：iframe 不显示** → 根因：父页面 HTTPS 但 iframe 的 SRC 是 HTTP（Mixed Content 被浏览器拦截）。解决：为 Dify 配置 SSL 证书
2. **坑：PostMessage 收不到消息** → 根因：事件监听代码放在 iframe 加载之前，或 event.origin 校验不匹配。解决：把监听代码放最前面，或用 `'*'` 临时跳过 origin 校验（仅开发环境）
3. **坑：Fork 前端后样式全乱** → 根因：Tailwind CSS 配置或版本不匹配。解决：确认 `tailwind.config.js` 与原版一致，检查 `pnpm-lock.yaml`

### 思考题

1. **进阶题**：如果用户在使用 Dify WebApp 时切换到其他标签页，当 AI 生成完回复后，如何通过浏览器的 Notification API 给用户发送系统通知？

2. **进阶题**：如果你想把 Dify 的聊天组件封装成一个标准的 Web Component（`<dify-chat>` 标签），让任何框架（React/Vue/Angular）都能使用，你会如何设计？（提示：Shadow DOM + 自定义元素）

> **参考答案**：见附录 D
