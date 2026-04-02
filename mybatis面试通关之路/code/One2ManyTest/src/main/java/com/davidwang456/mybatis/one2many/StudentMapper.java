package com.davidwang456.mybatis.one2many;

import java.util.List;

public interface StudentMapper {
	public Integer insertStudentInfo(StudentDTO dto);
	public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO);
	public StudentDTO getStudentById(Integer id);
    List<Address> selectByStudentId(Integer student_id);
    List<StudentDTO> getAddressByStudentId(Integer student_id);
}
