"""角色系统统一导出。

**Why**: 通过工厂函数集中管理角色实例化，调用方无需知道具体角色类名，
只需传入 :class:`Role` 枚举即可获得对应的角色实例。
角色注册表 :const:`_ROLE_REGISTRY` 提供了枚举到类的映射，
方便后续扩展新角色时只需在注册表中添加一行即可。
"""

from __future__ import annotations

from typing import Dict, Type

from ai_werewolf_core.schemas.enums import Role

from .base import BaseRole
from .hunter import HunterRole
from .seer import SeerRole
from .villager import VillagerRole
from .werewolf import WerewolfRole
from .witch import WitchRole

# ------------------------------------------------------------------
# 角色注册表
# ------------------------------------------------------------------

_ROLE_REGISTRY: Dict[Role, Type[BaseRole]] = {
    Role.VILLAGER: VillagerRole,
    Role.WEREWOLF: WerewolfRole,
    Role.SEER: SeerRole,
    Role.WITCH: WitchRole,
    Role.HUNTER: HunterRole,
}
"""角色枚举到具体类的映射。

**Why**: 使用字典而非 if/elif 链，使新增角色时只需添加一行，
符合开闭原则（对扩展开放、对修改关闭）。
"""

# ------------------------------------------------------------------
# 工厂函数
# ------------------------------------------------------------------


def create_role(role_type: Role, player_id: str) -> BaseRole:
    """工厂函数：根据角色枚举创建对应的角色实例。

    **Why**: Game Engine 在 ``INIT`` 阶段根据配置分配角色时，
    只需遍历角色列表并调用此函数，无需关心具体是哪个角色类。
    这解耦了角色分配逻辑与具体角色实现。

    Args:
        role_type: 角色类型枚举值。
        player_id: 绑定的玩家 ID，格式 ``player_{序号}``。

    Returns:
        对应角色类型的实例。

    Raises:
        KeyError: 如果传入的 ``role_type`` 不在注册表中（如未来新增角色但未注册）。
        TypeError: 如果 ``role_type`` 不是有效的 :class:`Role` 枚举成员。

    Example:
        >>> from ai_werewolf_core.schemas.enums import Role
        >>> witch = create_role(Role.WITCH, "player_3")
        >>> witch.role_type
        <Role.WITCH: 'WITCH'>
        >>> witch.has_antidote
        True
    """
    if not isinstance(role_type, Role):
        raise TypeError(
            f"role_type 必须是 Role 枚举成员，实际类型为 {type(role_type).__name__}"
        )

    role_cls = _ROLE_REGISTRY.get(role_type)
    if role_cls is None:
        raise KeyError(
            f"未找到角色类型 [{role_type}] 的注册信息。"
            f"已注册的角色类型: {list(_ROLE_REGISTRY.keys())}"
        )

    return role_cls(player_id=player_id)


def get_registered_roles() -> Dict[Role, Type[BaseRole]]:
    """获取当前已注册的所有角色类型。

    **Why**: 提供只读访问，供上层模块（如 Game Engine 的初始化流程）
    查询当前支持哪些角色。

    Returns:
        角色注册表的浅拷贝。
    """
    return dict(_ROLE_REGISTRY)


# ------------------------------------------------------------------
# 公开 API
# ------------------------------------------------------------------

__all__ = [
    "BaseRole",
    "VillagerRole",
    "WerewolfRole",
    "SeerRole",
    "WitchRole",
    "HunterRole",
    "create_role",
    "get_registered_roles",
]
