## Freeswitch Docker安装

### 背景

Linux 上部署 FreeSWITCH 是一件比较麻烦的事情，用 Docker 部署相对方便且更容易运维.

### 部署准备

1、Debian或者类似Linux操作系统，使用APT（Advanced Package Tool）进行软件包管理

2、已经按照Docker，若未安装，可以参考【5】所示。

3、已经按照git，比较常规，不再赘述。

### 部署步骤

（1）获取docker文件

```shell
git clone https://github.com/BetterVoice/freeswitch-container.git
```

其目录结构如下所示：

```textile
freeswitch-container# ls
build  conf  Dockerfile  README.md  sysv
```

- build目录包含了bashrc 、install-deps.sh 、modules.conf三个文件，其中install-deps.sh按照freeswitch所需要的依赖；modules.conf定义了freeswitch将会按照哪些模块。

- build Freeswitch的一些配置

- Dockerfile是Docker file，包含了执行的过程，如下所示：

```docker
# Jenkins.

FROM ubuntu:16.04
MAINTAINER Thomas Quintana <thomas@bettervoice.com>

# Enable the Ubuntu multiverse repository.
RUN echo "deb http://us.archive.ubuntu.com/ubuntu/ trusty multiverse" >> /etc/apt/source.list
RUN echo "deb-src http://us.archive.ubuntu.com/ubuntu/ trusty multiverse">> /etc/apt/source.list
RUN echo "deb http://us.archive.ubuntu.com/ubuntu/ trusty-updates multiverse" >> /etc/apt/source.list
RUN echo "deb-src http://us.archive.ubuntu.com/ubuntu/ trusty-updates multiverse" >> /etc/apt/source.list
# Enable videolan stable repository.
RUN apt-get update && apt-get install -y software-properties-common
RUN add-apt-repository ppa:videolan/stable-daily

# Install Dependencies.
# missing in 16.04 libmyodbc
RUN apt-get update && apt-get install -y autoconf automake bison build-essential fail2ban gawk git-core groff groff-base erlang-dev libasound2-dev libavcodec-dev libavutil-dev libavformat-dev libav-tools libavresample-dev libswscale-dev liba52-0.7.4-dev libssl-dev libdb-dev libexpat1-dev libcurl4-openssl-dev libgdbm-dev libgnutls-dev libjpeg-dev libmp3lame-dev libncurses5 libncurses5-dev libperl-dev libogg-dev libsnmp-dev libtiff5-dev libtool libvorbis-dev libx11-dev libzrtpcpp-dev make portaudio19-dev python-dev snmp snmpd subversion unixodbc-dev uuid-dev zlib1g-dev libsqlite3-dev libpcre3-dev libspeex-dev libspeexdsp-dev libldns-dev libedit-dev libladspa-ocaml-dev libmemcached-dev libmp4v2-dev libpq-dev libvlc-dev libv8-dev liblua5.2-dev libyaml-dev libpython-dev odbc-postgresql sendmail unixodbc wget yasm libldap2-dev

# Use Gawk.
RUN update-alternatives --set awk /usr/bin/gawk

# Install source code dependencies.
ADD build/install-deps.sh /root/install-deps.sh
WORKDIR /root
RUN chmod +x install-deps.sh
RUN ./install-deps.sh
RUN rm install-deps.sh

# Configure Fail2ban
ADD conf/freeswitch.conf /etc/fail2ban/filter.d/freeswitch.conf
ADD conf/freeswitch-dos.conf /etc/fail2ban/filter.d/freeswitch-dos.conf
ADD conf/jail.local /etc/fail2ban/jail.local
RUN touch /var/log/auth.log

# Download FreeSWITCH.
WORKDIR /usr/src
ENV GIT_SSL_NO_VERIFY=1
RUN git clone https://github.com/signalwire/freeswitch.git -b v1.6.16

# Bootstrap the build.
WORKDIR freeswitch
RUN ./bootstrap.sh

# Enable the desired modules.
ADD build/modules.conf /usr/src/freeswitch/modules.conf

# Build FreeSWITCH.
RUN ./configure --enable-core-pgsql-support
RUN make
RUN make install
RUN make uhd-sounds-install
RUN make uhd-moh-install
RUN make samples

# Post install configuration.
ADD sysv/init /etc/init.d/freeswitch
RUN chmod +x /etc/init.d/freeswitch
RUN update-rc.d -f freeswitch defaults
ADD sysv/default /etc/default/freeswitch
ADD build/bashrc /root/.bashrc
ADD conf/fs_sync /bin/fs_sync

# Add the freeswitch user.
RUN adduser --gecos "FreeSWITCH Voice Platform" --no-create-home --disabled-login --disabled-password --system --ingroup daemon --home /usr/local/freeswitch freeswitch
RUN chown -R freeswitch:daemon /usr/local/freeswitch

# Create the log file.
RUN touch /usr/local/freeswitch/log/freeswitch.log
RUN chown freeswitch:daemon /usr/local/freeswitch/log/freeswitch.log

# Open the container up to the world.
EXPOSE 5060/tcp 5060/udp 5080/tcp 5080/udp
EXPOSE 5066/tcp 7443/tcp
EXPOSE 8021/tcp
EXPOSE 64535-65535/udp

# Start the container.
CMD service snmpd start && service freeswitch start && tail -f /usr/local/freeswitch/log/freeswitch.log
```

