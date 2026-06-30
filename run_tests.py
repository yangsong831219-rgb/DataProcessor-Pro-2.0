"""运行测试套件的入口脚本。

py/ 已重命名为 dp_engine/，pytest 收集冲突已根治。
本脚本简化为干净的 pytest 入口。
"""

import sys
import pytest

if __name__ == '__main__':
    args = sys.argv[1:] if len(sys.argv) > 1 else ['tests/', '-v']
    sys.exit(pytest.main(args))
