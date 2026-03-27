# 背景

面试官：看你简历做过Elasticsearch的优化项目，能否谈谈Elasticsearch是如何处理http请求的？

面试者：？？？？？？？？

面试官：[捂脸] 大意了。

# Elasticsearch处理请求流程

**Netty处理http请求**

Elasticsearch内嵌了Netty来处理http请求，TransportService启动时启动Netty的http服务Netty4HttpServerTransport

```
    @Override
    protected void doStart() {
        boolean success = false;
        try {
            sharedGroup = sharedGroupFactory.getHttpGroup();
            serverBootstrap = new ServerBootstrap();

            serverBootstrap.group(sharedGroup.getLowLevelGroup());

            // NettyAllocator will return the channel type designed to work with the configuredAllocator
            serverBootstrap.channel(NettyAllocator.getServerChannelType());

            // Set the allocators for both the server channel and the child channels created
            serverBootstrap.option(ChannelOption.ALLOCATOR, NettyAllocator.getAllocator());
            serverBootstrap.childOption(ChannelOption.ALLOCATOR, NettyAllocator.getAllocator());

            serverBootstrap.childHandler(configureServerChannelHandler());
            serverBootstrap.handler(new ServerChannelExceptionHandler(this));

            serverBootstrap.childOption(ChannelOption.TCP_NODELAY, SETTING_HTTP_TCP_NO_DELAY.get(settings));
            serverBootstrap.childOption(ChannelOption.SO_KEEPALIVE, SETTING_HTTP_TCP_KEEP_ALIVE.get(settings));

            if (SETTING_HTTP_TCP_KEEP_ALIVE.get(settings)) {
                // Netty logs a warning if it can't set the option, so try this only on supported platforms
                if (IOUtils.LINUX || IOUtils.MAC_OS_X) {
                    if (SETTING_HTTP_TCP_KEEP_IDLE.get(settings) >= 0) {
                        final SocketOption<Integer> keepIdleOption = NetUtils.getTcpKeepIdleSocketOptionOrNull();
                        if (keepIdleOption != null) {
                            serverBootstrap.childOption(NioChannelOption.of(keepIdleOption), SETTING_HTTP_TCP_KEEP_IDLE.get(settings));
                        }
                    }
                    if (SETTING_HTTP_TCP_KEEP_INTERVAL.get(settings) >= 0) {
                        final SocketOption<Integer> keepIntervalOption = NetUtils.getTcpKeepIntervalSocketOptionOrNull();
                        if (keepIntervalOption != null) {
                            serverBootstrap.childOption(NioChannelOption.of(keepIntervalOption),
                                SETTING_HTTP_TCP_KEEP_INTERVAL.get(settings));
                        }
                    }
                    if (SETTING_HTTP_TCP_KEEP_COUNT.get(settings) >= 0) {
                        final SocketOption<Integer> keepCountOption = NetUtils.getTcpKeepCountSocketOptionOrNull();
                        if (keepCountOption != null) {
                            serverBootstrap.childOption(NioChannelOption.of(keepCountOption), SETTING_HTTP_TCP_KEEP_COUNT.get(settings));
                        }
                    }
                }
            }

            final ByteSizeValue tcpSendBufferSize = SETTING_HTTP_TCP_SEND_BUFFER_SIZE.get(settings);
            if (tcpSendBufferSize.getBytes() > 0) {
                serverBootstrap.childOption(ChannelOption.SO_SNDBUF, Math.toIntExact(tcpSendBufferSize.getBytes()));
            }

            final ByteSizeValue tcpReceiveBufferSize = SETTING_HTTP_TCP_RECEIVE_BUFFER_SIZE.get(settings);
            if (tcpReceiveBufferSize.getBytes() > 0) {
                serverBootstrap.childOption(ChannelOption.SO_RCVBUF, Math.toIntExact(tcpReceiveBufferSize.getBytes()));
            }

            serverBootstrap.option(ChannelOption.RCVBUF_ALLOCATOR, recvByteBufAllocator);
            serverBootstrap.childOption(ChannelOption.RCVBUF_ALLOCATOR, recvByteBufAllocator);

            final boolean reuseAddress = SETTING_HTTP_TCP_REUSE_ADDRESS.get(settings);
            serverBootstrap.option(ChannelOption.SO_REUSEADDR, reuseAddress);
            serverBootstrap.childOption(ChannelOption.SO_REUSEADDR, reuseAddress);

            bindServer();
            success = true;
        } finally {
            if (success == false) {
                doStop(); // otherwise we leak threads since we never moved to started
            }
        }
    }
```

