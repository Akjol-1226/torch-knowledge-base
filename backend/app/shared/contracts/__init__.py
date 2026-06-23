"""跨模块通信契约。

模块 A 需要调用模块 B 的功能时，禁止 `from app.modules.B.service import ...`。
正确做法：B 在本目录暴露 Protocol / 抽象基类，A 通过依赖注入获取实现。

v0 阶段还没有跨模块调用场景，本目录暂为空。
Sprint-1 之后第一次跨模块调用出现时，在这里定义对应的 Port/Protocol。
"""
