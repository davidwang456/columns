package com.davidwang456.mybatis.xmlannotation;

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
		if(params.get("firstName")!=null) {
			sbf.append(" and first_name like '%"+params.get("firstName").toString()+"%'");
		}
		if(params.get("lastName")!=null) {
			sbf.append(" and last_name like '%"+params.get("lastName").toString()+"%'");
		}		
		return sbf.toString();
	}
}