如果你对这段代码比较陌生，估计你没有了解过netty，我来看看netty的一个http服务器的创建过程：(Netty官方提供的一个实例)

```
/**
 * Discards any incoming data.
 */
public final class DiscardServer {

    static final boolean SSL = System.getProperty("ssl") != null;
    static final int PORT = Integer.parseInt(System.getProperty("port", "8009"));

    public static void main(String[] args) throws Exception {
        // Configure SSL.
        final SslContext sslCtx;
        if (SSL) {
            SelfSignedCertificate ssc = new SelfSignedCertificate();
            sslCtx = SslContextBuilder.forServer(ssc.certificate(), ssc.privateKey()).build();
        } else {
            sslCtx = null;
        }

        EventLoopGroup bossGroup = new NioEventLoopGroup(1);
        EventLoopGroup workerGroup = new NioEventLoopGroup();
        try {
            ServerBootstrap b = new ServerBootstrap();
            b.group(bossGroup, workerGroup)
             .channel(NioServerSocketChannel.class)
             .handler(new LoggingHandler(LogLevel.INFO))
             .childHandler(new ChannelInitializer<SocketChannel>() {
                 @Override
                 public void initChannel(SocketChannel ch) {
                     ChannelPipeline p = ch.pipeline();
                     if (sslCtx != null) {
                         p.addLast(sslCtx.newHandler(ch.alloc()));
                     }
                     p.addLast(new DiscardServerHandler());
                 }
             });

            // Bind and start to accept incoming connections.
            ChannelFuture f = b.bind(PORT).sync();

            // Wait until the server socket is closed.
            // In this example, this does not happen, but you can do that to gracefully
            // shut down your server.
            f.channel().closeFuture().sync();
        } finally {
            workerGroup.shutdownGracefully();
            bossGroup.shutdownGracefully();
        }
    }
}
```

发现最重要的代码在

```
serverBootstrap.childHandler(configureServerChannelHandler());
```

即添加http处理的逻辑类，此时新建了一个http处理Channel：

```
    public ChannelHandler configureServerChannelHandler() {
        return new HttpChannelHandler(this, handlingSettings);
    }
    protected static class HttpChannelHandler extends ChannelInitializer<Channel> {

        private final Netty4HttpServerTransport transport;
        private final Netty4HttpRequestCreator requestCreator;
        private final Netty4HttpRequestHandler requestHandler;
        private final Netty4HttpResponseCreator responseCreator;
        private final HttpHandlingSettings handlingSettings;

        protected HttpChannelHandler(final Netty4HttpServerTransport transport, final HttpHandlingSettings handlingSettings) {
            this.transport = transport;
            this.handlingSettings = handlingSettings;
            this.requestCreator =  new Netty4HttpRequestCreator();
            this.requestHandler = new Netty4HttpRequestHandler(transport);
            this.responseCreator = new Netty4HttpResponseCreator();
        }

        @Override
        protected void initChannel(Channel ch) throws Exception {
            Netty4HttpChannel nettyHttpChannel = new Netty4HttpChannel(ch);
            ch.attr(HTTP_CHANNEL_KEY).set(nettyHttpChannel);
            ch.pipeline().addLast("byte_buf_sizer", NettyByteBufSizer.INSTANCE);
            ch.pipeline().addLast("read_timeout", new ReadTimeoutHandler(transport.readTimeoutMillis, TimeUnit.MILLISECONDS));
            final HttpRequestDecoder decoder = new HttpRequestDecoder(
                handlingSettings.getMaxInitialLineLength(),
                handlingSettings.getMaxHeaderSize(),
                handlingSettings.getMaxChunkSize());
            decoder.setCumulator(ByteToMessageDecoder.COMPOSITE_CUMULATOR);
            ch.pipeline().addLast("decoder", decoder);
            ch.pipeline().addLast("decoder_compress", new HttpContentDecompressor());
            ch.pipeline().addLast("encoder", new HttpResponseEncoder());
            final HttpObjectAggregator aggregator = new HttpObjectAggregator(handlingSettings.getMaxContentLength());
            aggregator.setMaxCumulationBufferComponents(transport.maxCompositeBufferComponents);
            ch.pipeline().addLast("aggregator", aggregator);
            if (handlingSettings.isCompression()) {
                ch.pipeline().addLast("encoder_compress", new HttpContentCompressor(handlingSettings.getCompressionLevel()));
            }
            ch.pipeline().addLast("request_creator", requestCreator);
            ch.pipeline().addLast("response_creator", responseCreator);
            ch.pipeline().addLast("pipelining", new Netty4HttpPipeliningHandler(logger, transport.pipeliningMaxEvents));
            ch.pipeline().addLast("handler", requestHandler);
            transport.serverAcceptedChannel(nettyHttpChannel);
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
            ExceptionsHelper.maybeDieOnAnotherThread(cause);
            super.
```

