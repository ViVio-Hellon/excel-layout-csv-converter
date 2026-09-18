"""テストの入口

**取り込みより先に**ローカル領域を一時フォルダへ向けます。ログの置き場所
などは最初に触った時点で決まるので、ここで先回りしないと本番の記録に
混ざります(理由は `_isolation.py`)。
"""
from ._isolation import ensure_isolated

ensure_isolated()
