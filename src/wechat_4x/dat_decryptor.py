import os
import re
import logging
import struct
import glob
from typing import Optional

logger = logging.getLogger("WeChat4xDatDecryptor")


def _find_dat_file_in_account(account_dir: str, img_md5: str) -> Optional[str]:
    """
    在账号目录下按优先级定位含有指定 MD5 的 .dat 图片文件。
    优先顺序：
      1. msg/attach 快速前缀探测（微信4.x 新路径，MD5前2字节分片）
      2. msg/attach 全量 glob 搜索（微信4.x 回退）
      3. FileStorage 全量 walk（微信旧版路径兜底）
    """
    # ── 路径1：msg/attach 快速定位 ────────────────────────────────
    attach_dir = os.path.join(account_dir, "msg", "attach")
    if os.path.isdir(attach_dir):
        # 快速探测：MD5 前两字节决定目录分片（微信4.x 分片规律）
        prefix = img_md5[:2]
        pattern = os.path.join(attach_dir, "*", "*", "Img", f"*{img_md5}*.dat")
        matches = glob.glob(pattern)
        if matches:
            # 优先非缩略图（不含 _t 的高清图）
            hd = [m for m in matches if not os.path.basename(m).endswith("_t.dat")]
            return (hd or matches)[0]

        # 回退：在 attach 下按目录前缀 MD5 全量搜索
        for root, _, files in os.walk(attach_dir):
            for f in files:
                if img_md5 in f.lower() and f.endswith(".dat"):
                    return os.path.join(root, f)

    # ── 路径2：FileStorage（旧版微信路径兜底）───────────────────────
    file_storage_dir = os.path.join(account_dir, "FileStorage")
    if os.path.isdir(file_storage_dir):
        for root, _, files in os.walk(file_storage_dir):
            for f in files:
                if img_md5 in f.lower() and f.endswith(".dat"):
                    return os.path.join(root, f)

    return None

