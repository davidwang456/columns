# Mybatis的Resources和Spring中的Resource有什么关联吗

## 背景

> 小白：我们在单独使用Mybatis时，加载配置文件时使用的是Resources类，但Spring集成Mybatis时并没有使用Mybatis的Resources类来加载配置，而是利用Spring封装的Resource解析器来读取Mybatis的配置，这是为什么呢？
>
> 扫地僧：从功能上来讲，都是相同的；从加载对象上来看，Mybatis加载配置文件，Spring可以加载所有资源文件；总体上说，Spring因面临的资源对象种类更丰富，它需要更详细的资源分类体系，而Mybatis的资源比较固定，实现起来更简单。下面让我们探探它们的内部原理。

## Mybatis中的Resources

构建SqlSessionFactory通常有两种方式:

1.使用Reader读取文件

```java
	      Reader reader = Resources.getResourceAsReader("com/davidwang456/SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(reader);
```

2.使用InputStream读取文件

```java
	   	  InputStream inputStream = Resources.getResourceAsStream("com/davidwang456/SqlMapConfig.xml");
	      SqlSessionFactory sqlSessionFactory = new SqlSessionFactoryBuilder().build(inputStream);
```

我们发现Mybatis提供了一个公共的Resources类读取配置文件。

### Resources

 Resources类位于org.apache.ibatis.io包下,用于处理相关的IO操作,例如将文件,网络资源进行读取并转换为File,InputStream,Reader,URL等Java类。Resources类提供了多个getResourceAsxxx()方法作为对外暴露的方法,Resources并不提供方法实现,而是内部维护着一个静态ClassLoaderWrapper实例,Resources中的方法都是通过调用ClassLoaderWrapper相关方法实现。下面列出了Resources最常用的方法。

| 方法名                                  | 参数                 |
| --------------------------------------- | -------------------- |
| File getResourceAsFile()                | resource,classloader |
| Properties getResourceAsProperties()    | resource,classloader |
| Reader getResourceAsReader()            | resource,classloader |
| InputStream getResourceAsStream()       | resource,classloader |
| URL getResourceURL()                    | resource,classloader |
| Properties getUrlAsProperties()         | resource,classloader |
| setCharset()                            | Charset              |
| void setDefaultClassLoader(ClassLoader) | ClassLoader          |

#### 示例1

```java
Properties p=Resources.getResourceAsProperties(null, "com/davidwang456/SqlMapConfig.xml");
```

此时属性p的值为：

```java
{
</configuration>=, 
<transactionManager=type = "JDBC"/> ,
 </environments>=,
 </environment>=, 
 <environments=default = "development">, 
 <mappers>=, 
 </dataSource>=,
 <dataSource=type = "POOLED">, 
 </mappers>=, 
 <environment=id = "development">, 
 <mapper=resource = "com/davidwang456/Student.xml"/>,
 <configuration>=,
 <?xml=version = "1.0" encoding = "UTF-8"?>, 
 <!DOCTYPE=configuration PUBLIC "-//mybatis.org//DTD Config 3.0//EN" "http://mybatis.org/dtd/mybatis-3-config.dtd">, 
 <property=name = "password" value = "root"/>
 }
```

#### 示例2

```java
	   File f=Resources.getResourceAsFile(null, "com/davidwang456/SqlMapConfig.xml");	
	   System.out.println(f.getAbsolutePath());
```

此时运行结果为：

```tex
C:\workspace\mybatis-3\target\classes\com\davidwang456\SqlMapConfig.xml
```

#### 示例3

```java
	   URL url=Resources.getResourceURL(null, "com/davidwang456/SqlMapConfig.xml");	
	   System.out.println(url);
```

此时运行结果为：

```tex
file:/C:/workspace/mybatis-3/target/classes/com/davidwang456/SqlMapConfig.xml
```

通过追踪源码，我们发现上述的实现由ClassLoaderWrapper实现，如Reader和InputStream的内部实现均为getResourceAsStream