```
exceptionCaught(ctx, cause);
        }
    }
```

处理channel通过管道方式注册了很多处理逻辑，以注册顺序依次处理。其中，最重要的是Netty4HttpRequestHandler，它将消息传给RestController进行分发

```
    public ChannelHandler configureServerChannelHandler() {
        return new HttpChannelHandler(this, handlingSettings);
    }
    protected static class HttpChannelHandler extends ChannelInitializer<Channel> {

        private final Netty4HttpServerTransport transport;
        private final Netty4HttpRequestCreator requestCreator;
        private final Netty4HttpRequestHandler requestHandler;
        private final Netty4HttpResponseCreator responseCreator;
        private final HttpHandlingSettings handlingSettings;

        protected HttpChannelHandler(final Netty4HttpServerTransport transport, final HttpHandlingSettings handlingSettings) {
            this.transport = transport;
            this.handlingSettings = handlingSettings;
            this.requestCreator =  new Netty4HttpRequestCreator();
            this.requestHandler = new Netty4HttpRequestHandler(transport);
            this.responseCreator = new Netty4HttpResponseCreator();
        }

        @Override
        protected void initChannel(Channel ch) throws Exception {
            Netty4HttpChannel nettyHttpChannel = new Netty4HttpChannel(ch);
            ch.attr(HTTP_CHANNEL_KEY).set(nettyHttpChannel);
            ch.pipeline().addLast("byte_buf_sizer", NettyByteBufSizer.INSTANCE);
            ch.pipeline().addLast("read_timeout", new ReadTimeoutHandler(transport.readTimeoutMillis, TimeUnit.MILLISECONDS));
            final HttpRequestDecoder decoder = new HttpRequestDecoder(
                handlingSettings.getMaxInitialLineLength(),
                handlingSettings.getMaxHeaderSize(),
                handlingSettings.getMaxChunkSize());
            decoder.setCumulator(ByteToMessageDecoder.COMPOSITE_CUMULATOR);
            ch.pipeline().addLast("decoder", decoder);
            ch.pipeline().addLast("decoder_compress", new HttpContentDecompressor());
            ch.pipeline().addLast("encoder", new HttpResponseEncoder());
            final HttpObjectAggregator aggregator = new HttpObjectAggregator(handlingSettings.getMaxContentLength());
            aggregator.setMaxCumulationBufferComponents(transport.maxCompositeBufferComponents);
            ch.pipeline().addLast("aggregator", aggregator);
            if (handlingSettings.isCompression()) {
                ch.pipeline().addLast("encoder_compress", new HttpContentCompressor(handlingSettings.getCompressionLevel()));
            }
            ch.pipeline().addLast("request_creator", requestCreator);
            ch.pipeline().addLast("response_creator", responseCreator);
            ch.pipeline().addLast("pipelining", new Netty4HttpPipeliningHandler(logger, transport.pipeliningMaxEvents));
            ch.pipeline().addLast("handler", requestHandler);
            transport.serverAcceptedChannel(nettyHttpChannel);
        }

        @Override
        public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
            ExceptionsHelper.maybeDieOnAnotherThread(cause);
            super.exceptionCaught(ctx, cause);
        }
    }
```