def try_decrypt_wechat_dat(content_xml: str, db_path: str | None, wxid: str | None = None) -> str | None:
    """
    尝试解析图片消息 XML 中的 md5，并在本地微信 FileStorage 目录下搜寻对应的 .dat 加密文件，
    将其解密并保存到 API 静态资源目录下。
    """
    if not content_xml or not db_path:
        return None
    md5_match = re.search(r'md5="([a-fA-F0-9]{32})"', content_xml)
    if not md5_match:
        return None
    img_md5 = md5_match.group(1).lower()
    
    try:
        # 推算账号目录（db_path 形如 .../wxid_xxx/db_storage/xxx.db）
        db_dir = os.path.dirname(db_path)
        account_dir = os.path.dirname(os.path.dirname(db_dir))
        if not os.path.exists(account_dir):
            return None

        # 在新旧两套路径结构下智能定位 .dat 文件
        dat_path = _find_dat_file_in_account(account_dir, img_md5)
        if not dat_path:
            logger.info(f"[DAT解密] 未找到 md5={img_md5} 对应的 .dat 文件 (account={account_dir})")
            return None
            
        # 目标输出路径
        from src.api.file_api import UPLOAD_DIR
        out_name = f"chat_img_{img_md5}.png"
        out_path = os.path.join(UPLOAD_DIR, out_name)
        
        if os.path.exists(out_path):
            return f"/api/file/download/{out_name}"
            
        # 自动获取图片解密密钥（DLL → kvcomm扫描 → 内存扫描三级降级）
        try:
            from src.wechat_4x.image_key_extractor import get_image_keys
            aes_key_str, xor_key_val = get_image_keys(db_path=db_path, wxid=wxid)
        except Exception as _ike:
            logger.debug(f"[try_decrypt_wechat_dat] image_key_extractor 调用异常: {_ike}")
            aes_key_str, xor_key_val = None, None

        aes_key = None
        if aes_key_str:
            if isinstance(aes_key_str, str):
                aes_key = aes_key_str.encode('ascii')[:16]
            else:
                aes_key = aes_key_str[:16]
                
        xor_key = 0x88
        if xor_key_val:
            try:
                if isinstance(xor_key_val, str):
                    xor_key = int(xor_key_val, 0)
                else:
                    xor_key = int(xor_key_val)
            except Exception:
                xor_key = 0x88

        # 读取前6字节做签名判断
        with open(dat_path, "rb") as f:
            head = f.read(6)
            
        V2_MAGIC_FULL = b'\x07\x08V2\x08\x07'
        V1_MAGIC_FULL = b'\x07\x08V1\x08\x07'
        
        is_v1_or_v2 = (head == V2_MAGIC_FULL or head == V1_MAGIC_FULL)
        
        if is_v1_or_v2:
            if head == V1_MAGIC_FULL:
                # V1 格式使用固定 Key
                aes_key = b'cfcd208495d565ef'
            
            if not aes_key:
                logger.warning("[try_decrypt_wechat_dat] 检测到新版 V2 格式图片，但未配置 image_aes_key 密钥，跳过解密")
                return None
                
            with open(dat_path, "rb") as f:
                data = f.read()
                
            if len(data) < 15:
                return None
                
            aes_size, xor_size = struct.unpack_from('<LL', data, 6)
            
            # AES 对齐尺寸计算 (PKCS7 填充)
            aligned_aes_size = aes_size
            if aligned_aes_size % 16 != 0:
                aligned_aes_size += (16 - aligned_aes_size % 16)
            else:
                aligned_aes_size += 16
                
            offset = 15
            if offset + aligned_aes_size > len(data):
                return None
                
            # AES-128-ECB 解密
            aes_data = data[offset:offset + aligned_aes_size]
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                from cryptography.hazmat.backends import default_backend
                
                cipher = Cipher(algorithms.AES(aes_key[:16]), modes.ECB(), backend=default_backend())
                decryptor = cipher.decryptor()
                dec_aes_padded = decryptor.update(aes_data) + decryptor.finalize()
                
                # 手动反填充 PKCS7
                padding_len = dec_aes_padded[-1]
                if padding_len < 1 or padding_len > 16:
                    raise ValueError("Invalid padding length")
                for i in range(1, padding_len + 1):
                    if dec_aes_padded[-i] != padding_len:
                        raise ValueError("Invalid padding byte")
                dec_aes = dec_aes_padded[:-padding_len]
            except Exception as aes_err:
                logger.error(f"[try_decrypt_wechat_dat] V2 图片 AES 解密失败: {aes_err}")
                return None
                
            offset += aligned_aes_size
            
            # 未加密的 Raw 部分
            raw_end = len(data) - xor_size
            raw_data = data[offset:raw_end] if offset < raw_end else b''
            offset = raw_end
            
            # XOR 加密尾段
            xor_data = data[offset:]
            dec_xor = bytes(b ^ xor_key for b in xor_data)
            
            decrypted = dec_aes + raw_data + dec_xor
            
            with open(out_path, "wb") as f:
                f.write(decrypted)
                
            return f"/api/file/download/{out_name}"
            
        else:
            # 旧单字节 XOR 格式
            with open(dat_path, "rb") as f:
                data = f.read()
            if len(data) < 2:
                return None
                
            cipher = data[:2]
            xor_key_val_derived = None
            for plain in [(0xFF, 0xD8), (0x89, 0x50), (0x47, 0x49)]:
                k1 = cipher[0] ^ plain[0]
                k2 = cipher[1] ^ plain[1]
                if k1 == k2:
                    xor_key_val_derived = k1
                    break
            if xor_key_val_derived is None:
                xor_key_val_derived = cipher[0] ^ 0xFF
                
            decrypted = bytearray(b ^ xor_key_val_derived for b in data)
            with open(out_path, "wb") as f:
                f.write(decrypted)
                
            return f"/api/file/download/{out_name}"
    except Exception as e:
        logger.error(f"[try_decrypt_wechat_dat] 解密失败: {e}")
        return None
