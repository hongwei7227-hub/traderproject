-- 下单理由快照:agent 的投资论点，下单登记时随 proposal 落库。
-- 供事后跨工作区复盘自包含（原对话可能已被上下文两层压缩摘要，只留链接会指向被压缩版）。
-- 可空：老订单 / 无理由的订单照常。要看完整分析用 proposal_id 回 Python 平台解析。
-- 无独立 analysis_ref 列：proposal_id 已是跨服务约定的关联键，不在订单里塞 Python 内部路径。

ALTER TABLE orders ADD COLUMN rationale TEXT;
