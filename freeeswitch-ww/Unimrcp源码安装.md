## Unimrcp安装

### 依赖安装

1.获取依赖

```shell
wget https://www.unimrcp.org/project/component-view/unimrcp-deps-1-6-0
```

2.安装依赖

使用脚本安装apr,apr-util,sofia-sip

```
sh build-dep-libs.sh
```

### 编译源码

1.下载源码并编译

```shell
$ git clone https://github.com/unispeech/unimrcp.git
$ cd unimrcp
$ ./bootstrap
$ ./configure
$ make
$ sudo make install
$ cd ..
```

2.使用azure插件，需要安装微软的speech sdk

```shell
export SPEECHSDK_ROOT="/your/path"
mkdir -p "$SPEECHSDK_ROOT"
wget -O SpeechSDK-Linux.tar.gz https://aka.ms/csspeech/linuxbinary
tar --strip 1 -xzf SpeechSDK-Linux.tar.gz -C "$SPEECHSDK_ROOT"
```

参考资料

【1】https://github.com/Azure-Samples/Cognitive-Speech-TTS/tree/master/MRCP

【2】[GitHub - freeswitch/mod_unimrcp](https://github.com/freeswitch/mod_unimrcp)

【3】[GitHub - zhouhailin/unimrcp-1.6: 基于1.6.0版本，增加科大讯飞、接通华声、阿里云、百度云、腾讯云相关功能](https://github.com/zhouhailin/unimrcp-1.6)
