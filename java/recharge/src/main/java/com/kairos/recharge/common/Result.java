package com.kairos.recharge.common;

import lombok.Data;

/**
 * 统一返回体。兼容母本 CityAIHub 的 {@code Result.ok()/fail()} 与 登录 demo 的 success/data/errorMsg。
 */
@Data
public class Result {

    private boolean success;
    private String errorMsg;
    private Object data;

    public static Result ok() {
        Result r = new Result();
        r.success = true;
        return r;
    }

    public static Result ok(Object data) {
        Result r = ok();
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
