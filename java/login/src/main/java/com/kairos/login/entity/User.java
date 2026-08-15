package com.kairos.login.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/** 用户实体（存在内存 UserStore 里，模拟 DB 行；password 存 BCrypt 哈希）。 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class User {
    private Long id;
    private String username;
    private String password;   // BCrypt 哈希，不存明文
}