```java
  /**
   * Returns a resource on the classpath as a Stream object
   *
   * @param loader   The classloader used to fetch the resource
   * @param resource The resource to find
   * @return The resource
   * @throws java.io.IOException If the resource cannot be found or read
   */
  public static InputStream getResourceAsStream(ClassLoader loader, String resource) throws IOException {
    InputStream in = classLoaderWrapper.getResourceAsStream(resource, loader);
    if (in == null) {
      throw new IOException("Could not find resource " + resource);
    }
    return in;
  }
```

### ClassLoaderWrapper

   ClassLoaderWrapper没有构造器,无法直接进行实例化,只能通过Resources类进行获取。该类提供了获取资源的两种方式,以InputStream形式读取本地资源,以URL形式读取本地资源。除此之外,ClassLoaderWrappe还提供了classForName()方法用于获取类路径对应的Class实例。下面是ClassLoaderWrapper提供的所有方法。可以看到,每种类型方法都提供了三种入参形式,其中一种方法需要提供一个ClassLoader数组,需要注意的是这种方法并不能直接调用,前两种方法都是交给带有classloader[]参数的方法进行实现的。如果不提供ClassLoader则设置为null。

- Class<?> classForName(String name) 
- Class<?> classForName(String name, ClassLoader classLoader) 
- Class<?> classForName(String name, ClassLoader[] classLoader) 
- InputStream getResourceAsStream(String resource) 
- InputStream getResourceAsStream(String resource, ClassLoader classLoader) 
- InputStream getResourceAsStream(String resource, ClassLoader[] classLoader)
- URL getResourceAsURL(String resource) 
- URL getResourceAsURL(String resource, ClassLoader classLoader) 
- URL getResourceAsURL(String resource, ClassLoader[] classLoader)
- ClassLoader[] getClassLoaders(ClassLoader classLoader)

**getClassLoaders()**用于为getResouceAsxxx()方法提供多个加载器。前面说过,不传入加载器数组的方法都会调用 getResourceAsxxx(String resource, ClassLoader[] classLoader)相关的方法,而获取加载器数组的方式就是直接调用getClassLoaders()方法。

```java
 ClassLoader[] getClassLoaders(ClassLoader classLoader) {
return new ClassLoader[]{
classLoader,//自定义加载器
defaultClassLoader,
Thread.currentThread().getContextClassLoader(),//当前线程加载器
getClass().getClassLoader(),//类加载器
systemClassLoader//系统默认加载器
};
}
```

**classForName()**根据类路径生成Class实例,该方法的本质是调用classloader下的forname方法进行实例化的。

```java
  /**
   * Attempt to load a class from a group of classloaders
   *
   * @param name        - the class to load
   * @param classLoader - the group of classloaders to examine
   * @return the class
   * @throws ClassNotFoundException - Remember the wisdom of Judge Smails: Well, the world needs ditch diggers, too.
   */
  Class<?> classForName(String name, ClassLoader[] classLoader) throws ClassNotFoundException {

    for (ClassLoader cl : classLoader) {

      if (null != cl) {

        try {

          Class<?> c = Class.forName(name, true, cl);

          if (null != c) {
            return c;
          }

        } catch (ClassNotFoundException e) {
          // we'll ignore this until all classloaders fail to locate the class
        }

      }

    }

    throw new ClassNotFoundException("Cannot find class: " + name);

  }
```

**getResourceAsStream()和getResourceAsURL():**这两个方法都是获取相关的资源,与File类不同的是,这两个方法传递的参数必须是相对路径,否则抛出异常。同classForName()一样,必须遍历classLoader数组,然后调用getResourceAsStream()和getResource()。

ClassLoaderWrapper内部封装了JDK内部java.lang.ClassLoader的实现。

### ClassLoader

ClassLoaderWrapper的getResourceAsStream调用了ClassLoader的方法getResourceAsStream：

