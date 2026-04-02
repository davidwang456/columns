package com.davidwang456.mybatis.autoincrement;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public Integer deleteById(Integer id);
}
