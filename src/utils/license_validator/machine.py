"""
机器码与设备指纹模块
"""
import hashlib
import platform
import logging

logger = logging.getLogger(__name__)

class MachineMixin:
    @staticmethod
    def get_machine_code() -> str:
        """
        生成设备唯一标识码（机器码）
        算法与 xm-user 后端保持一致：SHA256(node-processor-machine) 取前16位
        格式: XXXX-XXXX-XXXX-XXXX
        """
        try:
            uname = platform.uname()
            info = f"{uname.node}-{uname.processor}-{uname.machine}"
            hash_value = hashlib.sha256(info.encode()).hexdigest()[:16].upper()
            # 格式化为 XXXX-XXXX-XXXX-XXXX
            machine_code = "-".join([hash_value[i:i+4] for i in range(0, 16, 4)])
            return machine_code
        except Exception as e:
            logger.error(f"生成机器码失败: {e}")
            return "0000-0000-0000-0000"

    @staticmethod
    def get_device_fingerprint() -> str:
        """兼容旧接口：返回机器码"""
        return MachineMixin.get_machine_code()
