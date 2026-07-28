import os
import logging
import time

import uiautomation as uia

from src.uia.retry import try_click, random_delay

logger = logging.getLogger("WeChatDriver")


class WeChatVoiceMixin:
    def send_voice_by_favorite(self, who: str, favorite_name: str, wxid: str = None) -> bool:
        """
        通过微信收藏夹转发真实语音气泡（安全、零封号风险）
        :param who: 接收好友的昵称/微信号/备注
        :param favorite_name: 收藏项在收藏夹里的备注名或名称
        :param wxid: 接收好友的唯一微信 ID，防止重名好友导致转发错人
        """
        if not self.is_connected():
            return False

        # 🛡️ 智能自愈：如果 who 是 wxid，反查其真实姓名
        if who and (who.startswith("wxid_") or "@chatroom" in who):
            if not wxid:
                wxid = who
            try:
                from src.utils.contacts_cache import contacts_cache
                _bot_wxid = getattr(self, "bot_wxid", None) or getattr(self, "_wxid", None) or "main"
                is_group = "@chatroom" in who
                resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, who, is_group=is_group)
                if resolved_name:
                    who = resolved_name
            except Exception as e:
                logger.debug(f"[语音发送] 自动反查微信号昵称异常: {e}")

        try:
            from src.uia.input_guard import uia_lock
            with self._lock, uia_lock(f"通过收藏转发语音给 {who}"):
                # 1. 切换到"收藏"侧边栏
                main_win = uia.WindowControl(ClassName='mmui::MainWindow')
                sidebar = main_win.ButtonControl(Name='收藏', ClassName='mmui::XTabBarItem')
                if not sidebar.Exists(1.0):
                    sidebar = main_win.ButtonControl(Name='收藏')
                if not sidebar.Exists(1.0):
                    logger.error("未找到微信侧边栏'收藏'按钮")
                    return False
                try_click(sidebar, max_retries=2, delay=0.2)
                random_delay(0.5, 0.8)

                # 2. 在收藏搜索框中搜索该语音
                search_box = main_win.EditControl(Name='搜索', ClassName='mmui::XValidatorTextEdit')
                if search_box.Exists(0.5):
                    try_click(search_box, max_retries=2, delay=0.2)
                    search_box.SendKeys('{Ctrl}a{Del}')
                    search_box.SendKeys(favorite_name)
                    random_delay(0.5, 0.8)

                # 3. 定位收藏列表中的对应条目
                fav_list = main_win.ListControl(ClassName='mmui::StickyHeaderRecyclerListView') or main_win.ListControl()
                target_item = None
                for item in fav_list.GetChildren():
                    if favorite_name in (item.Name or ""):
                        target_item = item
                        break

                if not target_item:
                    logger.error(f"收藏夹中未找到名称包含 {favorite_name} 的语音")
                    chats_btn = main_win.ButtonControl(Name='微信', ClassName='mmui::XTabBarItem') or main_win.ButtonControl(Name='微信')
                    try_click(chats_btn, max_retries=1)
                    return False

                # 4. 右键点击目标语音条目，弹出右键菜单
                rect = target_item.BoundingRectangle
                uia.RightClick((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                random_delay(0.3, 0.5)

                # 5. 点击"转发..."
                menu = uia.MenuControl(ClassName='CMenuWnd')
                forward_btn = menu.MenuItemControl(Name='转发...')
                if not forward_btn.Exists(0.5):
                    logger.error("未找到右键菜单中的'转发...'选项")
                    uia.SendKeys('{Esc}')
                    chats_btn = main_win.ButtonControl(Name='微信', ClassName='mmui::XTabBarItem') or main_win.ButtonControl(Name='微信')
                    try_click(chats_btn, max_retries=1)
                    return False
                try_click(forward_btn, max_retries=2, delay=0.2)
                random_delay(0.5, 0.8)

                # 6. 在弹出的"发送给"多选窗口中搜索并选中目标好友
                picker_win = uia.WindowControl(ClassName='mmui::SessionPickerWindow')
                if not picker_win.Exists(1.5):
                    logger.error("未成功弹出好友选择框(mmui::SessionPickerWindow)")
                    return False

                # 定位搜索框
                picker_search = picker_win.EditControl(Name='搜索', ClassName='mmui::XValidatorTextEdit')
                try_click(picker_search, max_retries=2, delay=0.2)
                search_key = wxid if wxid else who
                picker_search.SendKeys(search_key)
                random_delay(0.8, 1.2)

                # 物理按 Down + Enter 选中第一个搜索结果
                uia.SendKeys('{Down}{Enter}')
                random_delay(0.3, 0.5)

                # 点击"确定"或"发送"按钮
                confirm_btn = picker_win.ButtonControl(Name='确定') or picker_win.ButtonControl(Name='发送')
                if confirm_btn.Exists(0.5):
                    try_click(confirm_btn, max_retries=2, delay=0.2)
                else:
                    uia.SendKeys('{Enter}')

                random_delay(0.5, 0.8)

                # 7. 切换回"聊天"侧边栏
                chats_btn = main_win.ButtonControl(Name='微信', ClassName='mmui::XTabBarItem') or main_win.ButtonControl(Name='微信')
                try_click(chats_btn, max_retries=2, delay=0.2)
                random_delay(0.3, 0.5)

                logger.info(f"成功将收藏语音 '{favorite_name}' 转发给 {who}")
                return True

        except Exception as e:
            logger.error(f"收藏夹转发语音异常: {e}")
            try:
                main_win = uia.WindowControl(ClassName='mmui::MainWindow')
                chats_btn = main_win.ButtonControl(Name='微信', ClassName='mmui::XTabBarItem') or main_win.ButtonControl(Name='微信')
                try_click(chats_btn, max_retries=1)
            except Exception:
                pass
            return False

    def send_voice_by_tts_clone(self, who: str, text: str, voice_id: str, wxid: str = None) -> bool:
        """
        克隆音色实时合成内录物理发送
        :param who: 接收人昵称/微信号/备注
        :param text: 待朗读合成的文本内容
        :param voice_id: 选定音色的ID (如 S_xiaomei)
        :param wxid: 接收好友的唯一微信 ID，防止重名好友导致切换错窗口
        """
        if not self.is_connected():
            return False

        # 🛡️ 智能自愈：如果 who 是 wxid，反查其真实姓名
        if who and (who.startswith("wxid_") or "@chatroom" in who):
            if not wxid:
                wxid = who
            try:
                from src.utils.contacts_cache import contacts_cache
                _bot_wxid = getattr(self, "bot_wxid", None) or getattr(self, "_wxid", None) or "main"
                is_group = "@chatroom" in who
                resolved_name = contacts_cache.find_name_with_db_sync(_bot_wxid, who, is_group=is_group)
                if resolved_name:
                    who = resolved_name
            except Exception as e:
                logger.debug(f"[TTS语音发送] 自动反查微信号昵称异常: {e}")

        try:
            from src.uia.input_guard import uia_lock
            with self._lock, uia_lock(f"通过克隆音色 {voice_id} 实时内录发送给 {who}"):
                # 1. 物理定位并切换到与 who 的聊天窗口
                if not self.ChatWith(who, wxid=wxid):
                    logger.error(f"未找到与 {who} 的聊天窗口")
                    return False

                random_delay(0.5, 0.8)
                input_box = self._get_edit_control(who)
                if not input_box or not input_box.Exists(0.5):
                    logger.error("未找到输入框")
                    return False

                # 2. 后台合成音频
                from src.utils.tts_generator import generate_tts_audio
                wav_path = generate_tts_audio(text, voice_id)
                if not os.path.exists(wav_path):
                    logger.error("语音合成失败，音频文件未生成")
                    return False

                # 3. 聚焦输入框
                try_click(input_box, max_retries=2, delay=0.2)
                random_delay(0.5, 0.8)

                # 4. 按住 right-Alt 热键开始微信录音 (VK_RMENU = 0xA5)
                logger.info("模拟按住录音热键 right-Alt")
                uia.PressKey(0xA5)
                random_delay(0.8, 1.2)  # 留出微信响应录音的缓冲时间

                # 5. 后台开始播放生成的 TTS 音频
                logger.info(f"开始向音频输出端播放 TTS: '{text}'")
                play_success = False

                try:
                    import sounddevice as sd
                    import soundfile as sf
                    data, fs = sf.read(wav_path)
                    devices = sd.query_devices()
                    device_idx = None
                    for idx, dev in enumerate(devices):
                        if any(k in dev['name'] for k in ["CABLE Input", "VB-Audio", "Virtual"]):
                            if dev['max_output_channels'] > 0:
                                device_idx = idx
                                break
                    if device_idx is not None:
                        logger.info(f"使用虚拟通道输出: {devices[device_idx]['name']}")
                        sd.play(data, fs, device=device_idx)
                    else:
                        logger.warning("未找到虚拟音频线，将直接使用默认设备输出")
                        sd.play(data, fs)
                    sd.wait()
                    play_success = True
                except Exception as e:
                    logger.warning(f"使用 sounddevice 播放出错，将使用 winsound 兜底播放: {e}")
                    try:
                        import winsound
                        # 估算音频大约时长以实现异步可控播放，防止 winsound 同步调用导致线程及 Windows 消息循环死锁
                        play_duration = 5.0
                        try:
                            file_size = os.path.getsize(wav_path)
                            # 16kHz 16bit 单声道 WAV 每秒约 32000 字节，加上 WAV 头
                            play_duration = max(1.0, (file_size - 44) / 32000.0)
                        except Exception:
                            pass
                        
                        logger.info(f"[Winsound] 开始异步播放语音，预计时长: {play_duration:.2f}s")
                        winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                        time.sleep(play_duration)
                        winsound.PlaySound(None, winsound.SND_PURGE)  # 停止并释放设备
                        play_success = True
                    except Exception as ex:
                        logger.error(f"语音播放彻底失败: {ex}")

                random_delay(0.8, 1.2)  # 额外内录尾音延迟防止截断

                # 6. 释放 right-Alt 热键结束录音并发送
                logger.info("模拟释放录音热键 right-Alt 并发送")
                uia.ReleaseKey(0xA5)

                # 留出微信语音条打包上传的物理反应缓冲
                random_delay(1.5, 2.0)

                try:
                    os.remove(wav_path)
                except Exception:
                    pass

                return play_success

        except Exception as e:
            logger.error(f"语音实时内录发送异常: {e}")
            return False
