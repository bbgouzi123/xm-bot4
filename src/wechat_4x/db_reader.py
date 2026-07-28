import os
import sqlite3
import time
import logging
from typing import Dict, List, Callable, Optional

logger = logging.getLogger("WeChat4xDbReader")

class WeChatDbReader:
    """
    Scheme A: SQLCipher WAL incremental polling reader for WeChat NT databases.
    Connects with PRAGMA query_only = ON and journal_mode = WAL to prevent database locks.
    """
    def __init__(self, db_path: str, key_hex: str, poll_interval: float = 0.5):
        self.db_path = db_path
        self.key_hex = key_hex.strip()
        self.poll_interval = poll_interval
        self.conn: Optional[sqlite3.Connection] = None
        self.is_running = False
        self.last_local_id = 0

    def connect(self) -> bool:
        """Establish connection and apply SQLCipher settings."""
        if not os.path.exists(self.db_path):
            logger.error(f"[DB Reader] Database file not found: {self.db_path}")
            return False

        try:
            # We open the connection. Depending on python build, sqlite3 needs to support SQLCipher.
            # We set query_only to 1 to ensure zero write/modify interference with WeChat
            self.conn = sqlite3.connect(self.db_path, uri=True)
            self.conn.row_factory = sqlite3.Row
            
            # Apply security and read-only configurations
            self.conn.execute("PRAGMA query_only = ON;")
            
            # Configure hex key for SQLCipher decryption
            key_raw = self.key_hex.lower()
            if key_raw.startswith("0x"):
                key_raw = key_raw[2:]
            
            self.conn.execute(f"PRAGMA key = \"x'{key_raw}'\";")
            
            # Setup decryption parameters matching WeChat NT cipher config
            self.conn.execute("PRAGMA cipher_page_size = 4096;")
            self.conn.execute("PRAGMA kdf_iter = 64000;")
            self.conn.execute("PRAGMA cipher_compatibility = 4;")
            
            # Open database under WAL mode sharing
            self.conn.execute("PRAGMA journal_mode = WAL;")
            
            # Fast integrity validation
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;")
            cursor.fetchone()
            
            logger.info("[DB Reader] Connection successfully established and decrypted.")
            return True
        except Exception as e:
            logger.error(f"[DB Reader] Failed to open/decrypt database: {e}")
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
            return False

    def get_max_local_id(self) -> int:
        """Retrieve the current max localId in the message table to use as starting cursor."""
        if not self.conn:
            return 0
        try:
            cursor = self.conn.cursor()
            # Try typical column structures for WeChat NT message tables
            cursor.execute("SELECT MAX(localId) as max_id FROM message;")
            row = cursor.fetchone()
            if row and row['max_id'] is not None:
                return int(row['max_id'])
        except Exception as e:
            logger.error(f"[DB Reader] Error getting max localId: {e}")
        return 0

    def poll_new_messages(self, callback: Callable[[Dict], None]):
        """Poll the database for new messages starting from last_local_id."""
        if not self.conn:
            if not self.connect():
                return

        self.last_local_id = self.get_max_local_id()
        logger.info(f"[DB Reader] Starting poll cursor at localId = {self.last_local_id}")
        self.is_running = True

        while self.is_running:
            try:
                cursor = self.conn.cursor()
                # Fetch new messages added after last cursor
                cursor.execute(
                    "SELECT localId, Type, CreateTime, StrTalker, StrContent, IsSender FROM message WHERE localId > ? ORDER BY localId ASC LIMIT 50;",
                    (self.last_local_id,)
                )
                rows = cursor.fetchall()
                
                for row in rows:
                    msg = {
                        "local_id": row["localId"],
                        "msg_type": row["Type"],
                        "timestamp": row["CreateTime"],
                        "sender_wxid": row["StrTalker"],
                        "content": row["StrContent"],
                        "is_self": bool(row["IsSender"]),
                        "is_group": "@chatroom" in row["StrTalker"]
                    }
                    self.last_local_id = max(self.last_local_id, msg["local_id"])
                    
                    # Fire callback
                    try:
                        callback(msg)
                    except Exception as cb_err:
                        logger.error(f"[DB Reader] Callback error: {cb_err}")

            except sqlite3.DatabaseError as db_err:
                logger.error(f"[DB Reader] Database error occurred during query: {db_err}")
                # Try reconnecting if connection fails
                self.connect()
            except Exception as e:
                logger.error(f"[DB Reader] Error polling messages: {e}")

            time.sleep(self.poll_interval)

    def stop(self):
        """Clean shutdown of the polling loop."""
        self.is_running = False
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        logger.info("[DB Reader] Reader loop stopped.")