(2) 构建镜像

执行完构建命令后，会得到一个 docker 镜像。

```shell
docker build -t freeswitch .
```

出错信息：You must install libsndfile-dev to build mod_sndfile。

原因：网络原因，修改install-deps.sh文件为如下：

```shell
116 # Install libsndfile-dev
117 cd /usr/src
118 wget https://github.com/libsndfile/libsndfile/releases/download/1.1.0/libsndfile-1.1.0.tar.xz
119 tar -xf libsndfile-1.1.0.tar.xz
120 cd libsndfile-1.1.0
121 ./configure --enable-shared --prefix=/usr/local
122 ${MAKE} && make install
```

另外，当前freeswith并未安装mod_av，安装的方式

```shell
#错误提示: You must install libav-dev to build mod_av 或者 : You must install libavformat-dev to build mod_av
git clone https://freeswitch.org/stash/scm/sd/libav.git
#或者 wget https://freeswitch.org/stash/rest/api/latest/projects/SD/repos/libav/archive?format=zip
cd libav
./configure             #CFLAGS="-fPIC" ./configure --enable-pic --enable-shared
make                    # make CXXFLAGS="-fPIC"
make install
```

(3)创建容器

启动命令

```
docker run -itd  --net=host --privileged=true freeswitch:latest
```

(4) 打开http访问

开始时，并没有打开http访问的模块。进入容器内，然后进行如下步骤：

1. 在FreeSWITCH的配置文件中启用XML API接口。可以通过编辑`/etc/freeswitch/autoload_configs/xml_cdr.conf.xml`文件，在`<configuration>`标签中添加以下内容来启用XML API接口：

```xml
<configuration name="xml_cdr.conf" description="XML CDR">
  <bindings>
    <binding name="http://0.0.0.0:8080">
      <param name="gateway-url" value="http://127.0.0.1:8080"/>
      <param name="password" value="ClueCon"/>
    </binding>
  </bindings>
</configuration>
```

这里将XML API接口绑定到了本地的8080端口，并设置了访问密码为ClueCon。

2. 重启FreeSWITCH服务，使配置文件生效。

3. 使用HTTP客户端工具（如curl、Postman等）向XML API接口发送请求。例如，可以使用curl命令向XML API接口发送请求，命令格式如下：

`curl -u freeswitch:ClueCon http://localhost:8080/api/help`

其中，freeswitch是用户名，ClueCon是密码，[http://localhost:8080是XML](http://localhost:8080%E6%98%AFXML/) API接口的地址，/api/help是要执行的命令（这里是获取所有可用的命令列表）。

4. 解析XML API接口的响应。XML API接口的响应是一个XML文档，包含了请求执行的结果。可以使用XML解析库（如ElementTree、lxml等）对XML文档进行解析，从而获取请求执行的结果。

### 熟悉Freeswitch

进入容器，执行客户端命令

```shell
#fs_cli
> show --help
-USAGE: codec|endpoint|application|api|dialplan|file|timer|calls [count]|channels [count|like <match string>]|calls|detailed_calls|bridged_calls|detailed_bridged_calls|aliases|complete|chat|management|modules|nat_map|say|interfaces|interface_types|tasks|limits|status

>show modules
>reload module mod_xx
>reloadxml
>status
>show status
>....
```

注意

```tex
在FreeSWITCH的fs_cli客户端中，命令status和show status的区别如下：

status命令会显示当前FreeSWITCH系统的状态，包括系统负载、CPU使用率、内存使用情况、当前活动的会话数等。

show status命令会显示当前FreeSWITCH系统的状态，包括系统负载、CPU使用率、内存使用情况、当前活动的会话数等，与status命令的输出内容基本相同。

不同之处在于，show status命令会将状态信息以XML格式输出，可以通过XML API接口进行访问，而status命令的输出则没有这个功能。
```

### 参考资料：

【1】https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Installation/Linux/CentOS-7-and-RHEL-7_10289546#-freeswitch--centos-7-and-rhel-7-

【2】https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Installation/Linux/Debian_67240088#about

【3】[Docker 部署 FreeSWITCH](https://www.cnblogs.com/zhuminghui/p/12838167.html)

【4】[FreeSWITCH 安装配置的 各种坑, 填坑 ]([FreeSWITCH 安装配置的 各种坑, 填坑_makefile:940: *** you must install libsndfile-dev _hnzwx888的博客-CSDN博客](https://blog.csdn.net/hnzwx888/article/details/88745276))

【5】[Debian Docker 安装]([Debian Docker 安装 | 菜鸟教程](https://www.runoob.com/docker/debian-docker-install.html))
