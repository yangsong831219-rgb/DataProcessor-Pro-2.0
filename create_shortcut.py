"""创建桌面快捷方式 - DataProcessor Pro 2.0"""

import os
import subprocess
from pathlib import Path


def create_shortcut():
    """创建桌面快捷方式"""

    # 项目路径
    project_path = os.path.abspath(os.path.dirname(__file__))
    venv_python = str(Path(project_path) / "venv" / "Scripts" / "python.exe")
    main_script = str(Path(project_path) / "main.py")

    # 桌面路径（D:\桌面文件）
    desktop = r"D:\桌面文件"
    shortcut_path = os.path.join(desktop, "DataProcessor Pro 2.0.lnk")

    # PowerShell 脚本
    ps_code = f'''
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{venv_python}"
$Shortcut.Arguments = '"{main_script}"'
$Shortcut.WorkingDirectory = "{project_path}"
$Shortcut.Description = "DataProcessor Pro 2.0 - 传感器数据分析软件"
$Shortcut.Save()
Write-Host "OK"
'''

    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps_code],
            capture_output=True,
            text=True,
            timeout=10
        )

        if 'OK' in result.stdout.strip():
            print(f"快捷方式已创建: {shortcut_path}")
            return True
        else:
            print(f"创建失败")
            return False

    except Exception as e:
        print(f"创建失败: {e}")
        return False


if __name__ == "__main__":
    print("=== DataProcessor Pro 2.0 快捷方式创建工具 ===")
    print()
    print(f"项目路径: {os.path.abspath(os.path.dirname(__file__))}")
    print(f"桌面路径: D:\\桌面文件")
    print()

    if create_shortcut():
        print()
        print("完成！双击桌面上的图标即可启动程序")
    else:
        print()
        print("请以管理员身份运行此脚本")