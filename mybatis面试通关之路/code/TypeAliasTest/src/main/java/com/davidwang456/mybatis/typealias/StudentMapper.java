package com.davidwang456.mybatis.typealias;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public StudentDTO getStudentInfoById(Integer id);

}