在RestController中会将路径和handler关联起来，关联过程是：

Elasticsearch.init-->BootStrap.init-->BootStrap.setUp-->Node构造方法-->ActionModule.initRestHandlers，此处注册了具体处理http请求的action：

```
    public void initRestHandlers(Supplier<DiscoveryNodes> nodesInCluster) {
        List<AbstractCatAction> catActions = new ArrayList<>();
        Consumer<RestHandler> registerHandler = handler -> {
            if (handler instanceof AbstractCatAction) {
                catActions.add((AbstractCatAction) handler);
            }
            restController.registerHandler(handler);
        };
        registerHandler.accept(new RestAddVotingConfigExclusionAction());
        registerHandler.accept(new RestClearVotingConfigExclusionsAction());
        registerHandler.accept(new RestMainAction());
        registerHandler.accept(new RestNodesInfoAction(settingsFilter));
        registerHandler.accept(new RestRemoteClusterInfoAction());
        registerHandler.accept(new RestNodesStatsAction());
        registerHandler.accept(new RestNodesUsageAction());
        registerHandler.accept(new RestNodesHotThreadsAction());
        registerHandler.accept(new RestClusterAllocationExplainAction());
        registerHandler.accept(new RestClusterStatsAction());
        registerHandler.accept(new RestClusterStateAction(settingsFilter, threadPool));
        registerHandler.accept(new RestClusterHealthAction());
        registerHandler.accept(new RestClusterUpdateSettingsAction());
        registerHandler.accept(new RestClusterGetSettingsAction(settings, clusterSettings, settingsFilter));
        registerHandler.accept(new RestClusterRerouteAction(settingsFilter));
        registerHandler.accept(new RestClusterSearchShardsAction());
        registerHandler.accept(new RestPendingClusterTasksAction());
        registerHandler.accept(new RestPutRepositoryAction());
        registerHandler.accept(new RestGetRepositoriesAction(settingsFilter));
        registerHandler.accept(new RestDeleteRepositoryAction());
        registerHandler.accept(new RestVerifyRepositoryAction());
        registerHandler.accept(new RestCleanupRepositoryAction());
        registerHandler.accept(new RestGetSnapshotsAction());
        registerHandler.accept(new RestCreateSnapshotAction());
        registerHandler.accept(new RestCloneSnapshotAction());
        registerHandler.accept(new RestRestoreSnapshotAction());
        registerHandler.accept(new RestDeleteSnapshotAction());
        registerHandler.accept(new RestSnapshotsStatusAction());
        registerHandler.accept(new RestSnapshottableFeaturesAction());
        registerHandler.accept(new RestResetFeatureStateAction());
        registerHandler.accept(new RestGetFeatureUpgradeStatusAction());
        registerHandler.accept(new RestPostFeatureUpgradeAction());
        registerHandler.accept(new RestGetIndicesAction());
        registerHandler.accept(new RestIndicesStatsAction());
        registerHandler.accept(new RestIndicesSegmentsAction(threadPool));
        registerHandler.accept(new RestIndicesShardStoresAction());
        registerHandler.accept(new RestGetAliasesAction());
        registerHandler.accept(new RestIndexDeleteAliasesAction());
        registerHandler.accept(new RestIndexPutAliasAction());
        registerHandler.accept(new RestIndicesAliasesAction());
        registerHandler.accept(new RestCreateIndexAction());
        registerHandler.accept(new RestResizeHandler.RestShrinkIndexAction());
        registerHandler.accept(new RestResizeHandler.RestSplitIndexAction());
        registerHandler.accept(new RestResizeHandler.RestCloneIndexAction());
        registerHandler.accept(new RestRolloverIndexAction());
        registerHandler.accept(new RestDeleteIndexAction());
        registerHandler.accept(new RestCloseIndexAction());
        registerHandler.accept(new RestOpenIndexAction());
        registerHandler.accept(new RestAddIndexBlockAction());

        registerHandler.accept(new RestUpdateSettingsAction());
        registerHandler.accept(new RestGetSettingsAction());

        registerHandler.accept(new RestAnalyzeAction());
        registerHandler.accept(new RestGetIndexTemplateAction());
        registerHandler.accept(new RestPutIndexTemplateAction());
        registerHandler.accept(new RestDeleteIndexTemplateAction());
        registerHandler.accept(new RestPutComponentTemplateAction());
        registerHandler.accept(new RestGetComponentTemplateAction());
        registerHandler.accept(new RestDeleteComponentTemplateAction());
        registerHandler.accept(new RestPutComposableIndexTemplateAction());
        registerHandler.accept(new RestGetComposableIndexTemplateAction());
        registerHandler.accept(new RestDeleteComposableIndexTemplateAction());
        registerHandler.accept(new RestSimulateIndexTemplateAction());
        registerHandler.accept(new RestSimulateTemplateAction());

        registerHandler.accept(new RestPutMappingAction());
        registerHandler.accept(new RestGetMappingAction(threadPool));
        registerHandler.accept(new RestGetFieldMappingAction());

        registerHandler.accept(new RestRefreshAction());
        registerHandler.accept(new RestFlushAction());
        registerHandler.accept(new RestSyncedFlushAction());
        registerHandler.accept(new RestForceMergeAction());
        registerHandler.accept(new RestClearIndicesCacheAction());
        registerHandler.accept(new RestResolveIndexAction());

        registerHandler.accept(new RestIndexAction());
        registerHandler.accept(new CreateHandler());
        registerHandler.accept(new AutoIdHandler(nodesInCluster));
        registerHandler.accept(new RestGetAction());
        registerHandler.accept(new RestGetSourceAction());
        registerHandler.accept(new RestMultiGetAction(settings));
        registerHandler.accept(new RestDeleteAction());
        registerHandler.accept(new RestCountAction());
        registerHandler.accept(new RestTermVectorsAction());
        registerHandler.accept(new RestMultiTermVectorsAction());
        registerHandler.accept(new RestBulkAction(settings));
        registerHandler.accept(new RestUpdateAction());

        registerHandler.accept(new RestSearchAction());
        registerHandler.accept(new RestSearchScrollAction());
        registerHandler.accept(new RestClearScrollAction());
        registerHandler.accept(new RestOpenPointInTimeAction());
        registerHandler.accept(new RestClosePointInTimeAction());
        registerHandler.accept(new RestMultiSearchAction(settings));

        registerHandler.accept(new RestValidateQueryAction());

        registerHandler.accept(new RestExplainAction());

        registerHandler.accept(new RestRecoveryAction());

        registerHandler.accept(new RestReloadSecureSettingsAction());

        // Scripts API
        registerHandler.accept(new RestGetStoredScriptAction());
        registerHandler.accept(new RestPutStoredScriptAction());
        registerHandler.accept(new RestDeleteStoredScriptAction());
        registerHandler.accept(new RestGetScriptContextAction());
        registerHandler.accept(new RestGetScriptLanguageAction());

        registerHandler.accept(new RestFieldCapabilitiesAction());

        // Tasks API
        registerHandler.accept(new RestListTasksAction(nodesInCluster));
        registerHandler.accept(new RestGetTaskAction());
        registerHandler.accept(new RestCancelTasksAction(nodesInCluster));

        // Ingest API
        registerHandler.accept(new RestPutPipelineAction());
        registerHandler.accept(new RestGetPipelineAction());
        registerHandler.accept(new RestDeletePipelineAction());
        registerHandler.accept(new RestSimulatePipelineAction());

        // Dangling indices API
        registerHandler.accept(new RestListDanglingIndicesAction());
        registerHandler.accept(new RestImportDanglingIndexAction());
        registerHandler.accept(new RestDeleteDanglingIndexAction());

        // CAT API
        registerHandler.accept(new RestAllocationAction());
        registerHandler.accept(new RestShardsAction());
        registerHandler.accept(new RestMasterAction());
        registerHandler.accept(new RestNodesAction());
        registerHandler.accept(new RestTasksAction(nodesInCluster));
        registerHandler.accept(new RestIndicesAction());
        registerHandler.accept(new RestSegmentsAction());
        // Fully qualified to prevent interference with rest.action.count.RestCountAction
        registerHandler.accept(new org.elasticsearch.rest.action.cat.RestCountAction());
        // Fully qualified to prevent interference with rest.action.indices.RestRecoveryAction
        registerHandler.accept(new RestCatRecoveryAction());
        registerHandler.accept(new RestHealthAction());
        registerHandler.accept(new org.elasticsearch.rest.action.cat.RestPendingClusterTasksAction());
        registerHandler.accept(new RestAliasAction());
        registerHandler.accept(new RestThreadPoolAction());
        registerHandler.accept(new RestPluginsAction());
        registerHandler.accept(new RestFielddataAction());
        registerHandler.accept(new RestNodeAttrsAction());
        registerHandler.accept(new RestRepositoriesAction());
        registerHandler.accept(new RestSnapshotAction());
        registerHandler.accept(new RestTemplatesAction());
        registerHandler.accept(new RestAnalyzeIndexDiskUsageAction());
        registerHandler.accept(new RestFieldUsageStatsAction());

        registerHandler.accept(new RestUpgradeActionDeprecated());

        for (ActionPlugin plugin : actionPlugins) {
            for (RestHandler handler : plugin.getRestHandlers(settings, restController, clusterSettings, indexScopedSettings,
                    settingsFilter, indexNameExpressionResolver, nodesInCluster)) {
                registerHandler.accept(handler);
            }
        }
        registerHandler.accept(new RestCatAction(catActions));
    }
```

