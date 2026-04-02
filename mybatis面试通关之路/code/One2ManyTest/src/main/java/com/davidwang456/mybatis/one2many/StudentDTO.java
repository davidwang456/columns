package com.davidwang456.mybatis.one2many;

import java.io.Serializable;
import java.util.Date;
import java.util.List;

import lombok.Data;

@Data
public class StudentDTO implements Serializable{
	private static final long serialVersionUID = 1L;
	//字段
	private Integer id;
	private String firstName;
	private String lastName;
	private Integer age;
	private String content;
	private byte[] image;
	private Date createTime;
	private Date updateTime;
	private List<Address> addrs;
	@Override
	   public String toString() {
	    return "student [id=" + id + ", firstName=" + firstName
	    		 + ", lastName=" + lastName + ", age=" +age+ 
	    		 " 创建时间："+DateUtils.getDateString(createTime)+
	    		 " 更新时间："+DateUtils.getDateString(updateTime)+
	    		 " 地址：("+getAdds(addrs)+")"+
	    		 ']';
	   }
	
	private String getAdds(List<Address> address) {
		if(address==null||address.isEmpty()) {
			return "";
		}
		StringBuffer sbf=new StringBuffer();
		for(int i=0;i<address.size();i++) {
			
			if(i==address.size()-1) {
				sbf.append(address.get(i).getDetail());
			}else {
				sbf.append(address.get(i).getDetail()+", ");
			}
		}
		return sbf.toString();
	}
}
