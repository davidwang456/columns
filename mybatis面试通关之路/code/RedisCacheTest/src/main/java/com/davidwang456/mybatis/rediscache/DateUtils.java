package com.davidwang456.mybatis.rediscache;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Date;

public class DateUtils {
    public final static String shortFormat = "yyyyMMdd";

    public final static String longFormat = "yyyyMMddHHmmss";

    public final static String webFormat = "yyyy-MM-dd";

    public final static String timeFormat = "HHmmss";

    public final static String monthFormat = "yyyyMM";

    public final static String chineseDtFormat = "yyyy年MM月dd日";

    public final static String newFormat = "yyyy-MM-dd HH:mm:ss";

    public final static String newFormat2 = "yyyy-MM-dd HH:mm";

    public final static String newFormat3 = "yyyy-MM-dd HH";

    public final static String FULL_DATE_FORMAT = "yyyy-MM-dd HH:mm:ss.SSS";
	
	private static final SimpleDateFormat sdf=new SimpleDateFormat(newFormat);
	
	public static String getDateString(Date date) {
		return sdf.format(date);
	}
	
	public static Date getDateByString(String date) throws ParseException {
		return sdf.parse(date);
	}
}
