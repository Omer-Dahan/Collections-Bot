import psutil
import os


def _is_this_bot(proc, cmdline, current_dir):
    """True only if the process runs this directory's bot.py."""
    script_dir = os.path.normcase(os.path.abspath(current_dir))
    try:
        proc_cwd = proc.cwd()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        proc_cwd = None

    for arg in cmdline:
        if os.path.basename(arg) != "bot.py":
            continue
        # Relative script paths are relative to the process working directory
        if os.path.isabs(arg):
            resolved = arg
        elif proc_cwd:
            resolved = os.path.join(proc_cwd, arg)
        else:
            resolved = None
        if resolved and os.path.normcase(os.path.dirname(os.path.abspath(resolved))) == script_dir:
            return True
        if proc_cwd and os.path.normcase(os.path.abspath(proc_cwd)) == script_dir:
            return True
    return False


def find_and_kill_bot_processes():
    """Finds and kills only python processes running this directory's bot.py."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    current_pid = os.getpid()

    try:
        current_proc = psutil.Process(current_pid)
        parent_proc = current_proc.parent()
        parent_pid = parent_proc.pid if parent_proc else None
    except Exception:
        parent_pid = None

    print("🔍 Scanning for hidden bot processes...\n")

    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'exe']):
        try:
            # We only care about python processes
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                # Skip the currently running script AND its parent (the new bot starting it)
                if proc.info['pid'] in (current_pid, parent_pid):
                    continue

                cmdline = proc.info.get('cmdline')
                if cmdline and _is_this_bot(proc, cmdline, current_dir):
                    exe_path = proc.info.get('exe', '')
                    print(f"⚠️ Zombie process detected:")
                    print(f"  - PID: {proc.info['pid']}")
                    print(f"  - Cmd: {' '.join(cmdline).lower()}")
                    print(f"  - Exe: {exe_path}")

                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                        print(f"✅ Process {proc.info['pid']} closed gracefully.")
                    except psutil.TimeoutExpired:
                        print(f"⏳ Process resisting... forcing kill.")
                        proc.kill()
                        proc.wait(timeout=3)
                        print(f"✅ Process {proc.info['pid']} force closed.")

                    killed_count += 1
                    print("-" * 40)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if killed_count == 0:
        print("✨ No zombie bot processes found. All good!")
    else:
        print(f"\n✅ Total processes killed: {killed_count}")

    print("\nDisconnecting previous file lock (if it exists)...")
    lock_file = os.path.join(current_dir, ".bot.pid")
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            print("Removed old lock file (.bot.pid).")
    except Exception as e:
        print(f"Note: Could not delete lock file (maybe still in use): {e}")

if __name__ == "__main__":
    find_and_kill_bot_processes()
