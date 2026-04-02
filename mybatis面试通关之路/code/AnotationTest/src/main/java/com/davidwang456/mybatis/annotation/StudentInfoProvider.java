package com.davidwang456.mybatis.annotation;

import java.util.Map;

import org.apache.ibatis.jdbc.SQL;

public class StudentInfoProvider {
	public String getStudentById(Integer id) {
		return new SQL() {
			{
				SELECT("id,first_name,last_name,age");
				FROM("student");
				WHERE("id = "+id);
			}
		}.toString();
	}
	
	public String getStudentByCondition(Map<String,Object> params) {
		StringBuffer sbf=new StringBuffer();
		sbf.append("select id, first_name,last_name,age from student where 1=1");
		if(params.get("id")!=null) {
			sbf.append(" and id="+(int)params.get("id"));
		}
		if(params.get("firstName")!=null) {
			sbf.append(" and first_name= '"+params.get("firstName").toString()+"'");
		}
		if(params.get("lastName")!=null) {
			sbf.append(" and last_name= '"+params.get("lastName").toString()+"'");
		}
		if(params.get("age")!=null) {
			sbf.append(" and age="+(int)params.get("age"));
		}
		
		return sbf.toString();
	}
}
