-- ==========================================================
-- 数码商城第一阶段订单与物流模拟数据初始化脚本
-- ==========================================================
-- 商品目录的演示快照当前由 agent/data/digital_catalog.json 提供。
-- 本脚本建立真实商城接入时所需的订单侧关系表，字段命名与 MCP 工具返回
-- 保持一致。执行前请确认目标数据库为开发或测试环境。

-- 1. 创建数码商品订单主表
CREATE TABLE IF NOT EXISTS digital_orders (
    order_id VARCHAR(50) PRIMARY KEY COMMENT '订单唯一ID',
    user_id VARCHAR(50) NOT NULL COMMENT '所属用户ID，仅允许查询当前登录用户',
    status VARCHAR(20) NOT NULL COMMENT '订单状态',
    total_amount DECIMAL(10, 2) NOT NULL COMMENT '订单总金额（人民币）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_digital_orders_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数码商品订单主表';

-- 2. 创建订单商品明细表
CREATE TABLE IF NOT EXISTS digital_order_items (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '自增主键',
    order_id VARCHAR(50) NOT NULL COMMENT '关联订单ID',
    sku_id VARCHAR(100) NOT NULL COMMENT '商品目录中的标准SKU',
    product_name VARCHAR(200) NOT NULL COMMENT '下单时的商品名称快照',
    quantity INT NOT NULL DEFAULT 1 COMMENT '购买数量',
    unit_price DECIMAL(10, 2) NOT NULL COMMENT '下单时单价快照',
    INDEX idx_digital_order_items_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数码商品订单明细表';

-- 3. 创建物流状态表
CREATE TABLE IF NOT EXISTS digital_shipments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '自增主键',
    order_id VARCHAR(50) NOT NULL COMMENT '关联订单ID',
    carrier VARCHAR(50) NOT NULL COMMENT '物流承运商',
    tracking_no VARCHAR(100) NOT NULL COMMENT '物流单号',
    status VARCHAR(100) NOT NULL COMMENT '最新物流状态',
    updated_at DATETIME NOT NULL COMMENT '物流状态更新时间',
    UNIQUE KEY uk_digital_shipments_order (order_id),
    INDEX idx_digital_shipments_tracking (tracking_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数码商品订单物流表';

-- 4. 清理并写入可重复执行的第一阶段测试数据
TRUNCATE TABLE digital_shipments;
TRUNCATE TABLE digital_order_items;
TRUNCATE TABLE digital_orders;

INSERT INTO digital_orders (order_id, user_id, status, total_amount, created_at) VALUES
('DG-1001-0001', 'user_1001', '已完成', 7999.00, '2026-07-12 10:20:00'),
('DG-1001-0002', 'user_1001', '运输中', 5499.00, '2026-08-15 09:10:00'),
('DG-1002-0001', 'user_1002', '已完成', 2499.00, '2026-06-18 14:00:00'),
('DG-1002-0002', 'user_1002', '待付款', 4299.00, '2026-08-17 12:00:00');

INSERT INTO digital_order_items (
    order_id, sku_id, product_name, quantity, unit_price
) VALUES
('DG-1001-0001', 'PHONE-APPLE-IP16PRO-256-TI', 'iPhone 16 Pro 256GB 原色钛金属', 1, 7999.00),
('DG-1001-0002', 'LAPTOP-LENOVO-XIAOXINPRO14-R7-32-1T', '联想小新 Pro 14 锐龙7 32GB+1TB', 1, 5499.00),
('DG-1002-0001', 'PHONE-REDMI-K80-256', 'REDMI K80 12GB+256GB', 1, 2499.00),
('DG-1002-0002', 'LAPTOP-HONOR-X16PLUS-24-1T', '荣耀笔记本 X16 Plus 24GB+1TB', 1, 4299.00);

INSERT INTO digital_shipments (
    order_id, carrier, tracking_no, status, updated_at
) VALUES
('DG-1001-0001', '顺丰速运', 'SF-DEMO-10010001', '已签收', '2026-07-14 16:30:00'),
('DG-1001-0002', '京东物流', 'JD-DEMO-10010002', '运输中，预计次日送达', '2026-08-17 08:20:00'),
('DG-1002-0001', '顺丰速运', 'SF-DEMO-10020001', '已签收', '2026-06-20 11:00:00');
