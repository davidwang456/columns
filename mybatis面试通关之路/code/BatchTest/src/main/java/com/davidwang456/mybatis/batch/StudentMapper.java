package com.davidwang456.mybatis.batch;

import java.util.List;

import org.apache.ibatis.annotations.Param;

public interface StudentMapper {
	
	public Integer insertStudentInfo(StudentDTO dto);
	
	public Integer insertBatchStudentInfo(@Param("dtos")List<StudentDTO> dtos);

}
