package com.kairos.login.common;

import lombok.Data;

/** 统一返回体（对齐 CityAIHub 的 Result 风格）。 */
@Data
public class Result {
    private Boolean success;
    private String errorMsg;
    private Object data;

    public static Result ok() {
        Result r = new Result();
        r.success = true;
        return r;
    }

    public static Result ok(Object data) {
        Result r = new Result();
        r.success = true;
        r.data = data;
        return r;
    }

    public static Result fail(String errorMsg) {
        Result r = new Result();
        r.success = false;
        r.errorMsg = errorMsg;
        return r;
    }
}
