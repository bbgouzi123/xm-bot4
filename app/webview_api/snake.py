from __future__ import annotations
import json

class SnakeMixin:
    def _ensure_snake_ghost(self):
        """确保 SnakeGhostManager 已初始化"""
        if not getattr(self, "_snake_ghost", None):
            from xm_py_server.snake_ghost import SnakeGhostManager
            self._snake_ghost = SnakeGhostManager()
            # 传递主窗口引用和自身(api)
            self._snake_ghost.set_main_window(self._window, "", self)
        return self._snake_ghost

    def _ensure_snake_float_mgr(self):
        """确保 SnakeFloatingWindowManager 已初始化"""
        if not getattr(self, "_snake_float_mgr", None):
            from xm_py_server.snake_floating_window import SnakeFloatingWindowManager
            self._snake_float_mgr = SnakeFloatingWindowManager(
                main_window=self._window,
            )
        else:
            # 确保主窗口引用最新
            self._snake_float_mgr.set_main_window(self._window)
        return self._snake_float_mgr

    def create_snake_ghost(self, state_json: str, screen_x: int, screen_y: int):
        """蛇完全越狱到桌面（兼容旧接口）"""
        ghost = self._ensure_snake_ghost()
        ghost.finish_escape(state_json)

    def move_snake_ghost(self, x: int, y: int, w: int, h: int):
        """兼容旧接口（V2 不需要手动移动窗口）"""
        pass

    def destroy_snake_ghost(self):
        """销毁桌面蛇"""
        if getattr(self, "_snake_ghost", None):
            self._snake_ghost.destroy_ghost()

    def get_main_window_rect(self):
        """获取主窗口屏幕坐标"""
        ghost = self._ensure_snake_ghost()
        return ghost.get_main_rect()

    def return_snake_to_main(self, state_json: str = ''):
        """蛇归巢"""
        ghost = self._ensure_snake_ghost()
        return ghost.return_snake_to_main(state_json or None)

    def start_snake_crossing(self, head_json: str, history_json: str, viewport_w=None, viewport_h=None):
        """开始穿越：蛇正在拖向窗口边缘，实时渲染超出部分（viewport_* 与 window.innerWidth/Height 一致）"""
        ghost = self._ensure_snake_ghost()
        ghost.start_crossing(head_json, history_json, viewport_w, viewport_h)

    def update_snake_crossing(self, head_json: str, history_json: str, viewport_w=None, viewport_h=None):
        """更新穿越中的蛇位置（每帧调用）"""
        if getattr(self, "_snake_ghost", None):
            self._snake_ghost.update_crossing(head_json, history_json, viewport_w, viewport_h)

    def cancel_snake_crossing(self):
        """取消穿越：蛇完全退回主窗体腹地，撤销跨越遮罩渲染状态"""
        if getattr(self, "_snake_ghost", None):
            self._snake_ghost.cancel_snake_crossing()

    def finish_snake_escape(self, state_json: str):
        """蛇完全脱出主窗口，overlay 完全接管"""
        ghost = self._ensure_snake_ghost()
        ghost.finish_escape(state_json)

    def get_desktop_icons(self):
        """获取桌面图标列表 [{name, x, y}, ...]"""
        ghost = self._ensure_snake_ghost()
        return ghost._icon_mgr.enumerate_icons()

    def eat_desktop_icon(self, name: str):
        """吃掉指定桌面图标"""
        ghost = self._ensure_snake_ghost()
        return ghost.eat_icon(name)

    def show_snake_bump(self, css_x, css_y, direction: str, size: int = 50, energy: int = 100, viewport_w=None, viewport_h=None):
        """越狱冲刺撞墙时在窗口外侧显示蛇头凸出效果"""
        ghost = self._ensure_snake_ghost()
        ghost.show_bump_effect(float(css_x), float(css_y), direction, int(size), int(energy),
                               float(viewport_w) if viewport_w else None,
                               float(viewport_h) if viewport_h else None)

    def show_snake_float_window(self, screen_x: int = 100, screen_y: int = 100):
        """在桌面指定位置显示悬浮蛇独立浮窗（越狱后调用）。"""
        mgr = self._ensure_snake_float_mgr()
        mgr.show_float_window(int(screen_x), int(screen_y))

    def hide_snake_float_window(self):
        """归巢：关闭桌面悬浮蛇独立浮窗。"""
        if getattr(self, "_snake_float_mgr", None):
            self._snake_float_mgr.hide_float_window()

    def restore_desktop_icons(self):
        """恢复所有被吃掉的桌面图标"""
        ghost = self._ensure_snake_ghost()
        ghost._icon_mgr.restore_all()
