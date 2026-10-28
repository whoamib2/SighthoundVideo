"""Stop a process and the children it has already started."""

from __future__ import annotations

import os

import psutil


def kill_process_tree(pid):
    """Kill ``pid`` and every child process, including grandchildren.

    The parent is killed as well. A backend restart loop lives in that
    process, so leaving the parent alive lets it spawn the children again.
    The current process is never killed.
    """
    if pid is None or pid == os.getpid():
        return []
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return []
    processes = root.children(recursive=True)
    processes.append(root)
    killed = []
    for proc in processes:
        if proc.pid == os.getpid():
            continue
        try:
            proc.kill()
            killed.append(proc.pid)
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(processes, timeout=3)
    for proc in alive:
        if proc.pid == os.getpid():
            continue
        try:
            proc.kill()
            killed.append(proc.pid)
        except psutil.Error:
            pass
    return killed
