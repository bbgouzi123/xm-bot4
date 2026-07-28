from __future__ import annotations

class RemoteMixin:
    def remote_desktop_inject(self, ev_json: str):
        """
        接收从前端传来的 WebRTC 键鼠事件，并使用 ctypes 调用 Windows 原生 API 模拟操作。
        （无需额外依赖库，完美适配大厂规范底层 API 调用）。
        """
        import json
        import ctypes
        try:
            ev = json.loads(ev_json)
            ev_type = ev.get('type')
            if not ev_type: return

            # MOUSEEVENTF 常量
            MOUSEEVENTF_MOVE = 0x0001
            MOUSEEVENTF_LEFTDOWN = 0x0002
            MOUSEEVENTF_LEFTUP = 0x0004
            MOUSEEVENTF_RIGHTDOWN = 0x0008
            MOUSEEVENTF_RIGHTUP = 0x0010
            MOUSEEVENTF_WHEEL = 0x0800
            MOUSEEVENTF_ABSOLUTE = 0x8000

            user32 = ctypes.windll.user32
            
            # 如果是鼠标事件，计算绝对坐标（Windows 0~65535 表示全屏范围）
            if ev_type in ('mousemove', 'mousedown', 'mouseup', 'click'):
                screen_w = user32.GetSystemMetrics(0)
                screen_h = user32.GetSystemMetrics(1)
                
                # 优先使用比例坐标 (rx, ry)
                if 'rx' in ev and 'ry' in ev:
                    abs_x = int(ev['rx'] * 65535)
                    abs_y = int(ev['ry'] * 65535)
                elif 'x' in ev and 'y' in ev:
                    abs_x = int(ev['x'] * 65535 / screen_w)
                    abs_y = int(ev['y'] * 65535 / screen_h)
                else:
                    return

                if ev_type == 'mousemove':
                    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y, 0, 0)
                elif ev_type == 'mousedown':
                    user32.mouse_event(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE, abs_x, abs_y, 0, 0)
                    if ev.get('button') == 2:
                        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                    else:
                        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                elif ev_type == 'mouseup':
                    user32.mouse_event(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE, abs_x, abs_y, 0, 0)
                    if ev.get('button') == 2:
                        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                    else:
                        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                elif ev_type == 'click':
                    user32.mouse_event(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE, abs_x, abs_y, 0, 0)
                    if ev.get('button') == 2:
                        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN | MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
                    else:
                        user32.mouse_event(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                        
            elif ev_type == 'scroll':
                deltaY = ev.get('deltaY', 0)
                if deltaY:
                    # Windows 系统向上滚轮滚动为正值，Web 中向下滚动 deltaY 是正数，因此需要取反
                    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, -int(deltaY), 0)
                    
            elif ev_type in ('keydown', 'keyup'):
                # 使用 Windows 原生 keybd_event 模拟按键，避开 keyboard 模块的底层钩子干扰
                key = ev.get('key')
                if key:
                    try:
                        key_lower = key.lower()
                        vk_map = {
                            'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D,
                            'shift': 0x10, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12,
                            'pause': 0x13, 'capslock': 0x14, 'esc': 0x1B, 'escape': 0x1B,
                            'space': 0x20, 'pageup': 0x21, 'pagedown': 0x22, 'end': 0x23,
                            'home': 0x24, 'left': 0x25, 'up': 0x26, 'right': 0x27,
                            'down': 0x28, 'delete': 0x2E,
                            'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
                            'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79,
                            'f11': 0x7A, 'f12': 0x7B,
                        }
                        vk = vk_map.get(key_lower)
                        if not vk and len(key) == 1:
                            vk = user32.VkKeyScanW(ord(key)) & 0xFF

                        if vk and vk > 0:
                            KEYEVENTF_KEYUP = 0x0002
                            if ev_type == 'keydown':
                                user32.keybd_event(vk, 0, 0, 0)
                            else:
                                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                    except Exception:
                        pass
        except Exception:
            pass
