package com.davidwang456.mybatis.plugin;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

public class TestJdkDynamicProxy {

	public static void main(String[] args) {
		IHelloWorld iHelloWorld=new HelloWorldImp();
		InsertDataHandler insertDataHandler=new InsertDataHandler();
		IHelloWorld proxy=(IHelloWorld) insertDataHandler.getProxy(iHelloWorld);
		proxy.sayHello("world");
	}
}	
	class InsertDataHandler implements InvocationHandler{
		Object obj;
		
		public Object getProxy(Object obj) {
			this.obj=obj;
			return Proxy.newProxyInstance(obj.getClass().getClassLoader(), 
					obj.getClass().getInterfaces(), this);
		}
		
		@Override
		public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
			doBefore(method.getName());
			Object result=method.invoke(obj, args);
			doAfter(method.getName());
			return result;
		}
		
		private void doBefore(String methodName){
			System.out.println("method:"+methodName +" start");
		}
		
		private void doAfter(String methodName) {
			System.out.println("method:"+methodName +" end");
		}
		
	}


