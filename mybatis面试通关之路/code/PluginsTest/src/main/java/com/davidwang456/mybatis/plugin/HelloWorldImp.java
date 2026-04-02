package com.davidwang456.mybatis.plugin;

public class HelloWorldImp implements IHelloWorld{

	@Override
	public void sayHello(String msg) {
		System.out.println("hello "+ msg+ " !");
	}

}
