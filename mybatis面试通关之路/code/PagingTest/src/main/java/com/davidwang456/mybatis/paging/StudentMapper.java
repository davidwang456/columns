package com.davidwang456.mybatis.paging;

import java.util.List;

public interface StudentMapper {
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
}
