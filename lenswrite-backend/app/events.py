import asyncio
from collections import defaultdict

# 全局事件队列
event_queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)