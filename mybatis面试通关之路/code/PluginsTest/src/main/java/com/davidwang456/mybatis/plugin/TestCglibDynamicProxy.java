package com.davidwang456.mybatis.plugin;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

import net.sf.cglib.proxy.Enhancer;
import net.sf.cglib.proxy.MethodInterceptor;
import net.sf.cglib.proxy.MethodProxy;

public class TestCglibDynamicProxy {

	public static void main(String[] args) {
		IHelloWorld iHelloWorld=new HelloWorldImp();
		InsertDataHandler insertDataHandler=new InsertDataHandler();
		IHelloWorld proxy=(IHelloWorld) insertDataHandler.getProxy(iHelloWorld);
		proxy.sayHello("world");
	}
}	
	class InsertDataInterceptor implements MethodInterceptor{
		Object target;
		
		public Object getProxy(Object target) {
			this.target=target;
			Enhancer enhancer=new Enhancer();
			enhancer.setSuperclass(this.target.getClass());
			enhancer.setCallback(this);
			return enhancer.create();
		}
		
		
		private void doBefore(String methodName){
			System.out.println("method:"+methodName +" start");
		}
		
		private void doAfter(String methodName) {
			System.out.println("method:"+methodName +" end");
		}

		@Override
		public Object intercept(Object obj, Method method, Object[] args, MethodProxy proxy) throws Throwable {
			// TODO Auto-generated method stub
			return null;
		}
		
	}


