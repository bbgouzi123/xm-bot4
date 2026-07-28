import os
import sys
import unittest
import asyncio
import shutil
from pathlib import Path

# 将 src 目录加入 PATH
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.utils.db_manager import WeChatDBManager
from src.crm.account_data import set_active_account, get_active_account, APP_DATA_DIR

class TestDBConcurrencyAndIsolation(unittest.TestCase):
    def setUp(self):
        # 备份并清理临时测试账号数据目录
        self.test_dir = Path(APP_DATA_DIR)
        self.backup_dir = Path(APP_DATA_DIR).with_name(".xm-ai-bot.backup")
        
        if self.test_dir.exists():
            if self.backup_dir.exists():
                shutil.rmtree(self.backup_dir, ignore_errors=True)
            shutil.move(str(self.test_dir), str(self.backup_dir))
        
        # 强制重新初始化单例
        WeChatDBManager._instance = None
        self.db = WeChatDBManager()

    def tearDown(self):
        # 还原备份数据
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)
        if self.backup_dir.exists():
            shutil.move(str(self.backup_dir), str(self.test_dir))
        
        WeChatDBManager._instance = None

    def test_account_isolation(self):
        """测试多账号切换时数据目录完全隔离且不会互相渗透"""
        # 1. 切换到账号 A 并添加专属联系人/跟单任务
        set_active_account("wxid_account_a", "Account A")
        self.assertEqual(get_active_account(), "wxid_account_a")
        
        # 准备好友数据并保存
        friend_a = {"wxid": "friend_a_id", "nickname": "Friend A", "remark": "Remark A"}
        self.db._friend_queue.append(friend_a)
        self.db._persist_snapshot()
        
        # 2. 切换到账号 B 验证账号 A 的数据不渗透
        set_active_account("wxid_account_b", "Account B")
        self.assertEqual(get_active_account(), "wxid_account_b")
        
        # 账号 B 此时的队列应该不包含账号 A 的内容
        self.assertNotIn(friend_a, self.db._friend_queue)
        
        # 添加账号 B 专属数据并保存
        friend_b = {"wxid": "friend_b_id", "nickname": "Friend B", "remark": "Remark B"}
        self.db._friend_queue.append(friend_b)
        self.db._persist_snapshot()
        
        # 3. 再次切回账号 A，验证其原有数据恢复，且无账号 B 的数据
        set_active_account("wxid_account_a", "Account A")
        self.assertEqual(get_active_account(), "wxid_account_a")
        
        # 账号 A 应该只包含 Friend A
        wxids_in_a = [f.get("wxid") for f in self.db._friend_queue]
        self.assertIn("friend_a_id", wxids_in_a)
        self.assertNotIn("friend_b_id", wxids_in_a)

    def test_concurrent_switches_and_writes(self):
        """测试在多协程/多线程并发环境频繁读写与切换，无文件锁定冲突与损坏"""
        async def run_frequent_writes(wxid, payload_val):
            for i in range(20):
                set_active_account(wxid, f"Nick_{wxid}")
                # 写入动态数据
                item = {"wxid": f"id_{wxid}_{i}", "nickname": f"Nick_{payload_val}", "remark": f"Val_{i}"}
                self.db._friend_queue.append(item)
                self.db._persist_snapshot()
                await asyncio.sleep(0.01)

        # 启动三个并发协程在不同账号之间交替读写
        async def main():
            await asyncio.gather(
                run_frequent_writes("concurrent_user_x", "X"),
                run_frequent_writes("concurrent_user_y", "Y"),
                run_frequent_writes("concurrent_user_z", "Z")
            )

        asyncio.run(main())

        # 最终验证各个账号文件夹下的快照文件是否正常，且能正确反序列化
        for user_key in ["concurrent_user_x", "concurrent_user_y", "concurrent_user_z"]:
            set_active_account(user_key, f"Nick_{user_key}")
            self.assertGreater(len(self.db._friend_queue), 0)
            for friend in self.db._friend_queue:
                self.assertTrue(friend["wxid"].startswith(f"id_{user_key}_"))

if __name__ == "__main__":
    unittest.main()
