"""入库索引的全局串行锁（v0 单机）。

ingest_dir 全量重扫重写 catalog/workspace/indexes，多入口并发（worker / approve / delete /
build-*）会互相覆盖。用一把进程级 RLock 串行化所有"改索引"的操作；RLock 允许同线程内
approve→ingest_default→ingest_dir 这类嵌套获取。
"""

import threading

INDEX_LOCK = threading.RLock()
