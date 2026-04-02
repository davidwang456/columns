package com.davidwang456.excel;

import java.io.IOException;
import java.util.List;

import javax.servlet.ServletOutputStream;
import javax.servlet.http.HttpServletResponse;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseBody;
import org.springframework.web.multipart.MultipartFile;

import com.alibaba.excel.EasyExcel;

import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
@Api(tags="上传下载管理")
@Controller
public class ExcelController {
	  @Autowired
	  ExcelService excelService;
	  
	  @ApiOperation("学生信息列表")
	  @PostMapping("/list")
	  @ResponseBody
	  public List<StudentDTO> list(@RequestBody StudentQueryDTO studentQueryDTO) {
		  return excelService.getStudentInfoByCondition(studentQueryDTO);
	  }
	  @ApiOperation("文件上传")
	  @PostMapping("/uploadFile")
	  @ResponseBody
	  public String list(@RequestParam("file") MultipartFile file,@RequestParam("sheetName") String sheetName ) throws IOException {
		  excelService.batchParseExcel2Mysql(file.getInputStream(),sheetName);
		  return "upload success!";
	  }
	  @ApiOperation("文件下载")
      @GetMapping("/download/{fileName}")
      public void downloadFile(@PathVariable String fileName,HttpServletResponse response) {
	      //      设置响应类型为excel
	      response.setContentType("application/vnd.ms-excel");
	      /*
	       	* 设置响应头以及文件名称
	       *      Content-disposition 是 MIME 协议的扩展，MIME 协议指示 MIME 用户代理如何显示附加的文件。
	            *      浏览器接收到头时，它会激活文件下载对话框
	       *      attachment 附件
	       *      filename 附件名
	       */
	      response.setHeader("Content-Disposition", "attachment;filename="+fileName+".xlsx");
	    
	      try {
	//        从HttpServletResponse中获取OutputStream输出流
	        ServletOutputStream outputStream = response.getOutputStream();
	        /*
	         * EasyExcel 有多个不同的read方法，适用于多种需求
	                * 这里调用EasyExcel中通过OutputStream流方式输出Excel的write方法
	                * 它会返回一个ExcelWriterBuilder类型的返回值
	         * ExcelWriterBuilde中有一个doWrite方法，会输出数据到设置的Sheet中
	         */
	        StudentQueryDTO studentQueryDTO =new StudentQueryDTO();
	        studentQueryDTO.setFirstName("david");
	        studentQueryDTO.setLastName("wang");
	        EasyExcel.write(outputStream,StudentDTO.class).sheet("测试数据").doWrite(excelService.getStudentInfoByCondition(studentQueryDTO));
	    } catch (IOException e) {
	        e.printStackTrace();
	    }
    }
}
