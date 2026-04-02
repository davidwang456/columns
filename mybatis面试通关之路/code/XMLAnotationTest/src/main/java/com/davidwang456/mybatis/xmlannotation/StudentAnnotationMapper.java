package com.davidwang456.mybatis.xmlannotation;

import java.util.List;

import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.SelectProvider;

public interface StudentAnnotationMapper {
	@Select("select id,first_name,last_name,age from student where id=#{id}")
	public StudentDTO getStudentInfoById(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentById")
	public StudentDTO getStudentById1(Integer id);
	
	@SelectProvider(type = StudentInfoProvider.class, method = "getStudentByCondition")
	public List<StudentDTO> getStudentByIdCondition(@Param("firstName")String firstName,
			@Param("lastName")String lastName);
}
