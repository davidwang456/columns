本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

小白的梦想是成为一名黑客或者极客，可以在互联的路由中恣意奔跑，但生活的压力或者是小白本身的懒惰，导致这个梦想离小白还是比较遥远，但这不妨碍小白的梦想，这个梦想也可以通过游戏去实现。比如今天他正兴致勃勃玩的游戏《连环清洁工》。连环清洁工是一款犯罪题材动作冒险类游戏，故事剧情讲述的是一个专门山寨别人的杀手，专门模仿最近发生的大案要案，制造类似的凶杀案。游戏中玩家扮演一名专业凶案现场清扫人员，为客户处理尸体、清理血迹、隐藏凶器等犯罪证据，玩家接受任务的时候不能问任何问题。

![img](image/9a64ed6c58bf44b8a557eadb0ad46de9.jfif)

每次玩这个游戏的时候，小白总是不由自主的想到了扫地僧教他写代码的场景。

**Java中如何释放资源？**

刚进公司时小白在学习 Java，碰到一个编程问题：文件操作关闭资源的时候，会莫名其妙的报错。代码如下：

```java
 public void openFile() throws IOException {
 	FileReader reader = new FileReader("someFile");
 	int i=0;
 	while(i != -1){
 		i = reader.read();
 		System.out.println((char) i );
 	}
 	reader.close();
 	System.out.println("--- File End ---");
 }
```

扫地僧针对小白刚刚编程的经历，采用循循诱导的方式。

扫地僧：上面的代码是不是没有捕获异常？是不是可以把异常捕获到，再分析异常原因？

小白：对哦，那我使用 try ... catch 试试：

```java
 public void openFile(){
 try {
 	// constructor may throw FileNotFoundException
 	FileReader reader = new FileReader("someFile");
 	int i=0;
 	while(i != -1){
 	//reader.read() may throw IOException
 	i = reader.read();
 	System.out.println((char) i );
 }
 reader.close();
 System.out.println("--- File End ---");
 } catch (FileNotFoundException e) {
 //do something clever with the exception
 } catch (IOException e) {
 //do something clever with the exception
 }
 }
```

扫地僧：做的很不错，知道捕捉多重异常了，资源的关闭是不是放到 finally 比较好？

小白：对哦，我看语法有这样的，那我重新写一下：

```java
 public void openFile() throws IOException {
 FileReader reader = null;
 try {
 	reader = new FileReader("someFile");
 	int i=0;
 	while(i != -1){
  		i = reader.read();
 	System.out.println((char) i );
 	}
 } catch (FileNotFoundException e) {
 //do something clever with the exception
 } catch (IOException e) {
 //do something clever with the exception
 }finally {
 reader.close();
 System.out.println("--- File End ---");
 }
 }
```

小白：哦，还忘掉 reader 的判断，再改一下：

```java
 public void openFile() throws IOException {
 FileReader reader = null;
 try {
 	reader = new FileReader("someFile");
 	int i=0;
 	while(i != -1){
 		i = reader.read();
 		System.out.println((char) i );
 	}
 } catch (FileNotFoundException e) {
 //do something clever with the exception
 } catch (IOException e) {
 //do something clever with the exception
 }finally {
 	if(reader != null){
 		reader.close();
 }
 reader.close();
 System.out.println("--- File End ---");
 }
 }
```

扫地僧：reader 的关闭，是不是还有可能抛出异常，是不是还要捕获？

小白：是哦，我忘记了，修改后的是这样的吗？

```java
 public void openFile() throws IOException {
 FileReader reader = null;
 try {
 	reader = new FileReader("someFile");
 	int i=0;
 	while(i != -1){
 		i = reader.read();
 	System.out.println((char) i );
 	}
 } catch (FileNotFoundException e) {
 //do something clever with the exception
 } catch (IOException e) {
 //do something clever with the exception
 }finally {
 	if(reader != null){
 		try {
 			reader.close();
 		} catch (IOException e) {
 		//do something clever with the exception
 		}
 }
 reader.close();
 System.out.println("--- File End ---");
 }
 }
```

扫地僧：代码是不是太繁琐了？有没有更简洁的办法？让 Jvm 帮你处理一些繁琐的工作？

小白：听说过 try-with-resources，但没有用过。

扫地僧：那你看看这个是否简洁了一些呢？

```java
 public void openFile() throws IOException {
        String line;
        try (BufferedReader br = new BufferedReader(
                new FileReader("C:\\testing.txt"))) {
            while ((line = br.readLine()) != null) {
                System.out.println(line);
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
 }
```

从 JDK7 开始，使用 try-with-resources 可以自动释放资源，即把资源放到 try() 内部， JVM 会调用 java.lang.AutoCloseable.close() 方法，自动关闭 try() 内部的资源。

小白：厉害，我学会了。

扫地僧：那我考考你。

```java
 public static void main(String[] args) {
 try {
 	System.out.println("Hello world");
 	return;
 } finally {
 	System.out.println("Goodbye world");
 }
 }
 
```

这个会打印出什么结果？

小白：“hello world” ，因为 return 退出了，finally 不能执行。

扫地僧：不对，finally 总是会执行的，打印：

```java
Hello world

Goodbye world
```

小白：我明白了，finally 总是会执行的。

扫地僧：那可不一定哦，看看这个：

```java
 public static void main(String[] args) {
 try {
 	System.out.println("Hello world");
 	System.exit(0);
 } finally {
 	System.out.println("Goodbye world");
 }
 }
```

小白：不是打印？

```java
Hello world

Goodbye world
```

扫地僧：不论 try 语句块的执行是正常地还是意外地结束，finally 语句块确实都会执行。然而在这个程序中，try 语句块根本就没有结束其执行过程。System.exit 方法将停止当前线程和所有其他当场死亡的线程。finally 子句的出现并不能给予线程，继续去执行的特殊权限。如果想要执行，需要使用 ShutdownHook。JAVA中的ShutdownHook遇到进程挂掉的情况，且一些状态没有正确的保存下来，ShutdownHook可以在JVM关掉的时候执行一些清理现场的代码。

```java
 public static void main(String[] args) {
 System.out.println("Hello world");
 Runtime.getRuntime().addShutdownHook(
 	new Thread() {
 	public void run() {
 		System.out.println("Goodbye world");
 }
 });
 System.exit(0);
 }
```

小白：好神奇！

扫地僧：学无止境，一起加油！今天到这里了！

