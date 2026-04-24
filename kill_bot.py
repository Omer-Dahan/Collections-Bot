import psutil
import os
import time

def find_and_kill_bot_processes():
    """Finds and kills only python.exe processes running bot.py in the current directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    current_pid = os.getpid()
    target_script = "bot.py"
    
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
                if cmdline:
                    # Check if the command line that ran python contains 'bot.py'
                    joined_cmd = " ".join(cmdline).lower()
                    
                    # Ensure it's our bot (based on filename or path)
                    if target_script in joined_cmd:
                        exe_path = proc.info.get('exe', '')
                        print(f"⚠️ Zombie process detected:")
                        print(f"  - PID: {proc.info['pid']}")
                        print(f"  - Cmd: {joined_cmd}")
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
    lock_file = os.path.join(current_dir, ".bot.lock")
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            print("Removed old lock file (.bot.lock).")
    except Exception as e:
         print(f"Note: Could not delete lock file (maybe still in use): {e}")

if __name__ == "__main__":
    find_and_kill_bot_processes()
