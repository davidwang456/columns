package com.davidwang456.mybatis.annotation;

import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.SelectProvider;

public interface StudentMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentById")
	public StudentDTO getStudentById1(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentByCondition")
	public StudentDTO getStudentByIdCondition(@Param("id")Integer id,@Param("firstName")String firstName,
			@Param("lastName")String lastName,@Param("age")Integer age);
}
