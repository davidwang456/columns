package com.davidwang456.mybatis.SubSymbol;

import java.util.List;

public interface StudentMapper {
	
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);

}
