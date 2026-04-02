package com.davidwang456.mybatis.plugin;

import java.sql.Statement;

import org.apache.ibatis.executor.statement.StatementHandler;
import org.apache.ibatis.plugin.Interceptor;
import org.apache.ibatis.plugin.Intercepts;
import org.apache.ibatis.plugin.Invocation;
import org.apache.ibatis.plugin.Signature;
import org.apache.ibatis.session.ResultHandler;
@Intercepts({
    @Signature(type = StatementHandler.class, method = "query", args = {Statement.class,ResultHandler.class})
})
public class QueryConsumeTimePlugin implements Interceptor{

	@Override
	public Object intercept(Invocation invocation) throws Throwable {
		//Statement ms = (Statement) invocation.getArgs()[0];
		//ResultHandler resultHandler = (ResultHandler)invocation.getArgs()[1];
		String methodName = invocation.getMethod().getName();
		Long start=System.currentTimeMillis();
		Object result=invocation.proceed();
		System.out.println("execute method:"+methodName+",costTime:"+(System.currentTimeMillis()-start));
		return result;
	}

}
