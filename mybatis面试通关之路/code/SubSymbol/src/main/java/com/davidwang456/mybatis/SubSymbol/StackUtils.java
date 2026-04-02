package com.davidwang456.mybatis.SubSymbol;

public class StackUtils {
	
	public static void getStack() {
		   java.util.Map<Thread, StackTraceElement[]> ts = Thread.getAllStackTraces();
		    StackTraceElement[] ste = ts.get(Thread.currentThread());
		    int cnt=1;
		    for (int i=ste.length-1;i>0;i--) { 
		    	StackTraceElement s=ste[i];
		    	System.out.println("调用序号："+cnt+"  调用类和方法 "+s.getClassName()+"$"+s.getMethodName());
		    	cnt++;		   
		    }
	}

}
