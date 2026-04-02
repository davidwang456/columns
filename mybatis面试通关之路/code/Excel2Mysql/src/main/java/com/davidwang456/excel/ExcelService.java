package com.davidwang456.excel;

import java.io.InputStream;
import java.util.List;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import com.alibaba.excel.EasyExcel;
@Service
public class ExcelService {	
  @Autowired
  private StudentMapper studentMapper;

  public void batchParseExcel2Mysql(InputStream file,String sheetName) {
	EasyExcel.read(file, StudentDTO.class, new StudentDataListener(studentMapper)).sheet(sheetName).doRead();
  }
  
  public List<StudentDTO> getStudentInfoByCondition(StudentQueryDTO studentQueryDTO){
	 return studentMapper.getStudentInfoByCondition(studentQueryDTO);
  }
}
