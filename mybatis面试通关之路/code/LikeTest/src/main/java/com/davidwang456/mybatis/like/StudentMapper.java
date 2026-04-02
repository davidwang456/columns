package com.davidwang456.mybatis.like;

import java.util.List;

import org.apache.ibatis.annotations.Param;

import com.davidwang456.mybatis.like.StudentDTO;

public interface StudentMapper {
	public Integer insertBatchStudentInfo(@Param("dtos")List<StudentDTO> dtos);
	
	public List<StudentDTO> getStudentInfoByConditionBoth(StudentQueryDTO studentQueryDTO);
	
	public List<StudentDTO> getStudentInfoByConditionLeft(StudentQueryDTO studentQueryDTO);
	
	public List<StudentDTO> getStudentInfoByConditionRight(StudentQueryDTO studentQueryDTO);

}