```java
   /**
     * Returns an input stream for reading the specified resource.
     *
     * <p> The search order is described in the documentation for {@link
     * #getResource(String)}.  </p>
     *
     * @param  name
     *         The resource name
     *
     * @return  An input stream for reading the resource, or <tt>null</tt>
     *          if the resource could not be found
     *
     * @since  1.1
     */
    public InputStream getResourceAsStream(String name) {
        URL url = getResource(name);
        try {
            return url != null ? url.openStream() : null;
        } catch (IOException e) {
            return null;
        }
    }
```

ClassLoaderWrapper的getResourceAsURL调用了ClassLoader的方法getResourceAsURL：

```java
   /**
     * Finds the resource with the given name.  A resource is some data
     * (images, audio, text, etc) that can be accessed by class code in a way
     * that is independent of the location of the code.
     *
     * <p> The name of a resource is a '<tt>/</tt>'-separated path name that
     * identifies the resource.
     *
     * <p> This method will first search the parent class loader for the
     * resource; if the parent is <tt>null</tt> the path of the class loader
     * built-in to the virtual machine is searched.  That failing, this method
     * will invoke {@link #findResource(String)} to find the resource.  </p>
     *
     * @apiNote When overriding this method it is recommended that an
     * implementation ensures that any delegation is consistent with the {@link
     * #getResources(java.lang.String) getResources(String)} method.
     *
     * @param  name
     *         The resource name
     *
     * @return  A <tt>URL</tt> object for reading the resource, or
     *          <tt>null</tt> if the resource could not be found or the invoker
     *          doesn't have adequate  privileges to get the resource.
     *
     * @since  1.1
     */
    public URL getResource(String name) {
        URL url;
        if (parent != null) {
            url = parent.getResource(name);
        } else {
            url = getBootstrapResource(name);
        }
        if (url == null) {
            url = findResource(name);
        }
        return url;
    }
```

## Spring中的Resource

Spring集成Mybatis时并没有使用Mybatis中的Resources类加载配置文件，而是利用了自己的Resource来实现配置文件的加载。

```java
package com.davidwang456.mybatis.spring;

import javax.sql.DataSource;

import org.apache.ibatis.session.SqlSessionFactory;
import org.mybatis.spring.SqlSessionFactoryBean;
import org.mybatis.spring.annotation.MapperScan;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;

import com.alibaba.druid.pool.DruidDataSource;

@Configuration
@MapperScan("com.davidwang456.mybatis.spring.mapper")
public class SpringConfig {
    @Bean
    public DataSource getDataSource() {
       DruidDataSource dataSource = new DruidDataSource();
       dataSource.setDriverClassName("com.mysql.cj.jdbc.Driver");
       dataSource.setUrl("jdbc:mysql://localhost:3306/davidwang456?characterEncoding=UTF-8&useSSL=false&useLegacyDatetimeCode=false&serverTimezone=UTC");
       dataSource.setUsername("root");
       dataSource.setPassword("wangwei456");
       return dataSource;
   }
    
   @Bean
   public DataSourceTransactionManager transactionManager() {
     return new DataSourceTransactionManager(getDataSource());
   }
   
   @Bean
   public SqlSessionFactory sqlSessionFactory() throws Exception {
	  PathMatchingResourcePatternResolver resolver=new PathMatchingResourcePatternResolver();
      SqlSessionFactoryBean sessionFactory = new SqlSessionFactoryBean();
      sessionFactory.setDataSource(getDataSource());
      sessionFactory.setConfigLocation(resolver.getResource("SqlMapConfig.xml"));
      sesionFactory.setMapperLocations(resolver.getResource("StudentMapper.xml"));
      return sessionFactory.getObject();
   }
}
```

我们深入源码看一看这个PathMatchingResourcePatternResolver是如何加载配置文件的？

