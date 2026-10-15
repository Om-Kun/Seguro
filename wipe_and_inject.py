import json
from src.memory.memory_manager import LongTermMemory

def inject_memory():
    ltm = LongTermMemory("data/long_term_memory.db")
    
    # Clear the database
    ltm._conn.execute("DELETE FROM workflows")
    ltm._conn.commit()

    zonal_query = "open brave web browser go to youtube . com search kai cenat gta6 and play his video on full screen"
    
    perfect_workflow = [
        {"role": "human", "content": zonal_query},
        {"role": "ai", "content": json.dumps({"thought": "I need to open the Windows start menu to launch Brave.", "action": {"tool": "gui_hotkey", "args": {"keys": ["win"]}}})},
        {"role": "ai", "content": json.dumps({"thought": "I need to wait for the start menu to open.", "action": {"tool": "wait", "args": {"seconds": 1.0}}})},
        {"role": "ai", "content": json.dumps({"thought": "Start menu is open. I will type brave.", "action": {"tool": "gui_type", "args": {"text": "brave"}}})},
        {"role": "ai", "content": json.dumps({"thought": "I will press enter to launch Brave Browser.", "action": {"tool": "gui_hotkey", "args": {"keys": ["enter"]}}})},
        {"role": "ai", "content": json.dumps({"thought": "I need to wait for Brave to open.", "action": {"tool": "wait", "args": {"seconds": 3.0}}})},
        {"role": "ai", "content": json.dumps({"thought": "Brave is open. I will type the youtube search URL directly.", "action": {"tool": "gui_type", "args": {"text": "youtube.com/results?search_query=kai+cenat+gta6"}}})},
        {"role": "ai", "content": json.dumps({"thought": "I will press enter to search.", "action": {"tool": "gui_hotkey", "args": {"keys": ["enter"]}}})},
        {"role": "ai", "content": json.dumps({"thought": "I need to wait for the search results to load.", "action": {"tool": "wait", "args": {"seconds": 3.0}}})},
        {"role": "ai", "content": json.dumps({"thought": "I will click the first video.", "action": {"tool": "gui_click", "args": {"x": 600, "y": 450, "button": "left"}}})},
        {"role": "ai", "content": json.dumps({"thought": "I need to wait for the video to start.", "action": {"tool": "wait", "args": {"seconds": 2.0}}})},
        {"role": "ai", "content": json.dumps({"thought": "I will press 'f' to make the video full screen.", "action": {"tool": "gui_type", "args": {"text": "f"}}})},
        {"role": "ai", "content": json.dumps({"thought": "Video is playing in full screen. Task complete.", "action": {"tool": "final_answer", "args": {"message": "Kai Cenat GTA6 video is now playing in full screen."}}})}
    ]
    
    # store_workflow computes the embedding automatically!
    ltm.store_workflow(
        user_query=zonal_query,
        action_sequence=perfect_workflow,
        outcome="success",
        execution_time_s=20.0,
        notes="Perfect Zonal Final Run"
    )
    
    ltm.close()
    print("Properly wiped and injected with correct BLOB embedding!")

if __name__ == "__main__":
    inject_memory()
