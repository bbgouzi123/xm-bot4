import ctypes
from ctypes import HRESULT, POINTER, c_bool, c_void_p, c_ulong, c_float
import comtypes
from comtypes import GUID, COMMETHOD, IUnknown
from comtypes.client import CreateObject

# 1. Define Core Audio IMMDevice COM interfaces
class IMMDevice(IUnknown):
    _iid_ = GUID("{D66606E7-27D6-4E68-8F4F-38B504784C59}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate",
                  (['in'], POINTER(GUID), "refiid"),
                  (['in'], c_ulong, "dwClsCtx"),
                  (['in'], c_void_p, "pActivationParams"),
                  (['out'], POINTER(POINTER(IUnknown)), "ppInterface")),
    ]

class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (['in'], c_ulong, "dataFlow"),
                  (['in'], c_ulong, "dwStateMask"),
                  (['out'], POINTER(c_void_p), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (['in'], c_ulong, "dataFlow"),
                  (['in'], c_ulong, "role"),
                  (['out'], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]

class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (['in'], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (['in'], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "GetChannelCount", (['out'], POINTER(c_ulong), "pnChannelCount")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (['in'], c_float, "fLevelDB"), (['in'], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (['in'], c_float, "fLevel"), (['in'], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (['out'], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (['out'], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (['in'], c_ulong, "nChannel"), (['in'], c_float, "fLevelDB"), (['in'], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (['in'], c_ulong, "nChannel"), (['in'], c_float, "fLevel"), (['in'], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (['in'], c_ulong, "nChannel"), (['out'], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (['in'], c_ulong, "nChannel"), (['out'], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetMute", (['in'], c_bool, "bMute"), (['in'], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMute", (['out'], POINTER(c_bool), "pbMute")),
    ]

import logging
logger = logging.getLogger("VolumeHelper")

def get_system_mute_state() -> bool:
    """获取 Windows 系统主音量静音状态 (若任意主要输出设备已静音，则视作已静音)"""
    try:
        try: comtypes.CoInitialize()
        except: pass
        
        enumerator = CreateObject(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
            interface=IMMDeviceEnumerator
        )
        # 遍历三个核心输出角色 (0: eConsole, 1: eMultimedia, 2: eCommunications)
        for role in [0, 1, 2]:
            try:
                device = enumerator.GetDefaultAudioEndpoint(0, role)
                volume_unk = device.Activate(IAudioEndpointVolume._iid_, 23, None)
                volume = volume_unk.QueryInterface(IAudioEndpointVolume)
                if bool(volume.GetMute()):
                    return True
            except Exception as e_role:
                logger.debug(f"获取 role {role} 静音状态失败: {e_role}")
    except Exception as e:
        logger.error(f"获取系统静音状态抛出异常: {e}", exc_info=True)
    return False

def set_system_mute_state(mute: bool) -> bool:
    """设置 Windows 系统所有核心音频输出角色的静音状态"""
    success = False
    try:
        try: comtypes.CoInitialize()
        except: pass
        
        enumerator = CreateObject(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
            interface=IMMDeviceEnumerator
        )
        
        # 遍历设置所有主要输出通道 (0: eConsole, 1: eMultimedia, 2: eCommunications)
        for role in [0, 1, 2]:
            try:
                device = enumerator.GetDefaultAudioEndpoint(0, role)
                volume_unk = device.Activate(IAudioEndpointVolume._iid_, 23, None)
                volume = volume_unk.QueryInterface(IAudioEndpointVolume)
                volume.SetMute(mute, None)
                success = True
                logger.info(f"设置音频通道 role {role} 静音={mute} 成功")
            except Exception as e_role:
                logger.warning(f"设置音频通道 role {role} 静音={mute} 失败: {e_role}")
    except Exception as e:
        logger.error(f"调用系统静音 API 抛出异常: {e}", exc_info=True)
    return success

def is_wechat_uia_active() -> bool:
    """
    通过底层 COM 接口检测微信窗口的 UI Automation 节点树是否已激活构建。
    """
    try:
        import win32gui
        wechat_hwnds = []
        def enum_callback(hwnd, _):
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls.endswith("WeChatMainWndForPC") or cls.endswith("Qt51514QWindowIcon"):
                    wechat_hwnds.append(hwnd)
            except Exception:
                pass
            return True
        win32gui.EnumWindows(enum_callback, None)
        
        if not wechat_hwnds:
            # 微信未运行，无需执行激活检测，视作就绪以防误拉讲述人
            return True
            
        try: comtypes.CoInitialize()
        except: pass
            
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        uia_obj = comtypes.CoCreateInstance(
            comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"),
            interface=mod.IUIAutomation
        )
        
        for hwnd in wechat_hwnds:
            try:
                elem = uia_obj.ElementFromHandle(ctypes.c_int(hwnd))
                if elem:
                    walker = uia_obj.RawViewWalker
                    child = walker.GetFirstChildElement(elem)
                    if child:
                        return True
            except:
                pass
        return False
    except:
        return True
