import sys
import traceback
print("Starting test_main.py...")

try:
    print("Importing main...")
    import main
    print("main imported OK")

    print("Creating QApplication...")
    app = main.QApplication(sys.argv)
    print("QApplication created OK")

    print("Creating window...")
    window = main.DataProcessorWindow()
    print("DataProcessorWindow created OK")

    print("Showing window...")
    window.show()
    print("Window shown OK")

    print("Processing events for 2 seconds...")
    import time
    from PyQt6.QtCore import QTimer
    start = time.time()
    while time.time() - start < 2:
        app.processEvents()
        time.sleep(0.05)

    print("Test completed successfully!")
    sys.exit(0)
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)