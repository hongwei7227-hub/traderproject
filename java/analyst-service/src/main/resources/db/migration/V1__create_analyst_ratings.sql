-- 分析师观点真身表(Cache-Aside 的"数据库"层)。
-- 建在独立 analyst schema(Flyway default-schema=analyst 定位),不碰主平台 public。
-- payload 存 AnalystRating 的 JSON 文本(DB 无关、避免 jsonb 绑定复杂度);symbol 为主键。
CREATE TABLE analyst_ratings (
    symbol      VARCHAR(32)              NOT NULL,
    payload     TEXT                     NOT NULL,   -- AnalystRating 序列化 JSON
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_analyst_ratings PRIMARY KEY (symbol)
);
