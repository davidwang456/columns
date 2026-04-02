package com.davidwang456.mybatis.jdbc.mapper;

import com.davidwang456.mybatis.jdbc.dto.StudentDTO;

public interface StudentMapper {
	
	public StudentDTO getStudentInfoById(Integer id);

}