比如我们常用的查看elastisearch健康状态

```
# curl 'localhost:9200/_cat/health?v'
```

的action对应为：RestHealthAction

```
/*
 * Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
 * or more contributor license agreements. Licensed under the Elastic License
 * 2.0 and the Server Side Public License, v 1; you may not use this file except
 * in compliance with, at your election, the Elastic License 2.0 or the Server
 * Side Public License, v 1.
 */

package org.elasticsearch.rest.action.cat;

import org.elasticsearch.action.admin.cluster.health.ClusterHealthRequest;
import org.elasticsearch.action.admin.cluster.health.ClusterHealthResponse;
import org.elasticsearch.client.node.NodeClient;
import org.elasticsearch.common.Table;
import org.elasticsearch.rest.RestRequest;
import org.elasticsearch.rest.RestResponse;
import org.elasticsearch.rest.action.RestResponseListener;

import java.util.List;
import java.util.Locale;

import static org.elasticsearch.rest.RestRequest.Method.GET;

public class RestHealthAction extends AbstractCatAction {

    @Override
    public List<Route> routes() {
        return List.of(new Route(GET, "/_cat/health"));
    }

    @Override
    public String getName() {
        return "cat_health_action";
    }

    @Override
    public boolean allowSystemIndexAccessByDefault() {
        return true;
    }

    @Override
    protected void documentation(StringBuilder sb) {
        sb.append("/_cat/health\n");
    }


    @Override
    public RestChannelConsumer doCatRequest(final RestRequest request, final NodeClient client) {
        ClusterHealthRequest clusterHealthRequest = new ClusterHealthRequest();

        return channel -> client.admin().cluster().health(clusterHealthRequest, new RestResponseListener<ClusterHealthResponse>(channel) {
            @Override
            public RestResponse buildResponse(final ClusterHealthResponse health) throws Exception {
                return RestTable.buildResponse(buildTable(health, request), channel);
            }
        });
    }

    @Override
    protected Table getTableWithHeader(final RestRequest request) {
        Table t = new Table();
        t.startHeadersWithTimestamp();
        t.addCell("cluster", "alias:cl;desc:cluster name");
        t.addCell("status", "alias:st;desc:health status");
        t.addCell("node.total", "alias:nt,nodeTotal;text-align:right;desc:total number of nodes");
        t.addCell("node.data", "alias:nd,nodeData;text-align:right;desc:number of nodes that can store data");
        t.addCell("shards", "alias:t,sh,shards.total,shardsTotal;text-align:right;desc:total number of shards");
        t.addCell("pri", "alias:p,shards.primary,shardsPrimary;text-align:right;desc:number of primary shards");
        t.addCell("relo", "alias:r,shards.relocating,shardsRelocating;text-align:right;desc:number of relocating nodes");
        t.addCell("init", "alias:i,shards.initializing,shardsInitializing;text-align:right;desc:number of initializing nodes");
        t.addCell("unassign", "alias:u,shards.unassigned,shardsUnassigned;text-align:right;desc:number of unassigned shards");
        t.addCell("pending_tasks", "alias:pt,pendingTasks;text-align:right;desc:number of pending tasks");
        t.addCell("max_task_wait_time", "alias:mtwt,maxTaskWaitTime;text-align:right;desc:wait time of longest task pending");
        t.addCell("active_shards_percent", "alias:asp,activeShardsPercent;text-align:right;desc:active number of shards in percent");
        t.endHeaders();

        return t;
    }

    private Table buildTable(final ClusterHealthResponse health, final RestRequest request) {
        Table t = getTableWithHeader(request);
        t.startRow();
        t.addCell(health.getClusterName());
        t.addCell(health.getStatus().name().toLowerCase(Locale.ROOT));
        t.addCell(health.getNumberOfNodes());
        t.addCell(health.getNumberOfDataNodes());
        t.addCell(health.getActiveShards());
        t.addCell(health.getActivePrimaryShards());
        t.addCell(health.getRelocatingShards());
        t.addCell(health.getInitializingShards());
        t.addCell(health.getUnassignedShards());
        t.addCell(health.getNumberOfPendingTasks());
        t.addCell(health.getTaskMaxWaitingTime().millis() == 0 ? "-" : health.getTaskMaxWaitingTime());
        t.addCell(String.format(Locale.ROOT, "%1.1f%%", health.getActiveShardsPercent()));
        t.endRow();
        return t;
    }
}
```

