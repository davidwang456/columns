package com.davidwang456.mybatis.clobblob;

import java.util.List;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
