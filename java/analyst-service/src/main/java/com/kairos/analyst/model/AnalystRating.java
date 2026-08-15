package com.kairos.analyst.model;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * 一只股票的分析师观点(共享参考数据,读多写少)。
 *
 * <p>字段无参构造 + getter/setter 由 Lombok 生成 —— Jackson 反序列化(从 Redis JSON 读回)需要。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AnalystRating {

    private String symbol;
    private String companyName;

    /** 共识评级:Strong Buy / Buy / Hold / Sell / Strong Sell */
    private String consensusRating;
    /** 目标价共识 */
    private Double targetPriceConsensus;
    private Double targetPriceHigh;
    private Double targetPriceLow;
    /** 覆盖该股的分析师数量 */
    private Integer numAnalysts;

    /** 最近的评级变动记录(升/降级) */
    private List<Grade> recentGrades;

    /** 数据更新时间(epoch millis) */
    private Long updatedAt;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class Grade {
        private String firm;       // 投行/机构
        private String fromGrade;
        private String toGrade;
        private String action;     // upgrade / downgrade / maintain / initiate
        private String date;       // YYYY-MM-DD
    }
}
