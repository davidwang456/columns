package com.davidwang456.mybatis.spring.mapper;

import java.util.List;

import com.davidwang456.mybatis.spring.StudentDTO;
import com.davidwang456.mybatis.spring.StudentQueryDTO;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
