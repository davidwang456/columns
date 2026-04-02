package com.davidwang456.mybatis.enumtest;

public enum Status {
    NEW(1,"NEW"),ACTIVE(2,"ACTIVE"),INACTIVE(3,"INACTIVE"),DELETE(4,"DEL");
	private Integer code;
	private String  desc;
	
	Status(Integer code,String desc){
		this.setCode(code);
		this.setDesc(desc);
	}

	public Integer getCode() {
		return code;
	}

	public void setCode(Integer code) {
		this.code = code;
	}

	public String getDesc() {
		return desc;
	}

	public void setDesc(String desc) {
		this.desc = desc;
	}
	
	public static void main(String[] args) {
		Status st=Status.DELETE;
		System.out.println(st.name());
	}
	
}
