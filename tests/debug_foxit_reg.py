import winreg

def walk(k, path, depth=0):
    if depth > 4:
        return
    i = 0
    while True:
        try:
            name, val, _ = winreg.EnumValue(k, i)
            i += 1
            low = (path + name).lower()
            if "mru" in low or "recent" in low or "lastfile" in low:
                print(path, "|", name, "=", str(val)[:120])
        except OSError:
            break
    j = 0
    while True:
        try:
            sub = winreg.EnumKey(k, j)
            j += 1
            with winreg.OpenKey(k, sub) as sk:
                walk(sk, path + "/" + sub, depth + 1)
        except OSError:
            break

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Foxit Software\Foxit PDF Reader\Continuous") as k:
    walk(k, "Continuous")
print("---done---")
