package com.davidwang456.excel;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface StudentMapper {
	List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	Integer insertStudentInfo(StudentDTO studentDTO);
	Integer insertBatchStudentInfo(List<StudentDTO> dtos);
}
