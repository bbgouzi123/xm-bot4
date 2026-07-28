import asyncio
import json
import logging
import threading
from typing import Callable, Dict, Optional
import websockets

logger = logging.getLogger("WeChat4xWsListener")

class WeChatWsListener:
    """
    Scheme B: WebSocket receiver server.
    Listens for real-time messages forwarded by the Frida message_hook script.
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 9001):
        self.host = host
        self.port = port
        self.callback: Optional[Callable[[Dict], None]] = None
        self.server = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None

    def start(self, callback: Callable[[Dict], None]):
        """Start the WebSocket listener server in a separate background daemon thread."""
        self.callback = callback
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info(f"[WS Listener] Server starting thread on {self.host}:{self.port}")

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start_server())
        self.loop.run_forever()

    async def _start_server(self):
        self.server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=10,
            ping_timeout=10
        )
        logger.info(f"[WS Listener] Server successfully bound and listening on ws://{self.host}:{self.port}")

    async def _handler(self, websocket, path):
        logger.info(f"[WS Listener] Client connected from {websocket.remote_address}")
        try:
            async for message in websocket:
                try:
                    payload = json.loads(message)
                    if payload.get("type") == "incoming_msg" and self.callback:
                        msg_data = {
                            "local_id": payload.get("msg_id"),
                            "msg_type": 1, # Default text code
                            "timestamp": payload.get("timestamp"),
                            "sender_wxid": payload.get("sender_wxid"),
                            "content": payload.get("content"),
                            "is_self": False,
                            "is_group": payload.get("is_group", False)
                        }
                        self.callback(msg_data)
                except json.JSONDecodeError:
                    logger.warning(f"[WS Listener] Received invalid JSON payload: {message}")
                except Exception as e:
                    logger.error(f"[WS Listener] Error processing received payload: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.info("[WS Listener] Client connection closed.")

    def stop(self):
        """Shutdown the server and stop the event loop."""
        if self.server:
            self.server.close()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=2.0)
        logger.info("[WS Listener] Server shutdown completed.")
