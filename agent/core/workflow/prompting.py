"""专业 Agent 共用的提示词片段。"""


def history_source_guidance(agent_name: str) -> str:
    """说明共享历史中的回答来源及跨 Agent 使用边界。"""
    return f"""【共享历史消息来源】
- 历史 assistant 消息的 name 字段表示回答来源。
- name={agent_name} 表示你之前的最终回答；其他 name 来自其他专业 Agent 或缓存。
- 其他 Agent 的回答只能用于理解用户指代和对话背景，不得声称其中的查询或核验由你完成。
- 涉及你职责内的参数、价格、库存、订单、物流、政策、工单或兼容性事实时，必须按本轮要求重新调用工具核验，不得把历史回答当作最新工具结果。"""