从上面的代码上来看，请求调用NodeClient来获取health信息，然后组装成table返回用户。

**NodeClient调用服务组装响应**

RestIndicesAction调用doExecute方法将任务放入taskManager中

```
    @Override
    public <Request extends ActionRequest, Response extends ActionResponse>
    void doExecute(ActionType<Response> action, Request request, ActionListener<Response> listener) {
        // Discard the task because the Client interface doesn't use it.
        try {
            executeLocally(action, request, listener);
        } catch (TaskCancelledException | IllegalArgumentException | IllegalStateException e) {
            // #executeLocally returns the task and throws TaskCancelledException if it fails to register the task because the parent
            // task has been cancelled, IllegalStateException if the client was not in a state to execute the request because it was not
            // yet properly initialized or IllegalArgumentException if header validation fails we forward them to listener since this API
            // does not concern itself with the specifics of the task handling
            listener.onFailure(e);
        }
    }

    /**
     * Execute an {@link ActionType} locally, returning that {@link Task} used to track it, and linking an {@link ActionListener}.
     * Prefer this method if you don't need access to the task when listening for the response. This is the method used to
     * implement the {@link Client} interface.
     *
     * @throws TaskCancelledException if the request's parent task has been cancelled already
     */
    public <    Request extends ActionRequest,
                Response extends ActionResponse
            > Task executeLocally(ActionType<Response> action, Request request, ActionListener<Response> listener) {
        return taskManager.registerAndExecute("transport", transportAction(action), request, localConnection,
                (t, r) -> {
                    try {
                        listener.onResponse(r);
                    } catch (Exception e) {
                        assert false : new AssertionError("callback must handle its own exceptions", e);
                        throw e;
                    }
                }, (t, e) -> {
                    try {
                        listener.onFailure(e);
                    } catch (Exception ex) {
                        ex.addSuppressed(e);
                        assert false : new AssertionError("callback must handle its own exceptions", ex);
                        throw ex;
                    }
                });
    }
```

