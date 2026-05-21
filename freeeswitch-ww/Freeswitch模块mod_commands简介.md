Freeswitch模块mod_commands简介

### 背景

mod_commands是FreeSWITCH中的一个模块，它提供了一系列的命令和控制接口，用于管理和控制FreeSWITCH的各种功能和服务。

mod_commands模块的主要功能包括：

1. 系统管理命令：mod_commands提供了一些系统管理命令，如重启FreeSWITCH、查看系统状态、查看日志等。

2. 通话控制命令：mod_commands提供了一系列的通话控制命令，如呼叫转移、呼叫保持、呼叫会议等，可以对正在进行的通话进行控制和管理。

3. 账户管理命令：mod_commands可以用于管理FreeSWITCH中的用户账户，包括创建账户、删除账户、修改账户设置等。

4. 音频控制命令：mod_commands提供了一些音频控制命令，如静音、调节音量等，可以对通话中的音频进行控制。

5. 事件订阅命令：mod_commands支持事件订阅，可以订阅和接收FreeSWITCH中的各种事件，如呼叫状态变化、音频流状态变化等。

通过mod_commands模块，用户可以通过命令行界面或API接口来管理和控制FreeSWITCH的各种功能和服务。这样，用户可以方便地进行系统管理、通话控制、账户管理和音频控制等操作，以满足不同的需求和场景。

### 核心命令

