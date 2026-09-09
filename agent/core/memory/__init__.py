"""从对话中抽取结构化用户偏好，持久化由 MySQL 仓储负责。"""

from .preference_extractor import PreferenceExtractor, is_explicit_preference_message

__all__ = ["PreferenceExtractor", "is_explicit_preference_message"]