TaskManager执行任务

```
    public <Request extends ActionRequest, Response extends ActionResponse>
    Task registerAndExecute(String type, TransportAction<Request, Response> action, Request request, Transport.Connection localConnection,
                            BiConsumer<Task, Response> onResponse, BiConsumer<Task, Exception> onFailure) {
        final Releasable unregisterChildNode;
        if (request.getParentTask().isSet()) {
            unregisterChildNode = registerChildConnection(request.getParentTask().getId(), localConnection);
        } else {
            unregisterChildNode = () -> {};
        }
        final Task task;
        try {
            task = register(type, action.actionName, request);
        } catch (TaskCancelledException e) {
            unregisterChildNode.close();
            throw e;
        }
        // NOTE: ActionListener cannot infer Response, see https://bugs.openjdk.java.net/browse/JDK-8203195
        action.execute(task, request, new ActionListener<Response>() {
            @Override
            public void onResponse(Response response) {
                try {
                    Releasables.close(unregisterChildNode, () -> unregister(task));
                } finally {
                    onResponse.accept(task, response);
                }
            }

            @Override
            public void onFailure(Exception e) {
                try {
                    Releasables.close(unregisterChildNode, () -> unregister(task));
                } finally {
                    onFailure.accept(task, e);
                }
            }
        });
        return task;
    }
```

