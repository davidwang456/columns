本故事纯属虚构，如有雷同，纯属巧合。

**故事背景**

小白喜欢电影和音乐，会在高兴时，伤心时，郁闷时，无聊时看些电影，听些音乐。此刻，正在听<新欢旧爱>：

> 谁的新欢不是别人的旧爱
>
> 有过情伤的人都会有感慨
>
> 放弃最初的那份信赖
>
> 一路也就丢失了未来
>
> 谁的新欢不是别人的旧爱
>
> 有过回忆的心就不用再掩埋
>
> 好好的珍惜 所有现在
>
> 幸福它总会闯进你的心海

此时不由想到了和小胖在 codereview 上的争论。事情是这样的，早上评审小胖的代码，发现小胖使用的循环代码如下：

```java
	private static final int batchsize=1000000;
	private static List<String> nums=new ArrayList<>();
	public static void init(){		
		for(int i=0;i<batchsize;i++) {
			nums.add(""+i);
		}
	}
	public static void index() {
		int count=0;
		for(int i=0;i<nums.size();i++) {
			count++;
		}
		System.out.println(count);
	}
```

评审时，小白认为从 JDK 版本 1.5 后，都推荐使用新的 for-each 了，不用老旧的索引或者遍历，毕竟他们都过时了，而且新的 foreach 循环对算法做了优化，性能更优。

```java
	public static void foreach() {	
		int count=0;
		for(String str:nums) {
			count++;
		}
		System.out.println(count);
	}
	//淘汰了
	public static void iterator() {
		Iterator<String> it=nums.iterator();
		int count=0;
		while(it.hasNext()) {
			it.next();
			count++;
		}
		System.out.println(count);
	}
```

这番说法，立刻遭到小胖的针锋相对，小胖给出了三个程序的测评程序，发现 foreach 并没有如传说中的性能优势，反而效率更低了。

**三个火枪手foreach，for，Iterator**

测试程序如下：

```java
	public static void main(String[] args) {
		init();
		//foreach遍历测试
		long start=System.currentTimeMillis();
		foreach();
		System.out.println(System.currentTimeMillis()-start);
         //迭代器方式测试
		start=System.currentTimeMillis();
		iterator();
		System.out.println(System.currentTimeMillis()-start);
         //索引方式测试
		start=System.currentTimeMillis();
		index();
		System.out.println(System.currentTimeMillis()-start);		
	}
```

测试数据分别为 1w，10w，100w，1kw 四个级别。

数量级\耗时(毫秒)	foreach	Iterator	index

1w							 2 		    1 				0

10w 	 					 6		     4 			    3

100w						 13		   9                 5

1kw 	 					  52 		10                4

小白震惊了，事实胜于雄辩，网上的或者别人说的，都需要验证而不能盲目的接受。

![img](image/4617924f48a9404cae2756b7fdc9d0f9.jfif)

于是虚心下来，好好复习这方面的知识，总结如下：

for-each 循环在简洁性和预防 bug 方面，有着传统的 for 循环无法比拟的优势，也没有太多的性能损失。应该尽可能的使用 for-each 循环。有三种情况，不适合使用 for-each 循环：

1.**过滤**--若需要遍历集合，并删除选定的元素，需要使用显示的迭代器，这样可以调用它的 remove 方法。

2.**转换**--若需要遍历列表或者数组，并替换它部分或者全部的元素值，需要列表迭代器或者数组索引，以便设定元素的值。

3.**平行迭代**--若需要平行的遍历多个集合，需要显示地控制迭代器或者索引遍历，以便所有的迭代器或者索引遍历可以同步前移。

