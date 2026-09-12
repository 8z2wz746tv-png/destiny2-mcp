"""`python -m destiny_mcp` 入口 —— 写成 spawn 安全的，供构建求解的 worker 子进程重跑。

为什么单独有这么一个文件：`BuildCompute` 用 `anyio.to_process.run_sync` 把配装求解
放到子进程里跑（CPU 密集，不能阻塞事件循环）。anyio 的 worker 子进程会按路径
`runpy.run_path(__main__.__file__, run_name="__mp_main__")` **重新执行父进程的主模块**。

- 用控制台脚本 `.venv/bin/destiny-mcp` 启动：主模块是个不含相对导入的小启动器 → 子进程能跑；
- 用 `python -m destiny_mcp` 启动：主模块就是本文件，下面是**绝对导入** → 子进程也能跑；
- 用 `python -m destiny_mcp.server` 启动：主模块是 `server.py`，里面是相对导入
  （`from .services... import ...`）→ 子进程 `runpy` 时报
  `ImportError: attempted relative import with no known parent package`，
  于是任何走 worker 的 build intent 都以原始异常冒到 MCP 客户端（真机复现：术士 analyze 7.6s 裸抛
  `BrokenWorkerProcess`）。所以：**要 `-m` 就用 `python -m destiny_mcp`，别用 `-m destiny_mcp.server`。**

`if __name__ == "__main__"` 这个守卫是必须的：子进程以 `__mp_main__` 重跑本文件时，
不能真的把服务器再起一遍（那会递归起服务）。
"""

from __future__ import annotations

from destiny_mcp.server import main  # 绝对导入：子进程按路径重跑本文件时仍能解析

if __name__ == "__main__":
    main()
