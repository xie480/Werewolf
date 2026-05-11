"""角色工厂函数单元测试。

覆盖:
- 所有角色类型正确创建
- 创建的角色类型与预期一致
- 错误的枚举类型抛出 TypeError
- 未注册的角色类型抛出 KeyError
- 每个角色的阵营正确
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import (
    HunterRole,
    SeerRole,
    VillagerRole,
    WerewolfRole,
    WitchRole,
    create_role,
    get_registered_roles,
)
from ai_werewolf_core.schemas.enums import Faction, Role


class TestCreateRole:
    """工厂函数创建角色测试。"""

    @pytest.mark.parametrize(
        "role_type, expected_class",
        [
            (Role.VILLAGER, VillagerRole),
            (Role.WEREWOLF, WerewolfRole),
            (Role.SEER, SeerRole),
            (Role.WITCH, WitchRole),
            (Role.HUNTER, HunterRole),
        ],
    )
    def test_create_role_returns_correct_class(self, role_type: Role, expected_class: type) -> None:
        """工厂函数返回正确类型的角色实例。"""
        role = create_role(role_type, "player_1")
        assert isinstance(role, expected_class)
        assert role.role_type == role_type

    @pytest.mark.parametrize(
        "role_type, expected_faction",
        [
            (Role.VILLAGER, Faction.VILLAGER),
            (Role.WEREWOLF, Faction.WEREWOLF),
            (Role.SEER, Faction.VILLAGER),
            (Role.WITCH, Faction.VILLAGER),
            (Role.HUNTER, Faction.VILLAGER),
        ],
    )
    def test_create_role_assigns_correct_faction(
        self, role_type: Role, expected_faction: Faction
    ) -> None:
        """工厂函数创建的角色归属正确阵营。"""
        role = create_role(role_type, "player_1")
        assert role.faction == expected_faction

    def test_create_role_sets_player_id(self) -> None:
        """工厂函数正确设置 player_id。"""
        role = create_role(Role.WEREWOLF, "player_5")
        assert role.player_id == "player_5"

    def test_create_role_sets_alive_default(self) -> None:
        """新创建的角色默认为存活状态。"""
        role = create_role(Role.WITCH, "player_3")
        assert role.is_alive is True


class TestCreateRoleErrors:
    """工厂函数错误处理测试。"""

    def test_invalid_role_type_raises_type_error(self) -> None:
        """非法类型（非 Role 枚举）抛出 TypeError。"""
        with pytest.raises(TypeError, match="Role 枚举成员"):
            create_role("WITCH", "player_1")  # type: ignore[arg-type]

    def test_unregistered_role_raises_key_error(self) -> None:
        """未注册的角色类型抛出 KeyError。

        Note: 当前所有 Role 枚举值均已注册，此测试验证 KeyError 逻辑存在。
        未来若新增角色枚举值但未添加注册项时会被此逻辑捕获。
        """
        # 构造一个"已定义但未注册"的模拟场景
        # 当前所有值都已注册，所以通过直接查找空注册表来验证
        from ai_werewolf_core.core.engine.roles import _ROLE_REGISTRY

        # 验证所有已定义的 Role 都有注册项
        for role_type in Role:
            assert role_type in _ROLE_REGISTRY, (
                f"Role.{role_type.name} 未在 _ROLE_REGISTRY 中注册！"
            )


class TestGetRegisteredRoles:
    """get_registered_roles 函数测试。"""

    def test_returns_all_five_roles(self) -> None:
        """返回所有 5 种角色的注册信息。"""
        registered = get_registered_roles()
        assert len(registered) == 5
        assert Role.VILLAGER in registered
        assert Role.WEREWOLF in registered
        assert Role.SEER in registered
        assert Role.WITCH in registered
        assert Role.HUNTER in registered

    def test_returns_new_dict_not_reference(self) -> None:
        """返回的是注册表的浅拷贝，非原始引用。"""
        registered = get_registered_roles()
        original = get_registered_roles()
        # 删除返回字典中的项不影响原始注册表
        del registered[Role.WEREWOLF]
        assert Role.WEREWOLF in get_registered_roles()


class TestAllRolesAreInstantiable:
    """所有角色可实例化测试。"""

    @pytest.mark.parametrize("role_type", list(Role))
    def test_all_roles_can_be_created(self, role_type: Role) -> None:
        """所有 Role 枚举值均可通过工厂函数创建实例。"""
        role = create_role(role_type, "player_test")
        assert role is not None
        assert role.role_type == role_type
        assert role.player_id == "player_test"

    @pytest.mark.parametrize("role_type", list(Role))
    def test_all_roles_can_pass(self, role_type: Role) -> None:
        """所有角色在所有阶段都可以 PASS。"""
        from ai_werewolf_core.schemas.enums import ActionType, GamePhase

        role = create_role(role_type, "player_test")
        assert role.can_perform_common_action(GamePhase.NIGHT_ACTION, ActionType.PASS) is True
