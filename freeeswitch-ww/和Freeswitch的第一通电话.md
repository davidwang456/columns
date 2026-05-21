和Freeswitch的第一通电话

下面是在Linux系统上配置FreeSWITCH和X-Lite软电话的详细步骤：

1. 安装FreeSWITCH

首先需要安装FreeSWITCH，可以通过官方网站下载安装包，也可以通过包管理器进行安装。安装完成后，启动FreeSWITCH服务。

2. 配置sip_profiles

进入FreeSWITCH安装目录下的conf目录，找到sip_profiles目录，编辑internal.xml文件，添加以下内容：

```xml
<extension name="xlite">
  <condition field="destination_number" expression="^1000$">
    <action application="set" data="sip_h_X-Lite-Call-Info=<http://www.counterpath.com>;x-bria;app-version=4.5.5(79189)"/>
    <action application="bridge" data="{sip_gateway(xlite)}/1000"/>
  </condition>
</extension>
```

上述配置文件中，username和password分别为X-Lite软电话的账号和密码，proxy为FreeSWITCH的IP地址，register为true表示开启注册，expire-seconds为注册有效期，caller-id-in-from为true表示在From头部中包含主叫号码，caller-id-name为呼叫显示的名称。

3. 配置dialplan

编辑dialplan目录下的default.xml文件，添加以下内容：

```xml
<extension name="xlite">
  <condition field="destination_number" expression="^1000$">
    <action application="set" data="sip_h_X-Lite-Call-Info=<http://www.counterpath.com>;x-bria;app-version=4.5.5(79189)"/>
    <action application="bridge" data="{sip_gateway(xlite)}/1000"/>
  </condition>
</extension>
```

上述配置文件中，destination_number为被叫号码，action中的第一行表示设置X-Lite软电话的Call-Info头部信息，第二行表示将呼叫桥接到X-Lite软电话。

4. 配置X-Lite

打开X-Lite软电话，进入Preferences->Account Settings，添加一个新的账号，配置如下：

Account Name: FreeSWITCH  
User ID: 1000  
Password: 1234  
Domain: 127.0.0.1  
SIP Proxy: 127.0.0.1

保存配置后，点击“Enable”按钮启用该账号。

5. 测试

在X-Lite软电话中拨打1000，应该可以听到FreeSWITCH的欢迎语音，并且在FreeSWITCH的日志中可以看到呼叫记录。

以上是在Linux系统上配置FreeSWITCH和X-Lite软电话的详细步骤，如果是在Windows系统上配置，步骤类似，只需根据系统环境进行相应的调整。

使用Linphone与FreeSWITCH打电话需要按照以下步骤进行配置：

1. 安装Linphone

首先需要下载并安装Linphone软电话，可以在官网上下载相应的安装包。

2. 配置Linphone账号

打开Linphone软电话，在菜单栏中选择“Linphone”->“Preferences”，进入“Account”选项卡，点击“Add”按钮添加一个新的账号。

在弹出的对话框中，选择“SIP”作为账号类型，填写账号信息，如下图所示：

![Linphone账号配置](https://i.imgur.com/7hKvZV4.png)

其中，SIP ID为账号号码，认证用户名和密码为FreeSWITCH的账号和密码，域名为FreeSWITCH的IP地址或域名，代理服务器为FreeSWITCH的IP地址或域名，保存后关闭窗口。

3. 配置FreeSWITCH

进入FreeSWITCH的conf目录，找到sip_profiles目录，编辑internal.xml文件，添加以下内容：

```
<include>
  <gateway name="linphone">
    <param name="username" value="1001"/>
    <param name="password" value="1234"/>
    <param name="proxy" value="127.0.0.1"/>
    <param name="register" value="true"/>
    <param name="expire-seconds" value="600"/>
    <param name="caller-id-in-from" value="true"/>
    <param name="caller-id-name" value="Linphone"/>
  </gateway>
</include>
```

上述配置文件中，username和password分别为Linphone软电话的账号和密码，proxy为FreeSWITCH的IP地址，register为true表示开启注册，expire-seconds为注册有效期，caller-id-in-from为true表示在From头部中包含主叫号码，caller-id-name为呼叫显示的名称。

4. 配置dialplan

编辑dialplan目录下的default.xml文件，添加以下内容：

```
<extension name="linphone">
  <condition field="destination_number" expression="^1001$">
    <action application="set" data="sip_h_Linphone-Call-Info=<http://www.linphone.org>;x-linphone;app-version=4.1.1(1702)"/>
    <action application="bridge" data="{sip_gateway(linphone)}/1001"/>
  </condition>
</extension>
```

上述配置文件中，destination_number为被叫号码，action中的第一行表示设置Linphone软电话的Call-Info头部信息，第二行表示将呼叫桥接到Linphone软电话。

5. 测试

在Linphone软电话中拨打1001，应该可以听到FreeSWITCH的欢迎语音，并且在FreeSWITCH的日志中可以看到呼叫记录。

以上就是使用Linphone与FreeSWITCH打电话的详细步骤，如果有问题可以查看FreeSWITCH和Linphone的官方文档或者在社区寻求帮助。
