import sys
import os

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

base_dir = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    base_dir = getattr(sys, '_MEIPASS', base_dir)
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

def main():
    cli_flags = ["--task", "--resume", "--list-workflows", "--help", "-h", "--no-hud"]
    has_cli_flag = any(arg in sys.argv[1:] for arg in cli_flags)
    
    if has_cli_flag:
        import seguro.main
        seguro.main.main()
    else:
        import seguro.ui.app
        seguro.ui.app.main()

if __name__ == "__main__":
    main()
