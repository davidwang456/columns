package com.davidwang456.mybatis.enumtest;

import java.util.List;

public interface StudentMapper {
	public Integer insert(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert1(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert2(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	public Integer insert3(com.davidwang456.mybatis.enumtest.StudentDTO studentDTO);
	
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition1(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition2(StudentQueryDTO studentQueryDTO);
	public List<StudentDTO> getStudentInfoByCondition3(StudentQueryDTO studentQueryDTO);

}
