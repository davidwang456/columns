package com.davidwang456.mybatis.cache;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.davidwang456.mybatis.cache.StudentDTO;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public Integer upStudentInfoById(@Param("id")Integer id,@Param("age")Integer age);
	public List<StudentDTO> getStudentInfoByCondition2(StudentQueryDTO studentQueryDTO);
}
