# 背景

在Java程序中，redis提供的方法插入的key,value要么是string,要么就是byte[]数组。那如果是要插入的value是个对象怎么办呢？一种方式是将对象转换成JSON然后传送。另外一种redis使用JSON序列化。

# 示例

**1.创建一个包含web和redis的spring boot项目**

**pom.xml**

![](http://p26.toutiaoimg.com/large/pgc-image/ccae5f01cdef407fb5bdaeaf2ab97c5b)

**2.存储对象**

![](http://p9.toutiaoimg.com/large/pgc-image/bd3f81bad7004f82940b08401ee60fd3)

**3.配置redis，使用Jackson2JsonRedisSerializer作为序列化类**

![](http://p3.itoutiaoimg.com/large/pgc-image/68778a5b24a54cc4b68858bf14ca41d0)

**4.测试存储字符串和对象的时间<本机情况，仅供参考>**

![](http://p26.toutiaoimg.com/large/pgc-image/3bfab020232e4279b9d88e9f254c52ed)

**5.测试结果**

使用Jackson2JsonRedisSerializer作为序列化方式的设置和获取速度比StringRedisSerializer快超出的预计。数据仅供参考。

![](http://p6.toutiaoimg.com/large/pgc-image/fce06ff6ce474f9d80d993c7117c113e)

# 总结

使用Jackson2JsonRedisSerializer作为序列化方式，可以大大简化代码，不用提前转换成json字符串，之后解析字符为对象，代码看着清爽了很多，你值得拥有。
