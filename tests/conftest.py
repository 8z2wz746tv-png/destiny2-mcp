"""让测试在"干净克隆"（没有 `.env`）上也能跑。

`destiny_mcp.config` 在 **import 时**就把凭据读进模块级常量（`config.py` 顶部
`load_dotenv()` + `BUNGIE_API_KEY = os.getenv(...)`），所以：

- 没有 `.env` 时，凡是真正构造 Bungie 客户端的测试都会在 `validate_credentials()`
  拿到 `ConfigError`；干净克隆上实测 5 条失败。
- 单个测试文件里再写 `os.environ.setdefault(...)` 已经太晚 —— 配置早就快照过了，
  于是出现"单独跑绿、全量跑红"的顺序依赖。

conftest 比任何测试模块都先 import，所以在这里补上**假凭据**是最合适的位置。
这些值只用于构造客户端与签名校验，任何联网调用在测试里都被替身拦住了；
真要用真账号跑真机脚本（`scripts/run_corpus_weapon_rows.py` 等）时，`.env` 里
的真实值会覆盖这里的 setdefault，不受影响。
"""

from __future__ import annotations

import os

# `setdefault`：.env 或外部环境已经给了真值就不动它。
os.environ.setdefault("BUNGIE_API_KEY", "test-api-key")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "test-client-secret")