```java
	@Override
	public Resource getResource(String location) {
		return getResourceLoader().getResource(location);
	}
	/**
	 * Return the ResourceLoader that this pattern resolver works with.
	 */
	public ResourceLoader getResourceLoader() {
		return this.resourceLoader;
	}
```

其中，resourceLoader由DefaultResourceLoader实现，DefaultResourceLoader内部封装了java.lang.ClassLoader。

不同于Mybatis针对classpath下的资源文件，Spring对资源做了更多的封装，ResourceUtils定义了Spring支持的资源类型：

```java
	/** Pseudo URL prefix for loading from the class path: "classpath:" */
	public static final String CLASSPATH_URL_PREFIX = "classpath:";

	/** URL prefix for loading from the file system: "file:" */
	public static final String FILE_URL_PREFIX = "file:";

	/** URL prefix for loading from a jar file: "jar:" */
	public static final String JAR_URL_PREFIX = "jar:";

	/** URL prefix for loading from a war file on Tomcat: "war:" */
	public static final String WAR_URL_PREFIX = "war:";

	/** URL protocol for a file in the file system: "file" */
	public static final String URL_PROTOCOL_FILE = "file";

	/** URL protocol for an entry from a jar file: "jar" */
	public static final String URL_PROTOCOL_JAR = "jar";

	/** URL protocol for an entry from a war file: "war" */
	public static final String URL_PROTOCOL_WAR = "war";

	/** URL protocol for an entry from a zip file: "zip" */
	public static final String URL_PROTOCOL_ZIP = "zip";

	/** URL protocol for an entry from a WebSphere jar file: "wsjar" */
	public static final String URL_PROTOCOL_WSJAR = "wsjar";

	/** URL protocol for an entry from a JBoss jar file: "vfszip" */
	public static final String URL_PROTOCOL_VFSZIP = "vfszip";

	/** URL protocol for a JBoss file system resource: "vfsfile" */
	public static final String URL_PROTOCOL_VFSFILE = "vfsfile";

	/** URL protocol for a general JBoss VFS resource: "vfs" */
	public static final String URL_PROTOCOL_VFS = "vfs";

	/** File extension for a regular jar file: ".jar" */
	public static final String JAR_FILE_EXTENSION = ".jar";

	/** Separator between JAR URL and file path within the JAR: "!/" */
	public static final String JAR_URL_SEPARATOR = "!/";

	/** Special separator between WAR URL and jar part on Tomcat */
	public static final String WAR_URL_SEPARATOR = "*/";

```

这些类型是和Spring应用息息相关的，但Mybatis用不到这么多。Spring底层实现这些资源的读取逻辑如下：

```java
	@Override
	public Resource getResource(String location) {
		Assert.notNull(location, "Location must not be null");

		for (ProtocolResolver protocolResolver : getProtocolResolvers()) {
			Resource resource = protocolResolver.resolve(location, this);
			if (resource != null) {
				return resource;
			}
		}

		if (location.startsWith("/")) {
			return getResourceByPath(location);
		}
		else if (location.startsWith(CLASSPATH_URL_PREFIX)) {
			return new ClassPathResource(location.substring(CLASSPATH_URL_PREFIX.length()), getClassLoader());
		}
		else {
			try {
				// Try to parse the location as a URL...
				URL url = new URL(location);
				return (ResourceUtils.isFileURL(url) ? new FileUrlResource(url) : new UrlResource(url));
			}
			catch (MalformedURLException ex) {
				// No URL -> resolve as resource path.
				return getResourceByPath(location);
			}
		}
	}
```

Spring将支持的资源做了分类，不同的解析器做不同的事情。

## 总结

- Mybatis和Spring对资源的访问内部都是通过ClassLoader实现的。
- Mybatis和Spring因访问资源的场景不同，实现资源的读取逻辑也不同，Mybatis更简单，Spring更成体系。
- Mybaits的Resources类封装了资源的访问，和Spring中定义的Resource是不同的，Resources更接近于Spring中的DefaultResourceLoader。