主要在[mod_commands.c](http://fisheye.freeswitch.org/browse/freeswitch.git/src/mod/applications/mod_commands/mod_commands.c)中实现。

> 注：  
> 一些状态或列表命令的返回结果默认是以逗号进行分隔的列表。一些模块的返回结果可能也会包含逗号，这样就导致针对结果的自动化处理比较困难。一个解决方法是，是在命令的最后加上`as xml`，这样返回的就是`xml`格式的结果。

#### 1、acl

使用`acl`列表判断`ip`地址是否为合法访问。

```
acl
```

#### 2、命令别名alias

- 别名:一种针对常用命令的快捷输入方式
- alias,Alias,[add|stickyadd] <别名> <命令> | del [<别名>|*]

例子:

```
freeswitch> alias add reloadall reloadacl reloadxml  +OK  
freeswitch> alias add unreg sofia profile internal flush_inbound_reg  +OK
```

别名在重启后需要重设，如果需要重启后仍然生效，需要使用`stickyadd`参数，如下：

```
freeswitch> alias stickyadd reloadall reloadacl reloadxml  +OK
```

> 注：只在`mod_console`中起作用，在`fs_cli`中无效。    
> 译者注：`mod_console`为以前台模式启动的`freeswitch`的命令输入界面。而`fs_cli`指的是`freeswitch`的客户端。

#### 3、api

用于在线程中执行`api`命令,此命令为阻塞模式

```
api <api命令>[ <参数>]
```

例：

```
originate user/1005 &park
```

#### 4、bgapi

用于在线程中执行`api`命令,此命令为非阻塞

```
bgapi <api命令>[ <参数>]
```

例：

```
bgapi originate user/1005 &park
```

#### 5、complete

Complete.

```
complete add <word>|del [<word>|*]
```

注：该命令从没用过，不知道干啥的。

#### 6、cond

运算指定的条件，并返回结果。

```
cond <条件表达式> ? <true val> : <false val>
```

条件表达式支持的条件有：

- == 等于 
- < 小于 
- > 大于

例子: 如果第一个值大于第二个，则返回true

```
cond 5 > 3 ? true : false true
```

拨号方案中的例子:

```
<action application="set" data="voicemail_authorized=${cond(${sip_authorized} == true ? true : false)}"/>
```

稍复杂的例子:

```
<action application="set" data="voicemail_authorized=${cond(${sip_acl_authed_by} == domains ? false : ${cond(${sip_authorized} == true ? true : false)})}"/>
```

#### 7、domain_exists

检查指定的`domain`是否存在：

```
domain_exists <domain>
```

#### 8、eval

`Eval (noop)`,计算字符串，扩展通道变量.

```
eval [uuid:<uuid> ]<expression>
```

例:

```
eval ${domain} 10.15.0.94  eval Hello, World! Hello, World!
eval uuid:e72aff5c-6838-49a8-98fb-84c90ad840d9 ${channel-state} CS_EXECUTE
```

#### 9、expand

执行变量扩展`API`。

```
[uuid:<uuid> ]<cmd> <args>
```

例子:

```
expand originate sofia/internal/1001%${domain} 9999   
```

在这个例子中，扩展的变量是`${domain}`。比如`domain`的值是`192.168.1.1`，则扩展后执行的命令为：

```
originate sofia/internal/1001%192.168.1.1 9999
```

#### 10、fsctl

发送`freeswitch`控制消息。

```
fsctl,FS control messages,[recover|send_sighup|hupall|pause [inbound|outbound]|resume [inbound|outbound]|shutdown [cancel|elegant|asap|now|restart]|sps|sps_peak_reset|sync_clock|sync_clock_when_idle|reclaim_mem|max_sessions|min_dtmf_duration [num]|max_dtmf_duration [num]|default_dtmf_duration [num]|min_idle_cpu|loglevel [level]|debug_level [level]]
```

##### a、hupall

用于挂断呼向指定号码的通话。参数为：

```
clearing_type dialed_ext <extension number>
```

举个例子来说，杀掉正处于活跃状态、目标号码是1000的通话，命令为：

```
fsctl hupall normal_clearing dialed_ext 1000
```

##### b、sync_clock

`FreeSWITCH`不信任系统时间。当系统第一次启动的时候，从系统时间中获取样本时间，然后以此为基准使用单调时钟（`monotonic clock`）。你可以使用命令`fsctl sync_clock`将`FreeSWITCH`与系统时间进行同步。

> 注：该命令会立即生效，会影响`CDR`里面的时间统计。如会导致计费超前或延后，或者记录的挂断时间小于拨打时间。举个例子来说，如果`FS`的时钟比系统时间迟一个月，当进行时间同步后，`CDR`的呼叫记录里面就会出现有的呼叫持续时间为1个月。

命令**fsctl sync_clock_when_idle**要安全很多，作用和上面一样，但是要到系统中所有通道都空闲的时候才开始时间同步。这种方法不会对`CDR`产生影响。

##### c、sync_clock_when_idle

要到系统没有通话的时候才开始时间同步

##### d、sps

该设置会改变`swithch.conf`文件中设置的`sessions-per-second`（每秒并发通话数）属性限制

##### e、last_sps

查询显示目前生效的`sessions-per-second`属性。

##### f、pause

可以使用参数`inbound`或`outbound`来暂停创建呼入或呼出通话，如果没有指定参数的话，则呼入呼出都暂停。`resume`的用法类似。

#### 11、global_getvar

获取全局变量的值。如果没有提供参数，则返回所有全局变量的值;可以用来返回端口/配置等信息。

```
global_getvar <varname>
```

例：

```
global_getvar internal_sip_port
global_getvar external_sip_port
global_getvar db_dir
```

#### 12、global_setvar

设置全局变量

```
global_setvar <varname>=<value>
```

例子:

```
global_setvar wsonh=good
# 配合global_getvar获取
global_getvar wsonh
```

#### 13、group_call

返回群呼`bridge`字符串，群呼定义请参考[XML User Directory Guide](https://freeswitch.org/confluence/display/FREESWITCH/XML+User+Directory)。

```
group_call group@domain[+F][+A][+E]
```

- +F将会以串行呼叫模式返回组成员（以`|`隔开各成员）
- +A将会以并行呼叫模式返回组成员（以`,`隔开各成员）
- +E将会议呼叫模式返回组成员（以：_:隔开各成员），关于企业呼叫请参考[Freeswitch IVR Originate](https://freeswitch.org/confluence/display/FREESWITCH/Freeswitch+IVR+Originate?src=search).

> 请注意 如果你需要设置在外呼通道上面设置用户变量，需要确保你的`domain`或被拨打组的变量列表里面没有设置`dial-string`和`group-dial-string`，用设置用户默认组里面的`dial-string`和`group-dial-string`来替代。这样的话，`group_call`将会返回`user/101`,`user/`将会设置你的外呼通道变量。

### 14、help

显示所有`API`命令的帮助信息，如如果你要查找`api`中是否有你想要的命令，可以使用它。

```
help
```

### 15、host_lookup

查询指定域名所在的主机地址：

```
host_lookup <hostname>
```

例：

```
host_lookup wsonh.com
```

### 16、hupall

断开现有的所有通话。

```
hupall <cause> [<variable> <value>]
```

挂断所有含有变量，并且值为的通话，挂机原因为;如果不设置值表示挂断所有通话。

例子:

```
originate {foo=bar}sofia/internal/someone1@server.com,sofia/internal/someone2@server.com &park hupall normal_clearing foo bar
```

### 17、in_group

判断用户是否在指定的组中

```
in_group <user>[@<domain>] <group_name>
```

例：

```
in_group 1000 user
```

### 18、is_lan_addr

判断IP是否为内网地址

```
is_lan_addr <ip>
```

例：

```
is_lan_addr 127.0.0.1
```

### 19、load

加载外部模块,我们在安装完模块后都需要加载一下他，当然也可以通过`api`方式加载：

```
load <mod_name>
```

### 20、md5

返回指定数据的`MD5`值。

```
md5 <data>
```

例：

```
md5 wsonh.com
```

### 21、module_exists

检查模块是否存在。

```
module_exists <module>
```

例:

```
module_exists mod_php
```

### 22、msleep

休眠指定毫秒

```
msleep <休眠的毫秒数>
# 例
msleep 10
```

### 23、nat_map

```
nat_map [status|reinit|republish] | [add|del] <port> [tcp|udp] [sticky] | [mapping] <enable|disable>
```

- status - 用于显示NAT类型、外网IP（the external IP）以及当前映射的端口。
- reinit - 重新初始化NAT模块。当你更换路由器或将路由器由NAT切换到UPnP模式的时候，使用该参数。
- republish - 该参数会让FreeSWITCH重新（向路由器等）发布NAT映射信息。 正常情况下，没有必要使用该参数。
- mapping - 该参数用于控制是否向NAT设备发送端口映射请求(可使用-nonatmap参数在系统启动时关闭该功能)。

之所以存在该参数，是因为有可能需要通过`NAT`获取公网IP地址，而不需要通过`NAT`开启端口。

> `sticky`参数用于将映射信息固化下来，在下次`FreeSWITCH`重启后映射仍然生效。 如果你有多个网卡，并分别配置了使用相同端口的`sip profiles`。`nat_map`在映射端口的时候，会被弄昏头的，不需要将端口映射到哪个`sip profile`上面，千万别干这种挫事！

### 24、regex

执行正则表达式匹配。该参数会根据是否提供参数而实现不同的功能，如下：

- 如果没提供该参数, `regex`将会执行正常的匹配，返回`true`或者`false`。
- 如果提供该参数，如果匹配成功的话，会返回指定的子串。如果匹配失败，则返回全部源字符串。

默认的正则表达式分界符是|（管道符）。可以更改为~或者/，只要在字符串的前面加上m:。

```
regex <data>|<pattern>[|<subst string>]       
regex m:/<data>/<pattern>[/<subst string>]       
regex m:~<data>~<pattern>[~<subst string>]
```

例子:

```
regex test1234|\d                  <== Returns "true"
regex m:/test1234/\d               <== Returns "true"
regex m:~test1234~\d               <== Returns "true"
regex test|\d                      <== Returns "false"
regex test1234|(\d+)|$1            <== Returns "1234"
regex sip:foo@bar.baz|^sip:(.*)|$1 <== Returns "foo@bar.baz"
regex testingonetwo|(\d+)|$1       <== Returns "testingonetwo" (no match)
regex m:~30~/^(10|20|40)$/~$1      <== Returns "30" (no match)
regex m:~30~/^(10|20|40)$/~$1~n    <== Returns "" (no match)
regex m:~30~/^(10|20|40)$/~$1~b    <== Returns "false" (no match)
```

### 25、reload

重新加载模块。

```
reload [-f] <mod_name>
```

### 26、reloadacl

重新加载`ACL`规则。

```
reloadacl [reloadxml]
```

### 27、reloadxml

重新加载`conf/freeswitch.xml`的配置信息到内存中,修改配置后常常需要使用它。

```
reloadxml
```

### 28、show

输出多种（模块）状态报告。

```
show <item># item类型如下codec|endpoint|application|api|dialplan|file|timer|calls [count]|channels [count|like <match string>]|calls|detailed_calls|bridged_calls|detailed_bridged_calls|aliases|complete|chat|management|modules|nat_map|say|interfaces|interface_types|tasks|limits|status
```

- 输出`XML`格式: `show calls as xml`
- 输出`JSON`格式: `show calls as json`

修改输出分隔符: show foo as delim |

- codec - 列出所有编码
- endpoint - 列出所有endpoint类型模块
- application - 列出所有应用程序
- api - 列出所有api
- dialplan - 列出拨号方案涉及的模块
- file - 列出所有支持的文件类型
- timer - 列出计时器timer模块
- calls - 列出当前的通话[count]
- channels - 列出当前的通道 [count|like ]   
  注：关于calls与channels的对比，请参考[Channels vs Calls](http://wiki.freeswitch.org/wiki/Channels_vs_Calls)
- bridged_calls - 和"show calls"相同
- detailed_calls - 和"show calls"类似，但是显示字段更多
- detailed_bridged_calls - 和"show calls"类似，但是显示字段更多
- aliases - 列出所有别名（别名干啥用的，暂时未查到）
- complete - list command complete tables
- chat - 列出所有chat模块，包括api、sms、conf等
- management - list management?
- modules - 列出所有模块
- nat_map - 列出地址映射表
- say - 列出有支持语言的say模块
- interfaces - 列出所有接口
- interface_types - 列出所有接口类型
- tasks - 列出任务
- registrations - 列出所有注册用户

#### 1、Showing Calls和Channels提示

理解`show calls/channels`真义的最好方式是亲自去尝试。最近（2011.9）又在show命令家族中添加了几位：

- show detailed_calls
- show bridged_calls
- show detailed_bridged_calls

这三个命令用于取代简单的`show calls`。   
需要注意的是，`show detailed\_calls`取代的是`show distinct\_channels`。命令都是相似的，但是返回信息更多。   
同样需要注意的是，这里并没有`show detailed\_channels`命令，但是使用`show detailed\_calls`会让你得到相同的结果。该命令能让你得到`单腿通话`（one-legged calls）或桥接后的通话信息，所以，少年，习惯这条新命令吧！

小贴士2: 有时，你需要获取某个特定的`uuid`，可以使用下面的方式。   
假设你设置了通道变量`presence_data`，那可以使用下面的命令搜索符合条件的通道（即含有foo的通道）： `show channels like foo`

like将会搜索下面的关键字段：

- uuid
- channel name
- caller id name
- caller id number
- presence_data

> 注: `presence_data`必须在`bridge`或`originate`期间设置，而不是在通道已经建立完成后才设置。

### 29、shutdown

停止`FreeSWITCH`程序。该命令只在`cli`中起作用，如果想作为`api`进行调用，需要使用`fsctl shutdown`。

警告！在`cli`中运行`shutdown`会忽略掉参数，并立即退出！

```
fsctl shutdown [cancel|elegant|asap|restart|now] 
```

- cancel - 终止上一次提交的shutdown请求
- elegant - 等待所有通话都停止后才关闭，允许新发起通话.
- asap - 等待所有通话都停止后才关闭， 不再允许新通话.
- restart - 在执行完`shutdown`后立即重启FreeSWITCH。
- now - 立即重启FreeSWITCH。

当使用`elegant`, `asap`或者`now`参数后，还可以后跟`restart`命令，如下：

```
fsctl shutdown [elegant|asap|now] restart
```

### 30、status

显示当前`FreeSWITCH`的运行状态

```
status

UP 0 years, 0 days, 4 hours, 3 minutes, 59 seconds, 588 milliseconds, 331 microseconds
FreeSWITCH (Version 1.9.0 git 5dd4451 2018-08-31 19:05:39Z 64bit) is ready
23 session(s) since startup
0 session(s) - peak 4, last 5min 0 
0 session(s) per Sec out of max 30, peak 2, last 5min 0 
1000 session(s) max                    <- 每秒创建的最大通话数 .. 来自switch.conf.xml
min idle cpu 0.00/95.47                <- 同时并存的最大通话数 .. 来自switch.conf.xml
Current Stack Size/Max 240K/8192K      <- 达到拒接电话标准的最小闲置CPU值 .. 来自switch.conf.xml （如果该值被启动的话）
```

### 31、strftime_tz

根据不同的时区，显示格式化后的时间。需要查看`linux`时区标准列表的，请查看`/usr/share/zoneinfo/zone.tab`。

```
strftime_tz <timezone> [format_string]
```

示例: strftime_tz US/Eastern %Y-%m-%d %T

### 32、unload

卸载外部模块

```
unload [-f] <mod_name>
```

### 33、version

显示FreeSWITCH的版本号

```
version [short]
```

### 34、xml_locate

- xml_locate root: 返回FreeSWITCH使用的所有XML 

- xml_locate
  
  : 返回指定
  
  的XML

```
xml_locate directory
xml_locate configuration
xml_locate dialplan
xml_locate phrases
```

用法:

```
xml_locate [root | <section> | <section> <tag> <tag_attr_name> <tag_attr_val>]
```

示例:

```
xml_locate directory domain name example.com
```

### 35、xml_wrap

使用`xml`来包装`API`命令

```
xml_wrap <command> <args>
```

## 五、呼叫管理命令

---

### 1、create_uuid

创建一个新的UUID，并以字符串的形式返回。

```
create_uuid
```

### 2、originate

发起一个新的呼叫

```
originate <call_url> <exten>|&<application_name>(<app_args>) [<dialplan>] [<context>] [<cid_name>] [<cid_num>] [<timeout_sec>]
```

参数:

- 呼叫目标URL.  想多了解sofia sip URL语法的童鞋可以参考: \[\[Sofia|FreeSwitch Endpoint Sofia\]\]
- 目标有如下几类:
  - 进入拨号方案进行路由的目标号码
  - &<application_name>(<app_args>)
    - "&" 表明后面跟的是应用名称，不是一个目标号码
    - () 可选参数 (不是所有应用都需要传递参数，比如park)
    - 下面是可以用在'&'后面的应用列表：  
      park, bridge, javascript/lua/perl, playback (移除mod_native_file), and many others.
    - 注1: 用单引号传递含有空格的参数，如'&lua(test.lua arg1 arg2)'
    - 注2: 在&和application_name之间不能含有空格
- 默认为'XML'，如果没有特别指定的话。
- 默认为'default'，如果没有特别指定的话。
- 主叫名称.
- 主叫号码.
- 超时时长（单位为秒）.

可选参数:   
这些可选参数使用大括号包裹，如：

```
originate {ignore\_early_media=true}sofia/example/user 8334
```

参数需要使用逗号隔开，例子如下：

```
originate {ignore_early_media=true,originate_timeout=2}sofia/example/user 8334
```

- group_confirm_key
- group_confirm_file
- forked_dial
- fail_on_single_reject
- ignore_early_media
- return_ring_ready
- originate_retries
- originate_retry_sleep_ms
- origination_caller_id_name
- origination_caller_id_number
- originate_timeout
- sip_auto_answer

更多变量，参考下面的地址：   
[[Channel_Variables#Originate_related_variables|Description of originate's related variables]]

例子：   
假设，你想拨打一个本地注册的sip终端，号码为300，然后执行park操作，如下：   
(注：本例中用的sip profile是example，你在实际测试的时候，需要改成你本地电话注册的sip profile，一般为internal)

```
originate sofia/example/300%pbx.internal &park()
```

又或者，你想将远程注册的sip终端连到拨号规则8600上

```
originate sofia/example/300@foo.com 8600
```

再或者，你想将远程注册的sip终端连到另一个远程终端

```
originate sofia/example/300@foo.com &bridge(sofia/example/400@bar.com)
```

还或者， 你甚至可以在接通后执行javascript脚本test.js

```
originate sofia/example/1000@somewhere.com &javascript(test.js)
```

如果运行的javascript脚本需要传递参数，则需要使用单引号括起来。

```
originate sofia/example/1000@somewhere.com '&javascript(test.js myArg1 myArg2)'
```

在发起呼叫前，设置通道变量

```
originate {ignore_early_media=true}sofia/mydomain.com/18005551212@1.2.3.4 15555551212
```

在发起呼叫期间，设置通道变量，并传递给另一个FS

```
originate {sip_h_X-varA=111,sip_h_X-varB=222}sofia/mydomain.com/18005551212@1.2.3.4 15555551212
```

注: 你可以设置任何类型的通道变量，即使是自定义变量。如果变量的值含有空格或逗号等符号，使用单引号括起来即可。

```
originate {my_own_var=my_value}sofia/mydomain.com/that.ext@1.2.3.4 15555551212originate {my_own_var='my value'}sofia/mydomain.com/that.ext@1.2.3.4 15555551212
```

如果你想自造一段回铃音给被呼叫方听，try this：

```
originate {ringback=\'%(2000,4000,440.0,480.0)\'}sofia/example/300@foo.com &bridge(sofia/example/400@bar.com)
```

如果你想发起呼叫后，通道进入"Ring-Ready"状态后就立即返回，try this:

```
originate {return_ring_ready=true}sofia/gateway/someprovider/919246461929 &socket(127.0.0.1:8082 async full)
```

更多信息请查阅[return ring ready](http://blog.godson.in/2010/12/use-of-returnringready-originate.html)

你可以将保持等待音乐设置为回铃音，if you want:

```
originate {ringback=\'/path/to/music.wav\'}sofia/gateway/name/number &bridge(sofia/gateway/name/othernumber)
```

你可以在后台发起一个呼叫（异步模式），播放一段60秒的提示消息：

```
bgapi originate {ignore_early_media=true,originate_timeout=60}sofia/gateway/name/number &playback(message)
```

你可以指定被呼叫方的UUID，只需要下面几步：

- 使用create_uuid创建一个UUID，待用。
- 使用uuid_kill直接可以在对方未接听前杀掉该次呼叫。
- 使用origination_uuid指定uuid之后，被叫方会在整个通话的生命周期中使用该UUID。 * originate {origination_uuid=...}user/100@domain.name.com

下面例子作用：发起一个到外部sip服务器echo conference的呼叫，然后转接到本地用户分机上

```
originate sofia/internal/9996@conference.freeswitch.org &bridge(user/105@default)
```

下面例子作用：向'default'以外的context上的分机发起呼叫（FreePBX会用到该特性，如context名字为context_1，context_2等等）

```
originate sofia/internal/2001@foo.com 3001 xml context_3
```

如果你想对多个分机发起呼叫，可以使用下面的命令：

```
originate user/1001,user/1002,user/1003 &park()
```

如果需要在收到early media的时候，将外呼的电话转入会议中，可以使用下面的两个命令，作用一样

```
originate sofia/example/300@foo.com &conference(conf_uuid-TEST_CON)originate sofia/example/300@foo.com conference:conf_uuid-TEST_CON inline    ( See [[Misc._Dialplan_Tools_InlineDialplan]] for more detail on 'inline' Dialplans )
```

下面的例子演示如何在A-leg上面使用loopback和inline   
[例子](http://lists.freeswitch.org/pipermail/freeswitch-users/2013-January/091769.html)

### 3、pause

停止指定通道的媒体播放

```
用法: pause <uuid> <on|off>
```

### 4、uuid_answer

应答

```
用法: uuid_answer <uuid>
```

- See Also: [[Misc.Dialplan_Tools_answer]]

### 5、uuid_audio

调整信道上面的音量，或直接通过一个媒体bug进行静音（读/写）

```
用法: uuid_audio <uuid> [start [read|write] [mute|level <level>]|stop]
```

level的值范围从-4到4,默认值为0。

### 6、uuid_break

断开发送至指定信道的媒体流。举例来说，如果此时正在信道上面播放一个音频文件，使用uuid_break命令，就会断开媒体，呼叫会顺着拨号方案、脚本等往下执行。

```
用法: uuid_break <uuid> [all]
```

如果使用all标记的话，所有信道上面正在排队等待播放的音频文件都会被移除，但是如果没有all标记的话，只有当前正在播放的音频文件会被断开。

### 7、uuid_bridge

桥接两条呼叫的腿。

```
Usage: uuid_bridge <uuid> <other_uuid>
```

uuid_bridge至少需要有一条腿是被呼通的。

### 8、uuid_broadcast

在一个指定UUID的信道上执行任意一个拨号方案程序。如果指定了某录音文件名，则代表将会在该信道上播放该文件。 执行拨号方案程序的语法规则是`app::args`。

```
用法: uuid_broadcast <uuid> <path> [aleg|bleg|both]
```

在选定的腿上执行应用程序，执行完毕后挂断，并指明挂机原因。

```
用法: uuid_broadcast <uuid> app[![hangup_cause]]::args [aleg|bleg|both]
```

具体应用举例如下:

```
 uuid_broadcast 336889f2-1868-11de-81a9-3f4acc8e505e sorry.wav both uuid_broadcast 336889f2-1868-11de-81a9-3f4acc8e505e say::en\snumber\spronounced\s12345 aleg uuid_broadcast 336889f2-1868-11de-81a9-3f4acc8e505e say!::en\snumber\spronounced\s12345 aleg uuid_broadcast 336889f2-1868-11de-81a9-3f4acc8e505e say!user_busy::en\snumber\spronounced\s12345 aleg uuid_broadcast 336889f2-1868-11de-81a9-3f4acc8e505e playback!user_busy::sorry.wav aleg
```

### 9、uuid_buglist

列出信道上面的媒体bug（media bugs）

```
用法: uuid_buglist <uuid>
```

### 10、uuid_chat

发送聊天信息

```
用法: <uuid> <text>
```

如果和会话（session，由uuid指定）相关的终端有一个receive_event handler，该消息会被发往终端，并以及时消息的形式显示出来。

### 11、uuid_debug_media

该命令过去为uuid_debug_audio,但是因为加入了一些视频的内容，所以改为现在的名字。

调试媒体流

用法:

```
<uuid> <read|write|both|vread|vwrite|vboth> <on|off>
```

使用`read`、`write`或者`both`（同时调试两个方向）作为语音流的方向，以进行调试。 在前面加上`v`，代表视频流的调试。

#### a、Read Format

"R %s b=%4ld %s:%u %s:%u %s:%u pt=%d ts=%u m=%d\n"

where the values are:

```
* switch_channel_get_name(switch_core_session_get_channel(session)),* (long) bytes,* my_host, switch_sockaddr_get_port(rtp_session->local_addr),* old_host, rtp_session->remote_port,* tx_host, switch_sockaddr_get_port(rtp_session->from_addr),* rtp_session->recv_msg.header.pt, * ntohl(rtp_session->recv_msg.header.ts), * rtp_session->recv_msg.header.m
```

#### b、Write Format

"W %s b=%4ld %s:%u %s:%u %s:%u pt=%d ts=%u m=%d\n"

where the values are:

```
* switch_channel_get_name(switch_core_session_get_channel(session)),* (long) bytes,* my_host, switch_sockaddr_get_port(rtp_session->local_addr),* old_host, rtp_session->remote_port,* tx_host, switch_sockaddr_get_port(rtp_session->from_addr),* send_msg->header.pt, * ntohl(send_msg->header.ts), * send_msg->header.m);
```

### 12、uuid_deflect

通过发送REFER方法，将当前FreeSWITCH上面的某个已经应答的sip呼叫转移走。

```
用法: uuid_deflect <uuid> <sip URL>
```

在命令执行后，uuid_deflect等待远端的应答，以此判断转移是否成功。远端返回的sip内容（sip fragment)将会作为uuid_deflect命令的返回结果。如果远端报告REFER成功，FreeSWITCH将会向那条信道发送bye信令。

举例如下:

```
uuid_deflect 0c9520c4-58e7-40c4-b7e3-819d72a98614 sip:info@example.net
```

返回内容:

```
Content-Type: api/responseContent-Length: 30 +OK:SIP/2.0 486 Busy Here
```

### 13、uuid_displace

将目标信道上面的语音流替换为指定的录音（文件）。

参数：

```
* uuid = 通话的唯一标识符（通过`show channels"可查看到）* start|stop = 启动/停止该操作* file = 要播放的语音源(wav，shout等等)路径* limit = 语音替换（文件）的最大播放时长，秒数* mux = 该选项将会导致原始的语音流与录音（文件）进行混音。比如，你在替换语音的时候，仍想与另一端进行会话（即在听到替换的录音文件的时候，也能听到对方的声音）。
```

用法：

```
uuid_displace <uuid> [start|stop] <file> [<limit>] [mux]
```

举例如下：

```
uuid_displace 1a152be6-2359-11dc-8f1e-4d36f239dfb5 start /sounds/test.wav 60uuid_displace 1a152be6-2359-11dc-8f1e-4d36f239dfb5 stop /sounds/test.wav
```

### 14、uuid_display

更新话机的显示内容，前提是话机支持该功能。目前有Polycom和Snom等部分Sip话机支持该功能。

```
用法: <uuid> [<display>]
```

该命令会导致重新协商语音编码。SIP->RTP包的大小应该是0.020。如果在SPA系统话机上，设置为0.030的话，会引起DTMF延迟（DTMF lag）。当话机上的按键被按下的时候，我们可以通过fs_cli看到，但是会有4到6秒的延迟。

### 15、uuid_dual_transfer

将处于通话中的双方分别转移到不同的目的地。

```
-USAGE: <uuid> <A-dest-exten>[/<A-dialplan>][/<A-context>] <B-dest-exten>[/<B-dialplan>][/<B-context>]
```

### 16、uuid_dump

导出指定会话中的所有变量

```
Usage: uuid_dump <uuid> [format]
```

导出格式: XML

### 17、uuid_early_ok

停止忽略早期媒体（即正常播放early media）。 如果此时ignore_early_media=true，该命令将会停止忽略早期媒体（让参数ignore_early_media设置不起作用），并正常播放。

用法: uuid_early_ok

### 18、uuid_exists

检查给定的uuid是否存在。

用法: uuid_exists

### 19、uuid_flush_dtmf

刷新DTMF数字缓存，将在排队的DTMF全部送出

Usage: uuid_flush_dtmf

### 20、uuid_fileman

管理正在信道中播放的音频流，该音频来自一个语音文件。

用法: uuid_fileman 

命令如下:

```
*speed:<+[step]>|<-[step]>    语速*volume:<+[step]>|<-[step]>   音量*pause                         暂停*stop                          停止*truncate                      截断*restart                      重启*seek:<+[samples]>|<-[samples]> 定位
```

Samples，从字面上来讲，就是语音文件前进后退的取样数。在8KHZ的文件中，取样数8000代表的是一秒。同样，在16KHZ的文件中，16000代表的也是一秒。

### 21、uuid_getvar

获取指定的信道变量

用法: uuid_getvar

### 22、uuid_hold

保持通话

用法:

```
uuid_hold <uuid>           保持通话uuid_hold off <uuid>       结束保持，恢复正常通话uuid_hold toggle <uuid>    在保持和取消保持间切换
```

### 23、uuid_kill

重置（杀掉）指定的信道

用法: uuid_kill [cause]

### 25、uuid_limit

Apply or change limit(s) on a specified uuid.

Usage: uuid_limit [[/interval]] [number [dialplan [context]]]

See also [[Limit]]

### 26、uuid_media

Reinvite FreeSWITCH out of the media path:

Usage: uuid_media [off]

Reinvite FreeSWITCH back in:

Usage: uuid_media

### 27、uuid_media_reneg

API command to tell a channel to send a re-invite with optional list of new codecs

Usage: uuid_media_reneg

### 28、uuid_park

Park call

Usage: uuid_park

### 29、uuid_preanswer

Preanswer a channel.

Usage: uuid_preanswer

- See Also: [[Misc._Dialplan_Tools_pre_answer]]

### 30、uuid_preprocess

Pre-process Channel

Usage: uuid_preprocess <>

### 31、uuid_recv_dtmf

Send DTMF digits to set.

Usage: uuid_recv_dtmf [@]

Use the character w for a .5 second delay and the character W for a 1 second delay.

Default tone duration is 2000ms .

### 32、uuid_send_dtmf

Send DTMF digits.

Usage: uuid_send_dtmf [@]

Use the character w for a .5 second delay and the character W for a 1 second delay.

Default tone duration is 2000ms .

### 33、uuid_send_info

Send info to the endpoint

Usage: uuid_send_info

### 35、uuid_session_heartbeat

Usage: uuid_session_heartbeat [sched] [0|]

### 36、uuid_setvar

Set a variable on a channel. If value is omitted, the variable is unset.

Usage: uuid_setvar [value]

### 40、uuid_setvar_multi

Set multiple vars on a channel.

Usage: uuid_setvar_multi =[;=[;...]]

### 41、uuid_simplify

This command directs FreeSWITCH to remove itself from the SIP signaling path if it can safely do so

Usage:

uuid_simplify

### 42、uuid_transfer

Transfers an existing call to a specific extension within a and . Dialplan may be "xml" or "directory".

Usage:

uuid_transfer [-bleg|-both] [] []

The optional first argument will allow you to transfer both parties (-both) or only the party to whom is talking.(-bleg)

NOTE: if the call has been bridged, and you want to transfer either sides of the call, then you will need to use (or the API equivalent). If it's not set, transfer doesn't really work as you'd expect, and leaves calls in limbo.

## Record/Playback Commands

### 43、uuid_record

Record the audio associated with the given UUID into a file. The start command causes FreeSWITCH to start mixing all call legs together and saves the result as a file in the format that the file's extension dictates. (if available) The stop command will stop the recording and close the file. If media setup hasn't yet happened, the file will contain silent audio until media is available. Audio will be recorded for calls that are parked. The recording will continue through the bridged call. If the call is set to return to park after the bridge, the bug will remain on the call, but no audio is recorded until the call is bridged again. (TODO: What if media doesn't flow through FreeSWITCH? Will it re-INVITE first? Or do we just not get the audio in that case?)

Usage:

uuid_record [start|stop] []

Where limit is the max number of seconds to record.

If the path is not specified on start it will default to the channel variable "sound_prefix" or FreeSWITCH base_dir when the "sound_prefix" is empty.

You may also specify "all" for path when stop is used to remove all for this uuid

"stop" command must be followed by option.

[[Channel_Variables#Call_Recording_Related|See record's related variables]]

### 44、uuid_playback

在通道中播放录音`uuid_playback`,用法如下：

```
uuid_playback [uuid] <flie>"
```

例子：

```
conn.api("uuid_playback uuid hello.wav")
```

## Limit Commands

---

### [[Limit#API|limit_reset]]

Reset a limit backend.

### [[Limit#API|limit_status]]

Retrieve status from a limit backend.

### [[Limit#API|limit_usage]]

Retrieve usage for a given resource.

### [[Limit#API|uuid_limit_release]]

Manually decrease a resource usage by one.

### [[Limit#API|limit_interval_reset]]

Reset the interval counter to zero prior to the start of the next interval.

### Misc. Commands

---

### bg_system

Execute a system command in the background.

Usage: bg_system

### echo

Echo input back to the console echo This text will appear This text will appear

### file_exists

Tests whether ''filename'' exists.

file_exists filename

Examples:

> file_exists /tmp/real_file true file_exists /tmp/missing_file false

Example dialplan usage:

```
 <extension name="play-news-announcements">   <condition expression="${file_exists(${sounds_dir}/news.wav)}" expression="true"/>     <action application="playback" data="${sounds_dir}/news.wav"/>     <anti-action application="playback" data="${soufnds_dir}/no-news-is-good-news.wav"/>   </condition> </extension>
```

'''Note''' this tests whether FreeSWITCH can see the file, but the file may still be unreadable (permissions).

### find_user_xml

Checks to see if a user exists; Matches user tags found in the directory, similar to [[user_exists]], but returns an XML representation of the user as defined in the directory (like the one shown in [[Mod_commands#user_exists|user_exists]]).

Usage: find_user_xml

Where key references a key specified in a directory's user tag, user represents the value of the key, and the domain is the domain the user is assigned to.

### list_users

Lists Users configured in Directory

Usage: list_users [group ] [domain ] [user ] [context ]

Example:

freeswitch@localhost> list_users group default

userid|context|domain|group|contact|callgroup|effective_caller_id_name|effective_caller_id_number 2000|default|192.168.20.73|default|sofia/internal/sip:2000@192.168.20.219:5060|techsupport|B#-Test 2000|2000 2001|default|192.168.20.73|default|sofia/internal/sip:2001@192.168.20.150:63412;rinstance=8e2c8b86809acf2a|techsupport|Test 2001|2001 2002|default|192.168.20.73|default|error/user_not_registered|techsupport|Test 2002|2002 2003|default|192.168.20.73|default|sofia/internal/sip:2003@192.168.20.149:5060|techsupport|Test 2003|2003 2004|default|192.168.20.73|default|error/user_not_registered|techsupport|Test 2004|2004

+OK

Search items can be combined:

freeswitch@localhost> list_users group default user 2004

userid|context|domain|group|contact|callgroup|effective_caller_id_name|effective_caller_id_number 2004|default|192.168.20.73|default|error/user_not_registered|techsupport|Test 2004|2004

+OK

### sched_api

Schedule an API call in the future. Usage: sched_api [+@]

time is the UNIX timestamp at which the command should be executed. If it is prefixed by +, specifies the number of seconds to wait before executing the command. If prefixed by @, it will execute the command periodically everyseconds; for the first time it will be executed after seconds.  
group_name will be the value of "Task-Group" in generated events. "none" is the proper value for no group.  
command_string is the command executed

Scheduled task or group of tasks can be revoked with sched_del or unsched_api.

You could put "&" symbol at the end of the line to make command to be executed in its own thread.

Example: sched_api +1800 none originate sofia/internal/1000%${sip_profile} &echo() sched_api @600 check_sched log Periodic task is running...

### sched_broadcast

Play a file to a specific call in the future. Usage: sched_broadcast [+] [aleg|bleg|both]

Schedule execution of an application on a chosen leg(s) with optional hangup: Usage: sched_broadcast [+] app[![hangup_cause]]::args [aleg|bleg|both]

time is the UNIX timestamp at which the command should be executed (or if it is prefixed by +, the number of seconds to wait before executing the command)

Example: sched_broadcast +60 336889f2-1868-11de-81a9-3f4acc8e505e commercial.wav both sched_broadcast +60 336889f2-1868-11de-81a9-3f4acc8e505e say::en\snumber\spronounced\s12345 aleg

### sched_del

Removes a prior scheduled group or task ID Usage: sched_del <group_name|task_id>

The one argument can either be a group of prior scheduled tasks or the returned task-id from sched_api.

Example: sched_del my_group sched_del 2

### sched_hangup

Schedule a running call to hangup.

Usage: sched_hangup [+] []

Note: sched_hangup +0 is the same as uuid_kill

### sched_transfer

Schedule a transfer for a running call.

Usage: sched_transfer [+] [] []

### stun

Executes a STUN lookup. Usage: stun [:port]

Example: stun stun.freeswitch.org

### system

Execute a system command.

Usage: system

The command is passed to the system shell, where it may be expanded or interpreted in ways you don't expect. This can lead to security bugs if you're not careful. For example, the following command is dangerous:

If a malicious remote caller somehow sets their caller ID name to "; rm -rf /", you would unintentionally be executing this shell command:

log_caller_name; rm -rf /

### time_test

Time test.

Usage: time_test [count]

Runs a test to see how bad timer jitter is. It runs the test count times (default 10) and tries to sleep for mss microseconds. It returns the actual timer duration along with an average.

Sample:

time_test 100 5

test 1 sleep 100 99 test 2 sleep 100 97 test 3 sleep 100 96 test 4 sleep 100 97 test 5 sleep 100 102 avg 98

### timer_test

Timer test.

Usage: timer_test <10|20|40|60|120> [<1..200>] []

Runs a test to see how bad timer jitter is. Unlike time_test, this uses the actual freeswitch timer infrastructure to do the timer test and exercises the timers used for call processing.

First argument is the timer interval. Second is the count. Third is the timer name ("show timers" will give you a list)

Example:

timer_test 20 3

Avg: 16.408ms Total Time: 49.269ms

2010-01-29 12:01:15.504280 [CONSOLE] mod_commands.c:310 Timer Test: 1 sleep 20 9254 2010-01-29 12:01:15.524351 [CONSOLE] mod_commands.c:310 Timer Test: 2 sleep 20 20042 2010-01-29 12:01:15.544336 [CONSOLE] mod_commands.c:310 Timer Test: 3 sleep 20 19928

### tone_detect

Start Tone Detection on a channel.

Usage: tone_detect [ ]

### unsched_api

Unschedule an api command.

Usage: unsched_api

### url_decode

Usage: url_decode

Url decode a string.

### url_encode

Url encode a string.

Usage: url_encode

### user_data

Retrieves user information (parameters or variables) as defined in the directory.

Usage: user_data @ [attr|var|param]

Where user is the user's id, domain is the user's domain, var|param specifies whether the info we're requesting is a variable/parameter, and the name is the name (key) of the variable.

Example:

```
user_data 1000@192.168.1.101 param password
```

will return a result of 1234, and

```
user_data 1000@192.168.1.101 var accountcode
```

will return a result of 1000 from the example user shown in [[Mod_commands#user_exists|user_exists]], and

```
user_data 1000@192.168.1.101 attr id
```

will return the user's actual alphanumeric ID (i.e. "john") when number-alias="1000" was set as an attribute for that user.

### 参考资料

【1】https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_commands_1966741/

【2】[FreeSwitch控制台以及ESL常用命令/API命令大全 - pytorch中文社区](https://discuss.ptorch.com/article/93.html)


