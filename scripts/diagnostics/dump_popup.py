import uiautomation as auto
import ctypes
import time
import sys

def main():
    print(f"System encoding: {sys.stdout.encoding}")
    popups = auto.GetRootControl().GetChildren()
    for w in popups:
        if w.ClassName == "mmui::ProfileUniquePop":
            print(f"FOUND ProfileUniquePop: {w.Name}, NativeHandle={w.NativeWindowHandle}")
            for c, d in auto.WalkControl(w):
                name = c.Name.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding) if c.Name else ''
                print(f"Depth {d} [{c.ControlTypeName}]: '{name}'")
    print("Done")

if __name__ == "__main__":
    main()