注册了TransportAction来执行任务列表，其实现类为TransportMainAction

```
/*
 * Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
 * or more contributor license agreements. Licensed under the Elastic License
 * 2.0 and the Server Side Public License, v 1; you may not use this file except
 * in compliance with, at your election, the Elastic License 2.0 or the Server
 * Side Public License, v 1.
 */

package org.elasticsearch.action.main;

import org.elasticsearch.Build;
import org.elasticsearch.Version;
import org.elasticsearch.action.ActionListener;
import org.elasticsearch.action.support.ActionFilters;
import org.elasticsearch.action.support.HandledTransportAction;
import org.elasticsearch.cluster.ClusterState;
import org.elasticsearch.cluster.service.ClusterService;
import org.elasticsearch.common.inject.Inject;
import org.elasticsearch.common.settings.Settings;
import org.elasticsearch.node.Node;
import org.elasticsearch.tasks.Task;
import org.elasticsearch.transport.TransportService;

public class TransportMainAction extends HandledTransportAction<MainRequest, MainResponse> {

    private final String nodeName;
    private final ClusterService clusterService;

    @Inject
    public TransportMainAction(Settings settings, TransportService transportService,
                               ActionFilters actionFilters, ClusterService clusterService) {
        super(MainAction.NAME, transportService, actionFilters, MainRequest::new);
        this.nodeName = Node.NODE_NAME_SETTING.get(settings);
        this.clusterService = clusterService;
    }

    @Override
    protected void doExecute(Task task, MainRequest request, ActionListener<MainResponse> listener) {
        ClusterState clusterState = clusterService.state();
        listener.onResponse(
            new MainResponse(nodeName, Version.CURRENT, clusterState.getClusterName(),
                    clusterState.metadata().clusterUUID(), Build.CURRENT));
    }
}
```

即调用clusterService的stata方法来获取状态信息。

# 总结

Elasticsearch处理Http请求的过程如下图所示。

![](http://p6.toutiaoimg.com/large/tos-cn-i-jcdsk5yqko/eb212ddfa8bd43f0904a0b7b07729cf8)

注意，elasticsearch的http请求是reactor模式，是异步非阻塞的。
