package com.kairos.login.interceptor;

import com.kairos.login.dto.UserDTO;

/** ThreadLocal 保存当前请求的登录用户（对齐 CityAIHub UserHolder）。 */
public class UserHolder {
    private static final ThreadLocal<UserDTO> TL = new ThreadLocal<>();

    public static void saveUser(UserDTO user) {
        TL.set(user);
    }

    public static UserDTO getUser() {
        return TL.get();
    }

    public static void removeUser() {
        TL.remove();   // 线程复用必须清，否则串号
    }
}
