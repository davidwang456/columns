package com.davidwang456.mybatis.util;

import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;

public class MappedStatementUtil {
	//1.打印名称和对象
	public static void printMappedStatementName(SqlSession session) {
	      Configuration config=session.getConfiguration();
	      for(String sta:config.getMappedStatementNames()) {
	    	  System.out.println("id="+sta+",object:"+config.getMappedStatement(sta).toString());	    	 
	      }			
	}
	//2.打印名称和执行的sql
	public static void printMappedSql(SqlSession session,Object parameterObject) {
	      Configuration config=session.getConfiguration();
	      for(String sta:config.getMappedStatementNames()) {
	    	  MappedStatement mappedStatement=config.getMappedStatement(sta);
	    	  System.out.println("id="+sta+",sql"+mappedStatement.getBoundSql(parameterObject).getSql());	    	 
	      }	
	}
	
	//3.检查入参和出差
	@SuppressWarnings("rawtypes")
	public static boolean checkParameterResultType(SqlSession session) {
	Boolean flag=true;
	Configuration config=session.getConfiguration();
	for(String sta:config.getMappedStatementNames()) {
	  MappedStatement mappedStatement=config.getMappedStatement(sta);
	  try {
		Class parameterType =mappedStatement.getParameterMap().getType();
		System.out.println("mapppedStatement id:"+sta+",parameterType:"+parameterType.getName());
		Class resultType=mappedStatement.getResultMaps().get(0).getType();  
		System.out.println("mapppedStatement id:"+sta+",resultType:"+resultType.getName());
		Class.forName(parameterType.getName());
		Class.forName(resultType.getName());
	} catch (ClassNotFoundException e) {
		flag=false;
	}
	}	
	return flag;
	}
}
