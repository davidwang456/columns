package com.davidwang456.mybatis.one2many;

import java.util.List;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public List<String> getAllNames();
	public Integer getAgeById(Integer id);
	public List<StudentDTO> getStudentByCondition(StudentQueryDTO studentQueryDTO);
}
