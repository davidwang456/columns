package com.davidwang456.mybatis.mbg;

import java.util.List;

import org.apache.ibatis.annotations.Param;

public interface StudentDTOMapper {
    int countByExample(StudentDTOExample example);

    int deleteByExample(StudentDTOExample example);

    int insert(StudentDTO record);

    int insertSelective(StudentDTO record);

    List<StudentDTO> selectByExample(StudentDTOExample example);

    int updateByExampleSelective(@Param("record") StudentDTO record, @Param("example") StudentDTOExample example);

    int updateByExample(@Param("record") StudentDTO record, @Param("example") StudentDTOExample example);
}